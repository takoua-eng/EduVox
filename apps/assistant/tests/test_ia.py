"""Tâches IA : transcription Whisper et illustrations (Whisper, Gemini et Hugging Face simulés)."""

import os
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from apps.assistant.models import Illustration, JournalIA, MessageVocal

from .base import BaseAssistantTestCase, patch_gemini, wav_bytes

AJAX = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


def fichier_wav(secondes=1.0):
    return SimpleUploadedFile("question.wav", wav_bytes(secondes), content_type="audio/wav")


class TranscriptionTests(BaseAssistantTestCase):
    def setUp(self):
        super().setUp()
        self.connecter()
        self.url = reverse("audio-assistant", args=[self.session.pk])

    def envoyer(self, fichier=None):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(self.url, {"audio": fichier or fichier_wav()}, **AJAX)

    @mock.patch("apps.assistant.tasks.transcrire_question", return_value=("Qu'est-ce qu'un déterminant ?", "fr", 1.0))
    def test_transcription_reussie(self, transcrire):
        reponse = self.envoyer()
        self.assertEqual(reponse.status_code, 202)
        etat = self.client.get(reponse.json()["url_etat"]).json()
        self.assertTrue(etat["fini"])
        self.assertEqual(etat["statut"], MessageVocal.Statut.PRET)
        self.assertEqual(etat["texte"], "Qu'est-ce qu'un déterminant ?")
        message = MessageVocal.objects.get(pk=etat["id"])
        self.assertEqual(message.mode, MessageVocal.Mode.VOCAL)
        self.assertAlmostEqual(message.duree_audio_s, 1.0, places=1)
        self.assertTrue(JournalIA.objects.filter(type_appel=JournalIA.Type.TRANSCRIPTION, succes=True).exists())

    @mock.patch("apps.assistant.tasks.transcrire_question", side_effect=ValueError("Aucune parole n'a été détectée."))
    def test_silence(self, transcrire):
        etat = self.client.get(self.envoyer().json()["url_etat"]).json()
        self.assertEqual(etat["statut"], MessageVocal.Statut.ERREUR)
        self.assertIn("parole", etat["erreur"])

    @mock.patch("apps.assistant.tasks.transcrire_question", side_effect=RuntimeError("panne"))
    def test_erreur_inattendue(self, transcrire):
        with self.assertLogs("apps.assistant.tasks", "ERROR"):
            etat = self.client.get(self.envoyer().json()["url_etat"]).json()
        self.assertEqual(etat["statut"], MessageVocal.Statut.ERREUR)
        self.assertTrue(JournalIA.objects.filter(type_appel=JournalIA.Type.TRANSCRIPTION, succes=False).exists())

    @mock.patch("apps.assistant.tasks.transcrire_question")
    def test_enregistrement_trop_long_ou_trop_court(self, transcrire):
        with mock.patch.dict(os.environ, {"ASSISTANT_AUDIO_MAX_S": "1"}):
            etat = self.client.get(self.envoyer(fichier_wav(2)).json()["url_etat"]).json()
        self.assertIn("trop long", etat["erreur"])
        etat = self.client.get(self.envoyer(fichier_wav(0.2)).json()["url_etat"]).json()
        self.assertIn("trop court", etat["erreur"])
        transcrire.assert_not_called()

    def test_fichier_non_wav_refuse(self):
        reponse = self.envoyer(SimpleUploadedFile("x.wav", b"<?php echo 1; ?>" * 10))
        self.assertEqual(reponse.status_code, 400)
        self.assertFalse(MessageVocal.objects.exists())

    def test_sans_fichier(self):
        self.assertEqual(self.client.post(self.url, {}, **AJAX).status_code, 400)

    def test_fichier_trop_gros(self):
        with mock.patch.dict(os.environ, {"ASSISTANT_AUDIO_MAX_MO": "0"}):
            self.assertEqual(self.envoyer().status_code, 400)

    def test_session_fermee(self):
        self.session.fermer()
        self.assertEqual(self.envoyer().status_code, 400)

    def test_celery_injoignable(self):
        with mock.patch("apps.assistant.tasks.transcrire_message.delay", side_effect=ConnectionError),                 self.assertLogs("apps.assistant.tasks", "ERROR"):
            reponse = self.envoyer()
        etat = self.client.get(reponse.json()["url_etat"]).json()
        self.assertEqual(etat["statut"], MessageVocal.Statut.ERREUR)
        self.assertIn("injoignable", etat["erreur"])

    @mock.patch("apps.assistant.tasks.transcrire_question", return_value=("Bonjour", "fr", 1.0))
    def test_question_dictee_puis_reponse(self, transcrire):
        message_id = self.client.get(self.envoyer().json()["url_etat"]).json()["id"]
        with patch_gemini(reponses="Bonjour ! Comment puis-je t'aider ?"):
            reponse = self.client.post(reverse("question-assistant", args=[self.session.pk]),
                                       {"message_id": message_id, "texte": "Bonjour"}, **AJAX)
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.json()["question"]["id"], message_id)
        self.assertEqual(self.session.messages.count(), 2)


