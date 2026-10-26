"""Journal des connexions et détection IA des connexions suspectes."""

import logging
import math
import time
from datetime import timedelta

from django.core.cache import caches
from django.utils import timezone
from django.utils.timezone import localtime

logger = logging.getLogger(__name__)

HISTORIQUE_MIN_IA = 8       # connexions réussies nécessaires pour entraîner le modèle
TAILLE_HISTORIQUE = 200     # connexions passées prises en compte
DUREE_CACHE_MODELE = 24 * 3600  # le modèle entraîné d'un utilisateur est gardé 24 h (ou jusqu'à sa prochaine connexion)
SEUIL_SUSPECT = 60          # score de risque (sur 100) à partir duquel la connexion est suspecte
POIDS = {"heure": 40, "appareil": 35, "ip": 25}

MAX_ECHECS_COMPTE = 5       # mots de passe faux avant blocage du compte
MAX_ECHECS_IP = 20          # échecs depuis un même appareil avant blocage de cet appareil
MOTIF_PENDANT_BLOCAGE = "Tentative pendant le blocage"
DUREE_BLOCAGE = timedelta(minutes=15)


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


def cle_modele(utilisateur, historique):
    """Clé de cache du modèle : elle change dès que l'historique change (nombre, dernière et première connexion)."""
    if utilisateur is None or not historique:
        return None
    identifiant = utilisateur.pk or f"simule-{utilisateur.username}"  # utilisateurs simulés de l'évaluation
    dates = [c.date for c in historique]
    return f"accounts:modele_heures:{identifiant}:{len(dates)}:{max(dates).timestamp()}:{min(dates).timestamp()}"


def cache_modeles():
    """Cache partagé avec le worker Celery (réglage CACHES["modeles_ia"])."""
    return caches["modeles_ia"]


def _modele_heures(historique, cle=None):
    """Isolation Forest entraîné sur les heures de l'historique, réutilisé depuis le cache si possible.

    Une panne du cache (Redis arrêté, dossier inaccessible) ne bloque jamais la connexion : on entraîne.
    """
    modele = None
    if cle:
        try:
            modele = cache_modeles().get(cle)
        except Exception:
            logger.exception("Cache des modèles IA illisible")
    if modele is None:
        from sklearn.ensemble import IsolationForest

        modele = IsolationForest(n_estimators=300, contamination=0.05, random_state=42)
        modele.fit([_caracteristiques(c.date) for c in historique])
        if cle:
            try:
                cache_modeles().set(cle, modele, DUREE_CACHE_MODELE)
            except Exception:
                logger.exception("Cache des modèles IA inaccessible en écriture")
    return modele


def historique_connexions(utilisateur, avant=None):
    """Connexions réussies non suspectes, les plus récentes d'abord (ce sur quoi l'IA apprend les habitudes)."""
    from .models import JournalConnexion

    # Les connexions suspectes ne servent pas à apprendre les habitudes (un pirate ne doit pas « entraîner » l'IA)
    connexions = JournalConnexion.objects.filter(utilisateur=utilisateur, succes=True, suspect=False)
    if avant is not None:
        connexions = connexions.exclude(date__gte=avant)
    return list(connexions.order_by("-date")[:TAILLE_HISTORIQUE])


def preparer_modele_heures(utilisateur, historique=None):
    """Entraîne et met en cache le modèle de la PROCHAINE connexion (historique actuel complet).

    Appelé par la tâche Celery après chaque connexion réussie. Renvoie True si un modèle est prêt en cache.
    """
    historique = historique_connexions(utilisateur) if historique is None else historique
    if len(historique) < HISTORIQUE_MIN_IA:
        return False  # le modèle ne serait jamais utilisé
    _modele_heures(historique, cle_modele(utilisateur, historique))
    return True


