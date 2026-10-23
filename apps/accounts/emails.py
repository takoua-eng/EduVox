from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from .tokens import jeton_verification_email


def envoyer_email_verification(request, user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = jeton_verification_email.make_token(user)
    lien = request.build_absolute_uri(reverse("verifier-email", args=[uid, token]))
    message = render_to_string(
        "accounts/emails/verification_email.txt", {"user": user, "lien": lien}
    )
    send_mail("EduVox – Confirmez votre adresse e-mail", message, None, [user.email])