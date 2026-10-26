"""Carnet de révision espacée (méthode Leitner) alimenté par les erreurs de l'étudiant.

Seules les questions QCM / Vrai-Faux entrent dans le carnet (correction instantanée), et seulement si le
quiz montre le corrigé : le carnet ne doit jamais révéler une réponse que l'enseignant a choisi de cacher.
"""

import random
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from ..models import CarteRevision, Question, ReponseEtudiant


def alimenter_carnet(tentative):
    """Ajoute (ou renvoie en boîte 1) chaque question QCM / Vrai-Faux ratée de la tentative. Renvoie le nombre."""
    if not tentative.quiz.afficher_corrige:
        return 0
    ratees = ReponseEtudiant.objects.filter(
        tentative=tentative, est_correcte=False,
        question__type_question__in=[Question.Type.QCM, Question.Type.VRAI_FAUX],
    ).values_list("question_id", flat=True)
    demain = timezone.localdate() + timedelta(days=1)
    for question_id in ratees:
        CarteRevision.objects.update_or_create(
            etudiant_id=tentative.etudiant_id, question_id=question_id,
            defaults={"boite": 1, "maitrisee": False, "prochaine_revision": demain},
        )
    return len(ratees)


def cartes_dues(etudiant):
    """Fiches à réviser aujourd'hui (questions encore existantes, quiz montrant le corrigé)."""
    return (CarteRevision.objects.filter(etudiant=etudiant, maitrisee=False,
                                         prochaine_revision__lte=timezone.localdate(),
                                         question__quiz__afficher_corrige=True)
            .select_related("question__quiz").prefetch_related("question__choix"))


def statistiques(etudiant):
    cartes = CarteRevision.objects.filter(etudiant=etudiant)
    par_boite = dict(cartes.filter(maitrisee=False).values_list("boite").annotate(n=Count("pk")))
    agregat = cartes.aggregate(revisions=Count("pk", filter=Q(nb_revisions__gt=0)))
    prochaine = (cartes.filter(maitrisee=False, prochaine_revision__gt=timezone.localdate())
                 .order_by("prochaine_revision").values_list("prochaine_revision", flat=True).first())
    return {
        "total": cartes.count(),
        "dues": cartes_dues(etudiant).count(),
        "maitrisees": cartes.filter(maitrisee=True).count(),
        "boites": [{"numero": b, "nb": par_boite.get(b, 0)} for b in range(1, 6)],
        "deja_revisees": agregat["revisions"],
        "prochaine": prochaine,
    }


def choix_melanges(carte):
    """Choix de la fiche dans un ordre aléatoire (sans l'indicateur de bonne réponse)."""
    choix = [{"pk": c.pk, "texte": c.texte} for c in carte.question.choix.all()]
    random.Random(f"{carte.pk}-{carte.nb_revisions}").shuffle(choix)
    return choix
