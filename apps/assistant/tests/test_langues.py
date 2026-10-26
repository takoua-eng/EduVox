"""Multilingue : détection de la langue, consigne donnée à Gemini, affichage RTL, transcription."""

from unittest import mock

from django.urls import reverse

from apps.assistant.forms import SessionForm
from apps.assistant.langues import detecter_langue_texte
from apps.assistant.models import MessageVocal
from apps.assistant.services import configuration_reponse, preparer_question

from .base import BaseAssistantTestCase, patch_gemini
from .test_ia import fichier_wav

AJAX = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


class DetectionTexteTests(BaseAssistantTestCase):
    def test_langues(self):
        cas = {
            "Qu'est-ce qu'une matrice inversible ?": "fr",
            "Explique-moi le théorème de Pythagore": "fr",
            "What is the difference between a list and a tuple?": "en",
            "Can you explain photosynthesis please": "en",
            "ما هو الفرق بين القائمة والصف في بايثون؟": "ar",
            "12345": "fr",
            "": "fr",
        }
        for texte, attendu in cas.items():
            self.assertEqual(detecter_langue_texte(texte), attendu, texte)


class ConsigneLangueTests(BaseAssistantTestCase):
    def systeme(self, texte):
        self.session.cours = None
        with patch_gemini():
            question, reponse = preparer_question(self.session, texte)
            return question, reponse, configuration_reponse(self.session, question, reponse).system_instruction

    def test_langue_auto_suit_la_question(self):
        question, reponse, systeme = self.systeme("What is a determinant?")
        self.assertEqual((question.langue, reponse.langue), ("en", "en"))
        self.assertIn("La question est en anglais : réponds en anglais.", systeme)

    def test_langue_imposee_par_la_session(self):
        self.session.langue = "ar"
        self.session.save()
        question, reponse, systeme = self.systeme("Qu'est-ce qu'un déterminant ?")
        self.assertEqual(reponse.langue, "ar")
        self.assertIn("Réponds toujours en arabe", systeme)

    def test_reponse_arabe_affichee_de_droite_a_gauche(self):
        self.connecter()
        with patch_gemini(reponses="المحدد هو عدد."):
            donnees = self.client.post(reverse("question-assistant", args=[self.session.pk]),
                                       {"texte": "ما هو المحدد؟"}, **AJAX).json()
        html = self.client.get(reverse("fragment-message-assistant", args=[donnees["reponse_id"]])).content.decode()
        self.assertIn('lang="ar" dir="rtl"', html)
        self.assertIn('data-langue="ar"', html)

    def test_choix_de_la_langue_dans_le_formulaire(self):
        self.assertTrue(SessionForm({"titre": "English", "langue": "en"}, user=self.etudiant).is_valid())
        form = SessionForm({"titre": "Klingon", "langue": "tlh"}, user=self.etudiant)
        self.assertFalse(form.is_valid())
        self.assertIn("non prise en charge", form.errors["langue"][0])


class TranscriptionMultilingueTests(BaseAssistantTestCase):
    def setUp(self):
        super().setUp()
        self.connecter()

    def envoyer(self):
        with self.captureOnCommitCallbacks(execute=True):
            reponse = self.client.post(reverse("audio-assistant", args=[self.session.pk]), {"audio": fichier_wav()},
                                       **AJAX)
        return MessageVocal.objects.get(pk=reponse.json()["id"])

    def test_langue_detectee_enregistree(self):
        with mock.patch("apps.assistant.tasks.transcrire_question",
                        return_value=("What is a matrix?", "en", 1.0)) as transcrire:
            message = self.envoyer()
        transcrire.assert_called_once()
        self.assertIsNone(transcrire.call_args.kwargs["langue"])  # session en automatique : détection
        message.refresh_from_db()
        self.assertEqual((message.texte, message.langue), ("What is a matrix?", "en"))

    def test_langue_de_la_session_imposee_a_whisper(self):
        self.session.langue = "ar"
        self.session.save()
        with mock.patch("apps.assistant.tasks.transcrire_question", return_value=("ما هو المحدد؟", "ar", 1.0)) as t:
            self.envoyer()
        self.assertEqual(t.call_args.kwargs["langue"], "ar")
