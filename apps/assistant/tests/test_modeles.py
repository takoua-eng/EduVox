"""Tests unitaires : modèles, règles métier et fonctions de service (sans appel réseau)."""

import os
from pathlib import Path
from unittest import mock

from django.core.files.base import ContentFile
from django.test import SimpleTestCase

from apps.assistant.models import Illustration, MessageVocal, SessionAssistant
from apps.assistant.reglages import reglage
from apps.assistant.services import (
    ErreurAssistant,
    centile,
    contexte_cours,
    instruction_systeme,
    nettoyer_mermaid,
    preparer_question,
    rendre_markdown,
    taux_erreur_mots,
    traduire_erreur,
)

from .base import BaseAssistantTestCase, wav_bytes


class SessionTests(BaseAssistantTestCase):
    def test_fermer_puis_rouvrir(self):
        self.session.fermer()
        self.session.refresh_from_db()
        self.assertFalse(self.session.active)
        self.assertIsNotNone(self.session.date_fermeture)
        self.session.rouvrir()
        self.session.refresh_from_db()
        self.assertTrue(self.session.active)
        self.assertIsNone(self.session.date_fermeture)

    def test_str(self):
        self.assertEqual(str(self.session), "Révisions (etudiant)")

    def test_suppression_session_supprime_messages_et_fichiers(self):
        message = MessageVocal(session=self.session, role=MessageVocal.Role.UTILISATEUR, mode=MessageVocal.Mode.VOCAL)
        message.audio.save("q.wav", ContentFile(wav_bytes(0.2)), save=True)
        illustration = Illustration(session=self.session, demande="schéma", type_illustration=Illustration.Type.IMAGE)
        illustration.image.save("i.webp", ContentFile(b"RIFF....WEBP"), save=True)
        chemins = [Path(message.audio.path), Path(illustration.image.path)]
        self.assertTrue(all(c.exists() for c in chemins))
        self.session.delete()
        self.assertFalse(MessageVocal.objects.exists())
        self.assertFalse(Illustration.objects.exists())
        self.assertFalse(any(c.exists() for c in chemins), "les fichiers doivent être supprimés du disque")

    def test_suppression_cours_garde_la_session(self):
        from apps.courses.models import Cours
        from .base import creer_cours
        cours = creer_cours(self.prof.enseignant, titre="Temporaire")
        self.session.cours = cours
        self.session.save()
        Cours.objects.filter(pk=cours.pk).delete()
        self.session.refresh_from_db()
        self.assertIsNone(self.session.cours)


class ContexteTests(BaseAssistantTestCase):
    def test_contexte_contient_le_resume_du_cours(self):
        contexte = contexte_cours(self.session)
        self.assertIn("Algèbre linéaire", contexte)
        self.assertIn("déterminant", contexte)
        self.assertIn("déterminant", instruction_systeme(self.session))

    def test_contexte_tronque(self):
        with mock.patch.dict(os.environ, {"ASSISTANT_CONTEXTE_MAX_CARACTERES": "50"}):
            self.assertLess(len(contexte_cours(self.session)), 120)

    def test_cours_non_visible_ignore(self):
        """Un brouillon n'est pas visible de l'étudiant : son contenu ne doit pas fuiter dans le prompt."""
        self.session.cours = self.cours_brouillon
        self.assertEqual(contexte_cours(self.session), "")

    def test_sans_cours(self):
        self.session.cours = None
        self.assertEqual(contexte_cours(self.session), "")


