from django.contrib import admin

from .models import (
    Avis,
    Consultation,
    Cours,
    Favori,
    JournalActivite,
    Matiere,
    NotePersonnelle,
    Resume,
    Signalement,
    Telechargement,
    Transcription,
    TraductionResume,
)


@admin.register(Matiere)
class MatiereAdmin(admin.ModelAdmin):
    list_display = ["nom"]
    search_fields = ["nom"]


@admin.register(Cours)
class CoursAdmin(admin.ModelAdmin):
    list_display = ["titre", "enseignant", "matiere", "niveau", "publication", "masque", "archive", "statut", "date_creation"]
    list_filter = ["publication", "masque", "archive", "statut", "type_fichier", "niveau", "matiere"]
    search_fields = ["titre", "description", "mots_cles"]
    filter_horizontal = ["prerequis"]


@admin.register(Transcription)
class TranscriptionAdmin(admin.ModelAdmin):
    list_display = ["cours", "date"]


class TraductionInline(admin.TabularInline):
    model = TraductionResume
    extra = 0


@admin.register(Resume)
class ResumeAdmin(admin.ModelAdmin):
    list_display = ["cours", "type_resume", "date_generation"]
    list_filter = ["type_resume"]
    inlines = [TraductionInline]


@admin.register(Consultation)
class ConsultationAdmin(admin.ModelAdmin):
    list_display = ["cours", "utilisateur", "date"]
    list_filter = ["date"]


@admin.register(Telechargement)
class TelechargementAdmin(admin.ModelAdmin):
    list_display = ["cours", "utilisateur", "format", "date"]
    list_filter = ["format"]


@admin.register(Favori)
class FavoriAdmin(admin.ModelAdmin):
    list_display = ["cours", "utilisateur", "date_ajout"]


@admin.register(Avis)
class AvisAdmin(admin.ModelAdmin):
    list_display = ["cours", "utilisateur", "note", "date_modification"]
    list_filter = ["note"]


@admin.register(NotePersonnelle)
class NotePersonnelleAdmin(admin.ModelAdmin):
    list_display = ["cours", "utilisateur", "date_modification"]


@admin.register(Signalement)
class SignalementAdmin(admin.ModelAdmin):
    list_display = ["cours", "auteur", "motif", "statut", "date"]
    list_filter = ["statut", "motif"]


@admin.register(JournalActivite)
class JournalActiviteAdmin(admin.ModelAdmin):
    list_display = ["date", "utilisateur", "action", "titre_cours"]
    list_filter = ["action"]
    search_fields = ["titre_cours"]
