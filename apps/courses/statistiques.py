"""Statistiques par cours (enseignant, classement et export CSV de l'administrateur).

Chaque compteur est une sous-requête : des Count() sur plusieurs relations dans la
même requête multiplieraient les lignes entre elles et fausseraient les totaux.
"""

from django.db.models import Avg, Count, FloatField, IntegerField, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce

from .models import Avis, Consultation, Signalement, Telechargement


def _compte(modele):
    sous_requete = (
        modele.objects.filter(cours=OuterRef("pk")).order_by().values("cours")
        .annotate(n=Count("pk")).values("n")
    )
    return Coalesce(Subquery(sous_requete, output_field=IntegerField()), Value(0))


def _note_moyenne():
    sous_requete = (
        Avis.objects.filter(cours=OuterRef("pk")).order_by().values("cours")
        .annotate(m=Avg("note")).values("m")
    )
    return Subquery(sous_requete, output_field=FloatField())


def avec_note_moyenne(queryset):
    """Ajoute note_moyenne et nb_avis (liste des cours)."""
    return queryset.annotate(note_moyenne=_note_moyenne(), nb_avis=_compte(Avis))


def avec_statistiques(queryset):
    """Ajoute nb_consultations, nb_telechargements, nb_avis, note_moyenne et nb_signalements."""
    return queryset.annotate(
        nb_consultations=_compte(Consultation),
        nb_telechargements=_compte(Telechargement),
        nb_avis=_compte(Avis),
        nb_signalements=_compte(Signalement),
        note_moyenne=_note_moyenne(),
    )
