import base64
import hashlib
import io
import secrets

import pyotp
import qrcode

from .models import CodeRecuperation


def generer_secret():
    """Crée une clé secrète partagée entre EduVox et l'application du téléphone."""
    return pyotp.random_base32()


def uri_totp(user, secret):
    """Lien lu par Google Authenticator (contenu du QR code)."""
    return pyotp.TOTP(secret).provisioning_uri(
        name=user.email or user.username, issuer_name="EduVox"
    )


def qr_code_base64(uri):
    """Génère l'image du QR code, prête à afficher dans une balise <img>."""
    image = qrcode.make(uri)
    tampon = io.BytesIO()
    image.save(tampon, format="PNG")
    return base64.b64encode(tampon.getvalue()).decode()


def verifier_code_totp(secret, code):
    """Vérifie le code à 6 chiffres (tolère 30 s de décalage d'horloge)."""
    if not secret:
        return False
    code = code.replace(" ", "").strip()
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def _hacher(code):
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


def generer_codes_recuperation(user, nombre=8):
    """Remplace les anciens codes par 8 nouveaux. Renvoie les codes en clair (affichés une seule fois)."""
    user.codes_recuperation.all().delete()
    codes = []
    for _ in range(nombre):
        code = f"{secrets.token_hex(3)}-{secrets.token_hex(3)}".upper()  # ex. 3F2A9B-1C7D0E
        CodeRecuperation.objects.create(utilisateur=user, code_hash=_hacher(code))
        codes.append(code)
    return codes


def utiliser_code_recuperation(user, code):
    """Vérifie un code de récupération et le marque comme utilisé."""
    code_trouve = user.codes_recuperation.filter(code_hash=_hacher(code), utilise=False).first()
    if code_trouve:
        code_trouve.utilise = True
        code_trouve.save(update_fields=["utilise"])
        return True
    return False