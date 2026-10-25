from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import Enseignant, Etudiant, ProfilFacial, Utilisateur


@admin.register(Utilisateur)
class UtilisateurAdmin(UserAdmin):
    list_display = ["username", "email", "first_name", "last_name", "role", "is_active"]
    list_filter = ["role", "is_active"]
    fieldsets = UserAdmin.fieldsets + (("EduVox", {"fields": ("role", "photo_profil")}),)

@admin.register(ProfilFacial)
class ProfilFacialAdmin(admin.ModelAdmin):
    list_display = ["utilisateur", "cree_le", "derniere_utilisation"]
    readonly_fields = ["utilisateur", "consentement_le", "cree_le", "modifie_le", "derniere_utilisation"]
    exclude = ["encodages"]  # données biométriques : jamais affichées

    def has_add_permission(self, request):
        return False  # un visage s'enregistre uniquement par l'utilisateur, avec la caméra
    
admin.site.register(Etudiant)
admin.site.register(Enseignant)