from django.contrib import admin

from .models import AppelIA, CarteRevision, Choix, Question, Quiz, ReponseEtudiant, TentativeQuiz


class ChoixInline(admin.TabularInline):
    model = Choix
    extra = 0


@admin.register(Quiz)
class QuizAdmin(admin.ModelAdmin):
    list_display = ["titre", "titre_cours", "enseignant", "statut", "origine", "statut_generation", "version",
                    "date_creation"]
    list_filter = ["statut", "origine", "statut_generation", "difficulte"]
    search_fields = ["titre", "titre_cours"]


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ["quiz", "ordre", "type_question", "points"]
    list_filter = ["type_question"]
    inlines = [ChoixInline]


@admin.register(TentativeQuiz)
class TentativeQuizAdmin(admin.ModelAdmin):
    list_display = ["quiz", "etudiant", "numero", "statut", "score", "score_max", "date_soumission"]
    list_filter = ["statut"]
    readonly_fields = ["score", "score_max"]


@admin.register(ReponseEtudiant)
class ReponseEtudiantAdmin(admin.ModelAdmin):
    list_display = ["tentative", "question", "statut_correction", "points_obtenus", "points_ia"]
    list_filter = ["statut_correction"]


@admin.register(AppelIA)
class AppelIAAdmin(admin.ModelAdmin):
    list_display = ["type_appel", "date", "succes", "code", "modele", "duree_ms", "essais"]
    list_filter = ["type_appel", "succes", "code"]


@admin.register(CarteRevision)
class CarteRevisionAdmin(admin.ModelAdmin):
    list_display = ["etudiant", "question", "boite", "prochaine_revision", "maitrisee", "nb_revisions"]
    list_filter = ["boite", "maitrisee"]
