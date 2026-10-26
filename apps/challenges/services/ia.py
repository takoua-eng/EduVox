"""Création des demandes IA : configuration, quota horaire, anti-doublon, puis envoi différé à Celery.

- Quota : EDUSPRINT_IA_APPELS_PAR_HEURE demandes par utilisateur et par heure (toutes fonctions confondues).
- Anti-doublon : index UNIQUE partiel (un seul traitement actif par version, par défi ou par enseignant) ;
  une double soumission renvoie la demande déjà en cours au lieu d'en créer une seconde.
- Envoi après validation de la transaction ; si Celery/Redis est injoignable, la demande passe « échouée »
  avec un message clair (les brouillons et dépôts ne sont jamais touchés).
"""

import logging
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from ..models import Defi, DemandeIA, Jalon
from .gemini import ErreurIA, gemini_configure
from .reglages import reglage

logger = logging.getLogger(__name__)


class DemandeEnCours(Exception):
    def __init__(self, demande):
        super().__init__("Un traitement identique est déjà en cours.")
        self.demande = demande


def verifier_quota(user):
    limite = reglage("EDUSPRINT_IA_APPELS_PAR_HEURE")
    recentes = DemandeIA.objects.filter(demandeur=user, date_creation__gte=timezone.now() - timedelta(hours=1)).count()
    if recentes >= limite:
        raise ErreurIA(f"Limite atteinte : {limite} demandes IA par heure. Réessayez plus tard.", "quota_local")


def _envoyer(demande_id):
    from ..tasks import traiter_demande

    def envoyer():
        try:
            traiter_demande.delay(demande_id)
        except Exception:
            logger.exception("Demande IA %s non envoyée à Celery", demande_id)
            DemandeIA.objects.filter(pk=demande_id, statut=DemandeIA.Statut.EN_ATTENTE).update(
                statut=DemandeIA.Statut.ECHOUE, code_erreur="file",
                message="Le service de traitement est injoignable (Celery / Redis). Réessayez plus tard.",
                date_fin=timezone.now())

    transaction.on_commit(envoyer)


def creer_demande(type_demande, user, actif, **champs):
    """Crée et envoie la demande. `actif` : filtre de la demande active équivalente (anti-doublon)."""
    existante = DemandeIA.objects.filter(type_demande=type_demande, statut__in=DemandeIA.ACTIFS, **actif).first()
    if existante:
        raise DemandeEnCours(existante)
    if not gemini_configure():
        raise ErreurIA("L'IA n'est pas configurée sur ce serveur (clé Gemini absente). "
                       "Vous pouvez continuer manuellement.", "configuration")
    verifier_quota(user)
    try:
        with transaction.atomic():
            demande = DemandeIA.objects.create(type_demande=type_demande, demandeur=user, **champs)
            _envoyer(demande.pk)
    except IntegrityError:
        existante = DemandeIA.objects.filter(type_demande=type_demande, statut__in=DemandeIA.ACTIFS, **actif).first()
        if existante:
            raise DemandeEnCours(existante)
        raise
    return demande


def demander_brief(user, parametres):
    return creer_demande(DemandeIA.Type.BRIEF, user, {"demandeur": user}, parametres=parametres)


def demander_evenement(user, defi, consigne=""):
    return creer_demande(DemandeIA.Type.EVENEMENT, user, {"defi": defi}, defi=defi,
                         parametres={"consigne": consigne[:500]})


def demander_verification(user, version):
    return creer_demande(DemandeIA.Type.VERIFICATION, user, {"version": version},
                         version=version, defi=version.livrable.equipe.defi)


def creer_defi_depuis_brief(demande, user, donnees_defi, jalons, co_enseignants=()):
    """Enregistre l'aperçu relu par l'enseignant en défi BROUILLON. Un second envoi ne crée pas de doublon."""
    with transaction.atomic():
        demande = DemandeIA.objects.select_for_update().get(pk=demande.pk)
        if demande.demandeur_id != user.pk or demande.type_demande != DemandeIA.Type.BRIEF \
                or demande.statut != DemandeIA.Statut.TERMINE:
            raise ValidationError("Ce brief n'est pas disponible.")
        if demande.defi_cree_id:
            return demande.defi_cree, False
        defi = Defi(**donnees_defi, enseignant=user, origine=Defi.Origine.IA, statut=Defi.Statut.BROUILLON)
        defi.full_clean()
        defi.save()
        defi.co_enseignants.set(co_enseignants)
        for ordre, jalon in enumerate(jalons, start=1):
            objet = Jalon(defi=defi, ordre=ordre, **jalon)
            objet.full_clean()
            objet.save()
        demande.defi_cree = defi
        demande.defi = defi
        demande.save(update_fields=["defi_cree", "defi"])
    return defi, True
