"""Certificat de réussite vérifiable et export des résultats.

Le QR code du certificat pointe vers une page publique de vérification. Le lien contient un jeton signé
avec la SECRET_KEY de Django (module django.core.signing) : il est impossible de fabriquer un faux
certificat ou de modifier le score sans que la vérification échoue.
"""

import base64
import csv
import hashlib
import io
from decimal import Decimal

import qrcode
from django.core import signing

from ..models import SEUIL_CERTIFICAT, TentativeQuiz

SEL = "eduvox.quizzes.certificat"


def peut_obtenir(tentative):
    return (tentative.statut == TentativeQuiz.Statut.CORRIGEE and tentative.pourcentage is not None
            and tentative.pourcentage >= SEUIL_CERTIFICAT)


def _score(tentative):
    """Score sous une forme stable (« 5.00 »), qu'il vienne de la base ou d'un objet en mémoire."""
    return f"{Decimal(tentative.score or 0):.2f}"


def jeton(tentative):
    # Le score fait partie du jeton : une note révisée ensuite invalide l'ancien certificat
    return signing.dumps({"t": tentative.pk, "s": _score(tentative)}, salt=SEL, compress=True)


def code_court(tentative):
    """Référence lisible imprimée sur le certificat (dérivée du jeton signé)."""
    return hashlib.sha256(jeton(tentative).encode()).hexdigest()[:12].upper()


def verifier(valeur):
    """Renvoie la tentative si le jeton est authentique et toujours valable, sinon None."""
    try:
        donnees = signing.loads(valeur, salt=SEL)
    except signing.BadSignature:
        return None
    tentative = (TentativeQuiz.objects.select_related("quiz__cours", "quiz__enseignant__utilisateur", "etudiant")
                 .filter(pk=donnees.get("t")).first())
    if tentative is None or _score(tentative) != donnees.get("s") or not peut_obtenir(tentative):
        return None
    return tentative


def qr_base64(texte):
    tampon = io.BytesIO()
    qrcode.make(texte, box_size=6, border=2).save(tampon, format="PNG")
    return base64.b64encode(tampon.getvalue()).decode()


def mention(pourcentage):
    if pourcentage >= 90:
        return "Excellent"
    if pourcentage >= 80:
        return "Très bien"
    return "Bien"


def export_csv(quiz, tentatives):
    """CSV (séparateur « ; » et BOM UTF-8 pour Excel) : une ligne par tentative soumise."""
    tampon = io.StringIO()
    tampon.write("﻿")
    ecrivain = csv.writer(tampon, delimiter=";")
    ecrivain.writerow(["Étudiant", "Nom complet", "Tentative", "Soumise le", "Durée", "État", "Score",
                       "Score max", "Pourcentage", "Indices IA", "Signaux d'intégrité"])
    for t in tentatives:
        ecrivain.writerow([
            t.etudiant.username, t.etudiant.get_full_name(), t.numero,
            t.date_soumission.strftime("%d/%m/%Y %H:%M") if t.date_soumission else "",
            t.duree_texte, t.get_statut_display(), str(t.score or 0).replace(".", ","),
            str(t.score_max or 0).replace(".", ","), t.pourcentage if t.pourcentage is not None else "",
            len(t.indices or {}), " | ".join(t.signaux_integrite),
        ])
    return tampon.getvalue()
