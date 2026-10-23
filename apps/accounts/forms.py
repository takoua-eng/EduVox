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

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not user.email_verifie and not user.is_superuser:
            raise forms.ValidationError(
                "Vous devez d'abord confirmer votre adresse e-mail. Vérifiez votre boîte mail.",
                code="email_non_verifie",
            )