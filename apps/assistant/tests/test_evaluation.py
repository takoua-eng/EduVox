"""Commande d'évaluation de Whisper (WER), avec un Whisper simulé."""

import io
import json
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase

from .base import wav_bytes


class EvaluerWhisperTests(SimpleTestCase):
    def setUp(self):
        self.dossier = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dossier, ignore_errors=True)
        for nom, reference in (("q1", "Qu'est-ce qu'un déterminant ?"), ("q2", "Explique la photosynthèse")):
            (self.dossier / f"{nom}.wav").write_bytes(wav_bytes(1.0))
            (self.dossier / f"{nom}.txt").write_text(reference, encoding="utf-8")

    def test_calcul_du_wer(self):
        hypotheses = {"q1.wav": "Qu'est-ce qu'un déterminant", "q2.wav": "Explique la photo synthèse"}
        sortie_json = self.dossier / "resultats.json"
        with mock.patch("apps.assistant.management.commands.evaluer_whisper.transcrire_question",
                        side_effect=lambda chemin, langue=None: (hypotheses[Path(chemin).name], "fr", 1.0)):
            sortie = io.StringIO()
            call_command("evaluer_whisper", str(self.dossier), "--json", str(sortie_json), stdout=sortie)
        resultats = json.loads(sortie_json.read_text(encoding="utf-8"))
        # Ponctuation ignorée. q1 : 0 erreur sur 6 mots (« qu est ce qu un déterminant ») ;
        # q2 : 1 substitution + 1 insertion sur 3 mots → 2 erreurs / 9 mots au total
        self.assertEqual(resultats["synthese"]["fichiers"], 2)
        self.assertAlmostEqual(resultats["synthese"]["wer_global"], 0.2222, places=4)
        self.assertIn("WER global : 22.2%", sortie.getvalue())

    def test_dossier_sans_paires(self):
        vide = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, vide, ignore_errors=True)
        with self.assertRaises(CommandError):
            call_command("evaluer_whisper", str(vide), stdout=io.StringIO())
        with self.assertRaises(CommandError):
            call_command("evaluer_whisper", str(vide / "absent"), stdout=io.StringIO())
