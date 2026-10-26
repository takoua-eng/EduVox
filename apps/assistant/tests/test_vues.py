"""Tests d'intégration des vues : CRUD des sessions, questions, avis, export (Gemini simulé)."""

import os
from unittest import mock

from django.urls import reverse

from apps.assistant.models import JournalIA, MessageVocal, SessionAssistant

from .base import BaseAssistantTestCase, patch_gemini

AJAX = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


class SessionsCrudTests(BaseAssistantTestCase):
    def setUp(self):
        super().setUp()
        self.connecter()

    def test_liste(self):
        reponse = self.client.get(reverse("assistant"))
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "Révisions")
        self.assertContains(reponse, "<h1", count=1)

    def test_recherche_et_filtres(self):
        SessionAssistant.objects.create(utilisateur=self.etudiant, titre="Physique quantique", active=False)
        self.assertNotContains(self.client.get(reverse("assistant"), {"q": "quantique"}), "Révisions")
        self.assertContains(self.client.get(reverse("assistant"), {"etat": "fermees"}), "Physique quantique")
        self.assertNotContains(self.client.get(reverse("assistant"), {"etat": "ouvertes"}), "Physique quantique")

    def test_creer(self):
        reponse = self.client.post(reverse("creer-session-assistant"), {"titre": "Nouvelle", "cours": self.cours.pk})
        session = SessionAssistant.objects.get(titre="Nouvelle")
        self.assertRedirects(reponse, reverse("session-assistant", args=[session.pk]))
        self.assertEqual(session.utilisateur, self.etudiant)
        self.assertEqual(session.cours, self.cours)

    def test_creer_titre_invalide(self):
        reponse = self.client.post(reverse("creer-session-assistant"), {"titre": " a "})
        self.assertEqual(reponse.status_code, 400)
        self.assertContains(reponse, "au moins 3 caractères", status_code=400)
        self.assertContains(reponse, 'aria-invalid="true"', status_code=400)

    def test_formulaire_ne_propose_que_les_cours_visibles(self):
        reponse = self.client.get(reverse("creer-session-assistant"))
        self.assertContains(reponse, "Algèbre linéaire")
        self.assertNotContains(reponse, "Brouillon secret")

    def test_detail(self):
        MessageVocal.objects.create(session=self.session, role=MessageVocal.Role.ASSISTANT,
                                    texte="Un **déterminant** est un nombre.")
        reponse = self.client.get(reverse("session-assistant", args=[self.session.pk]))
        self.assertContains(reponse, "<strong>déterminant</strong>", html=False)
        self.assertContains(reponse, 'role="log"')
        self.assertContains(reponse, f"/ws/assistant/sessions/{self.session.pk}/")

    def test_modifier(self):
        reponse = self.client.post(reverse("modifier-session-assistant", args=[self.session.pk]),
                                   {"titre": "Révisions finales", "cours": ""})
        self.assertRedirects(reponse, reverse("session-assistant", args=[self.session.pk]))
        self.session.refresh_from_db()
        self.assertEqual(self.session.titre, "Révisions finales")
        self.assertIsNone(self.session.cours)

    def test_modifier_invalide(self):
        reponse = self.client.post(reverse("modifier-session-assistant", args=[self.session.pk]), {"titre": ""})
        self.assertEqual(reponse.status_code, 400)

    def test_fermer_rouvrir(self):
        self.client.post(reverse("fermer-session-assistant", args=[self.session.pk]))
        self.session.refresh_from_db()
        self.assertFalse(self.session.active)
        self.assertContains(self.client.get(reverse("session-assistant", args=[self.session.pk])), "Rouvrir la session")
        self.client.post(reverse("rouvrir-session-assistant", args=[self.session.pk]))
        self.session.refresh_from_db()
        self.assertTrue(self.session.active)

    def test_supprimer(self):
        MessageVocal.objects.create(session=self.session, role=MessageVocal.Role.UTILISATEUR, texte="Q")
        self.assertEqual(self.client.get(reverse("supprimer-session-assistant", args=[self.session.pk])).status_code, 200)
        reponse = self.client.post(reverse("supprimer-session-assistant", args=[self.session.pk]))
        self.assertRedirects(reponse, reverse("assistant"))
        self.assertFalse(SessionAssistant.objects.filter(pk=self.session.pk).exists())
        self.assertFalse(MessageVocal.objects.exists())

    def test_exporter_markdown(self):
        MessageVocal.objects.create(session=self.session, role=MessageVocal.Role.UTILISATEUR, texte="Ma question")
        MessageVocal.objects.create(session=self.session, role=MessageVocal.Role.ASSISTANT, texte="Ma réponse")
        reponse = self.client.get(reverse("exporter-session-assistant", args=[self.session.pk]))
        self.assertEqual(reponse["Content-Type"], "text/markdown; charset=utf-8")
        self.assertIn('filename="revisions.md"', reponse["Content-Disposition"])
        contenu = reponse.content.decode()
        self.assertIn("# Révisions", contenu)
        self.assertIn("Ma question", contenu)
        self.assertIn("Ma réponse", contenu)