def _heure_inhabituelle(historique, date, cle=None):
    """L'heure de connexion est-elle inhabituelle pour cet utilisateur ?

    - Modèle IA : Isolation Forest (scikit-learn) entraîné sur les heures habituelles de l'utilisateur
      (mis en cache : il n'est réentraîné que si l'historique a changé).
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

    return _modele_heures(historique, cle).predict([_caracteristiques(date)])[0] == -1


def evaluer_risque(utilisateur, ip, appareil, date, historique=None):
    """Renvoie (score sur 100, liste des raisons) pour une connexion réussie.

    `historique` permet de fournir les connexions passées sans base de données (évaluation de l'IA).
    """
    if historique is None:
        historique = historique_connexions(utilisateur, avant=date)
    if not historique:
        return 0, []  # première connexion : rien à comparer

    raisons, score = [], 0
    if _heure_inhabituelle(historique, date, cle_modele(utilisateur, historique)):
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
    temps_visage = getattr(request, "temps_ia_visage_ms", None)  # renseigné par la connexion par visage
    entree = JournalConnexion.objects.create(
        utilisateur=utilisateur, nom_saisi=nom_saisi or (utilisateur.username if utilisateur else ""),
        ip=ip, appareil=decrire_appareil(user_agent), navigateur=user_agent[:500],
        methode=methode, double_auth=double_auth, succes=succes, motif=motif, temps_ia_ms=temps_visage,
    )

    if succes and utilisateur:
        debut = time.perf_counter()
        score, raisons = evaluer_risque(utilisateur, ip, entree.appareil, entree.date)
        entree.temps_ia_ms = round((temps_visage or 0) + (time.perf_counter() - debut) * 1000, 1)
        entree.score_risque, entree.raisons_risque = score, raisons
        entree.suspect = score >= SEUIL_SUSPECT
        entree.save(update_fields=["score_risque", "raisons_risque", "suspect", "temps_ia_ms"])
        if entree.suspect:
            alerter_utilisateur(request, entree)
    return entree


def alerter_utilisateur(request, entree):
    """E-mail d'alerte en cas de connexion inhabituelle (envoyé en arrière-plan)."""
    from .emails import envoyer_en_arriere_plan
    from .tasks import email_alerte_connexion

    if entree.utilisateur.email:
        envoyer_en_arriere_plan(email_alerte_connexion, entree.pk, request.build_absolute_uri("/profil/connexions/"))



# ---------- Protection contre la force brute ----------

def _debut_comptage(utilisateur):
    """On compte les échecs depuis : il y a 15 min, la dernière connexion réussie, ou la fin du dernier blocage."""
    from .models import JournalConnexion

    debut = timezone.now() - DUREE_BLOCAGE
    derniere_reussite = JournalConnexion.objects.filter(utilisateur=utilisateur, succes=True).order_by("-date").first()
    if derniere_reussite and derniere_reussite.date > debut:
        debut = derniere_reussite.date
    if utilisateur.bloque_jusqua and utilisateur.bloque_jusqua > debut:
        debut = utilisateur.bloque_jusqua
    return debut


def bloquer_si_necessaire(request, utilisateur):
    """Après un mot de passe faux : bloque le compte au bout de 5 échecs."""
    from .models import JournalConnexion

    echecs = JournalConnexion.objects.filter(
        utilisateur=utilisateur, succes=False, methode=JournalConnexion.Methode.MOT_DE_PASSE,
        date__gt=_debut_comptage(utilisateur),
    ).exclude(motif=MOTIF_PENDANT_BLOCAGE).count()
    if echecs >= MAX_ECHECS_COMPTE:
        utilisateur.bloque_jusqua = timezone.now() + DUREE_BLOCAGE
        utilisateur.save(update_fields=["bloque_jusqua"])
        alerter_blocage(request, utilisateur)


def ip_bloquee(request):
    """Trop d'échecs depuis cet appareil (même sur des comptes différents) ?"""
    from .models import JournalConnexion

    ip = adresse_ip(request)
    if not ip:
        return False
    return JournalConnexion.objects.filter(
        ip=ip, succes=False, date__gte=timezone.now() - DUREE_BLOCAGE,
    ).exclude(motif=MOTIF_PENDANT_BLOCAGE).count() >= MAX_ECHECS_IP


def debloquer(utilisateur):
    """Débloque le compte (les échecs précédents ne sont plus comptés)."""
    utilisateur.bloque_jusqua = timezone.now()
    utilisateur.save(update_fields=["bloque_jusqua"])


def alerter_blocage(request, utilisateur):
    """E-mail « compte bloqué » (envoyé en arrière-plan)."""
    from .emails import envoyer_en_arriere_plan
    from .tasks import email_compte_bloque

    if utilisateur.email:
        envoyer_en_arriere_plan(email_compte_bloque, utilisateur.pk, request.build_absolute_uri("/mot-de-passe-oublie/"))