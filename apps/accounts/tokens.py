from django.contrib.auth.tokens import PasswordResetTokenGenerator


class JetonVerificationEmail(PasswordResetTokenGenerator):
    """Jeton du lien de confirmation : devient invalide dès que l'e-mail est vérifié."""

    def _make_hash_value(self, user, timestamp):
        return f"{user.pk}{timestamp}{user.email_verifie}{user.email}"


jeton_verification_email = JetonVerificationEmail()