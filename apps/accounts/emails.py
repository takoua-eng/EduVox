"""Envoi des e-mails du module en arrière-plan (tâches Celery de tasks.py)."""

import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def lien_absolu(request, chemin):
    """Lien complet (https://exemple.com/chemin) pour un e-mail.

    Avec la requête : son adresse (build_absolute_uri). Sans requête, ou sans hôte (connexion simulée
    par client.login / force_login) : settings.BASE_URL, car un lien relatif est inutilisable dans un e-mail.
    """
    if request is not None:
        try:
            return request.build_absolute_uri(chemin)
        except KeyError:
            pass
    return f"{settings.BASE_URL.rstrip('/')}{chemin}"


def racine_site(request):
    """« https://exemple.com » : les tâches construisent les liens sans avoir la requête."""
    return lien_absolu(request, "/").rstrip("/")


def envoyer_en_arriere_plan(tache, *arguments):
    """Confie l'e-mail à Celery ; si Celery est injoignable, il est envoyé tout de suite (rien n'est perdu)."""
    try:
        tache.delay(*arguments)
    except Exception:
        logger.exception("E-mail %s non confié à Celery : envoi direct", tache.name)
        tache.apply(args=arguments)


def envoyer_email_verification(request, user):
    from .tasks import email_verification

    envoyer_en_arriere_plan(email_verification, user.pk, racine_site(request))
