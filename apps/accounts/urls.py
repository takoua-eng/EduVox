from django.contrib.auth.views import LogoutView
from django.urls import path
from . import views, views_securite, views_visage

urlpatterns = [
    path("connexion/", views.ConnexionView.as_view(), name="connexion"),
    path("inscription/", views.InscriptionView.as_view(), name="inscription"),
    path("deconnexion/", LogoutView.as_view(), name="deconnexion"),
    path("profil/", views.MonProfilView.as_view(), name="mon-profil"),
    path("profil/supprimer/", views.SupprimerMonCompteView.as_view(), name="supprimer-mon-compte"),
    path("roles-permissions/", views.RolesPermissionsView.as_view(), name="roles-permissions"),
        
    # Espace administrateur
    path("utilisateurs/", views.ListeUtilisateursView.as_view(), name="liste-utilisateurs"),
    path("utilisateurs/ajouter/", views.AjouterUtilisateurView.as_view(), name="ajouter-utilisateur"),
    path("utilisateurs/<int:pk>/modifier/", views.ModifierUtilisateurView.as_view(), name="modifier-utilisateur"),
    path("utilisateurs/<int:pk>/activer/", views.ActiverUtilisateurView.as_view(), name="activer-utilisateur"),
    path("utilisateurs/<int:pk>/supprimer/", views.SupprimerUtilisateurView.as_view(), name="supprimer-utilisateur"),
    
        # Mot de passe oublié
    path("mot-de-passe-oublie/", views.MotDePasseOublieView.as_view(), name="mdp-oublie"),
    path("mot-de-passe-oublie/envoye/", views.MotDePasseEnvoyeView.as_view(), name="mdp-envoye"),
    path("reinitialiser/<uidb64>/<token>/", views.ReinitialiserMotDePasseView.as_view(), name="mdp-reinitialiser"),
    path("reinitialiser/termine/", views.MotDePasseTermineView.as_view(), name="mdp-termine"),
    
    
        # Vérification de l'e-mail
    path("verification-envoyee/", views.VerificationEnvoyeeView.as_view(), name="verification-envoyee"),
    path("verifier-email/<uidb64>/<token>/", views.VerifierEmailView.as_view(), name="verifier-email"),
    path("renvoyer-verification/", views.RenvoyerVerificationView.as_view(), name="renvoyer-verification"),
    
    
        # Double authentification
    path("connexion/2fa/", views.Connexion2FAView.as_view(), name="connexion-2fa"),
    path("profil/2fa/activer/", views.Activer2FAView.as_view(), name="activer-2fa"),
    path("profil/2fa/codes/", views.CodesRecuperationView.as_view(), name="codes-recuperation"),
    path("profil/2fa/regenerer/", views.RegenererCodesView.as_view(), name="regenerer-codes"),
    path("profil/2fa/desactiver/", views.Desactiver2FAView.as_view(), name="desactiver-2fa"),
    
        # Reconnaissance faciale
    path("connexion/visage/", views_visage.ConnexionVisageView.as_view(), name="connexion-visage"),
    path("profil/visage/", views_visage.EnregistrerVisageView.as_view(), name="enregistrer-visage"),
    path("profil/visage/supprimer/", views_visage.SupprimerVisageView.as_view(), name="supprimer-visage"),
    
    
        # Journal des connexions
    path("profil/connexions/", views_securite.MesConnexionsView.as_view(), name="mes-connexions"),
    path("journal-connexions/", views_securite.JournalConnexionsView.as_view(), name="journal-connexions"),
]