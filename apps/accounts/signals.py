"""Enregistre automatiquement chaque connexion dans le journal."""

import logging

from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.db import transaction
from django.dispatch import receiver

from .securite import bloquer_si_necessaire, enregistrer_connexion


@receiver(user_logged_in)
def connexion_reussie(sender, request, user, **kwargs):
    if request is None:
        return
    # Après la 2FA, on retrouve la première étape utilisée (mot de passe ou visage)
    via_2fa = request.path.rstrip("/").endswith("2fa")
    methode = request.session.pop("2fa_methode", "MOT_DE_PASSE") if via_2fa else getattr(request, "methode_connexion", "MOT_DE_PASSE")
    temps_visage = request.session.pop("2fa_temps_ia_ms", None) if via_2fa else None
    if methode == "VISAGE" and temps_visage is not None:
        request.temps_ia_visage_ms = temps_visage  # temps de l'IA visage mesuré avant la 2FA
    enregistrer_connexion(request, utilisateur=user, succes=True, methode=methode, double_auth=via_2fa)
    transaction.on_commit(lambda: preparer_modele_suivant(user.pk))


def preparer_modele_suivant(user_id):
    """Le worker Celery entraîne dès maintenant le modèle de la prochaine connexion de cet utilisateur.

    En mode eager (sans worker), on ne le fait pas : l'entraînement ralentirait chaque connexion
    au lieu de seulement celles qui en ont besoin.
    """
    from .tasks import preparer_modele_risque

    if preparer_modele_risque.app.conf.task_always_eager:
        return
    try:
        preparer_modele_risque.delay(user_id)
    except Exception:
        logging.getLogger(__name__).exception("Pré-calcul du modèle de risque non envoyé à Celery")


@receiver(user_login_failed)
def connexion_echouee(sender, credentials, request=None, **kwargs):
    if request is None:
        return
    from .models import Utilisateur

    nom = credentials.get("username", "")
    utilisateur = Utilisateur.objects.filter(username__iexact=nom).first()
    enregistrer_connexion(request, utilisateur=utilisateur, nom_saisi=nom, succes=False,
                          motif="Mot de passe incorrect" if utilisateur else "Compte inconnu")
    if utilisateur:
        bloquer_si_necessaire(request, utilisateur)