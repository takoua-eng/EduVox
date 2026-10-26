"""Enregistre automatiquement chaque connexion dans le journal."""

from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.dispatch import receiver

from .securite import bloquer_si_necessaire, enregistrer_connexion


@receiver(user_logged_in)
def connexion_reussie(sender, request, user, **kwargs):
    if request is None:
        return
    # Après la 2FA, on retrouve la première étape utilisée (mot de passe ou visage)
    via_2fa = request.path.rstrip("/").endswith("2fa")
    methode = request.session.pop("2fa_methode", "MOT_DE_PASSE") if via_2fa else getattr(request, "methode_connexion", "MOT_DE_PASSE")
    enregistrer_connexion(request, utilisateur=user, succes=True, methode=methode, double_auth=via_2fa)


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