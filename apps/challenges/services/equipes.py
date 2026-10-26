"""Équipes et adhésions, sûres en cas d'accès concurrents.

- Capacité : chaque acceptation verrouille la ligne de l'équipe (`select_for_update`) puis recompte les
  membres. Deux coordinateurs qui acceptent en même temps la dernière place sont donc sérialisés par
  PostgreSQL : le second voit l'équipe pleine.
- Une seule équipe acceptée par étudiant et par défi : vérifiée ici ET garantie par l'index UNIQUE partiel
  `edusprint_une_equipe_par_defi` (si deux équipes acceptent le même étudiant au même instant, la base
  refuse la seconde : IntegrityError traduite en message).
- Demandes devenues incompatibles : quand un étudiant est accepté quelque part (ou crée son équipe), ses
  autres demandes en attente pour ce défi passent à « retirée » avec un motif explicite.
"""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from ..models import Defi, Equipe, ParticipationEquipe, Tache
from ..permissions import est_etudiant
from .reglages import reglage

Etat = ParticipationEquipe.Etat


def _verifier_inscriptions(defi):
    if not defi.inscriptions_ouvertes:
        raise ValidationError("Les inscriptions de ce défi ne sont pas ouvertes.")


def _deja_membre(defi, etudiant):
    return ParticipationEquipe.objects.filter(defi=defi, etudiant=etudiant, etat=Etat.ACCEPTEE).exists()


def _retirer_autres_demandes(defi, etudiant, equipe):
    ParticipationEquipe.objects.filter(defi=defi, etudiant=etudiant, etat=Etat.EN_ATTENTE).exclude(
        equipe=equipe).update(etat=Etat.RETIREE, date_retrait=timezone.now(),
                              motif=f"Retirée automatiquement : membre de l'équipe « {equipe.nom} ».")


def creer_equipe(defi, etudiant, nom, description=""):
    if not est_etudiant(etudiant):
        raise ValidationError("Seul un étudiant peut créer une équipe.")
    try:
        with transaction.atomic():
            defi = Defi.objects.select_for_update().get(pk=defi.pk)
            _verifier_inscriptions(defi)
            if _deja_membre(defi, etudiant):
                raise ValidationError("Vous faites déjà partie d'une équipe pour ce défi.")
            if Equipe.objects.filter(defi=defi, nom__iexact=nom.strip()).exists():
                raise ValidationError("Une équipe porte déjà ce nom dans ce défi.")
            equipe = Equipe.objects.create(defi=defi, nom=nom.strip(), description=description, coordinateur=etudiant)
            ParticipationEquipe.objects.create(equipe=equipe, etudiant=etudiant, etat=Etat.ACCEPTEE,
                                               role="Coordinateur", date_reponse=timezone.now())
            _retirer_autres_demandes(defi, etudiant, equipe)
    except IntegrityError:
        raise ValidationError("Vous faites déjà partie d'une équipe pour ce défi, ou ce nom est déjà pris.")
    return equipe


def demander_adhesion(equipe, etudiant, message=""):
    if not est_etudiant(etudiant):
        raise ValidationError("Seul un étudiant peut rejoindre une équipe.")
    try:
        with transaction.atomic():
            equipe = Equipe.objects.select_for_update(of=("self",)).select_related("defi").get(pk=equipe.pk)
            defi = equipe.defi
            _verifier_inscriptions(defi)
            if not equipe.active:
                raise ValidationError("Cette équipe n'accepte plus de demandes.")
            if _deja_membre(defi, etudiant):
                raise ValidationError("Vous faites déjà partie d'une équipe pour ce défi.")
            if equipe.participations.filter(etudiant=etudiant, etat=Etat.EN_ATTENTE).exists():
                raise ValidationError("Votre demande pour cette équipe est déjà en attente.")
            if equipe.nb_membres() >= defi.taille_max:
                raise ValidationError("Cette équipe est complète.")
            maximum = reglage("EDUSPRINT_DEMANDES_EN_ATTENTE_MAX")
            if ParticipationEquipe.objects.filter(defi=defi, etudiant=etudiant, etat=Etat.EN_ATTENTE).count() >= maximum:
                raise ValidationError(f"Vous avez déjà {maximum} demandes en attente pour ce défi : retirez-en une.")
            return ParticipationEquipe.objects.create(equipe=equipe, etudiant=etudiant, message=message)
    except IntegrityError:
        raise ValidationError("Votre demande pour cette équipe existe déjà.")


