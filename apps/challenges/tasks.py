"""Tâche Celery unique des traitements IA EduSprint (brief, imprévu, vérification).

Idempotente : la demande est « réservée » (en attente → en cours) par un UPDATE conditionnel avant tout appel
à Gemini ; une seconde exécution de la même tâche ne trouve plus rien à réserver et s'arrête sans appel.
"""

import logging

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from .models import DemandeIA, EvenementScenario
from .services.brief import generer_brief
from .services.gemini import ErreurIA
from .services.scenario import proposer_evenement
from .services.verification import NonAnalysable, verifier

logger = logging.getLogger(__name__)


def _terminer(demande_id, **champs):
    DemandeIA.objects.filter(pk=demande_id, statut=DemandeIA.Statut.EN_COURS).update(date_fin=timezone.now(), **champs)


def _creer_evenement(demande, proposition, meta):
    with transaction.atomic():
        demande = DemandeIA.objects.select_for_update().get(pk=demande.pk)
        if demande.statut != DemandeIA.Statut.EN_COURS or demande.evenement_cree_id:
            return
        evenement = EvenementScenario.objects.create(
            defi_id=demande.defi_id, titre=proposition["titre"], description=proposition["description"],
            type_contrainte=proposition["type"], origine=EvenementScenario.Origine.IA,
            justification=proposition["justification_pedagogique"],
            impacts_attendus="\n".join(proposition["impacts_attendus"]),
            elements_attendus="\n".join(proposition["elements_attendus"]),
            cree_par_id=demande.demandeur_id,
        )
        demande.evenement_cree = evenement
        demande.resultat = proposition
        demande.statut = DemandeIA.Statut.TERMINE
        demande.modele, demande.duree_ms, demande.essais = meta["modele"], meta["duree_ms"], meta["essais"]
        demande.date_fin = timezone.now()
        demande.save()


@shared_task(soft_time_limit=300, time_limit=330)
def traiter_demande(demande_id):
    reservee = DemandeIA.objects.filter(pk=demande_id, statut=DemandeIA.Statut.EN_ATTENTE).update(
        statut=DemandeIA.Statut.EN_COURS, date_debut=timezone.now())
    if not reservee:
        return
    demande = DemandeIA.objects.select_related("defi", "version__livrable__equipe__defi", "version__livrable__jalon").get(
        pk=demande_id)
    try:
        if demande.type_demande == DemandeIA.Type.BRIEF:
            brief, meta = generer_brief(demande.parametres)
            _terminer(demande_id, statut=DemandeIA.Statut.TERMINE, resultat=brief, modele=meta["modele"],
                      duree_ms=meta["duree_ms"], essais=meta["essais"])
        elif demande.type_demande == DemandeIA.Type.EVENEMENT:
            proposition, meta = proposer_evenement(demande.defi, demande.parametres.get("consigne", ""))
            _creer_evenement(demande, proposition, meta)
        else:
            try:
                resultat, meta, empreinte = verifier(demande.version)
            except NonAnalysable as erreur:
                # Pas d'appel IA : le dossier n'est pas analysable (≠ dossier vide ou non conforme)
                _terminer(demande_id, statut=DemandeIA.Statut.TERMINE, code_erreur="non_analysable",
                          resultat={"analysable": False, "limites": erreur.limites, "version": demande.version.numero},
                          message="Dossier non analysable automatiquement.")
                return
            _terminer(demande_id, statut=DemandeIA.Statut.TERMINE, resultat=resultat, empreinte=empreinte,
                      modele=meta["modele"], duree_ms=meta["duree_ms"], essais=meta["essais"])
    except ErreurIA as erreur:
        _terminer(demande_id, statut=DemandeIA.Statut.ECHOUE, message=erreur.message[:300], code_erreur=erreur.code,
                  essais=erreur.essais, duree_ms=erreur.duree_ms)
    except Exception:
        logger.exception("Échec inattendu de la demande IA %s", demande_id)
        _terminer(demande_id, statut=DemandeIA.Statut.ECHOUE, code_erreur="inattendu",
                  message="Erreur inattendue pendant le traitement. Réessayez plus tard.")
