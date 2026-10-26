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


def _resampler(signal, frequence, cible=16000):
    """Rééchantillonnage linéaire vers `cible` Hz (Whisper attend du 16 kHz)."""
    import numpy as np

    if frequence == cible or not len(signal):
        return signal
    duree = len(signal) / frequence
    positions = np.linspace(0, len(signal) - 1, int(duree * cible))
    return np.interp(positions, np.arange(len(signal)), signal).astype(np.float32)


def lire_wav(chemin_wav):
    """Lit un WAV PCM 16 bits : renvoie (signal mono float32 à 16 kHz, durée en secondes)."""
    import wave

    import numpy as np

    with wave.open(str(chemin_wav), "rb") as fichier:
        if fichier.getsampwidth() != 2:
            raise ValueError("Format audio non pris en charge (WAV PCM 16 bits attendu).")
        canaux, frequence = fichier.getnchannels(), fichier.getframerate()
        donnees = fichier.readframes(fichier.getnframes())
    signal = np.frombuffer(donnees, dtype="<i2").astype(np.float32) / 32768.0
    if canaux > 1:
        signal = signal.reshape(-1, canaux).mean(axis=1)
    return _resampler(signal, frequence), len(signal) / frequence


def transcrire_wav(chemin_wav, langue=None):
    """Comme transcrire(), pour un WAV PCM 16 bits : lu directement en Python, sans FFmpeg.

    Pensé pour de courtes questions dictées :
    - langue imposée (WHISPER_LANGUE, « french » par défaut ; vide = détection automatique) : sans cela,
      un accent prononcé fait parfois détecter l'anglais et Whisper « traduit » la question ;
    - pas d'horodatage sous 30 s et longueur de sortie bornée : évite les boucles de répétition
      (hallucinations) observées sur les extraits courts.
    """
    signal, duree = lire_wav(chemin_wav)
    langue = langue if langue is not None else os.environ.get("WHISPER_LANGUE", "french")
    options = {"generate_kwargs": {"task": "transcribe", "max_new_tokens": 220, "no_repeat_ngram_size": 4}}
    if langue:
        options["generate_kwargs"]["language"] = langue
    if duree > 30:
        options["return_timestamps"] = True
    resultat = _pipeline_whisper()({"raw": signal, "sampling_rate": 16000}, **options)
    texte = resultat["text"].strip()
    if not texte:
        raise ValueError("Aucune parole n'a été détectée dans l'enregistrement.")
    return texte


def _detecter_langue(modele, tokenizer, sorties_encodeur, langues):
    """Langue la plus probable parmi `langues` (codes Whisper : fr, en, ar…), avec ses probabilités.

    Restreindre le choix évite qu'un accent fasse détecter une langue sans rapport avec la plateforme.
    """
    import torch

    ids = {code: tokenizer.convert_tokens_to_ids(f"<|{code}|>") for code in langues}
    debut = torch.tensor([[modele.generation_config.decoder_start_token_id]], device=modele.device)
    logits = modele(encoder_outputs=sorties_encodeur, decoder_input_ids=debut).logits[0, -1]
    probas = torch.softmax(torch.stack([logits[i] for i in ids.values()]), 0)
    resultat = {code: float(p) for code, p in zip(ids, probas)}
    return max(resultat, key=resultat.get), resultat


def transcrire_question(chemin_wav, langue=None, langues_possibles=("fr", "en", "ar")):
    """Transcrit une question dictée (WAV 16 bits). Renvoie (texte, code de langue, durée en secondes).

    `langue` : code imposé (« fr », « en », « ar ») ; None = détection parmi `langues_possibles`.
    Jusqu'à 30 s, l'encodeur de Whisper ne tourne qu'une fois : sa sortie sert à la fois à détecter
    la langue et à transcrire (environ 2 fois plus rapide que le pipeline). Au-delà : transcrire_wav().
    """
    import torch

    signal, duree = lire_wav(chemin_wav)
    if duree > 30:
        noms = {"fr": "french", "en": "english", "ar": "arabic"}
        return transcrire_wav(chemin_wav, langue=noms.get(langue, "")), langue or "", duree

    pipe = _pipeline_whisper()
    modele, tokenizer = pipe.model, pipe.tokenizer
    caracteristiques = pipe.feature_extractor(signal, sampling_rate=16000, return_tensors="pt").input_features
    caracteristiques = caracteristiques.to(modele.device, modele.dtype)
    with torch.inference_mode():
        sorties = modele.get_encoder()(caracteristiques)
        if not langue:
            langue, _ = _detecter_langue(modele, tokenizer, sorties, langues_possibles)
        jetons = modele.generate(encoder_outputs=sorties, language=langue, task="transcribe",
                                 max_new_tokens=220, no_repeat_ngram_size=4)
    texte = tokenizer.batch_decode(jetons, skip_special_tokens=True)[0].strip()
    if not texte:
        raise ValueError("Aucune parole n'a été détectée dans l'enregistrement.")
    return texte, langue, duree
