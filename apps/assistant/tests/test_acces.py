"""Contrôle d'accès : impossible de contourner les rôles ou d'accéder aux données d'un autre via l'URL."""

from django.urls import reverse

from apps.assistant.models import Illustration, MessageVocal, SessionAssistant

from .base import BaseAssistantTestCase


class AccesTests(BaseAssistantTestCase):
    def setUp(self):
        super().setUp()
        self.question = MessageVocal.objects.create(session=self.session, role=MessageVocal.Role.UTILISATEUR, texte="Q")
        self.reponse = MessageVocal.objects.create(session=self.session, role=MessageVocal.Role.ASSISTANT, texte="R")
        self.illustration = Illustration.objects.create(session=self.session, demande="schéma",
                                                        statut=Illustration.Statut.TERMINEE,
                                                        type_illustration=Illustration.Type.DIAGRAMME,
                                                        code_mermaid="flowchart TD\nA-->B")
        s = self.session.pk
        self.urls_get = [
            reverse("session-assistant", args=[s]), reverse("modifier-session-assistant", args=[s]),
            reverse("supprimer-session-assistant", args=[s]), reverse("exporter-session-assistant", args=[s]),
            reverse("fragment-message-assistant", args=[self.question.pk]),
            reverse("etat-message-assistant", args=[self.question.pk]),
            reverse("etat-illustration-assistant", args=[self.illustration.pk]),
        ]
        self.urls_post = [
            reverse("modifier-session-assistant", args=[s]), reverse("supprimer-session-assistant", args=[s]),
            reverse("fermer-session-assistant", args=[s]), reverse("rouvrir-session-assistant", args=[s]),
            reverse("question-assistant", args=[s]), reverse("audio-assistant", args=[s]),
            reverse("illustrer-assistant", args=[s]),
            reverse("evaluer-message-assistant", args=[self.reponse.pk]),
            reverse("supprimer-message-assistant", args=[self.question.pk]),
            reverse("supprimer-illustration-assistant", args=[self.illustration.pk]),
        ]

    def test_anonyme_redirige_vers_connexion(self):
        for url in [reverse("assistant"), *self.urls_get]:
            reponse = self.client.get(url)
            self.assertEqual(reponse.status_code, 302, url)
            self.assertIn(reverse("connexion"), reponse["Location"])

    def test_enseignant_sans_permission_refuse(self):
        """Le rôle Enseignant n'a pas la permission « poser_question » (roles.py) : 403."""
        self.connecter(self.prof)
        self.assertEqual(self.client.get(reverse("assistant")).status_code, 403)
        self.assertEqual(self.client.get(reverse("creer-session-assistant")).status_code, 403)

    def test_autre_etudiant_obtient_404_partout(self):
        self.connecter(self.autre_etudiant)
        for url in self.urls_get:
            self.assertEqual(self.client.get(url).status_code, 404, url)
        for url in self.urls_post:
            self.assertEqual(self.client.post(url, {"evaluation": 1, "titre": "Piratée", "texte": "x"}).status_code,
                             404, url)
        self.session.refresh_from_db()
        self.assertEqual(self.session.titre, "Révisions")
        self.assertTrue(MessageVocal.objects.filter(pk=self.question.pk).exists())
        self.assertTrue(Illustration.objects.filter(pk=self.illustration.pk).exists())

    def test_admin_consulte_en_lecture_seule(self):
        """L'admin lit toutes les conversations dans l'espace de modération, sans pouvoir y écrire."""
        self.connecter(self.admin)
        liste = self.client.get(reverse("moderation-assistant"))
        self.assertContains(liste, "Révisions")
        self.assertContains(liste, "etudiant@eduvox.test")

        page = self.client.get(reverse("moderation-session-assistant", args=[self.session.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "lecture seule")
        self.assertContains(page, f'id="message-{self.question.pk}"')
        for absent in ('id="form-question"', 'id="form-illustration"', 'id="form-mode"', "ast-form-supprimer",
                       "ast-form-evaluer", reverse("supprimer-session-assistant", args=[self.session.pk])):
            self.assertNotContains(page, absent)

        export = self.client.get(reverse("moderation-exporter-assistant", args=[self.session.pk]))
        self.assertIn("# Révisions", export.content.decode())

        # Les URL de l'utilisateur restent fermées : l'admin ne peut ni écrire ni modifier
        self.assertEqual(self.client.get(reverse("session-assistant", args=[self.session.pk])).status_code, 404)
        for url in self.urls_post:
            self.assertEqual(self.client.post(url, {"texte": "x", "titre": "Modifiée", "evaluation": 1}).status_code,
                             404, url)
        self.session.refresh_from_db()
        self.assertEqual(self.session.titre, "Révisions")

    def test_moderation_filtres(self):
        SessionAssistant.objects.create(utilisateur=self.autre_etudiant, titre="Chimie organique", active=False)
        self.connecter(self.admin)
        url = reverse("moderation-assistant")
        self.assertNotContains(self.client.get(url, {"utilisateur": self.etudiant.pk}), "Chimie organique")
        self.assertContains(self.client.get(url, {"q": "autre_etudiant"}), "Chimie organique")
        self.assertContains(self.client.get(url, {"etat": "fermees"}), "Chimie organique")
        self.assertNotContains(self.client.get(url, {"etat": "fermees"}), ">Révisions<")
        self.assertEqual(self.client.get(url, {"utilisateur": "abc"}).status_code, 200)

    def test_moderation_reservee_a_l_admin(self):
        for user in (self.etudiant, self.prof):
            self.connecter(user)
            for url in (reverse("moderation-assistant"), reverse("moderation-session-assistant", args=[self.session.pk]),
                        reverse("moderation-exporter-assistant", args=[self.session.pk])):
                self.assertEqual(self.client.get(url).status_code, 403, url)

    def test_etudiant_informe_de_la_consultation(self):
        self.connecter()
        self.assertContains(self.client.get(reverse("assistant")), "peuvent consulter les conversations")

    def test_liste_ne_montre_que_mes_sessions(self):
        SessionAssistant.objects.create(utilisateur=self.autre_etudiant, titre="Session privée de l'autre")
        self.connecter()
        reponse = self.client.get(reverse("assistant"))
        self.assertContains(reponse, "Révisions")
        self.assertNotContains(reponse, "Session privée de l&#x27;autre")

    def test_galerie_ne_montre_que_mes_illustrations(self):
        autre = SessionAssistant.objects.create(utilisateur=self.autre_etudiant, titre="Autre")
        Illustration.objects.create(session=autre, demande="illustration de l'autre", statut=Illustration.Statut.TERMINEE,
                                    type_illustration=Illustration.Type.DIAGRAMME, code_mermaid="flowchart TD\nX-->Y")
        self.connecter()
        reponse = self.client.get(reverse("galerie-assistant"))
        self.assertContains(reponse, "schéma")
        self.assertNotContains(reponse, "illustration de l")

    def test_cours_non_visible_refuse_a_la_creation(self):
        self.connecter()
        reponse = self.client.post(reverse("creer-session-assistant"),
                                   {"titre": "Tentative", "cours": self.cours_brouillon.pk})
        self.assertEqual(reponse.status_code, 400)
        self.assertFalse(SessionAssistant.objects.filter(titre="Tentative").exists())

    def test_get_sur_action_post_refuse(self):
        self.connecter()
        self.assertEqual(self.client.get(reverse("fermer-session-assistant", args=[self.session.pk])).status_code, 405)
