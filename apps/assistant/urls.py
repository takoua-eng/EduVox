from django.urls import path

from . import views

urlpatterns = [
    path("", views.ListeSessionsView.as_view(), name="assistant"),
    path("sessions/nouvelle/", views.CreerSessionView.as_view(), name="creer-session-assistant"),
    path("sessions/<int:pk>/", views.DetailSessionView.as_view(), name="session-assistant"),
    path("sessions/<int:pk>/modifier/", views.ModifierSessionView.as_view(), name="modifier-session-assistant"),
    path("sessions/<int:pk>/supprimer/", views.SupprimerSessionView.as_view(), name="supprimer-session-assistant"),
    path("sessions/<int:pk>/fermer/", views.FermerSessionView.as_view(), name="fermer-session-assistant"),
    path("sessions/<int:pk>/rouvrir/", views.RouvrirSessionView.as_view(), name="rouvrir-session-assistant"),
    path("sessions/<int:pk>/exporter/", views.ExporterSessionView.as_view(), name="exporter-session-assistant"),
    path("sessions/<int:pk>/mode-reponse/", views.ModeReponseView.as_view(), name="mode-reponse-assistant"),

    # Questions (écrites, orales) et réponses
    path("sessions/<int:pk>/question/", views.QuestionView.as_view(), name="question-assistant"),
    path("sessions/<int:pk>/audio/", views.AudioView.as_view(), name="audio-assistant"),
    path("messages/<int:pk>/", views.FragmentMessageView.as_view(), name="fragment-message-assistant"),
    path("messages/<int:pk>/etat/", views.EtatMessageView.as_view(), name="etat-message-assistant"),
    path("messages/<int:pk>/evaluer/", views.EvaluerMessageView.as_view(), name="evaluer-message-assistant"),
    path("messages/<int:pk>/supprimer/", views.SupprimerMessageView.as_view(), name="supprimer-message-assistant"),

    # Illustrations et diagrammes
    path("sessions/<int:pk>/illustrer/", views.IllustrationView.as_view(), name="illustrer-assistant"),
    path("illustrations/", views.GalerieView.as_view(), name="galerie-assistant"),
    path("illustrations/<int:pk>/etat/", views.EtatIllustrationView.as_view(), name="etat-illustration-assistant"),
    path("illustrations/<int:pk>/supprimer/", views.SupprimerIllustrationView.as_view(),
         name="supprimer-illustration-assistant"),

    # Modération (administrateur) : consultation en lecture seule
    path("moderation/", views.ModerationListeView.as_view(), name="moderation-assistant"),
    path("moderation/sessions/<int:pk>/", views.ModerationSessionView.as_view(), name="moderation-session-assistant"),
    path("moderation/sessions/<int:pk>/exporter/", views.ModerationExporterView.as_view(),
         name="moderation-exporter-assistant"),
]
