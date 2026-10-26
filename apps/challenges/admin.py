"""Admin EduSprint : consultation et modération SANS contournement des règles métier.

- Statuts, états, auteurs, dates de soumission : en lecture seule. Les transitions passent par des actions
  qui appellent les mêmes services que l'interface (vérifications et verrous identiques).
- Historique (participations, versions soumises, réponses, imprévus publiés, demandes IA) : lecture seule,
  jamais supprimable depuis l'admin. Les verrous de save()/delete() des modèles restent la dernière barrière.
- Modération possible : archiver un défi, supprimer un commentaire inapproprié (tracé par le journal de l'admin).
"""

from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from .models import (
    CommentaireTache,
    Defi,
    DemandeIA,
    Equipe,
    EvenementScenario,
    Jalon,
    Livrable,
    ParticipationEquipe,
    ReponseEvenement,
    Tache,
    VersionLivrable,
)
from .services import defis as regles


class LectureSeuleAdmin(admin.ModelAdmin):
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class JalonInline(admin.TabularInline):
    model = Jalon
    extra = 0
    fields = ["ordre", "titre", "echeance"]
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Defi)
class DefiAdmin(admin.ModelAdmin):
    list_display = ["titre", "enseignant", "statut", "niveau", "date_debut", "date_fin", "origine"]
    list_filter = ["statut", "niveau", "origine"]
    search_fields = ["titre", "description", "enseignant__username", "enseignant__last_name"]
    readonly_fields = ["statut", "origine", "enseignant", "date_creation", "date_modification", "date_ouverture",
                       "date_demarrage", "date_cloture", "date_archivage"]
    filter_horizontal = ["co_enseignants"]
    inlines = [JalonInline]
    actions = ["archiver"]

    def has_add_permission(self, request):
        return False  # un défi se crée par l'enseignant, dans l'interface

    def has_change_permission(self, request, obj=None):
        # Modification du contenu seulement avant le démarrage (save() du modèle le garantit aussi)
        return super().has_change_permission(request, obj) and (obj is None or obj.brief_modifiable)

    def has_delete_permission(self, request, obj=None):
        return obj is not None and obj.statut == Defi.Statut.BROUILLON and not obj.a_des_contributions() \
            and super().has_delete_permission(request, obj)

    @admin.action(description="Archiver (modération)")
    def archiver(self, request, queryset):
        for defi in queryset:
            try:
                regles.archiver(defi)
            except ValidationError as erreur:
                self.message_user(request, f"« {defi} » : {' '.join(erreur.messages)}", messages.ERROR)
            else:
                self.message_user(request, f"« {defi} » archivé.")


@admin.register(Equipe)
class EquipeAdmin(LectureSeuleAdmin):
    list_display = ["nom", "defi", "coordinateur", "statut", "date_creation"]
    list_filter = ["statut"]
    search_fields = ["nom", "defi__titre"]


@admin.register(ParticipationEquipe)
class ParticipationAdmin(LectureSeuleAdmin):
    list_display = ["etudiant", "equipe", "defi", "etat", "date_demande", "date_reponse"]
    list_filter = ["etat"]
    search_fields = ["etudiant__username", "equipe__nom"]


@admin.register(Tache)
class TacheAdmin(LectureSeuleAdmin):
    list_display = ["titre", "equipe", "responsable", "statut", "priorite", "echeance"]
    list_filter = ["statut", "priorite"]
    search_fields = ["titre", "equipe__nom"]


@admin.register(CommentaireTache)
class CommentaireAdmin(LectureSeuleAdmin):
    list_display = ["tache", "auteur", "date_creation"]
    search_fields = ["contenu"]

    def has_delete_permission(self, request, obj=None):
        return super(LectureSeuleAdmin, self).has_delete_permission(request, obj)  # modération


class VersionInline(admin.TabularInline):
    model = VersionLivrable
    extra = 0
    fields = ["numero", "etat", "nom_fichier", "lien", "date_soumission", "en_retard", "date_retour"]
    readonly_fields = fields
    can_delete = False
    show_change_link = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Livrable)
class LivrableAdmin(LectureSeuleAdmin):
    list_display = ["titre", "equipe", "jalon", "date_creation"]
    search_fields = ["titre", "equipe__nom"]
    inlines = [VersionInline]


@admin.register(EvenementScenario)
class EvenementAdmin(LectureSeuleAdmin):
    list_display = ["titre", "defi", "type_contrainte", "origine", "etat", "date_publication"]
    list_filter = ["etat", "origine", "type_contrainte"]
    search_fields = ["titre", "defi__titre"]


@admin.register(ReponseEvenement)
class ReponseAdmin(LectureSeuleAdmin):
    list_display = ["evenement", "equipe", "etat", "date_soumission", "en_retard"]
    list_filter = ["etat", "en_retard"]


@admin.register(DemandeIA)
class DemandeIAAdmin(LectureSeuleAdmin):
    list_display = ["type_demande", "statut", "code_erreur", "duree_ms", "essais", "modele", "date_creation"]
    list_filter = ["type_demande", "statut", "code_erreur"]
    exclude = ["resultat", "parametres"]  # contenus pédagogiques : consultés dans l'interface, pas ici
