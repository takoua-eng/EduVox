"""Réglages du module assistant, surchargeables par variables d'environnement."""

import os

DEFAUTS = {
    "ASSISTANT_GEMINI_TIMEOUT_S": 60,
    "ASSISTANT_QUESTIONS_PAR_HEURE": 30,
    "ASSISTANT_ILLUSTRATIONS_PAR_HEURE": 10,
    "ASSISTANT_FICHES_PAR_HEURE": 5,
    "ASSISTANT_QUESTION_MAX_CARACTERES": 2000,
    "ASSISTANT_CONTEXTE_MAX_CARACTERES": 12000,
    "ASSISTANT_HISTORIQUE_MESSAGES": 10,
    "ASSISTANT_AUDIO_MAX_S": 120,
    "ASSISTANT_AUDIO_MAX_MO": 8,
}


def reglage(nom):
    try:
        return int(os.environ.get(nom, DEFAUTS[nom]))
    except ValueError:
        return DEFAUTS[nom]
