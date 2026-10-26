"""Cycle de vie d'un défi : brouillon → inscriptions → en cours → terminé → archivé.

Chaque transition verrouille la ligne du défi (`select_for_update`) : deux clics simultanés ne peuvent pas
démarrer deux fois le même défi ni l'archiver pendant son démarrage.
"""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from ..models import Defi, Equipe, ParticipationEquipe


def _verrouiller(defi):
    return Defi.objects.select_for_update().get(pk=defi.pk)


def _changer(defi, statut, **dates):
    if not defi.peut_passer_a(statut):
        raise ValidationError(
            f"Action impossible : le défi est « {defi.get_statut_display()} ».")
    defi.statut = statut
    for champ, valeur in dates.items():
        setattr(defi, champ, valeur)
    defi.save(update_fields=["statut", "date_modification", *dates])
    return defi


def problemes_publication(defi):
    """Liste des raisons qui empêchent d'ouvrir les inscriptions (vide si tout est prêt)."""
    problemes = []
    try:
        defi.full_clean()
    except ValidationError as erreur:
        problemes.extend(erreur.messages)
    if not defi.jalons.exists():
        problemes.append("Ajoutez au moins un jalon.")
    if defi.inscriptions_fin <= timezone.now():
        problemes.append("La date de clôture des inscriptions est déjà passée : modifiez les dates.")
    if not defi.objectifs_liste() or not defi.livrables_liste():
        problemes.append("Précisez au moins un objectif et un livrable attendu.")
    return problemes


def ouvrir_inscriptions(defi):
    with transaction.atomic():
        defi = _verrouiller(defi)
        if defi.statut != Defi.Statut.BROUILLON:
            raise ValidationError("Seul un brouillon peut être publié.")
        problemes = problemes_publication(defi)
        if problemes:
            raise ValidationError(problemes)
        return _changer(defi, Defi.Statut.INSCRIPTIONS, date_ouverture=timezone.now())


def repasser_brouillon(defi):
    with transaction.atomic():
        defi = _verrouiller(defi)
        if defi.equipes.exists():
            raise ValidationError("Des équipes sont déjà inscrites : le défi ne peut plus redevenir un brouillon.")
        return _changer(defi, Defi.Statut.BROUILLON)


def bilan_equipes(defi):
    """Répartit les équipes actives entre celles qui atteignent la taille minimale et les autres."""
    equipes = (Equipe.objects.filter(defi=defi, statut=Equipe.Statut.ACTIVE)
               .annotate(nb=Count("participations", filter=Q(participations__etat=ParticipationEquipe.Etat.ACCEPTEE)))
               .order_by("nom"))
    retenues = [e for e in equipes if e.nb >= defi.taille_min]
    incompletes = [e for e in equipes if e.nb < defi.taille_min]
    attente = ParticipationEquipe.objects.filter(defi=defi, etat=ParticipationEquipe.Etat.EN_ATTENTE).count()
    return retenues, incompletes, attente


def demarrer(defi, user):
    """Démarre le défi. Règle pour les équipes incomplètes : elles sont ÉCARTÉES (consultables, non modifiables),
    et toutes les demandes encore en attente sont refusées (inscriptions closes)."""
    with transaction.atomic():
        defi = _verrouiller(defi)
        if defi.statut != Defi.Statut.INSCRIPTIONS:
            raise ValidationError("Le défi doit être en phase d'inscription pour démarrer.")
        retenues, incompletes, _ = bilan_equipes(defi)
        if not retenues:
            raise ValidationError(
                f"Aucune équipe n'atteint la taille minimale ({defi.taille_min} membres) : le défi ne peut pas démarrer.")
        maintenant = timezone.now()
        Equipe.objects.filter(pk__in=[e.pk for e in incompletes]).update(statut=Equipe.Statut.ECARTEE)
        ParticipationEquipe.objects.filter(defi=defi, etat=ParticipationEquipe.Etat.EN_ATTENTE).update(
            etat=ParticipationEquipe.Etat.REFUSEE, motif="Inscriptions closes au démarrage du défi.",
            date_reponse=maintenant, traite_par=user)
        return _changer(defi, Defi.Statut.EN_COURS, date_demarrage=maintenant)


def cloturer(defi):
    with transaction.atomic():
        defi = _verrouiller(defi)
        return _changer(defi, Defi.Statut.TERMINE, date_cloture=timezone.now())


def archiver(defi):
    with transaction.atomic():
        defi = _verrouiller(defi)
        if defi.statut == Defi.Statut.INSCRIPTIONS:
            # Annulation pendant les inscriptions : les demandes en attente sont closes proprement
            ParticipationEquipe.objects.filter(defi=defi, etat=ParticipationEquipe.Etat.EN_ATTENTE).update(
                etat=ParticipationEquipe.Etat.REFUSEE, motif="Défi annulé.", date_reponse=timezone.now())
        return _changer(defi, Defi.Statut.ARCHIVE, date_archivage=timezone.now())


def supprimer(defi):
    with transaction.atomic():
        defi = _verrouiller(defi)
        defi.delete()


ACTIONS = {
    "publier": ouvrir_inscriptions,
    "brouillon": repasser_brouillon,
    "cloturer": cloturer,
    "archiver": archiver,
}
