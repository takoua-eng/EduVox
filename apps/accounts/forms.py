from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from .models import Utilisateur, Etudiant, Enseignant


def creer_profil(user):
    """Crée le profil Etudiant ou Enseignant qui correspond au rôle."""
    if user.role == Utilisateur.Role.ETUDIANT:
        Etudiant.objects.get_or_create(utilisateur=user)
    elif user.role == Utilisateur.Role.ENSEIGNANT:
        Enseignant.objects.get_or_create(utilisateur=user)


class InscriptionForm(UserCreationForm):
    role = forms.ChoiceField(
        choices=[
            (Utilisateur.Role.ETUDIANT, "Étudiant"),
            (Utilisateur.Role.ENSEIGNANT, "Enseignant"),
        ]
    )

    class Meta:
        model = Utilisateur
        fields = ["username", "first_name", "last_name", "email", "role"]

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = self.cleaned_data["role"]
        if commit:
            user.save()
            creer_profil(user)
        return user


class AjoutUtilisateurForm(UserCreationForm):
    """Formulaire de l'admin pour créer un compte (tous les rôles)."""

    class Meta:
        model = Utilisateur
        fields = ["photo_profil", "username", "first_name", "last_name", "email", "role", "is_active"]
        labels = {"photo_profil": "Photo de profil", "role": "Rôle"}
    def save(self, commit=True):
        user = super().save(commit=False)
        user.email_verifie = True
        if commit:
            user.save()
            creer_profil(user)
        return user


class ModifierUtilisateurForm(forms.ModelForm):
    """Formulaire de l'admin pour modifier un compte."""

    class Meta:
        model = Utilisateur
        fields = ["photo_profil", "username", "first_name", "last_name", "email", "role", "is_active"]
        labels = {"photo_profil": "Photo de profil", "role": "Rôle"}
    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            creer_profil(user)
        return user

class ProfilForm(forms.ModelForm):
    """Formulaire de l'utilisateur pour modifier son propre profil."""

    class Meta:
        model = Utilisateur
        fields = ["photo_profil", "first_name", "last_name", "email"]
        labels = {
            "photo_profil": "Photo de profil",
            "first_name": "Prénom",
            "last_name": "Nom",
            "email": "E-mail",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user = self.instance
        # Champ supplémentaire selon le rôle
        if hasattr(user, "etudiant"):
            self.fields["niveau"] = forms.CharField(
                label="Niveau", required=False, initial=user.etudiant.niveau
            )
        elif hasattr(user, "enseignant"):
            self.fields["specialite"] = forms.CharField(
                label="Spécialité", required=False, initial=user.enseignant.specialite
            )

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            if "niveau" in self.cleaned_data:
                user.etudiant.niveau = self.cleaned_data["niveau"]
                user.etudiant.save()
            if "specialite" in self.cleaned_data:
                user.enseignant.specialite = self.cleaned_data["specialite"]
                user.enseignant.save()
        return user
    
class ConnexionForm(AuthenticationForm):
    error_messages = {
        "invalid_login": "Nom d'utilisateur ou mot de passe incorrect.",
        "inactive": "Ce compte a été désactivé par un administrateur.",
    }

    def clean(self):
        from django.utils.formats import time_format
        from django.utils.timezone import localtime

        from .securite import MOTIF_PENDANT_BLOCAGE, enregistrer_connexion, ip_bloquee

        nom = (self.cleaned_data.get("username") or "").strip()
        if self.request and ip_bloquee(self.request):
            raise forms.ValidationError(
                "Trop de tentatives depuis cet appareil. Réessayez dans 15 minutes.", code="ip_bloquee")

        user = Utilisateur.objects.filter(username__iexact=nom).first() if nom else None
        if user and user.est_bloque:
            # On refuse AVANT de vérifier le mot de passe : un pirate ne peut plus rien tester
            if self.request:
                enregistrer_connexion(self.request, utilisateur=user, nom_saisi=nom, succes=False,
                                      motif=MOTIF_PENDANT_BLOCAGE)
            heure = time_format(localtime(user.bloque_jusqua), "H:i")
            raise forms.ValidationError(
                f"Compte temporairement bloqué après {self._max_echecs()} essais incorrects. "
                f"Réessayez après {heure} ou réinitialisez votre mot de passe.", code="compte_bloque")

        try:
            return super().clean()
        except forms.ValidationError:
            if user:
                user.refresh_from_db(fields=["bloque_jusqua"])
                if user.est_bloque:  # le blocage vient d'être déclenché par cet essai
                    heure = time_format(localtime(user.bloque_jusqua), "H:i")
                    raise forms.ValidationError(
                        f"Trop d'essais incorrects : compte bloqué jusqu'à {heure}. "
                        "Un e-mail vous a été envoyé.", code="compte_bloque")
            raise

    @staticmethod
    def _max_echecs():
        from .securite import MAX_ECHECS_COMPTE
        return MAX_ECHECS_COMPTE

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not user.email_verifie and not user.is_superuser:
            raise forms.ValidationError(
                "Vous devez d'abord confirmer votre adresse e-mail. Vérifiez votre boîte mail.",
                code="email_non_verifie",
            )