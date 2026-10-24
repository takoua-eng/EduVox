"""Données du menu latéral pour le module cours (rôle, page active, signalements à traiter)."""

from .models import Signalement
from .permissions import est_admin, est_enseignant, est_etudiant

# Nom d'URL → entrée du menu à mettre en surbrillance
ENTREES_MENU = {
    "statistiques-cours": "statistiques",
    "avis-recus": "avis-recus",
    "mes-favoris": "favoris",
    "historique-cours": "historique",
    "signalements": "signalements",
    "classement-cours": "classement",
    "journal-activite": "journal",
    "archivage-cours": "archivage",
    "matieres": "matieres",
}


def menu_cours(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}

    if est_admin(user):
        role = "admin"
    elif est_enseignant(user):
        role = "enseignant"
    elif est_etudiant(user):
        role = "etudiant"
    else:
        role = ""

    nom_url = request.resolver_match.url_name if request.resolver_match else None
    actif = ENTREES_MENU.get(nom_url) or ("cours" if request.path.startswith("/cours/") else "")

    contexte = {"cours_role": role, "cours_menu_actif": actif}
    if role == "admin":
        contexte["cours_signalements_nouveaux"] = Signalement.objects.filter(statut=Signalement.Statut.NOUVEAU).count()
    return contexte