class IllustrationTests(BaseAssistantTestCase):
    def setUp(self):
        super().setUp()
        self.connecter()
        self.url = reverse("illustrer-assistant", args=[self.session.pk])

    def demander(self, type_illustration=Illustration.Type.DIAGRAMME, demande="Le calcul d'un déterminant", **extra):
        with self.captureOnCommitCallbacks(execute=True):
            reponse = self.client.post(self.url, {"type_illustration": type_illustration, "demande": demande, **extra},
                                       **AJAX)
        return reponse

    def test_diagramme(self):
        with patch_gemini(reponses={"code_mermaid": "flowchart TD\n  A[Matrice] --> B{det = 0 ?}",
                                    "texte_alternatif": "Étapes du calcul"}):
            reponse = self.demander()
        self.assertEqual(reponse.status_code, 202)
        illustration = Illustration.objects.get()
        self.assertEqual(illustration.statut, Illustration.Statut.TERMINEE)
        self.assertTrue(illustration.code_mermaid.startswith("flowchart TD"))
        self.assertEqual(illustration.texte_alternatif, "Étapes du calcul")
        html = self.client.get(reponse.json()["url_etat"]).json()["html"]
        self.assertIn('role="img"', html)
        self.assertIn('aria-label="Étapes du calcul"', html)
        self.assertTrue(JournalIA.objects.filter(type_appel=JournalIA.Type.DIAGRAMME, succes=True).exists())

    def test_diagramme_invalide(self):
        with patch_gemini(reponses={"code_mermaid": "<script>alert(1)</script>", "texte_alternatif": "x"}):
            self.demander()
        illustration = Illustration.objects.get()
        self.assertEqual(illustration.statut, Illustration.Statut.ERREUR)
        self.assertEqual(illustration.code_mermaid, "")

    @mock.patch("apps.assistant.services.time.sleep")
    def test_json_illisible(self, _sleep):
        with patch_gemini(reponses="pas du json"):
            self.demander()
        self.assertEqual(Illustration.objects.get().statut, Illustration.Statut.ERREUR)

    @mock.patch("apps.assistant.services.time.sleep")
    def test_erreur_passagere_retentee(self, _sleep):
        class Erreur503(Exception):
            code = 503
        with patch_gemini(reponses=[Erreur503(), {"code_mermaid": "flowchart TD\nA-->B", "texte_alternatif": "x"}]), \
                self.assertLogs("apps.assistant.services", "WARNING"):
            self.demander()
        self.assertEqual(Illustration.objects.get().statut, Illustration.Statut.TERMINEE)

    @mock.patch.dict(os.environ, {"HF_TOKEN": "hf_test"})
    def test_image_hugging_face(self):
        reponse_ia = MessageVocal.objects.create(session=self.session, role=MessageVocal.Role.ASSISTANT,
                                                 texte="Le déterminant mesure un changement d'aire.")
        with patch_gemini(reponses={"prompt": "Educational illustration of a matrix determinant as area",
                                    "texte_alternatif": "Parallélogramme dont l'aire représente le déterminant"}), \
                mock.patch("apps.assistant.tasks.generer_image", return_value=Image.new("RGB", (64, 64), "teal")) as gen:
            self.demander(Illustration.Type.IMAGE, "Le déterminant en image", message=reponse_ia.pk)
        illustration = Illustration.objects.get()
        self.assertEqual(illustration.statut, Illustration.Statut.TERMINEE)
        self.assertTrue(illustration.image.name.endswith(".webp"))
        self.assertEqual(illustration.message, reponse_ia)
        gen.assert_called_once_with("Educational illustration of a matrix determinant as area")
        html = self.client.get(reverse("etat-illustration-assistant", args=[illustration.pk])).json()["html"]
        self.assertIn('alt="Parallélogramme dont l&#x27;aire représente le déterminant"', html)

    @mock.patch.dict(os.environ, {"HF_TOKEN": ""})
    def test_image_sans_jeton_hugging_face(self):
        with patch_gemini(reponses=AssertionError("Gemini ne doit pas être appelé")):
            self.demander(Illustration.Type.IMAGE)
        illustration = Illustration.objects.get()
        self.assertEqual(illustration.statut, Illustration.Statut.ERREUR)
        self.assertIn("Hugging Face", illustration.message_erreur)
        self.assertTrue(JournalIA.objects.filter(type_appel=JournalIA.Type.ILLUSTRATION, code="configuration").exists())

    @mock.patch.dict(os.environ, {"HF_TOKEN": "hf_test"})
    def test_quota_hugging_face(self):
        class Erreur429(Exception):
            response = type("R", (), {"status_code": 429})()
        with patch_gemini(reponses={"prompt": "x", "texte_alternatif": "y"}), \
                mock.patch("apps.assistant.tasks.generer_image", side_effect=Erreur429()):
            self.demander(Illustration.Type.IMAGE)
        self.assertIn("Quota", Illustration.objects.get().message_erreur)

    def test_demande_invalide(self):
        self.assertEqual(self.demander(demande="x").status_code, 400)
        self.assertFalse(Illustration.objects.exists())

    def test_limite(self):
        with mock.patch.dict(os.environ, {"ASSISTANT_ILLUSTRATIONS_PAR_HEURE": "0"}):
            self.assertEqual(self.demander().status_code, 400)

    def test_suppression(self):
        illustration = Illustration.objects.create(session=self.session, demande="x", statut=Illustration.Statut.ERREUR)
        self.client.post(reverse("supprimer-illustration-assistant", args=[illustration.pk]))
        self.assertFalse(Illustration.objects.exists())

    def test_galerie(self):
        Illustration.objects.create(session=self.session, demande="Mon diagramme", statut=Illustration.Statut.TERMINEE,
                                    type_illustration=Illustration.Type.DIAGRAMME, code_mermaid="flowchart TD\nA-->B")
        self.assertContains(self.client.get(reverse("galerie-assistant")), "Mon diagramme")
        self.assertNotContains(self.client.get(reverse("galerie-assistant"), {"type": "IMAGE"}), "Mon diagramme")
