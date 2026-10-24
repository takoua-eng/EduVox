from django.urls import path

from . import views

urlpatterns = [
    path("", views.ListeCoursView.as_view(), name="liste-cours"),
    path("ajouter/", views.AjouterCoursView.as_view(), name="ajouter-cours"),
    path("<int:pk>/", views.DetailCoursView.as_view(), name="detail-cours"),
    path("<int:pk>/modifier/", views.ModifierCoursView.as_view(), name="modifier-cours"),
    path("<int:pk>/supprimer/", views.SupprimerCoursView.as_view(), name="supprimer-cours"),
    path("<int:pk>/transcrire/", views.TranscrireCoursView.as_view(), name="transcrire-cours"),
    path("<int:pk>/generer-resume/", views.GenererResumeView.as_view(), name="generer-resume"),
    path("<int:pk>/traduire/", views.TraduireResumeView.as_view(), name="traduire-resume"),
    path("<int:pk>/progression/", views.ProgressionCoursView.as_view(), name="progression-cours"),
    path("<int:pk>/resume.txt", views.TelechargerResumeView.as_view(), name="telecharger-resume"),
    path("<int:pk>/resume.pdf", views.TelechargerResumePdfView.as_view(), name="telecharger-resume-pdf"),

    # Suivi de ses cours (enseignant)
    path("statistiques/", views.StatistiquesView.as_view(), name="statistiques-cours"),
    path("avis-recus/", views.AvisRecusView.as_view(), name="avis-recus"),
    path("avis/<int:pk>/repondre/", views.RepondreAvisView.as_view(), name="repondre-avis"),

    # Espace étudiant
    path("favoris/", views.MesFavorisView.as_view(), name="mes-favoris"),
    path("historique/", views.HistoriqueView.as_view(), name="historique-cours"),
    path("<int:pk>/favori/", views.BasculerFavoriView.as_view(), name="basculer-favori"),
    path("<int:pk>/avis/", views.EnregistrerAvisView.as_view(), name="enregistrer-avis"),
    path("<int:pk>/notes/", views.EnregistrerNoteView.as_view(), name="enregistrer-note"),
    path("<int:pk>/signaler/", views.SignalerCoursView.as_view(), name="signaler-cours"),

    # Administration
    path("<int:pk>/masquer/", views.MasquerCoursView.as_view(), name="masquer-cours"),
    path("<int:pk>/archiver/", views.ArchiverCoursView.as_view(), name="archiver-cours"),
    path("signalements/", views.SignalementsView.as_view(), name="signalements"),
    path("signalements/<int:pk>/traiter/", views.TraiterSignalementView.as_view(), name="traiter-signalement"),
    path("archivage/", views.ArchivageView.as_view(), name="archivage-cours"),
    path("journal/", views.JournalActiviteView.as_view(), name="journal-activite"),
    path("classement/", views.ClassementView.as_view(), name="classement-cours"),
    path("export.csv", views.ExportCsvView.as_view(), name="export-cours-csv"),
    path("matieres/", views.MatieresView.as_view(), name="matieres"),
    path("matieres/<int:pk>/modifier/", views.ModifierMatiereView.as_view(), name="modifier-matiere"),
    path("matieres/<int:pk>/supprimer/", views.SupprimerMatiereView.as_view(), name="supprimer-matiere"),
]