class QuestionsTests(BaseAssistantTestCase):
    def setUp(self):
        super().setUp()
        self.connecter()
        self.url = reverse("question-assistant", args=[self.session.pk])

    def test_question_sans_javascript(self):
        with patch_gemini(reponses="Le déterminant indique si la matrice est **inversible**."):
            reponse = self.client.post(self.url, {"texte": "À quoi sert le déterminant ?"})
        question, reponse_ia = self.session.messages.all()
        self.assertRedirects(reponse, f"{reverse('session-assistant', args=[self.session.pk])}#message-{reponse_ia.pk}",
                             fetch_redirect_response=False)
        self.assertEqual(question.texte, "À quoi sert le déterminant ?")
        self.assertEqual(reponse_ia.statut, MessageVocal.Statut.PRET)
        self.assertIn("inversible", reponse_ia.texte)
        self.assertIsNotNone(reponse_ia.duree_ms)
        self.assertTrue(JournalIA.objects.filter(type_appel=JournalIA.Type.REPONSE, succes=True).exists())

    def test_question_ajax(self):
        with patch_gemini(reponses="Réponse"):
            reponse = self.client.post(self.url, {"texte": "Question ?"}, **AJAX)
        self.assertEqual(reponse.status_code, 200)
        donnees = reponse.json()
        self.assertIn("<p>Réponse</p>", donnees["html"])
        self.assertEqual(MessageVocal.objects.get(pk=donnees["reponse_id"]).texte, "Réponse")

    def test_question_vide(self):
        reponse = self.client.post(self.url, {"texte": ""}, **AJAX)
        self.assertEqual(reponse.status_code, 400)
        self.assertFalse(self.session.messages.exists())

    def test_erreur_gemini_affichee_et_journalisee(self):
        with patch_gemini(reponses=TimeoutError()):
            reponse = self.client.post(self.url, {"texte": "Question ?"}, **AJAX)
        self.assertEqual(reponse.status_code, 503)
        self.assertIn("pas répondu à temps", reponse.json()["erreur"])
        reponse_ia = self.session.messages.get(role=MessageVocal.Role.ASSISTANT)
        self.assertEqual(reponse_ia.statut, MessageVocal.Statut.ERREUR)
        self.assertTrue(JournalIA.objects.filter(succes=False, code="delai").exists())

    def test_reponse_vide_de_gemini(self):
        with patch_gemini(reponses=""):
            reponse = self.client.post(self.url, {"texte": "Question ?"}, **AJAX)
        self.assertEqual(reponse.status_code, 503)

    def test_session_fermee(self):
        self.session.fermer()
        reponse = self.client.post(self.url, {"texte": "Question ?"})
        self.assertRedirects(reponse, reverse("session-assistant", args=[self.session.pk]))
        self.assertFalse(self.session.messages.exists())

    def test_limite_horaire(self):
        with mock.patch.dict(os.environ, {"ASSISTANT_QUESTIONS_PAR_HEURE": "1"}), patch_gemini(reponses="ok"):
            self.client.post(self.url, {"texte": "Q1"}, **AJAX)
            reponse = self.client.post(self.url, {"texte": "Q2"}, **AJAX)
        self.assertEqual(reponse.status_code, 400)
        self.assertIn("limite", reponse.json()["erreur"])

    def test_historique_envoye_a_gemini(self):
        MessageVocal.objects.create(session=self.session, role=MessageVocal.Role.UTILISATEUR, texte="Première question")
        MessageVocal.objects.create(session=self.session, role=MessageVocal.Role.ASSISTANT, texte="Première réponse")
        appels = []
        from .base import faux_client
        client = faux_client(reponses="ok")
        original = client.models.generate_content
        client.models.generate_content = lambda **kw: appels.append(kw) or original(**kw)
        with mock.patch("apps.assistant.services._client", return_value=client):
            self.client.post(self.url, {"texte": "Deuxième question"})
        contenus = appels[0]["contents"]
        self.assertEqual([c.role for c in contenus], ["user", "model", "user"])
        self.assertEqual(contenus[-1].parts[0].text, "Deuxième question")
        self.assertIn("déterminant", appels[0]["config"].system_instruction)  # contexte du cours


class MessagesTests(BaseAssistantTestCase):
    def setUp(self):
        super().setUp()
        self.connecter()
        self.question = MessageVocal.objects.create(session=self.session, role=MessageVocal.Role.UTILISATEUR, texte="Q")
        self.reponse = MessageVocal.objects.create(session=self.session, role=MessageVocal.Role.ASSISTANT, texte="R")

    def test_evaluer_puis_retirer(self):
        url = reverse("evaluer-message-assistant", args=[self.reponse.pk])
        self.assertEqual(self.client.post(url, {"evaluation": 1}, **AJAX).json(), {"evaluation": 1})
        self.assertEqual(self.client.post(url, {"evaluation": -1}, **AJAX).json(), {"evaluation": -1})
        self.assertEqual(self.client.post(url, {"evaluation": 0}, **AJAX).json(), {"evaluation": None})

    def test_evaluation_invalide(self):
        url = reverse("evaluer-message-assistant", args=[self.reponse.pk])
        self.assertEqual(self.client.post(url, {"evaluation": 5}).status_code, 400)
        self.assertEqual(self.client.post(url, {"evaluation": "abc"}).status_code, 400)
        url_question = reverse("evaluer-message-assistant", args=[self.question.pk])
        self.assertEqual(self.client.post(url_question, {"evaluation": 1}).status_code, 400)

    def test_supprimer_un_echange(self):
        autre = MessageVocal.objects.create(session=self.session, role=MessageVocal.Role.UTILISATEUR, texte="Q2")
        self.client.post(reverse("supprimer-message-assistant", args=[self.question.pk]))
        self.assertEqual(list(self.session.messages.all()), [autre])

    def test_fragment(self):
        reponse = self.client.get(reverse("fragment-message-assistant", args=[self.reponse.pk]))
        self.assertContains(reponse, f'id="message-{self.reponse.pk}"')
        self.assertContains(reponse, 'aria-label="Réponse utile"')
        self.assertNotContains(reponse, "<html")
