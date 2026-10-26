from django.contrib import admin

from .models import Illustration, JournalIA, MessageVocal, SessionAssistant


class MessageInline(admin.TabularInline):
    model = MessageVocal
    extra = 0
    fields = ("role", "mode", "texte", "statut", "evaluation", "date")
    readonly_fields = ("date",)


@admin.register(SessionAssistant)
class SessionAssistantAdmin(admin.ModelAdmin):
    list_display = ("titre", "utilisateur", "cours", "active", "date_ouverture", "date_maj")
    list_filter = ("active",)
    search_fields = ("titre", "utilisateur__username")
    inlines = [MessageInline]


@admin.register(MessageVocal)
class MessageVocalAdmin(admin.ModelAdmin):
    list_display = ("session", "role", "mode", "statut", "evaluation", "duree_ms", "date")
    list_filter = ("role", "mode", "statut", "evaluation")


@admin.register(Illustration)
class IllustrationAdmin(admin.ModelAdmin):
    list_display = ("demande", "type_illustration", "statut", "session", "duree_ms", "date")
    list_filter = ("type_illustration", "statut")


@admin.register(JournalIA)
class JournalIAAdmin(admin.ModelAdmin):
    list_display = ("type_appel", "succes", "code", "modele", "duree_ms", "premier_morceau_ms", "date")
    list_filter = ("type_appel", "succes")
