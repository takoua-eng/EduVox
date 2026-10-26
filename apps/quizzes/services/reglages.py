"""Réglages du module quiz : settings.py s'ils y sont définis, sinon variables d'environnement, sinon défaut."""

import os

from django.conf import settings

DEFAUTS = {
    "QUIZ_GEMINI_TIMEOUT_S": 60,           # délai maximum d'un appel Gemini
    "QUIZ_GEMINI_ESSAIS": 2,               # essais au total pour une erreur passagère (délai, réseau, 5xx)
    "QUIZ_TEXTE_MAX_CARACTERES": 30000,    # texte du cours envoyé à Gemini pour la génération
    "QUIZ_GENERATIONS_PAR_HEURE": 10,      # générations IA autorisées par enseignant et par heure
}


def reglage(nom):
    valeur = getattr(settings, nom, None)
    if valeur is None:
        valeur = os.environ.get(nom) or DEFAUTS[nom]
    try:
        return max(1, int(valeur))
    except (TypeError, ValueError):
        return DEFAUTS[nom]
