"""Définition des rôles et de leurs permissions (équivalent de Spatie Permission)."""

# Toutes les permissions de la plateforme : (code, description)
PERMISSIONS = [
    ("gerer_utilisateurs", "Gérer les utilisateurs"),
    ("voir_statistiques", "Voir les statistiques globales"),
    ("consulter_cours", "Consulter les cours"),
    ("publier_cours", "Publier des cours"),
    ("generer_quiz", "Générer et valider des quiz"),
    ("passer_quiz", "Passer des quiz"),
    ("poser_question", "Poser des questions à l'assistant"),
    ("marquer_presence", "Marquer sa présence"),
    ("suivre_presence", "Suivre la présence et l'engagement"),
]

# Nom du groupe Django pour chaque rôle
NOM_GROUPE = {
    "ETUDIANT": "Étudiant",
    "ENSEIGNANT": "Enseignant",
    "ADMIN": "Administrateur",
}

# Permissions de chaque rôle
PERMISSIONS_PAR_ROLE = {
    "ETUDIANT": ["consulter_cours", "poser_question", "passer_quiz", "marquer_presence"],
    "ENSEIGNANT": ["consulter_cours", "publier_cours", "generer_quiz", "suivre_presence"],
    "ADMIN": [code for code, _ in PERMISSIONS],  # l'administrateur a tout
}