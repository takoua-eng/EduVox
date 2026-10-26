"""Imprévus du client virtuel et réponses d'adaptation des équipes.

Cycle : brouillon → validé (relu par un enseignant du défi) → publié (commun à toutes les équipes actives).
- Toute modification du contenu après validation ramène l'imprévu en brouillon (garanti dans le modèle).
- Publié : figé et conservé. Les éventuels changements réels (échéance d'un jalon, budget) sont saisis par
  l'enseignant, appliqués à la publication et tracés dans `modifications_appliquees` (l'IA n'en décide jamais).
"""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from ..models import Defi, EvenementScenario, Jalon, ReponseEvenement
from ..permissions import peut_deposer, peut_gerer_defi, peut_soumettre

Etat = EvenementScenario.Etat


def _verrouiller(evenement):
    return EvenementScenario.objects.select_for_update(of=("self",)).select_related("defi", "jalon_concerne").get(pk=evenement.pk)


def valider(evenement, enseignant):
    with transaction.atomic():
        evenement = _verrouiller(evenement)
        if not peut_gerer_defi(enseignant, evenement.defi):
            raise ValidationError("Vous ne gérez pas ce défi.")
        if evenement.etat != Etat.BROUILLON:
            raise ValidationError("Seul un brouillon peut être validé.")
        evenement.full_clean()
        evenement.etat = Etat.VALIDE
        evenement.valide_par = enseignant
        evenement.date_validation = timezone.now()
        evenement.save(update_fields=["etat", "valide_par", "date_validation", "date_modification"])
    return evenement


def publier(evenement, enseignant):
    with transaction.atomic():
        evenement = _verrouiller(evenement)
        defi = Defi.objects.select_for_update().get(pk=evenement.defi_id)
        if not peut_gerer_defi(enseignant, defi):
            raise ValidationError("Vous ne gérez pas ce défi.")
        if evenement.etat != Etat.VALIDE:
            raise ValidationError("L'imprévu doit être validé avant sa publication.")
        if defi.statut != Defi.Statut.EN_COURS:
            raise ValidationError("Un imprévu ne se publie que pendant le défi.")
        evenement.full_clean()
        modifications = {}
        if evenement.jalon_concerne_id and evenement.nouvelle_echeance:
            jalon = Jalon.objects.select_for_update().get(pk=evenement.jalon_concerne_id)
            modifications["jalon"] = {"id": jalon.pk, "titre": jalon.titre,
                                      "avant": jalon.echeance.isoformat(),
                                      "apres": evenement.nouvelle_echeance.isoformat()}
            jalon.echeance = evenement.nouvelle_echeance
            jalon._via_evenement = True
            jalon.save(update_fields=["echeance"])
        if evenement.nouveau_budget is not None:
            modifications["budget"] = {"avant": str(defi.budget) if defi.budget is not None else None,
                                       "apres": str(evenement.nouveau_budget)}
            defi.budget = evenement.nouveau_budget
            defi._via_evenement = True
            defi.save(update_fields=["budget", "date_modification"])
        evenement.etat = Etat.PUBLIE
        evenement.date_publication = timezone.now()
        evenement.modifications_appliquees = modifications
        evenement.save(update_fields=["etat", "date_publication", "modifications_appliquees", "date_modification"])
    return evenement


def supprimer(evenement, enseignant):
    with transaction.atomic():
        evenement = _verrouiller(evenement)
        if not peut_gerer_defi(enseignant, evenement.defi):
            raise ValidationError("Vous ne gérez pas ce défi.")
        evenement.delete()


# ---------- Réponses d'adaptation ----------

def _verifier_equipe(evenement, equipe, user):
    if evenement.defi_id != equipe.defi_id:
        raise ValidationError("Cette équipe ne participe pas au défi de cet imprévu.")
    if evenement.etat != Etat.PUBLIE:
        raise ValidationError("Cet imprévu n'est pas publié.")
    if not peut_deposer(user, equipe):
        raise ValidationError("Votre équipe ne peut plus répondre (défi clos ou équipe inactive).")


def enregistrer_reponse(evenement, equipe, user, analyse_impact, decisions, adaptation_planning):
    try:
        with transaction.atomic():
            _verifier_equipe(evenement, equipe, user)
            reponse = ReponseEvenement.objects.select_for_update().filter(evenement=evenement, equipe=equipe).first()
            if reponse is None:
                reponse = ReponseEvenement(evenement=evenement, equipe=equipe, auteur=user)
            elif reponse.etat == ReponseEvenement.Etat.SOUMISE:
                raise ValidationError("La réponse a déjà été soumise.")
            reponse.analyse_impact = analyse_impact
            reponse.decisions = decisions
            reponse.adaptation_planning = adaptation_planning
            reponse.modifie_par = user
            reponse.full_clean()
            reponse.save()
    except IntegrityError:
        raise ValidationError("Un autre membre vient de créer la réponse : rechargez la page.")
    return reponse


def soumettre_reponse(reponse, user):
    with transaction.atomic():
        reponse = ReponseEvenement.objects.select_for_update(of=("self",)).select_related(
            "evenement", "equipe__defi").get(pk=reponse.pk)
        _verifier_equipe(reponse.evenement, reponse.equipe, user)
        if reponse.etat != ReponseEvenement.Etat.BROUILLON:
            raise ValidationError("La réponse a déjà été soumise.")
        if not peut_soumettre(user, reponse.equipe):
            raise ValidationError("Seul le coordinateur soumet la réponse de l'équipe.")
        maintenant = timezone.now()
        reponse.etat = ReponseEvenement.Etat.SOUMISE
        reponse.soumis_par = user
        reponse.date_soumission = maintenant
        echeance = reponse.evenement.echeance_reponse
        reponse.en_retard = bool(echeance and maintenant > echeance)
        reponse.save(update_fields=["etat", "soumis_par", "date_soumission", "en_retard", "date_modification"])
    return reponse