class PreparerQuestionTests(BaseAssistantTestCase):
    def test_question_ecrite(self):
        question, reponse = preparer_question(self.session, "  Qu'est-ce qu'un déterminant ?  ")
        self.assertEqual(question.texte, "Qu'est-ce qu'un déterminant ?")
        self.assertEqual(reponse.statut, MessageVocal.Statut.EN_ATTENTE)
        self.assertEqual(reponse.role, MessageVocal.Role.ASSISTANT)

    def test_question_vide_refusee(self):
        with self.assertRaises(ErreurAssistant):
            preparer_question(self.session, "   ")

    def test_question_trop_longue_refusee(self):
        with self.assertRaises(ErreurAssistant) as ctx:
            preparer_question(self.session, "x" * 2001)
        self.assertEqual(ctx.exception.code, "validation")

    def test_session_fermee_refusee(self):
        self.session.fermer()
        with self.assertRaises(ErreurAssistant) as ctx:
            preparer_question(self.session, "Bonjour")
        self.assertEqual(ctx.exception.code, "session_fermee")

    def test_sans_cle_gemini(self):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            with self.assertRaises(ErreurAssistant) as ctx:
                preparer_question(self.session, "Bonjour")
        self.assertEqual(ctx.exception.code, "configuration")

    def test_quota_par_heure(self):
        with mock.patch.dict(os.environ, {"ASSISTANT_QUESTIONS_PAR_HEURE": "2"}):
            preparer_question(self.session, "Q1")
            preparer_question(self.session, "Q2")
            with self.assertRaises(ErreurAssistant) as ctx:
                preparer_question(self.session, "Q3")
        self.assertEqual(ctx.exception.code, "limite")

    def test_question_dictee_corrigee_par_l_utilisateur(self):
        vocal = MessageVocal.objects.create(session=self.session, role=MessageVocal.Role.UTILISATEUR,
                                            mode=MessageVocal.Mode.VOCAL, texte="qu'est ce qu'un déterminent")
        question, _ = preparer_question(self.session, "Qu'est-ce qu'un déterminant ?", vocal.pk)
        self.assertEqual(question.pk, vocal.pk)
        self.assertEqual(question.texte, "Qu'est-ce qu'un déterminant ?")

    def test_question_dictee_deja_repondue_refusee(self):
        vocal = MessageVocal.objects.create(session=self.session, role=MessageVocal.Role.UTILISATEUR,
                                            mode=MessageVocal.Mode.VOCAL, texte="Bonjour")
        preparer_question(self.session, "", vocal.pk)
        with self.assertRaises(ErreurAssistant):
            preparer_question(self.session, "", vocal.pk)

    def test_question_dictee_d_une_autre_session_refusee(self):
        autre = SessionAssistant.objects.create(utilisateur=self.autre_etudiant, titre="Autre")
        vocal = MessageVocal.objects.create(session=autre, role=MessageVocal.Role.UTILISATEUR,
                                            mode=MessageVocal.Mode.VOCAL, texte="secret")
        with self.assertRaises(ErreurAssistant):
            preparer_question(self.session, "", vocal.pk)


class FonctionsPuresTests(SimpleTestCase):
    def test_wer_identique(self):
        self.assertEqual(taux_erreur_mots("Bonjour, le monde !", "bonjour le monde"), 0.0)

    def test_wer_substitution_suppression_insertion(self):
        self.assertAlmostEqual(taux_erreur_mots("un deux trois quatre", "un deux trois cinq"), 0.25)
        self.assertAlmostEqual(taux_erreur_mots("un deux trois quatre", "un deux"), 0.5)
        self.assertAlmostEqual(taux_erreur_mots("un deux", "un deux trois"), 0.5)

    def test_wer_references_vides(self):
        self.assertEqual(taux_erreur_mots("", ""), 0.0)
        self.assertEqual(taux_erreur_mots("", "bruit"), 1.0)

    def test_centile(self):
        self.assertEqual(centile([100, 200, 300, 400, 500], 50), 300)
        self.assertEqual(centile([100, 200], 95), 195)
        self.assertIsNone(centile([], 95))

    def test_markdown_sans_html_ni_javascript(self):
        html = rendre_markdown("**gras** <script>alert(1)</script> [lien](javascript:alert(1))")
        self.assertIn("<strong>gras</strong>", html)
        self.assertNotIn("<script>", html)
        self.assertNotIn('href="javascript:', html)

    def test_mermaid_valide_et_balises_retirees(self):
        self.assertEqual(nettoyer_mermaid("```mermaid\nflowchart TD\n A-->B\n```"), "flowchart TD\n A-->B")

    def test_mermaid_invalide(self):
        for code in ("", "<script>alert(1)</script>", "%%{init: {}}%%\nflowchart TD", "flowchart TD\n" + "A-->B\n" * 1000):
            with self.assertRaises(ErreurAssistant):
                nettoyer_mermaid(code)

    def test_traduction_des_erreurs(self):
        self.assertEqual(traduire_erreur(TimeoutError()).code, "delai")
        self.assertEqual(traduire_erreur(ConnectionError()).code, "reseau")

        class ErreurApi(Exception):
            def __init__(self, code):
                self.code = code
        self.assertEqual(traduire_erreur(ErreurApi(429)).code, "quota")
        self.assertEqual(traduire_erreur(ErreurApi(403)).code, "configuration")
        self.assertEqual(traduire_erreur(ErreurApi(503)).code, "indisponible")
        self.assertEqual(traduire_erreur(ValueError()).code, "service")

    def test_reglage_invalide_valeur_par_defaut(self):
        with mock.patch.dict(os.environ, {"ASSISTANT_AUDIO_MAX_S": "abc"}):
            self.assertEqual(reglage("ASSISTANT_AUDIO_MAX_S"), 120)


class LectureWavTests(SimpleTestCase):
    def test_lecture_et_reechantillonnage(self):
        import tempfile
        from ai_services import lire_wav
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav_bytes(1.0, frequence=8000, canaux=2))
        try:
            signal, duree = lire_wav(f.name)
        finally:
            os.unlink(f.name)
        self.assertAlmostEqual(duree, 1.0, places=2)
        self.assertEqual(len(signal), 16000)  # rééchantillonné à 16 kHz, mono
        self.assertLessEqual(float(abs(signal).max()), 1.0)