def retirer_demande(participation, etudiant):
    with transaction.atomic():
        participation = ParticipationEquipe.objects.select_for_update().get(pk=participation.pk)
        if participation.etudiant_id != etudiant.pk or participation.etat != Etat.EN_ATTENTE:
            raise ValidationError("Seule une demande en attente peut être retirée par son auteur.")
        participation.etat = Etat.RETIREE
        participation.date_retrait = timezone.now()
        participation.motif = "Retirée par l'étudiant."
        participation.save(update_fields=["etat", "date_retrait", "motif"])
    return participation


def _verifier_coordinateur(equipe, user):
    if not equipe.est_coordinateur(user):
        raise ValidationError("Seul le coordinateur de l'équipe peut effectuer cette action.")


def accepter_demande(participation, coordinateur):
    try:
        with transaction.atomic():
            # Verrou sur l'équipe d'abord : toutes les acceptations d'une même équipe sont sérialisées
            equipe = Equipe.objects.select_for_update(of=("self",)).select_related("defi").get(pk=participation.equipe_id)
            participation = ParticipationEquipe.objects.select_for_update().get(pk=participation.pk)
            _verifier_coordinateur(equipe, coordinateur)
            _verifier_inscriptions(equipe.defi)
            if participation.etat != Etat.EN_ATTENTE:
                raise ValidationError("Cette demande n'est plus en attente.")
            if not equipe.active:
                raise ValidationError("Cette équipe n'accepte plus de membres.")
            if equipe.nb_membres() >= equipe.defi.taille_max:
                raise ValidationError(f"L'équipe est complète ({equipe.defi.taille_max} membres maximum).")
            if _deja_membre(equipe.defi, participation.etudiant):
                participation.etat = Etat.RETIREE
                participation.motif = "L'étudiant a déjà rejoint une autre équipe."
                participation.date_retrait = timezone.now()
                participation.save(update_fields=["etat", "motif", "date_retrait"])
                raise ValidationError("Cet étudiant a déjà rejoint une autre équipe : sa demande a été retirée.")
            participation.etat = Etat.ACCEPTEE
            participation.date_reponse = timezone.now()
            participation.traite_par = coordinateur
            participation.save(update_fields=["etat", "date_reponse", "traite_par"])
            _retirer_autres_demandes(equipe.defi, participation.etudiant, equipe)
    except IntegrityError:
        raise ValidationError("Cet étudiant vient de rejoindre une autre équipe : la demande ne peut pas être acceptée.")
    return participation


def refuser_demande(participation, coordinateur, motif=""):
    with transaction.atomic():
        equipe = Equipe.objects.select_for_update().get(pk=participation.equipe_id)
        participation = ParticipationEquipe.objects.select_for_update().get(pk=participation.pk)
        _verifier_coordinateur(equipe, coordinateur)
        if participation.etat != Etat.EN_ATTENTE:
            raise ValidationError("Cette demande n'est plus en attente.")
        participation.etat = Etat.REFUSEE
        participation.motif = (motif or "Refusée par le coordinateur.")[:200]
        participation.date_reponse = timezone.now()
        participation.traite_par = coordinateur
        participation.save(update_fields=["etat", "motif", "date_reponse", "traite_par"])
    return participation


def _liberer_taches(equipe, etudiant):
    """Les tâches du membre sortant redeviennent non affectées (son travail et ses commentaires sont conservés)."""
    Tache.objects.filter(equipe=equipe, responsable=etudiant).update(responsable=None)


