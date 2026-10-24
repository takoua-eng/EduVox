"""Cours similaires : même matière et mots-clés communs."""

from django.db.models import Q

NB_RECOMMANDATIONS = 4


def cours_similaires(cours, cours_autorises, limite=NB_RECOMMANDATIONS):
    """Renvoie les cours de `cours_autorises` les plus proches de `cours`.

    Score : 2 points pour la même matière, 1 point par mot-clé en commun.
    """
    mots = {mot.lower() for mot in cours.liste_mots_cles}
    if not cours.matiere_id and not mots:
        return []

    filtre = Q(matiere_id=cours.matiere_id) if cours.matiere_id else Q()
    for mot in mots:
        filtre |= Q(mots_cles__icontains=mot)
    candidats = cours_autorises.filter(filtre).exclude(pk=cours.pk).select_related("matiere", "enseignant__utilisateur")

    scores = []
    for candidat in candidats:
        score = 2 if cours.matiere_id and candidat.matiere_id == cours.matiere_id else 0
        score += len(mots & {mot.lower() for mot in candidat.liste_mots_cles})
        if score:
            scores.append((score, candidat.date_creation, candidat))
    scores.sort(key=lambda element: (element[0], element[1]), reverse=True)
    return [candidat for _, _, candidat in scores[:limite]]
