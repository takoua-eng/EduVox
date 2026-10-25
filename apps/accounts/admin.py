from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import Enseignant, Etudiant, JournalConnexion, ProfilFacial, Utilisateur

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
    
@admin.register(JournalConnexion)
class JournalConnexionAdmin(admin.ModelAdmin):
    list_display = ["date", "nom_saisi", "methode", "succes", "score_risque", "suspect", "appareil", "ip"]
    list_filter = ["succes", "suspect", "methode"]
    search_fields = ["nom_saisi", "ip"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False  # un journal ne se modifie pas
    
admin.site.register(Etudiant)
admin.site.register(Enseignant)