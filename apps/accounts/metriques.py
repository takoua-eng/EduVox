"""Métriques d'évaluation des deux IA du module (fonctions pures, sans base de données).

Convention : la classe « positive » vaut 1.
- Visage : 1 = même personne (le visage doit être accepté), 0 = personnes différentes.
- Connexions : 1 = connexion suspecte (doit déclencher une alerte), 0 = connexion normale.
"""

import statistics
from bisect import bisect_left


def _division(a, b):
    return a / b if b else 0.0


def _pourcent(valeur):
    return round(100 * valeur, 1)


def matrice_confusion(vrais, predits):
    """Compte les vrais/faux positifs/négatifs : {"VP", "FP", "VN", "FN"}."""
    if len(vrais) != len(predits):
        raise ValueError("Les listes « vrais » et « predits » doivent avoir la même longueur.")
    matrice = {"VP": 0, "FP": 0, "VN": 0, "FN": 0}
    for vrai, predit in zip(vrais, predits):
        if predit:
            matrice["VP" if vrai else "FP"] += 1
        else:
            matrice["FN" if vrai else "VN"] += 1
    return matrice


def precision(m):
    """Parmi les cas prédits positifs, part de vrais positifs."""
    return _division(m["VP"], m["VP"] + m["FP"])


def rappel(m):
    """Parmi les vrais positifs, part retrouvée par l'IA."""
    return _division(m["VP"], m["VP"] + m["FN"])


def f1(m):
    """Moyenne harmonique de la précision et du rappel."""
    p, r = precision(m), rappel(m)
    return _division(2 * p * r, p + r)


def exactitude(m):
    """Part des prédictions correctes."""
    return _division(m["VP"] + m["VN"], sum(m.values()))


def far(m):
    """Taux de fausses acceptations : imposteurs acceptés / imposteurs."""
    return _division(m["FP"], m["FP"] + m["VN"])


def frr(m):
    """Taux de faux rejets : vraies personnes refusées / vraies personnes."""
    return _division(m["FN"], m["FN"] + m["VP"])


def resume_metriques(vrais, predits):
    """Matrice de confusion et métriques (en %, arrondies à 0,1) dans un dict lisible."""
    m = matrice_confusion(vrais, predits)
    return {
        "matrice": m,
        "nb": len(vrais),
        "precision": _pourcent(precision(m)),
        "rappel": _pourcent(rappel(m)),
        "f1": _pourcent(f1(m)),
        "exactitude": _pourcent(exactitude(m)),
        "far": _pourcent(far(m)),
        "frr": _pourcent(frr(m)),
    }


def resume_scores(scores_meme, scores_differents, seuil):
    """Métriques du visage pour un seuil : accepté si similarité ≥ seuil."""
    vrais = [1] * len(scores_meme) + [0] * len(scores_differents)
    predits = [int(s >= seuil) for s in list(scores_meme) + list(scores_differents)]
    return resume_metriques(vrais, predits)


def courbe_seuils(scores_meme, scores_differents, seuils):
    """Une ligne par seuil : FAR, FRR, précision, rappel, F1 (en %)."""
    lignes = []
    for seuil in seuils:
        r = resume_scores(scores_meme, scores_differents, seuil)
        lignes.append({"seuil": seuil, **{cle: r[cle] for cle in ("far", "frr", "precision", "rappel", "f1")}})
    return lignes


def taux_egal_erreur(scores_meme, scores_differents):
    """EER : seuil où le FAR et le FRR sont (presque) égaux. Renvoie {"seuil", "taux"} ou None."""
    if not scores_meme or not scores_differents:
        return None
    meme, differents = sorted(scores_meme), sorted(scores_differents)
    meilleur = None
    for seuil in sorted(set(meme) | set(differents)):
        taux_far = 1 - bisect_left(differents, seuil) / len(differents)  # imposteurs avec score ≥ seuil
        taux_frr = bisect_left(meme, seuil) / len(meme)                  # vraies personnes avec score < seuil
        ecart = abs(taux_far - taux_frr)
        if meilleur is None or ecart < meilleur[0]:
            meilleur = (ecart, seuil, (taux_far + taux_frr) / 2)
    return {"seuil": round(meilleur[1], 3), "taux": _pourcent(meilleur[2])}


def stats_temps(durees_ms):
    """Temps moyen, médian et 95e centile (méthode du rang le plus proche), en millisecondes."""
    if not durees_ms:
        return None
    triees = sorted(durees_ms)
    rang_p95 = max(0, -(-95 * len(triees) // 100) - 1)  # arrondi supérieur de 0,95 × n, puis indice
    return {
        "n": len(triees),
        "moyenne": round(statistics.fmean(triees), 2),
        "mediane": round(statistics.median(triees), 2),
        "p95": round(triees[rang_p95], 2),
        "max": round(triees[-1], 2),
    }