def quitter_equipe(equipe, etudiant):
    with transaction.atomic():
        equipe = Equipe.objects.select_for_update(of=("self",)).select_related("defi").get(pk=equipe.pk)
        defi = equipe.defi
        if not defi.equipes_modifiables or not equipe.active:
            raise ValidationError("L'équipe ne peut plus être modifiée.")
        participation = equipe.participations.select_for_update().filter(
            etudiant=etudiant, etat=Etat.ACCEPTEE).first()
        if participation is None:
            raise ValidationError("Vous n'êtes pas membre de cette équipe.")
        autres = equipe.nb_membres() - 1
        if equipe.est_coordinateur(etudiant):
            if autres > 0:
                raise ValidationError("Transférez d'abord la coordination à un autre membre.")
            if defi.statut != Defi.Statut.INSCRIPTIONS:
                raise ValidationError("Vous êtes le seul membre : l'équipe ne peut pas être abandonnée pendant le défi.")
        maintenant = timezone.now()
        participation.etat = Etat.RETIREE
        participation.date_retrait = maintenant
        participation.motif = "A quitté l'équipe."
        participation.save(update_fields=["etat", "date_retrait", "motif"])
        _liberer_taches(equipe, etudiant)
        if autres == 0:
            # Dernier membre parti pendant les inscriptions : l'équipe est dissoute (historique conservé)
            equipe.participations.filter(etat=Etat.EN_ATTENTE).update(
                etat=Etat.REFUSEE, motif="Équipe dissoute.", date_reponse=maintenant)
            equipe.coordinateur = None
            equipe.statut = Equipe.Statut.DISSOUTE
            equipe.save(update_fields=["coordinateur", "statut"])
    return participation


def exclure_membre(participation, coordinateur):
    """Le coordinateur peut retirer un membre pendant les inscriptions seulement (avant tout travail évalué)."""
    with transaction.atomic():
        equipe = Equipe.objects.select_for_update(of=("self",)).select_related("defi").get(pk=participation.equipe_id)
        participation = ParticipationEquipe.objects.select_for_update().get(pk=participation.pk)
        _verifier_coordinateur(equipe, coordinateur)
        if equipe.defi.statut != Defi.Statut.INSCRIPTIONS:
            raise ValidationError("Un membre ne peut être retiré que pendant les inscriptions.")
        if participation.etat != Etat.ACCEPTEE or participation.etudiant_id == coordinateur.pk:
            raise ValidationError("Action impossible sur ce membre.")
        participation.etat = Etat.RETIREE
        participation.date_retrait = timezone.now()
        participation.motif = "Retiré par le coordinateur."
        participation.traite_par = coordinateur
        participation.save(update_fields=["etat", "date_retrait", "motif", "traite_par"])
        _liberer_taches(equipe, participation.etudiant)
    return participation


def transferer_coordination(equipe, coordinateur, nouveau):
    with transaction.atomic():
        equipe = Equipe.objects.select_for_update(of=("self",)).select_related("defi").get(pk=equipe.pk)
        _verifier_coordinateur(equipe, coordinateur)
        if not equipe.defi.equipes_modifiables or not equipe.active:
            raise ValidationError("L'équipe ne peut plus être modifiée.")
        if nouveau.pk == coordinateur.pk or not equipe.est_membre(nouveau):
            raise ValidationError("Choisissez un autre membre accepté de l'équipe.")
        equipe.coordinateur = nouveau
        equipe.save(update_fields=["coordinateur"])
    return equipe


def reprendre_coordination(equipe, membre):
    """Coordination vacante (compte du coordinateur supprimé) : un membre accepté peut la reprendre."""
    with transaction.atomic():
        equipe = Equipe.objects.select_for_update(of=("self",)).select_related("defi").get(pk=equipe.pk)
        if equipe.coordinateur_id is not None:
            raise ValidationError("L'équipe a déjà un coordinateur.")
        if not equipe.est_membre(membre) or not equipe.defi.equipes_modifiables:
            raise ValidationError("Action impossible.")
        equipe.coordinateur = membre
        equipe.save(update_fields=["coordinateur"])
    return equipe
