"""Contrôle de saisie (serveur) et mode de réponse écrit / parole."""

from django.urls import reverse

from apps.assistant.forms import IllustrationForm, SessionForm, nettoyer_espaces, valider_question
from apps.assistant.models import Illustration, MessageVocal, SessionAssistant

from .base import BaseAssistantTestCase, patch_gemini

AJAX = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


class SessionFormTests(BaseAssistantTestCase):
    def form(self, donnees, instance=None, user=None):
        return SessionForm(donnees, instance=instance, user=user or self.etudiant)

    def test_titre_nettoye(self):
        form = self.form({"titre": "   Révisions   de   maths  "})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["titre"], "Révisions de maths")
        self.assertEqual(form.cleaned_data["mode_reponse"], SessionAssistant.ModeReponse.TEXTE)

    def test_titre_invalide(self):
        cas = {"": "Donnez un titre", "ab": "au moins 3", "12345": "au moins une lettre", "!!! ???": "au moins une lettre",
               "x" * 151: "150 caractères"}
        for titre, attendu in cas.items():
            form = self.form({"titre": titre})
            self.assertFalse(form.is_valid(), titre)
            self.assertIn(attendu, form.errors["titre"][0], titre)

    def test_titre_en_double_pour_le_meme_utilisateur(self):
        form = self.form({"titre": "RÉVISIONS"})  # la session « Révisions » existe déjà
        self.assertFalse(form.is_valid())
        self.assertIn("déjà une session", form.errors["titre"][0])
        # ... mais autorisé pour un autre utilisateur, et pour la session elle-même
        self.assertTrue(self.form({"titre": "Révisions"}, user=self.autre_etudiant).is_valid())
        self.assertTrue(self.form({"titre": "Révisions"}, instance=self.session).is_valid())

    def test_mode_reponse(self):
        self.assertEqual(self.form({"titre": "Oral", "mode_reponse": "VOCAL"}).is_valid(), True)
        form = self.form({"titre": "Oral", "mode_reponse": "CHANT"})
        self.assertFalse(form.is_valid())
        self.assertIn("Écrit", form.errors["mode_reponse"][0])

    def test_attributs_html_coherents_avec_le_serveur(self):
        html = str(self.form({})["titre"])
        for attribut in ('minlength="3"', 'maxlength="150"', "required", 'aria-describedby="titre-aide id_titre_erreur"'):
            self.assertIn(attribut, html)


class QuestionValidationTests(BaseAssistantTestCase):
    def test_regles(self):
        self.assertEqual(valider_question("  Qu'est-ce   qu'une   matrice ?  "), "Qu'est-ce qu'une matrice ?")
        for texte in ("", "   ", "a", "12345 ???", "x" * 2001):
            with self.assertRaises(Exception, msg=texte):
                valider_question(texte)

    def test_nettoyage_garde_les_paragraphes(self):
        self.assertEqual(nettoyer_espaces("Ligne 1  \n\n\n\n  Ligne   2"), "Ligne 1\n\nLigne 2")

    def test_vue_renvoie_le_message_precis(self):
        self.connecter()
        reponse = self.client.post(reverse("question-assistant", args=[self.session.pk]), {"texte": "?? !!"}, **AJAX)
        self.assertEqual(reponse.status_code, 400)
        self.assertIn("au moins une lettre", reponse.json()["erreur"])
        self.assertFalse(MessageVocal.objects.exists())

    def test_question_nettoyee_avant_envoi(self):
        self.connecter()
        with patch_gemini(reponses="ok"):
            self.client.post(reverse("question-assistant", args=[self.session.pk]),
                             {"texte": "  Bonjour    l'assistant  "}, **AJAX)
        self.assertEqual(self.session.messages.first().texte, "Bonjour l'assistant")


class IllustrationValidationTests(BaseAssistantTestCase):
    def test_regles(self):
        self.assertTrue(IllustrationForm({"type_illustration": "DIAGRAMME", "demande": "Cycle de l'eau"}).is_valid())
        cas = {"": "Décrivez", "ab": "au moins 3", "123 456": "au moins une lettre", "x" * 501: "500 caractères"}
        for demande, attendu in cas.items():
            form = IllustrationForm({"type_illustration": "DIAGRAMME", "demande": demande})
            self.assertFalse(form.is_valid())
            self.assertIn(attendu, form.premiere_erreur(), demande)
        self.assertFalse(IllustrationForm({"type_illustration": "VIDEO", "demande": "Cycle"}).is_valid())

    def test_vue_renvoie_le_message_precis(self):
        self.connecter()
        reponse = self.client.post(reverse("illustrer-assistant", args=[self.session.pk]),
                                   {"type_illustration": "DIAGRAMME", "demande": "123"}, **AJAX)
        self.assertEqual(reponse.status_code, 400)
        self.assertIn("au moins une lettre", reponse.json()["erreur"])
        self.assertFalse(Illustration.objects.exists())


class ModeReponseTests(BaseAssistantTestCase):
    def setUp(self):
        super().setUp()
        self.connecter()
        self.url = reverse("mode-reponse-assistant", args=[self.session.pk])

    def test_passer_en_mode_parole_puis_ecrit(self):
        self.assertEqual(self.client.post(self.url, {"mode_reponse": "VOCAL"}, **AJAX).json(), {"mode_reponse": "VOCAL"})
        self.session.refresh_from_db()
        self.assertEqual(self.session.mode_reponse, SessionAssistant.ModeReponse.VOCAL)
        page = self.client.get(reverse("session-assistant", args=[self.session.pk]))
        self.assertContains(page, 'data-mode-reponse="VOCAL"')
        self.assertContains(page, 'id="mode-VOCAL" value="VOCAL" checked')
        self.client.post(self.url, {"mode_reponse": "TEXTE"})
        self.session.refresh_from_db()
        self.assertEqual(self.session.mode_reponse, SessionAssistant.ModeReponse.TEXTE)

    def test_mode_invalide(self):
        self.assertEqual(self.client.post(self.url, {"mode_reponse": "CHANT"}, **AJAX).status_code, 400)

    def test_choix_a_la_creation(self):
        self.client.post(reverse("creer-session-assistant"), {"titre": "À l'oral", "mode_reponse": "VOCAL"})
        self.assertEqual(SessionAssistant.objects.get(titre="À l'oral").mode_reponse, "VOCAL")

    def test_mode_d_une_autre_session_refuse(self):
        self.connecter(self.autre_etudiant)
        self.assertEqual(self.client.post(self.url, {"mode_reponse": "VOCAL"}).status_code, 404)
