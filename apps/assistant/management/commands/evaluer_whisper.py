"""Évalue la transcription Whisper : taux d'erreur de mots (WER) et facteur temps réel.

Usage :
    python manage.py evaluer_whisper <dossier> [--json resultats.json]

Le dossier contient des paires `nom.wav` + `nom.txt` (transcription de référence, écrite à la main).
Pour créer un jeu de test : enregistrer 10 à 20 questions typiques d'étudiants (WAV 16 bits, de
préférence 16 kHz mono) et taper exactement ce qui a été dit dans le .txt du même nom.
"""

import json
import time
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from ai_services import lire_wav, transcrire_wav
from apps.assistant.services import normaliser_transcription, taux_erreur_mots


class Command(BaseCommand):
    help = "Mesure le WER et la vitesse de Whisper sur un dossier de fichiers WAV annotés."

    def add_arguments(self, parser):
        parser.add_argument("dossier", type=Path)
        parser.add_argument("--json", type=Path, help="Fichier où enregistrer les résultats détaillés")

    def handle(self, *args, dossier, **options):
        if not dossier.is_dir():
            raise CommandError(f"Dossier introuvable : {dossier}")
        paires = [(wav, wav.with_suffix(".txt")) for wav in sorted(dossier.glob("*.wav")) if wav.with_suffix(".txt").exists()]
        if not paires:
            raise CommandError("Aucune paire fichier.wav + fichier.txt trouvée dans ce dossier.")

        self.stdout.write("Chargement du modèle Whisper…")
        transcrire_wav(paires[0][0])  # préchauffage : le premier appel charge le modèle

        resultats, total_mots, total_erreurs, total_audio, total_calcul = [], 0, 0.0, 0.0, 0.0
        for wav, txt in paires:
            reference = txt.read_text(encoding="utf-8-sig").strip()  # tolère le BOM (Bloc-notes, PowerShell)
            _, duree_audio = lire_wav(wav)
            debut = time.monotonic()
            try:
                hypothese = transcrire_wav(wav)
            except ValueError:
                hypothese = ""
            calcul = time.monotonic() - debut
            wer = taux_erreur_mots(reference, hypothese)
            mots = len(normaliser_transcription(reference))  # même découpage que le calcul du WER
            total_mots += mots
            total_erreurs += wer * mots
            total_audio += duree_audio
            total_calcul += calcul
            resultats.append({"fichier": wav.name, "reference": reference, "transcription": hypothese,
                              "wer": round(wer, 4), "duree_audio_s": round(duree_audio, 2), "calcul_s": round(calcul, 2)})
            self.stdout.write(f"  {wav.name:<30} WER {wer:6.1%}   {calcul:5.1f} s pour {duree_audio:5.1f} s d'audio")

        wer_global = total_erreurs / total_mots if total_mots else 0
        synthese = {
            "fichiers": len(resultats),
            "wer_global": round(wer_global, 4),
            "precision_mots": round(1 - wer_global, 4),
            "facteur_temps_reel": round(total_calcul / total_audio, 3) if total_audio else None,
        }
        self.stdout.write(self.style.SUCCESS(
            f"\nWER global : {wer_global:.1%} (précision mots ~ {1 - wer_global:.1%}) sur {len(resultats)} fichiers"
            f" - facteur temps réel x{synthese['facteur_temps_reel']}"
        ))
        if options.get("json"):
            options["json"].write_text(json.dumps({"synthese": synthese, "details": resultats}, ensure_ascii=False,
                                                  indent=2), encoding="utf-8")
            self.stdout.write(f"Résultats détaillés : {options['json']}")
