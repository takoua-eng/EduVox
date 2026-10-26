"""Réglages EduSprint : settings.py s'ils y sont définis, sinon variables d'environnement, sinon défaut."""

import os
from pathlib import Path

from django.conf import settings

DEFAUTS = {
    "EDUSPRINT_GEMINI_TIMEOUT_S": 60,          # délai maximum d'un appel Gemini (secondes)
    "EDUSPRINT_GEMINI_ESSAIS": 2,              # essais au total pour une erreur passagère
    "EDUSPRINT_IA_APPELS_PAR_HEURE": 10,       # demandes IA autorisées par utilisateur et par heure
    "EDUSPRINT_TEXTE_MAX_CARACTERES": 40000,   # texte de livrable envoyé à Gemini
    "EDUSPRINT_FICHIER_MAX_MO": 20,            # taille maximale d'un fichier déposé
    "EDUSPRINT_DEMANDES_EN_ATTENTE_MAX": 3,    # demandes d'adhésion en attente par étudiant et par défi
}


def reglage(nom):
    valeur = getattr(settings, nom, None)
    if valeur is None:
        valeur = os.environ.get(nom) or DEFAUTS[nom]
    try:
        return max(1, int(valeur))
    except (TypeError, ValueError):
        return DEFAUTS[nom]


def dossier_stockage_prive():
    """Dossier des fichiers déposés : HORS de MEDIA_ROOT, qui est servi publiquement en développement."""
    valeur = getattr(settings, "EDUSPRINT_STOCKAGE_PRIVE", None) or os.environ.get("EDUSPRINT_STOCKAGE_PRIVE")
    return Path(valeur) if valeur else Path(settings.BASE_DIR) / "stockage_prive" / "edusprint"
