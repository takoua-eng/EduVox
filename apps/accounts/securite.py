"""Journal des connexions et détection IA des connexions suspectes."""

import math

from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.timezone import localtime

HISTORIQUE_MIN_IA = 8       # connexions réussies nécessaires pour entraîner le modèle
SEUIL_SUSPECT = 60          # score de risque (sur 100) à partir duquel la connexion est suspecte
POIDS = {"heure": 40, "appareil": 35, "ip": 25}


# ---------- Informations sur la requête ----------

def adresse_ip(request):
    transfert = request.META.get("HTTP_X_FORWARDED_FOR")
    if transfert:
        return transfert.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def decrire_appareil(user_agent):
    """Transforme le « user agent » du navigateur en texte lisible : « Chrome sur Windows »."""
    ua = user_agent or ""
    if "Edg/" in ua:
        navigateur = "Edge"
    elif "OPR/" in ua or "Opera" in ua:
        navigateur = "Opera"
    elif "Firefox/" in ua:
        navigateur = "Firefox"
    elif "Chrome/" in ua:
        navigateur = "Chrome"
    elif "Safari/" in ua:
        navigateur = "Safari"
    else:
        navigateur = "Navigateur inconnu"

    if "Android" in ua:
        systeme = "Android"
    elif "iPhone" in ua or "iPad" in ua:
        systeme = "iPhone/iPad"
    elif "Windows" in ua:
        systeme = "Windows"
    elif "Mac OS" in ua:
        systeme = "Mac"
    elif "Linux" in ua:
        systeme = "Linux"
    else:
        systeme = "système inconnu"
    return f"{navigateur} sur {systeme}"


def prefixe_reseau(ip):
    """Les 3 premiers nombres de l'adresse IPv4 (même box Internet ou même réseau)."""
    if not ip:
        return ""
    return ".".join(ip.split(".")[:3]) if "." in ip else ip.rsplit(":", 4)[0]


# ---------- Détection IA ----------

def _caracteristiques(date):
    """Heure de connexion placée sur un cercle, pour que 23 h soit proche de 0 h."""
    locale = localtime(date)
    heure = locale.hour + locale.minute / 60
    return [math.sin(2 * math.pi * heure / 24), math.cos(2 * math.pi * heure / 24)]


def _ecart_heures(date_a, date_b):
    """Écart en heures entre deux heures de la journée (entre 0 et 12)."""
    a, b = localtime(date_a), localtime(date_b)
    difference = abs((a.hour + a.minute / 60) - (b.hour + b.minute / 60))
    return min(difference, 24 - difference)


def _heure_inhabituelle(historique, date):
    """L'heure de connexion est-elle inhabituelle pour cet utilisateur ?

    - Modèle IA : Isolation Forest (scikit-learn) entraîné sur les heures habituelles de l'utilisateur.
    - Garde-fous : jamais d'alerte si une connexion habituelle a eu lieu à moins d'1 h de cette heure,
      et toujours une alerte si aucune connexion n'a eu lieu à moins de 3 h.
    """
    if not historique:
        return False
    plus_proche = min(_ecart_heures(date, c.date) for c in historique)
    if plus_proche <= 1:
        return False
    if plus_proche > 3:
        return True
    if len(historique) < HISTORIQUE_MIN_IA:
        return False

    from sklearn.ensemble import IsolationForest

    modele = IsolationForest(n_estimators=300, contamination=0.05, random_state=42)
    modele.fit([_caracteristiques(c.date) for c in historique])
    return modele.predict([_caracteristiques(date)])[0] == -1


def evaluer_risque(utilisateur, ip, appareil, date):
    """Renvoie (score sur 100, liste des raisons) pour une connexion réussie."""
    from .models import JournalConnexion

    historique = list(
        # Les connexions suspectes ne servent pas à apprendre les habitudes (un pirate ne doit pas « entraîner » l'IA)
        JournalConnexion.objects.filter(utilisateur=utilisateur, succes=True, suspect=False)
        .exclude(date__gte=date).order_by("-date")[:200]
    )
    if not historique:
        return 0, []  # première connexion : rien à comparer

    raisons, score = [], 0
    if _heure_inhabituelle(historique, date):
        score += POIDS["heure"]
        raisons.append(f"Heure inhabituelle ({localtime(date):%H} h)")
    if appareil not in {c.appareil for c in historique}:
        score += POIDS["appareil"]
        raisons.append(f"Nouvel appareil ({appareil})")
    if ip and prefixe_reseau(ip) not in {prefixe_reseau(c.ip) for c in historique}:
        score += POIDS["ip"]
        raisons.append("Nouvelle adresse réseau")
    return score, raisons


# ---------- Enregistrement ----------

def enregistrer_connexion(request, utilisateur=None, nom_saisi="", succes=False, methode="MOT_DE_PASSE",
                          double_auth=False, motif=""):
    from .models import JournalConnexion

    ip = adresse_ip(request)
    user_agent = request.META.get("HTTP_USER_AGENT", "")
    entree = JournalConnexion.objects.create(
        utilisateur=utilisateur, nom_saisi=nom_saisi or (utilisateur.username if utilisateur else ""),
        ip=ip, appareil=decrire_appareil(user_agent), navigateur=user_agent[:500],
        methode=methode, double_auth=double_auth, succes=succes, motif=motif,
    )

    if succes and utilisateur:
        score, raisons = evaluer_risque(utilisateur, ip, entree.appareil, entree.date)
        entree.score_risque, entree.raisons_risque = score, raisons
        entree.suspect = score >= SEUIL_SUSPECT
        entree.save(update_fields=["score_risque", "raisons_risque", "suspect"])
        if entree.suspect:
            alerter_utilisateur(request, entree)
    return entree


def alerter_utilisateur(request, entree):
    """E-mail d'alerte en cas de connexion inhabituelle."""
    if not entree.utilisateur.email:
        return
    message = render_to_string("accounts/emails/alerte_connexion.txt", {
        "user": entree.utilisateur, "entree": entree,
        "lien_profil": request.build_absolute_uri("/profil/connexions/"),
    })
    send_mail("EduVox – Connexion inhabituelle à votre compte", message, None, [entree.utilisateur.email],
              fail_silently=True)