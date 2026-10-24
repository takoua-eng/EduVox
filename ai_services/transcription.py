import os
import subprocess
from functools import lru_cache

MODELE_PAR_DEFAUT = "openai/whisper-small"


@lru_cache(maxsize=1)
def _pipeline_whisper():
    """Charge le modèle Whisper une seule fois par processus (le chargement est long)."""
    from transformers import pipeline

    modele = os.environ.get("WHISPER_MODEL") or MODELE_PAR_DEFAUT
    return pipeline("automatic-speech-recognition", model=modele, chunk_length_s=30)


def transcrire(chemin_audio):
    """Transcrit le fichier audio situé à `chemin_audio` avec Whisper (FFmpeg requis)."""
    resultat = _pipeline_whisper()(str(chemin_audio), return_timestamps=True)
    texte = resultat["text"].strip()
    if not texte:
        raise ValueError("Aucune parole n'a été détectée dans le fichier audio.")
    return texte


def duree_audio(chemin_audio):
    """Durée du fichier audio en secondes (via ffprobe, fourni avec FFmpeg), ou None si inconnue."""
    try:
        resultat = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(chemin_audio)],
            capture_output=True, text=True, timeout=30, check=True,
        )
        return round(float(resultat.stdout.strip()))
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
