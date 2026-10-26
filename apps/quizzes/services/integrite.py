"""Intégrité des évaluations : ordre des choix propre à chaque tentative et signaux d'attention.

Les signaux (sorties de la page, textes collés, vitesse) sont affichés à l'enseignant comme un contexte à
vérifier, jamais comme une preuve de triche. L'étudiant en est informé avant de commencer.
"""

import random

SIGNAUX_MAX = 1000


def melanger_choix(choix, graine, question_pk):
    """Mélange déterministe : même ordre à chaque rechargement de la même tentative, différent entre étudiants."""
    choix = list(choix)
    random.Random(f"{graine}-{question_pk}").shuffle(choix)
    return choix


def lire_compteur(valeur):
    """Compteur envoyé par le navigateur : entier borné, sinon None (donnée ignorée)."""
    try:
        valeur = int(valeur)
    except (TypeError, ValueError):
        return None
    return valeur if 0 <= valeur <= SIGNAUX_MAX else None


def fusionner(actuel, sorties, collages):
    """Les compteurs ne font que croître : un envoi plus ancien ou falsifié à la baisse ne réduit rien."""
    actuel = dict(actuel or {})
    for cle, valeur in (("sorties", sorties), ("collages", collages)):
        if valeur is not None:
            actuel[cle] = max(actuel.get(cle, 0), valeur)
    return actuel
