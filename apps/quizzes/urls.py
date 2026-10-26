from django.urls import path

from . import views

urlpatterns = [
    path("", views.ListeQuizView.as_view(), name="liste-quiz"),
    path("creer/", views.CreerQuizView.as_view(), name="creer-quiz"),
    path("generer/", views.GenererQuizView.as_view(), name="generer-quiz"),
    path("<int:pk>/", views.DetailQuizView.as_view(), name="detail-quiz"),
    path("<int:pk>/modifier/", views.ModifierQuizView.as_view(), name="modifier-quiz"),
    path("<int:pk>/supprimer/", views.SupprimerQuizView.as_view(), name="supprimer-quiz"),
    path("<int:pk>/publier/", views.PublierQuizView.as_view(), name="publier-quiz"),
    path("<int:pk>/depublier/", views.DepublierQuizView.as_view(), name="depublier-quiz"),
    path("<int:pk>/archiver/", views.ArchiverQuizView.as_view(), name="archiver-quiz"),
    path("<int:pk>/nouvelle-version/", views.NouvelleVersionView.as_view(), name="nouvelle-version-quiz"),
    path("<int:pk>/generation/", views.EtatGenerationView.as_view(), name="etat-generation-quiz"),
    path("<int:pk>/relancer-generation/", views.RelancerGenerationView.as_view(), name="relancer-generation-quiz"),
    path("<int:pk>/resultats/", views.ResultatsQuizView.as_view(), name="resultats-quiz"),

    # Questions et choix
    path("<int:pk>/questions/ajouter/", views.AjouterQuestionView.as_view(), name="ajouter-question"),
    path("questions/<int:pk>/modifier/", views.ModifierQuestionView.as_view(), name="modifier-question"),
    path("questions/<int:pk>/supprimer/", views.SupprimerQuestionView.as_view(), name="supprimer-question"),
    path("questions/<int:pk>/deplacer/", views.DeplacerQuestionView.as_view(), name="deplacer-question"),

    # Passage et résultats
    path("<int:pk>/commencer/", views.CommencerQuizView.as_view(), name="commencer-quiz"),
    path("tentatives/<int:pk>/", views.TentativeView.as_view(), name="tentative"),
    path("tentatives/<int:pk>/etat/", views.EtatTentativeView.as_view(), name="etat-tentative"),
    path("tentatives/<int:pk>/relancer-correction/", views.RelancerCorrectionView.as_view(),
         name="relancer-correction"),
    path("reponses/<int:pk>/reviser/", views.ReviserReponseView.as_view(), name="reviser-reponse"),
    path("mes-resultats/", views.MesTentativesView.as_view(), name="mes-tentatives"),

    # Assistant IA
    path("tentatives/<int:pk>/indice/<int:question_pk>/", views.IndiceView.as_view(), name="indice-question"),
    path("reponses/<int:pk>/expliquer/", views.ExpliquerErreurView.as_view(), name="expliquer-erreur"),
    path("tentatives/<int:pk>/relancer-bilan/", views.RelancerBilanView.as_view(), name="relancer-bilan"),
    path("<int:pk>/audit/", views.AuditQuizView.as_view(), name="audit-quiz"),
    path("<int:pk>/analyse-classe/", views.AnalyseClasseView.as_view(), name="analyse-classe"),
    path("<int:pk>/etat-ia/", views.EtatIAQuizView.as_view(), name="etat-ia-quiz"),
    path("pilotage/", views.PilotageView.as_view(), name="pilotage-quiz"),

    # Carnet de révision espacée, intégrité, import intelligent, certificats et export
    path("revision/", views.RevisionView.as_view(), name="revision"),
    path("revision/<int:pk>/", views.RepondreRevisionView.as_view(), name="repondre-revision"),
    path("tentatives/<int:pk>/integrite/", views.IntegriteView.as_view(), name="integrite-tentative"),
    path("<int:pk>/importer/", views.ImporterQuestionsView.as_view(), name="importer-questions"),
    path("tentatives/<int:pk>/certificat.pdf", views.CertificatView.as_view(), name="certificat"),
    path("certificat/<str:jeton>/", views.VerifierCertificatView.as_view(), name="verifier-certificat"),
    path("<int:pk>/resultats.csv", views.ExportResultatsView.as_view(), name="export-resultats"),
]
