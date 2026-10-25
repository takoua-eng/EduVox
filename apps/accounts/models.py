from django.contrib.auth.models import AbstractUser
from django.db import models
from .roles import NOM_GROUPE, PERMISSIONS

class Utilisateur(AbstractUser):
    class Role(models.TextChoices):
        ETUDIANT = "ETUDIANT", "Étudiant"
        ENSEIGNANT = "ENSEIGNANT", "Enseignant"
        ADMIN = "ADMIN", "Administrateur"

    email = models.EmailField(unique=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.ETUDIANT)
    photo_profil = models.ImageField(upload_to="profils/", blank=True, null=True)
    date_inscription = models.DateTimeField(auto_now_add=True)
    email_verifie = models.BooleanField(default=False)
    double_auth_active = models.BooleanField(default=False)
    secret_totp = models.CharField(max_length=32, blank=True)

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.get_role_display()})"
    class Meta(AbstractUser.Meta):
        permissions = PERMISSIONS

    def synchroniser_groupe(self):
        """Place l'utilisateur dans le groupe correspondant à son rôle."""
        from django.contrib.auth.models import Group
        groupe, _ = Group.objects.get_or_create(name=NOM_GROUPE[self.role])
        self.groups.set([groupe])

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.synchroniser_groupe()
    


class Etudiant(models.Model):
    utilisateur = models.OneToOneField(Utilisateur, on_delete=models.CASCADE, related_name="etudiant")
    niveau = models.CharField(max_length=50, blank=True)

    def __str__(self):
        return str(self.utilisateur)


class Enseignant(models.Model):
    utilisateur = models.OneToOneField(Utilisateur, on_delete=models.CASCADE, related_name="enseignant")
    specialite = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return str(self.utilisateur)
    
class CodeRecuperation(models.Model):
    utilisateur = models.ForeignKey(Utilisateur, on_delete=models.CASCADE, related_name="codes_recuperation")
    code_hash = models.CharField(max_length=64)
    utilise = models.BooleanField(default=False)

    def __str__(self):
        return f"Code de {self.utilisateur.username} ({'utilisé' if self.utilise else 'disponible'})"
class ProfilFacial(models.Model):
    """Visage enregistré d'un utilisateur. On ne stocke que les encodages, jamais les photos."""

    utilisateur = models.OneToOneField(Utilisateur, on_delete=models.CASCADE, related_name="profil_facial")
    encodages = models.JSONField(help_text="Encodages SFace (128 nombres) des captures d'enregistrement")
    consentement_le = models.DateTimeField()
    cree_le = models.DateTimeField(auto_now_add=True)
    modifie_le = models.DateTimeField(auto_now=True)
    derniere_utilisation = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "profil facial"
        verbose_name_plural = "profils faciaux"

    def __str__(self):
        return f"Visage de {self.utilisateur.username}"

    def score(self, encodage):
        """Meilleure similarité entre un nouvel encodage et les captures enregistrées."""
        from ai_services import similarite
        return max(similarite(encodage, reference) for reference in self.encodages)