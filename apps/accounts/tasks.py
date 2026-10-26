"""Tâches Celery du module Utilisateurs : évaluation des IA, pré-calcul du modèle de risque, e-mails.

Les tâches reçoivent des identifiants (jamais des objets) : le worker relit les données à jour en base.
"""

import smtplib

from celery import shared_task
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from .evaluation import lancer_evaluation
from .models import JournalConnexion, Utilisateur
from .securite import preparer_modele_heures
from .tokens import jeton_verification_email

REPETITIONS_PAGE = 30  # moins de répétitions que la commande : la page reste réactive en mode eager

# E-mails : 3 nouvelles tentatives en cas de panne SMTP ou réseau (après 30 s, 60 s puis 120 s)
REESSAIS_EMAIL = {"autoretry_for": (smtplib.SMTPException, OSError), "max_retries": 3,
                  "retry_backoff": 30, "retry_jitter": False}


@shared_task(soft_time_limit=600, time_limit=660)
def lancer_evaluation_ia(repetitions=REPETITIONS_PAGE, images=None):
    """Lance l'évaluation et renvoie les identifiants des EvaluationIA créées et les messages."""
    evaluations, messages = lancer_evaluation(images=images, repetitions=repetitions)
    return {"evaluations": [e.pk for e in evaluations], "messages": messages}


@shared_task(ignore_result=True, soft_time_limit=60, time_limit=90)
def preparer_modele_risque(user_id):
    """Après une connexion réussie : entraîne le modèle de la connexion suivante pour qu'elle le trouve en cache."""
    utilisateur = Utilisateur.objects.filter(pk=user_id).first()
    return bool(utilisateur) and preparer_modele_heures(utilisateur)


# ---------- E-mails ----------

@shared_task(**REESSAIS_EMAIL)
def email_verification(user_id, racine):
    user = Utilisateur.objects.filter(pk=user_id).first()
    if user is None or not user.email:
        return
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    lien = racine + reverse("verifier-email", args=[uid, jeton_verification_email.make_token(user)])
    message = render_to_string("accounts/emails/verification_email.txt", {"user": user, "lien": lien})
    send_mail("EduVox – Confirmez votre adresse e-mail", message, None, [user.email])


@shared_task(**REESSAIS_EMAIL)
def email_invitation(user_id, racine):
    """E-mail de bienvenue avec un lien pour choisir son mot de passe."""
    user = Utilisateur.objects.filter(pk=user_id).first()
    if user is None or not user.email:
        return
    lien = racine + reverse("mdp-reinitialiser", args=[
        urlsafe_base64_encode(force_bytes(user.pk)), default_token_generator.make_token(user),
    ])
    message = render_to_string("accounts/emails/invitation.txt",
                               {"user": user, "lien": lien, "connexion": racine + reverse("connexion")})
    send_mail("Bienvenue sur EduVox – Activez votre compte", message, None, [user.email])


@shared_task(**REESSAIS_EMAIL)
def email_alerte_connexion(entree_id, lien_profil):
    """E-mail d'alerte en cas de connexion inhabituelle."""
    entree = JournalConnexion.objects.select_related("utilisateur").filter(pk=entree_id).first()
    if entree is None or entree.utilisateur is None or not entree.utilisateur.email:
        return
    message = render_to_string("accounts/emails/alerte_connexion.txt",
                               {"user": entree.utilisateur, "entree": entree, "lien_profil": lien_profil})
    send_mail("EduVox – Connexion inhabituelle à votre compte", message, None, [entree.utilisateur.email])


@shared_task(**REESSAIS_EMAIL)
def email_compte_bloque(user_id, lien_mdp):
    user = Utilisateur.objects.filter(pk=user_id).first()
    if user is None or not user.email:
        return
    message = render_to_string("accounts/emails/compte_bloque.txt",
                               {"user": user, "jusqua": user.bloque_jusqua, "lien_mdp": lien_mdp})
    send_mail("EduVox – Votre compte est temporairement bloqué", message, None, [user.email])
