"""Génération d'images avec Hugging Face (API d'inférence, modèle texte → image).

Variables d'environnement : HF_TOKEN (obligatoire), HF_IMAGE_MODEL (facultatif).
"""

import os

MODELE_PAR_DEFAUT = "black-forest-labs/FLUX.1-schnell"


def modele_image():
    return os.environ.get("HF_IMAGE_MODEL") or MODELE_PAR_DEFAUT


def generer_image(prompt, timeout=120):
    """Renvoie une image PIL générée à partir de `prompt` (de préférence en anglais)."""
    from huggingface_hub import InferenceClient

    jeton = os.environ.get("HF_TOKEN")
    if not jeton:
        raise RuntimeError("La variable d'environnement HF_TOKEN n'est pas définie.")
    client = InferenceClient(token=jeton, timeout=timeout)
    return client.text_to_image(prompt, model=modele_image())
