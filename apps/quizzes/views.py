import io
import logging
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Avg, Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.text import slugify
from django.views import View
from django.views.generic import ListView, TemplateView
from xhtml2pdf import pisa

from apps.accounts.views import layout_sans_menu

from web_project import TemplateLayout

from .forms import GenerationQuizForm, QuestionForm, QuizForm, RevisionForm, choix_formset
from .models import (
    COEF_INDICE,
    INDICES_MAX,
    INTERVALLES_LEITNER,
    QUESTIONS_MAX,
    SEUIL_CERTIFICAT,
    AppelIA,
    CarteRevision,
    Choix,
    Question,
    Quiz,
    ReponseEtudiant,
    TentativeQuiz,
    TraitementIA,
)
from .permissions import (
    EnseignantQuizMixin,
    EtudiantQuizMixin,
    est_admin,
    est_enseignant,
    est_etudiant,
    get_quiz_visible,
    peut_gerer,
    quiz_publics,
    quiz_visibles,
    verifier_tentative,
)
from .services import certificat as certificats
from .services.assistant import expliquer_erreur, generer_indice
from .services.importation import TEXTE_IMPORT_MAX, analyser_texte
from .services.integrite import fusionner, lire_compteur, melanger_choix
from .services.revision import alimenter_carnet, cartes_dues, choix_melanges
from .services.revision import statistiques as stats_revision
from .services.evaluation import DejaSoumise, SoumissionInvalide, commencer, nouvelle_version, publier, soumettre
from .services.gemini import ErreurIA, gemini_configure
from .services.generation import SOURCES, sources_disponibles
from .services.reglages import reglage
from .services.statistiques import analyser_quiz, metriques_ia, tableau_de_bord_global
from .tasks import (
    analyser_classe_ia,
    auditer_quiz_ia,
    corriger_tentative,
    declencher_bilan,
    generer_quiz_ia,
    journaliser,
    lancer_apres_validation,
    reserver,
)

logger = logging.getLogger(__name__)


def lancer_tache(tache, objet_id, en_cas_d_echec):
    """Envoie la tâche Celery une fois la transaction validée ; si Celery/Redis est injoignable,
    `en_cas_d_echec()` remet l'objet dans un état cohérent (rien n'est perdu)."""

    def envoyer():
        try:
            tache.delay(objet_id)
        except Exception:
            logger.exception("Impossible d'envoyer la tâche %s (%s) à Celery", tache.name, objet_id)
            en_cas_d_echec()

    transaction.on_commit(envoyer)


class LayoutMixin:
    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))


def role(user):
    if est_admin(user):
        return "admin"
    if est_enseignant(user):
        return "enseignant"
    return "etudiant" if est_etudiant(user) else ""


# ---------- Consultation ----------

class ListeQuizView(LayoutMixin, ListView):
    template_name = "quizzes/liste.html"
    context_object_name = "liste_quiz"
    paginate_by = 20

    def get_queryset(self):
        user = self.request.user
        self.filtre_statut = self.request.GET.get("statut", "")
        self.recherche = self.request.GET.get("q", "").strip()
        quiz = quiz_visibles(user).select_related("cours", "enseignant__utilisateur").annotate(
            nb_questions=Count("questions", distinct=True),
            nb_tentatives=Count("tentatives", filter=~Q(tentatives__statut=TentativeQuiz.Statut.EN_COURS),
                                distinct=True),
        ).order_by("-date_creation")  # l'ordre par défaut est ignoré avec un GROUP BY
        if self.filtre_statut in Quiz.Statut.values and not est_etudiant(user):
            quiz = quiz.filter(statut=self.filtre_statut)
        if self.recherche:
            quiz = quiz.filter(Q(titre__icontains=self.recherche) | Q(titre_cours__icontains=self.recherche))
        return quiz

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        page = list(context["liste_quiz"])
        if est_etudiant(user):
            mes_tentatives = {}
            # Triées de la plus récente à la plus ancienne (ordering du modèle)
            for tentative in TentativeQuiz.objects.filter(etudiant=user, quiz__in=[q.pk for q in page]):
                mes_tentatives.setdefault(tentative.quiz_id, []).append(tentative)
            for quiz in page:
                liste = mes_tentatives.get(quiz.pk, [])
                quiz.mes_nb, quiz.ma_derniere = len(liste), (liste[0] if liste else None)
                scores = [t.pourcentage for t in liste if t.pourcentage is not None and t.est_soumise]
                quiz.meilleur = max(scores) if scores else None
                quiz.en_cours = next((t for t in liste if t.statut == TentativeQuiz.Statut.EN_COURS), None)
            toutes = [t for t in TentativeQuiz.objects.filter(etudiant=user).exclude(statut=TentativeQuiz.Statut.EN_COURS)
                      if t.pourcentage is not None]
            context["revision_dues"] = cartes_dues(user).count()
            context["bandeau"] = {
                "disponibles": quiz_publics().count(),
                "termines": len({t.quiz_id for t in toutes}),
                "moyenne": round(sum(t.pourcentage for t in toutes) / len(toutes)) if toutes else None,
                "meilleur": max((t.pourcentage for t in toutes), default=None),
            }
        else:
            for quiz in page:
                soumises = [t.pourcentage for t in quiz.tentatives.exclude(statut=TentativeQuiz.Statut.EN_COURS)
                            if t.pourcentage is not None]
                quiz.moyenne = round(sum(soumises) / len(soumises)) if soumises else None
            base = quiz_visibles(user)
            compte = dict(base.values_list("statut").annotate(n=Count("pk")))
            context["onglets"] = [("", "Tous", base.count())] + [
                (valeur, libelle, compte.get(valeur, 0)) for valeur, libelle in Quiz.Statut.choices]
        context.update({
            "liste_quiz": page,
            "role": role(user),
            "statuts": Quiz.Statut.choices,
            "filtre_statut": self.filtre_statut,
            "recherche": self.recherche,
            "ia_disponible": gemini_configure(),
        })
        return context


class DetailQuizView(LayoutMixin, TemplateView):
    template_name = "quizzes/detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        quiz = get_quiz_visible(user, self.kwargs["pk"])
        gestion = peut_gerer(user, quiz)
        nb_questions = quiz.questions.count()
        types = dict(quiz.questions.values_list("type_question").annotate(n=Count("pk")))
        context.update({
            "quiz": quiz, "role": role(user), "peut_gerer": gestion,
            "points_total": quiz.points_total(), "nb_questions": nb_questions,
            "repartition_types": [
                ("QCM" if t == Question.Type.QCM else "Vrai/Faux" if t == Question.Type.VRAI_FAUX
                 else "question ouverte" if types[t] == 1 else "questions ouvertes", types[t])
                for t in Question.Type.values if types.get(t)],
            "duree_estimee": max(1, round(nb_questions * 1.5)),
            "ia_disponible": gemini_configure(), "indices_max": INDICES_MAX,
        })
        if est_etudiant(user):
            # L'étudiant ne voit ni les questions ni les réponses avant de commencer
            tentatives = list(quiz.tentatives.filter(etudiant=user).order_by("numero"))
            en_cours = next((t for t in tentatives if t.statut == TentativeQuiz.Statut.EN_COURS), None)
            scores = [t.pourcentage for t in tentatives if t.est_soumise and t.pourcentage is not None]
            context.update({
                "mes_tentatives": tentatives, "tentative_en_cours": en_cours,
                "tentatives_restantes": max(0, quiz.tentatives_max - len(tentatives)),
                "meilleur": max(scores) if scores else None,
            })
        else:
            context.update({
                "questions": quiz.questions.prefetch_related("choix"),
                "erreurs_publication": quiz.erreurs_publication() if gestion and quiz.statut == Quiz.Statut.BROUILLON else [],
                "nb_tentatives": quiz.tentatives.exclude(statut=TentativeQuiz.Statut.EN_COURS).count(),
                "versions_suivantes": quiz.versions_suivantes.all(),
                "etat_audit": TraitementIA.etat(quiz.audit_ia),
                "audit": quiz.audit_ia.get("resultat") if quiz.audit_ia else None,
                "audit_par_question": {q["ordre"]: q for q in (quiz.audit_ia.get("resultat") or {}).get("questions", [])}
                if quiz.audit_ia else {},
            })
        return context


class EtatGenerationView(EnseignantQuizMixin, View):
    """Avancement de la génération IA, interrogé régulièrement par la page détail."""

    def get(self, request, pk):
        quiz = self.get_quiz()
        return JsonResponse({
            "statut": quiz.statut_generation,
            "libelle": quiz.get_statut_generation_display(),
            "message": quiz.message_generation,
            "fini": quiz.statut_generation != Quiz.Generation.EN_COURS,
        })


# ---------- Gestion des quiz (enseignant) ----------

class CreerQuizView(EnseignantQuizMixin, LayoutMixin, TemplateView):
    template_name = "quizzes/form_quiz.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("form", QuizForm(user=self.request.user, initial={"cours": self.request.GET.get("cours")}))
        context["titre"] = "Créer un quiz"
        return context

    def post(self, request):
        enseignant = self.get_enseignant()
        form = QuizForm(request.POST, user=request.user)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form), status=400)
        quiz = form.save(commit=False)
        quiz.enseignant = enseignant
        quiz.origine = Quiz.Origine.MANUEL
        quiz.save()
        messages.success(request, "Quiz créé en brouillon. Ajoutez maintenant ses questions.")
        return redirect("detail-quiz", pk=quiz.pk)


class ModifierQuizView(EnseignantQuizMixin, LayoutMixin, TemplateView):
    template_name = "quizzes/form_quiz.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and est_enseignant(request.user):
            self.quiz = self.get_quiz()
            if not self.quiz.est_modifiable:
                messages.error(request, message_non_modifiable(self.quiz))
                return redirect("detail-quiz", pk=self.quiz.pk)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("form", QuizForm(instance=self.quiz, user=self.request.user))
        context.update({"titre": "Modifier le quiz", "quiz": self.quiz})
        return context

    def post(self, request, pk):
        form = QuizForm(request.POST, instance=self.quiz, user=request.user)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form), status=400)
        quiz = form.save(commit=False)
        quiz.titre_cours = quiz.cours.titre
        quiz.save()
        messages.success(request, "Quiz modifié.")
        return redirect("detail-quiz", pk=quiz.pk)


def message_non_modifiable(quiz):
    if quiz.generation_en_cours:
        return "La génération par l'IA est en cours : attendez qu'elle se termine."
    if quiz.a_des_tentatives:
        return ("Ce quiz a déjà été passé par des étudiants : il n'est plus modifiable pour préserver leurs "
                "résultats. Créez une nouvelle version.")
    if quiz.statut == Quiz.Statut.PUBLIE:
        return "Ce quiz est publié : repassez-le en brouillon pour le modifier."
    return "Ce quiz archivé n'est plus modifiable."


class SupprimerQuizView(EnseignantQuizMixin, View):
    def post(self, request, pk):
        quiz = self.get_quiz()
        if not quiz.est_supprimable:
            messages.error(request, "Seul un brouillon jamais passé peut être supprimé. "
                                    "Archivez ce quiz : ses résultats seront conservés.")
            return redirect("detail-quiz", pk=quiz.pk)
        titre = quiz.titre
        quiz.delete()
        messages.success(request, f"Le quiz « {titre} » a été supprimé.")
        return redirect("liste-quiz")


class PublierQuizView(EnseignantQuizMixin, View):
    def post(self, request, pk):
        quiz = self.get_quiz()
        if quiz.statut != Quiz.Statut.BROUILLON:
            messages.error(request, "Seul un brouillon peut être publié.")
            return redirect("detail-quiz", pk=quiz.pk)
        erreurs = quiz.erreurs_publication()
        if erreurs:
            messages.error(request, "Publication impossible : " + " ".join(erreurs))
            return redirect("detail-quiz", pk=quiz.pk)
        publier(quiz)
        messages.success(request, "Quiz publié : les étudiants peuvent maintenant le passer.")
        if not quiz_publics().filter(pk=quiz.pk).exists():
            messages.warning(request, "Le cours associé n'est pas visible des étudiants (brouillon, masqué ou "
                                      "archivé) : le quiz leur sera accessible quand le cours le sera.")
        return redirect("detail-quiz", pk=quiz.pk)


class DepublierQuizView(EnseignantQuizMixin, View):
    def post(self, request, pk):
        quiz = self.get_quiz()
        if quiz.statut != Quiz.Statut.PUBLIE or quiz.a_des_tentatives:
            messages.error(request, "Un quiz déjà passé ne peut pas revenir en brouillon : archivez-le ou "
                                    "créez une nouvelle version.")
        else:
            quiz.statut = Quiz.Statut.BROUILLON
            quiz.save(update_fields=["statut", "date_modification"])
            messages.success(request, "Le quiz est repassé en brouillon et n'est plus visible des étudiants.")
        return redirect("detail-quiz", pk=quiz.pk)


class ArchiverQuizView(EnseignantQuizMixin, View):
    def post(self, request, pk):
        quiz = self.get_quiz()
        if quiz.statut == Quiz.Statut.ARCHIVE or quiz.generation_en_cours:
            messages.error(request, "Ce quiz ne peut pas être archivé maintenant.")
        else:
            quiz.statut = Quiz.Statut.ARCHIVE
            quiz.save(update_fields=["statut", "date_modification"])
            messages.success(request, "Quiz archivé : il n'est plus proposé aux étudiants, les résultats sont conservés.")
        return redirect("detail-quiz", pk=quiz.pk)


class NouvelleVersionView(EnseignantQuizMixin, View):
    def post(self, request, pk):
        quiz = self.get_quiz()
        if quiz.generation_en_cours:
            messages.error(request, "Attendez la fin de la génération.")
            return redirect("detail-quiz", pk=quiz.pk)
        copie = nouvelle_version(quiz)
        messages.success(request, f"Version {copie.version} créée en brouillon. La version précédente et ses "
                                  "résultats restent intacts ; elle sera archivée quand vous publierez celle-ci.")
        return redirect("detail-quiz", pk=copie.pk)


# ---------- Questions et choix ----------

class QuestionMixin(EnseignantQuizMixin, LayoutMixin):
    template_name = "quizzes/form_question.html"

    def verifier_modifiable(self, quiz):
        if not quiz.est_modifiable:
            messages.error(self.request, message_non_modifiable(quiz))
            return redirect("detail-quiz", pk=quiz.pk)
        return None

    def rendre(self, quiz, form, formset, question=None, status=200):
        context = self.get_context_data(quiz=quiz, form=form, formset=formset, question=question,
                                        types=Question.Type)
        return self.render_to_response(context, status=status)

    def enregistrer(self, quiz, form, formset_classe, question=None, repartir_de_zero=False):
        """Valide la question et, pour un QCM, ses choix ; enregistre tout ou rien.

        `repartir_de_zero` : la question devient un QCM, ses anciens choix (Vrai/Faux) sont remplacés.
        """
        formset = formset_classe(self.request.POST, instance=question or Question(quiz=quiz), prefix="choix")
        type_question = form.data.get("type_question")
        valide = form.is_valid() and (type_question != Question.Type.QCM or formset.is_valid())
        if not valide:
            return None, formset
        with transaction.atomic():
            question = form.save(commit=False)
            question.quiz = quiz
            if not question.pk:
                question.ordre = quiz.prochain_ordre()
            question.save()
            if question.type_question == Question.Type.QCM:
                if repartir_de_zero:
                    question.choix.all().delete()
                formset.instance = question
                formset.save()
                for ordre, choix in enumerate(question.choix.all(), start=1):
                    Choix.objects.filter(pk=choix.pk).update(ordre=ordre)
            elif question.type_question == Question.Type.VRAI_FAUX:
                question.choix.all().delete()
                vrai = form.cleaned_data["bonne_reponse_vf"] == "vrai"
                Choix.objects.bulk_create([
                    Choix(question=question, texte="Vrai", est_correct=vrai, ordre=1),
                    Choix(question=question, texte="Faux", est_correct=not vrai, ordre=2),
                ])
            else:
                question.choix.all().delete()
        return question, formset


class AjouterQuestionView(QuestionMixin, TemplateView):
    def get(self, request, pk):
        quiz = self.get_quiz()
        return self.verifier_modifiable(quiz) or self.rendre(
            quiz, QuestionForm(initial={"type_question": request.GET.get("type", Question.Type.QCM)}),
            choix_formset()(instance=Question(quiz=quiz), prefix="choix"),
        )

    def post(self, request, pk):
        quiz = self.get_quiz()
        refus = self.verifier_modifiable(quiz)
        if refus:
            return refus
        if quiz.questions.count() >= QUESTIONS_MAX:
            messages.error(request, f"Un quiz ne peut pas dépasser {QUESTIONS_MAX} questions.")
            return redirect("detail-quiz", pk=quiz.pk)
        form = QuestionForm(request.POST, instance=Question(quiz=quiz))
        question, formset = self.enregistrer(quiz, form, choix_formset())
        if question is None:
            return self.rendre(quiz, form, formset, status=400)
        messages.success(request, f"Question {question.ordre} ajoutée.")
        if "ajouter_autre" in request.POST:
            return redirect("ajouter-question", pk=quiz.pk)
        return redirect(reverse("detail-quiz", args=[quiz.pk]) + f"#question-{question.pk}")


class ModifierQuestionView(QuestionMixin, TemplateView):
    def get(self, request, pk):
        question = self.get_question()
        nb = question.choix.count() if question.type_question == Question.Type.QCM else 0
        return self.verifier_modifiable(question.quiz) or self.rendre(
            question.quiz, QuestionForm(instance=question),
            choix_formset(nb)(instance=question if nb else Question(quiz=question.quiz), prefix="choix"),
            question,
        )

    def post(self, request, pk):
        question = self.get_question()
        quiz = question.quiz
        refus = self.verifier_modifiable(quiz)
        if refus:
            return refus
        # Une question qui devient un QCM repart de choix vierges (le formulaire n'affichait pas les Vrai/Faux)
        devient_qcm = question.type_question != Question.Type.QCM
        nb_choix = 0 if devient_qcm else question.choix.count()
        form = QuestionForm(request.POST, instance=question)
        enregistree, formset = self.enregistrer(quiz, form, choix_formset(nb_choix), question,
                                                repartir_de_zero=devient_qcm)
        if enregistree is None:
            return self.rendre(quiz, form, formset, question, status=400)
        messages.success(request, f"Question {enregistree.ordre} modifiée.")
        return redirect(reverse("detail-quiz", args=[quiz.pk]) + f"#question-{enregistree.pk}")


class SupprimerQuestionView(EnseignantQuizMixin, View):
    def post(self, request, pk):
        question = self.get_question()
        quiz = question.quiz
        if not quiz.est_modifiable:
            messages.error(request, message_non_modifiable(quiz))
            return redirect("detail-quiz", pk=quiz.pk)
        with transaction.atomic():
            ordre = question.ordre
            question.delete()
            # Renumérotation : les questions suivantes remontent d'un rang
            for suivante in quiz.questions.filter(ordre__gt=ordre).order_by("ordre"):
                Question.objects.filter(pk=suivante.pk).update(ordre=suivante.ordre - 1)
        messages.success(request, "Question supprimée.")
        return redirect("detail-quiz", pk=quiz.pk)


class DeplacerQuestionView(EnseignantQuizMixin, View):
    def post(self, request, pk):
        question = self.get_question()
        quiz = question.quiz
        if not quiz.est_modifiable:
            messages.error(request, message_non_modifiable(quiz))
            return redirect("detail-quiz", pk=quiz.pk)
        vers_le_haut = request.POST.get("sens") == "haut"
        voisins = quiz.questions.filter(ordre__lt=question.ordre).order_by("-ordre") if vers_le_haut \
            else quiz.questions.filter(ordre__gt=question.ordre).order_by("ordre")
        voisin = voisins.first()
        if voisin:
            with transaction.atomic():
                # Échange en trois temps pour respecter la contrainte d'unicité (quiz, ordre)
                Question.objects.filter(pk=question.pk).update(ordre=0)
                Question.objects.filter(pk=voisin.pk).update(ordre=question.ordre)
                Question.objects.filter(pk=question.pk).update(ordre=voisin.ordre)
        return redirect(reverse("detail-quiz", args=[quiz.pk]) + f"#question-{question.pk}")


# ---------- Génération IA ----------

def refus_generation(user):
    """Raison d'un refus de lancer une génération (configuration ou limite de débit), sinon None."""
    if not gemini_configure():
        return "L'IA n'est pas configurée sur ce serveur (clé Gemini absente). Créez le quiz manuellement."
    if Quiz.objects.filter(enseignant__utilisateur=user, statut_generation=Quiz.Generation.EN_COURS).exists():
        return "Une génération est déjà en cours. Attendez qu'elle se termine."
    limite = reglage("QUIZ_GENERATIONS_PAR_HEURE")
    recentes = AppelIA.objects.filter(type_appel=AppelIA.Type.GENERATION, utilisateur=user,
                                      date__gte=timezone.now() - timedelta(hours=1)).count()
    if recentes >= limite:
        return f"Limite de {limite} générations par heure atteinte. Réessayez plus tard."
    return None


def lancer_generation(quiz):
    def echec():
        Quiz.objects.filter(pk=quiz.pk).update(
            statut_generation=Quiz.Generation.ERREUR,
            message_generation="Le service de traitement est injoignable (Celery / Redis). Réessayez plus tard.",
        )

    lancer_tache(generer_quiz_ia, quiz.pk, echec)


class GenererQuizView(EnseignantQuizMixin, LayoutMixin, TemplateView):
    template_name = "quizzes/generer.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = kwargs.get("form") or GenerationQuizForm(user=self.request.user,
                                                        initial={"cours": self.request.GET.get("cours")})
        libelles = dict(SOURCES)
        context.update({
            "form": form,
            "ia_disponible": gemini_configure(),
            # Textes disponibles pour chaque cours (aide affichée à côté du choix)
            "sources_par_cours": [
                (c, [libelles[s] for s in sources_disponibles(c)]) for c in form.fields["cours"].queryset
            ],
        })
        return context

    def post(self, request):
        enseignant = self.get_enseignant()
        form = GenerationQuizForm(request.POST, user=request.user)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form), status=400)
        refus = refus_generation(request.user)
        if refus:
            messages.error(request, refus)
            return self.render_to_response(self.get_context_data(form=form), status=429 if "Limite" in refus else 400)
        donnees = form.cleaned_data
        cours = donnees["cours"]
        with transaction.atomic():
            quiz = Quiz.objects.create(
                titre=donnees["titre"] or f"Quiz – {cours.titre}"[:200], cours=cours, enseignant=enseignant,
                difficulte=donnees["difficulte"], origine=Quiz.Origine.IA,
                statut_generation=Quiz.Generation.EN_COURS, message_generation="",
                parametres_generation={"source": donnees["source"], "nb_questions": donnees["nb_questions"],
                                       "types": donnees["types"]},
            )
            lancer_generation(quiz)
        messages.info(request, "Génération lancée. Le quiz restera en brouillon : relisez-le avant de le publier.")
        return redirect("detail-quiz", pk=quiz.pk)


class RelancerGenerationView(EnseignantQuizMixin, View):
    def post(self, request, pk):
        quiz = self.get_quiz()
        if quiz.statut_generation != Quiz.Generation.ERREUR or quiz.questions.exists() \
                or quiz.statut != Quiz.Statut.BROUILLON:
            messages.error(request, "Ce quiz ne peut pas être régénéré.")
            return redirect("detail-quiz", pk=quiz.pk)
        refus = refus_generation(request.user)
        if refus:
            messages.error(request, refus)
            return redirect("detail-quiz", pk=quiz.pk)
        with transaction.atomic():
            quiz.statut_generation = Quiz.Generation.EN_COURS
            quiz.message_generation = ""
            quiz.save(update_fields=["statut_generation", "message_generation"])
            lancer_generation(quiz)
        messages.info(request, "Génération relancée.")
        return redirect("detail-quiz", pk=quiz.pk)


# ---------- Passage du quiz (étudiant) ----------

def questions_sans_corrige(quiz, graine=0):
    """Questions et choix envoyés au navigateur pendant une tentative : jamais l'indicateur de bonne réponse.

    L'ordre des choix QCM est mélangé avec la graine de la tentative (Vrai/Faux garde son ordre naturel).
    """
    questions = []
    for q in quiz.questions.prefetch_related("choix"):
        choix = [{"pk": c.pk, "texte": c.texte} for c in q.choix.all()]
        if q.type_question == Question.Type.QCM:
            choix = melanger_choix(choix, graine, q.pk)
        questions.append({"pk": q.pk, "ordre": q.ordre, "type": q.type_question, "enonce": q.enonce,
                          "points": q.points, "choix": choix})
    return questions


class CommencerQuizView(EtudiantQuizMixin, View):
    def post(self, request, pk):
        quiz = get_object_or_404(quiz_publics(), pk=pk)
        tentative = commencer(quiz, request.user)
        if tentative is None:
            messages.error(request, "Vous avez utilisé toutes vos tentatives pour ce quiz.")
            return redirect("detail-quiz", pk=quiz.pk)
        return redirect("tentative", pk=tentative.pk)


class TentativeView(LayoutMixin, TemplateView):
    """Tentative en cours : formulaire (étudiant). Tentative soumise : résultat et corrections."""

    def get_template_names(self):
        return ["quizzes/passer.html" if self.tentative.statut == TentativeQuiz.Statut.EN_COURS
                else "quizzes/resultat.html"]

    def get(self, request, pk):
        self.tentative = verifier_tentative(request.user, pk)
        if self.tentative.statut == TentativeQuiz.Statut.EN_COURS and self.tentative.etudiant_id != request.user.pk:
            messages.info(request, "Cette tentative est encore en cours : le résultat sera visible après sa soumission.")
            return redirect("resultats-quiz", pk=self.tentative.quiz_id)
        return self.render_to_response(self.get_context_data())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tentative = self.tentative
        quiz = tentative.quiz
        user = self.request.user
        context.update({"tentative": tentative, "quiz": quiz, "role": role(user)})
        if tentative.statut == TentativeQuiz.Statut.EN_COURS:
            questions = questions_sans_corrige(quiz, tentative.graine)
            valeurs, erreurs = kwargs.get("valeurs", {}), kwargs.get("erreurs", {})
            indices = tentative.indices or {}
            for question in questions:
                # Après une soumission refusée, on réaffiche ce que l'étudiant avait saisi
                question["valeur"] = valeurs.get(f"question_{question['pk']}", "")
                question["erreur"] = erreurs.get(question["pk"])
                question["indice"] = indices.get(str(question["pk"]), "")
            context.update({
                "questions": questions,
                "accessible": quiz_publics().filter(pk=quiz.pk).exists(),
                "nb_erreurs": len(erreurs),
                "ia_disponible": gemini_configure(),
                "indices_max": INDICES_MAX,
                "coef_indice_pct": int(COEF_INDICE * 100),
            })
        else:
            gestion = peut_gerer(user, quiz)
            proprietaire = tentative.etudiant_id == user.pk
            montrer_corrige = gestion or est_admin(user) or quiz.afficher_corrige
            reponses = list(tentative.reponses.select_related("question", "choix", "corrige_par")
                            .prefetch_related("question__choix"))
            par_type = {}
            for reponse in reponses:
                question = reponse.question
                if gestion and question.type_question == Question.Type.OUVERTE:
                    reponse.form_revision = RevisionForm(maximum=question.points, prefix=f"r{reponse.pk}",
                                                         initial={"points": reponse.points_obtenus})
                # « Pourquoi ma réponse est fausse ? » : QCM / Vrai-Faux ratés, corrigé visible, IA disponible
                reponse.peut_expliquer = (proprietaire and montrer_corrige and question.a_des_choix
                                          and reponse.choix_id and reponse.est_correcte is False)
                cumul = par_type.setdefault(question.get_type_question_display(), [0, 0])
                cumul[0] += float(reponse.points_obtenus or 0)
                cumul[1] += float(question.points)
            context.update({
                "reponses": reponses,
                "nb_en_attente": sum(1 for r in reponses if r.en_attente),
                "nb_erreurs": sum(1 for r in reponses if r.statut_correction == ReponseEtudiant.Correction.ERREUR),
                "nb_justes": sum(1 for r in reponses if r.est_correcte),
                "peut_gerer": gestion,
                "proprietaire": proprietaire,
                # Corrigé : toujours pour l'enseignant et l'admin, selon le réglage du quiz pour l'étudiant
                "montrer_corrige": montrer_corrige,
                "ia_disponible": gemini_configure(),
                "peut_certificat": proprietaire and certificats.peut_obtenir(tentative),
                "seuil_certificat": SEUIL_CERTIFICAT,
                "signaux": tentative.signaux_integrite if (gestion or est_admin(user)) else [],
                "etat_bilan": TraitementIA.etat(tentative.bilan_ia),
                "bilan": (tentative.bilan_ia or {}).get("resultat"),
                "message_bilan": (tentative.bilan_ia or {}).get("message", ""),
                "par_type": [{"type": t, "obtenu": round(v[0], 2), "max": round(v[1], 2),
                              "pct": round(100 * v[0] / v[1]) if v[1] else 0} for t, v in par_type.items()],
            })
        return context

    def post(self, request, pk):
        """Soumission : réservée à l'étudiant propriétaire ; le score est calculé côté serveur."""
        if not est_etudiant(request.user):
            raise PermissionDenied
        tentative = get_object_or_404(TentativeQuiz.objects.select_related("quiz"), pk=pk, etudiant=request.user)
        if tentative.statut == TentativeQuiz.Statut.EN_COURS and not quiz_publics().filter(pk=tentative.quiz_id).exists():
            messages.error(request, "Ce quiz n'est plus disponible : la tentative ne peut pas être soumise.")
            return redirect("liste-quiz")
        try:
            tentative, a_corriger = soumettre(tentative.pk, request.user, request.POST)
        except DejaSoumise:
            messages.info(request, "Cette tentative a déjà été soumise : vos réponses n'ont pas été modifiées.")
            return redirect("tentative", pk=pk)
        except SoumissionInvalide as erreur:
            self.tentative = tentative
            messages.error(request, "Certaines réponses sont invalides. Corrigez-les puis soumettez à nouveau.")
            context = self.get_context_data(valeurs=request.POST, erreurs=erreur.args[0])
            return self.render_to_response(context, status=400)

        ajoutees = alimenter_carnet(tentative)
        if ajoutees:
            messages.info(request, f"{ajoutees} question(s) ratée(s) ajoutée(s) à ton carnet de révision : "
                                   "elles reviendront au bon moment pour que tu les maîtrises.")
        if a_corriger and not gemini_configure():
            # Pas d'IA : la réponse est conservée et attend l'enseignant (aucune note attribuée par défaut)
            tentative.reponses.filter(statut_correction=ReponseEtudiant.Correction.EN_ATTENTE).update(
                statut_correction=ReponseEtudiant.Correction.ERREUR,
                message_erreur="Correction automatique indisponible : l'enseignant corrigera cette réponse.",
            )
            messages.success(request, f"Réponses enregistrées. {a_corriger} réponse(s) ouverte(s) seront corrigées "
                                      "par l'enseignant : votre score est provisoire.")
        elif a_corriger:
            def echec():
                logger.warning("Correction de la tentative %s non envoyée : elle reste en attente", tentative.pk)

            lancer_tache(corriger_tentative, tentative.pk, echec)
            messages.success(request, f"Réponses enregistrées. {a_corriger} réponse(s) ouverte(s) en cours de "
                                      "correction par l'IA : votre score est provisoire.")
        else:
            messages.success(request, "Réponses enregistrées et corrigées.")
            declencher_bilan(tentative.pk)
        return redirect("tentative", pk=tentative.pk)


class EtatTentativeView(View):
    """État des corrections (interrogé par la page résultat tant que des réponses sont en attente)."""

    def get(self, request, pk):
        tentative = verifier_tentative(request.user, pk)
        en_attente = tentative.reponses.filter(points_obtenus__isnull=True)
        return JsonResponse({
            "statut": tentative.statut,
            "libelle": tentative.get_statut_display(),
            "score": str(tentative.score) if tentative.score is not None else None,
            "score_max": str(tentative.score_max) if tentative.score_max is not None else None,
            "en_attente": en_attente.count(),
            "en_cours_ia": en_attente.filter(statut_correction__in=[ReponseEtudiant.Correction.EN_ATTENTE,
                                                                    ReponseEtudiant.Correction.EN_COURS]).count(),
            "fini": tentative.statut == TentativeQuiz.Statut.CORRIGEE,
            "bilan": TraitementIA.etat(tentative.bilan_ia),
        })


class MesTentativesView(EtudiantQuizMixin, LayoutMixin, ListView):
    template_name = "quizzes/mes_tentatives.html"
    context_object_name = "tentatives"
    paginate_by = 20

    def get_queryset(self):
        return (TentativeQuiz.objects.filter(etudiant=self.request.user)
                .select_related("quiz__cours").order_by("-date_debut"))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        soumises = [t for t in TentativeQuiz.objects.filter(etudiant=self.request.user, date_soumission__isnull=False)
                    .select_related("quiz").order_by("date_soumission") if t.pourcentage is not None]
        pourcentages = [t.pourcentage for t in soumises]
        moitie = len(pourcentages) // 2
        context.update({
            "stats": {
                "nb": len(soumises), "quiz": len({t.quiz_id for t in soumises}),
                "moyenne": round(sum(pourcentages) / len(pourcentages)) if pourcentages else None,
                "meilleur": max(pourcentages, default=None),
                # Tendance : moyenne de la seconde moitié − moyenne de la première moitié
                "tendance": round(sum(pourcentages[moitie:]) / len(pourcentages[moitie:])
                                  - sum(pourcentages[:moitie]) / moitie) if moitie else None,
                "indices": sum(len(t.indices or {}) for t in soumises),
            },
            "graphique": {"dates": [timezone.localtime(t.date_soumission).strftime("%d/%m") for t in soumises],
                          "scores": pourcentages, "quiz": [t.quiz.titre[:40] for t in soumises]},
        })
        return context


# ---------- Résultats et révision (enseignant) ----------

def quiz_pour_resultats(user, pk):
    if est_admin(user):
        return get_object_or_404(Quiz.objects.select_related("cours"), pk=pk)
    if est_enseignant(user):
        return get_object_or_404(Quiz.objects.select_related("cours"), pk=pk, enseignant__utilisateur=user)
    raise PermissionDenied


class ResultatsQuizView(LayoutMixin, TemplateView):
    template_name = "quizzes/resultats.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        quiz = quiz_pour_resultats(user, self.kwargs["pk"])
        soumises = quiz.tentatives.exclude(statut=TentativeQuiz.Statut.EN_COURS).select_related("etudiant")
        analyse = analyser_quiz(quiz)
        context.update({
            "quiz": quiz, "role": role(user), "peut_gerer": peut_gerer(user, quiz),
            "tentatives": soumises.order_by("-date_soumission"),
            "nb_en_cours": quiz.tentatives.filter(statut=TentativeQuiz.Statut.EN_COURS).count(),
            "nb_en_attente": soumises.filter(statut=TentativeQuiz.Statut.SOUMISE).count(),
            "points_total": quiz.points_total(),
            "analyse": analyse,
            "graphique": {
                "distribution": analyse["distribution"],
                "items": [{"q": f"Q{i['question'].ordre}", "p": i["p_pct"], "d": i["d"]} for i in analyse["items"]],
            },
            "nb_erreurs_ia": ReponseEtudiant.objects.filter(
                tentative__quiz=quiz, statut_correction=ReponseEtudiant.Correction.ERREUR).count(),
            "ia_disponible": gemini_configure(),
            "etat_analyse": TraitementIA.etat(quiz.analyse_ia),
            "analyse_ia": (quiz.analyse_ia or {}).get("resultat"),
            "message_analyse": (quiz.analyse_ia or {}).get("message", ""),
        })
        return context


class RelancerCorrectionView(EnseignantQuizMixin, View):
    """Remet en file les corrections IA échouées (ou bloquées) d'une tentative d'un de ses quiz."""

    def post(self, request, pk):
        tentative = get_object_or_404(TentativeQuiz, pk=pk, quiz__enseignant__utilisateur=request.user)
        if not gemini_configure():
            messages.error(request, "L'IA n'est pas configurée : notez ces réponses vous-même.")
            return redirect("tentative", pk=tentative.pk)
        bloquees = Q(statut_correction=ReponseEtudiant.Correction.ERREUR)
        if tentative.date_soumission and tentative.date_soumission < timezone.now() - timedelta(minutes=15):
            bloquees |= Q(statut_correction=ReponseEtudiant.Correction.EN_COURS)
        nb = tentative.reponses.filter(bloquees, points_obtenus__isnull=True).update(
            statut_correction=ReponseEtudiant.Correction.EN_ATTENTE, message_erreur=""
        )
        if nb or tentative.reponses.filter(statut_correction=ReponseEtudiant.Correction.EN_ATTENTE).exists():
            lancer_tache(corriger_tentative, tentative.pk, lambda: None)
            messages.info(request, "Correction IA relancée.")
        else:
            messages.info(request, "Aucune correction à relancer.")
        return redirect("tentative", pk=tentative.pk)


class ReviserReponseView(EnseignantQuizMixin, View):
    """L'enseignant fixe (ou corrige) la note d'une réponse ouverte ; la note IA reste tracée à part."""

    def post(self, request, pk):
        reponse = get_object_or_404(
            ReponseEtudiant.objects.select_related("question", "tentative"),
            pk=pk, tentative__quiz__enseignant__utilisateur=request.user,
            question__type_question=Question.Type.OUVERTE,
        )
        form = RevisionForm(request.POST, maximum=reponse.question.points, prefix=f"r{reponse.pk}")
        if not form.is_valid():
            messages.error(request, "Note invalide : " + " ".join(e for errs in form.errors.values() for e in errs))
            return redirect(reverse("tentative", args=[reponse.tentative_id]) + f"#reponse-{reponse.pk}")
        with transaction.atomic():
            tentative = TentativeQuiz.objects.select_for_update().get(pk=reponse.tentative_id)
            points = form.cleaned_data["points"]
            reponse.points_obtenus = points
            reponse.est_correcte = True if points == reponse.question.points else (False if points == 0 else None)
            reponse.statut_correction = ReponseEtudiant.Correction.ENSEIGNANT
            reponse.corrige_par = request.user
            reponse.date_correction = timezone.now()
            reponse.message_erreur = ""
            if form.cleaned_data["commentaire"].strip():
                reponse.explication_correction = form.cleaned_data["commentaire"].strip()
            reponse.save()
            tentative.recalculer_score()
        declencher_bilan(tentative.pk)
        messages.success(request, "Note enregistrée.")
        return redirect(reverse("tentative", args=[reponse.tentative_id]) + f"#reponse-{reponse.pk}")


# ---------- IA au service de l'étudiant ----------

def veut_json(request):
    return request.headers.get("x-requested-with") == "fetch" or "application/json" in request.headers.get("accept", "")


def repondre(request, donnees, status, url):
    """Réponse JSON pour les appels JavaScript, sinon message + redirection (fonctionne sans JavaScript)."""
    if veut_json(request):
        return JsonResponse(donnees, status=status)
    if "erreur" in donnees:
        messages.error(request, donnees["erreur"])
    return redirect(url)


class IndiceView(EtudiantQuizMixin, View):
    """Indice IA pendant une tentative : INDICES_MAX par tentative, question aidée = COEF_INDICE des points."""

    def post(self, request, pk, question_pk):
        tentative = get_object_or_404(TentativeQuiz.objects.select_related("quiz"), pk=pk, etudiant=request.user)
        url = reverse("tentative", args=[tentative.pk]) + f"#q{question_pk}"
        if tentative.statut != TentativeQuiz.Statut.EN_COURS or not quiz_publics().filter(pk=tentative.quiz_id).exists():
            return repondre(request, {"erreur": "Cette tentative n'est plus modifiable."}, 400, url)
        question = get_object_or_404(Question.objects.prefetch_related("choix"), pk=question_pk, quiz=tentative.quiz)
        cle = str(question.pk)
        if cle in (tentative.indices or {}):
            return repondre(request, {"indice": tentative.indices[cle], "restants": tentative.indices_restants}, 200, url)
        if not gemini_configure():
            return repondre(request, {"erreur": "L'assistant IA n'est pas disponible sur ce serveur."}, 503, url)
        if tentative.indices_restants == 0:
            return repondre(request, {"erreur": f"Vous avez utilisé vos {INDICES_MAX} indices."}, 429, url)
        try:
            indice, meta = generer_indice(question)
        except ErreurIA as erreur:
            journaliser(AppelIA.Type.INDICE, tentative.quiz, erreur=erreur)
            return repondre(request, {"erreur": erreur.message}, 502, url)
        with transaction.atomic():
            # Revérification sous verrou : deux clics simultanés ne dépassent pas la limite
            tentative = TentativeQuiz.objects.select_for_update().get(pk=tentative.pk)
            indices = dict(tentative.indices or {})
            if cle not in indices:
                if len(indices) >= INDICES_MAX or tentative.statut != TentativeQuiz.Statut.EN_COURS:
                    return repondre(request, {"erreur": "Plus aucun indice disponible."}, 429, url)
                indices[cle] = indice
                tentative.indices = indices
                tentative.save(update_fields=["indices"])
        journaliser(AppelIA.Type.INDICE, tentative.quiz, meta=meta)
        return repondre(request, {"indice": indices[cle], "restants": tentative.indices_restants}, 200, url)


class ExpliquerErreurView(EtudiantQuizMixin, View):
    """« Pourquoi ma réponse est fausse ? » : un seul appel IA par réponse, puis mise en cache."""

    def post(self, request, pk):
        reponse = get_object_or_404(
            ReponseEtudiant.objects.select_related("question", "choix", "tentative__quiz")
            .prefetch_related("question__choix"),
            pk=pk, tentative__etudiant=request.user,
        )
        url = reverse("tentative", args=[reponse.tentative_id]) + f"#reponse-{reponse.pk}"
        quiz = reponse.tentative.quiz
        if not (reponse.tentative.est_soumise and quiz.afficher_corrige and reponse.question.a_des_choix
                and reponse.choix_id and reponse.est_correcte is False):
            return repondre(request, {"erreur": "Aucune explication disponible pour cette réponse."}, 400, url)
        if reponse.explication_ia:
            return repondre(request, {"explication": reponse.explication_ia}, 200, url)
        if not gemini_configure():
            return repondre(request, {"erreur": "L'assistant IA n'est pas disponible sur ce serveur."}, 503, url)
        try:
            explication, meta = expliquer_erreur(reponse)
        except ErreurIA as erreur:
            journaliser(AppelIA.Type.EXPLICATION, quiz, erreur=erreur)
            return repondre(request, {"erreur": erreur.message}, 502, url)
        ReponseEtudiant.objects.filter(pk=reponse.pk, explication_ia="").update(explication_ia=explication)
        journaliser(AppelIA.Type.EXPLICATION, quiz, meta=meta)
        reponse.refresh_from_db(fields=["explication_ia"])
        return repondre(request, {"explication": reponse.explication_ia}, 200, url)


class RelancerBilanView(View):
    """Nouvel essai du bilan IA après un échec (étudiant concerné ou enseignant du quiz)."""

    def post(self, request, pk):
        tentative = verifier_tentative(request.user, pk)
        if not (tentative.etudiant_id == request.user.pk or peut_gerer(request.user, tentative.quiz)):
            raise PermissionDenied
        if TraitementIA.etat(tentative.bilan_ia) == TraitementIA.ERREUR and declencher_bilan(tentative.pk, relancer=True):
            messages.info(request, "Le bilan personnalisé est en cours de génération.")
        else:
            messages.error(request, "Le bilan ne peut pas être relancé pour le moment.")
        return redirect(reverse("tentative", args=[tentative.pk]) + "#bilan")


# ---------- IA au service de l'enseignant ----------

def refus_ia_enseignant(user):
    if not gemini_configure():
        return "L'IA n'est pas configurée sur ce serveur (clé Gemini absente)."
    limite = reglage("QUIZ_GENERATIONS_PAR_HEURE")
    recents = AppelIA.objects.filter(utilisateur=user, type_appel__in=[AppelIA.Type.AUDIT, AppelIA.Type.ANALYSE, AppelIA.Type.IMPORT],
                                     date__gte=timezone.now() - timedelta(hours=1)).count()
    if recents >= limite:
        return f"Limite de {limite} analyses IA par heure atteinte. Réessayez plus tard."
    return None


class AuditQuizView(EnseignantQuizMixin, View):
    def post(self, request, pk):
        quiz = self.get_quiz()
        url = reverse("detail-quiz", args=[quiz.pk]) + "#audit"
        refus = refus_ia_enseignant(request.user) or (None if quiz.questions.exists() else "Ajoutez d'abord des questions.")
        if refus:
            messages.error(request, refus)
        elif reserver(Quiz, quiz.pk, "audit_ia", relancer=True):
            lancer_apres_validation(auditer_quiz_ia, quiz.pk, Quiz, "audit_ia")
            messages.info(request, "Audit qualité lancé : l'IA relit chaque question.")
        else:
            messages.info(request, "Un audit est déjà en cours.")
        return redirect(url)


class AnalyseClasseView(EnseignantQuizMixin, View):
    def post(self, request, pk):
        quiz = self.get_quiz()
        url = reverse("resultats-quiz", args=[quiz.pk]) + "#analyse-ia"
        a_des_resultats = quiz.tentatives.exclude(statut=TentativeQuiz.Statut.EN_COURS).exists()
        refus = refus_ia_enseignant(request.user) or (None if a_des_resultats else "Aucune tentative soumise à analyser.")
        if refus:
            messages.error(request, refus)
        elif reserver(Quiz, quiz.pk, "analyse_ia", relancer=True):
            lancer_apres_validation(analyser_classe_ia, quiz.pk, Quiz, "analyse_ia")
            messages.info(request, "Analyse de la classe lancée (données anonymisées).")
        else:
            messages.info(request, "Une analyse est déjà en cours.")
        return redirect(url)


class EtatIAQuizView(EnseignantQuizMixin, View):
    def get(self, request, pk):
        quiz = self.get_quiz()
        return JsonResponse({"audit": TraitementIA.etat(quiz.audit_ia), "analyse": TraitementIA.etat(quiz.analyse_ia)})


# ---------- Pilotage (administrateur) ----------

class PilotageView(LayoutMixin, TemplateView):
    """Tableau de bord global : activité, résultats et performance de l'IA."""

    template_name = "quizzes/pilotage.html"

    def dispatch(self, request, *args, **kwargs):
        if not est_admin(request.user):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tous = Quiz.objects.all()
        depuis = timezone.localdate() - timedelta(days=29)
        soumises = TentativeQuiz.objects.exclude(statut=TentativeQuiz.Statut.EN_COURS)
        par_jour = {depuis + timedelta(days=i): 0 for i in range(30)}
        for date in soumises.filter(date_soumission__isnull=False).values_list("date_soumission", flat=True):
            jour = timezone.localtime(date).date()
            if jour in par_jour:
                par_jour[jour] += 1
        distribution = [0] * 10
        for t in soumises.only("score", "score_max"):
            if t.pourcentage is not None:
                distribution[min(9, t.pourcentage // 10)] += 1
        classement = []
        for quiz in tous.select_related("enseignant__utilisateur").annotate(
                nb=Count("tentatives", filter=~Q(tentatives__statut=TentativeQuiz.Statut.EN_COURS))).order_by("-nb")[:8]:
            pourcentages = [t.pourcentage for t in quiz.tentatives.exclude(statut=TentativeQuiz.Statut.EN_COURS)
                            if t.pourcentage is not None]
            quiz.moyenne = round(sum(pourcentages) / len(pourcentages)) if pourcentages else None
            classement.append(quiz)
        metriques = metriques_ia(AppelIA.objects.all())
        a_verifier, certifies = 0, 0
        for t in soumises.prefetch_related("reponses"):
            a_verifier += bool(t.signaux_integrite)
            certifies += certificats.peut_obtenir(t)
        context.update({
            "role": "admin",
            "a_verifier": a_verifier,
            "certifies": certifies,
            "kpi": tableau_de_bord_global(tous),
            "metriques_ia": metriques,
            "classement": classement,
            "derniers_echecs": AppelIA.objects.filter(succes=False).select_related("quiz")[:6],
            "graphique": {
                "jours": [d.strftime("%d/%m") for d in par_jour],
                "tentatives": list(par_jour.values()),
                "distribution": distribution,
                "ia_types": [m["libelle"] for m in metriques["types"]],
                "ia_succes": [m["succes"] for m in metriques["types"]],
                "ia_echecs": [m["n"] - m["succes"] for m in metriques["types"]],
                "ia_mediane": [m["mediane_ms"] or 0 for m in metriques["types"]],
                "ia_p95": [m["p95_ms"] or 0 for m in metriques["types"]],
            },
            "ia_disponible": gemini_configure(),
        })
        return context


# ---------- Carnet de révision espacée (étudiant) ----------

class RevisionView(EtudiantQuizMixin, LayoutMixin, TemplateView):
    """Une fiche à la fois, réponse immédiate, planification selon la méthode de Leitner."""

    template_name = "quizzes/revision.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        dues = cartes_dues(self.request.user)
        carte = dues.first()
        context.update({
            "role": "etudiant",
            "stats": stats_revision(self.request.user),
            "carte": carte,
            "choix": choix_melanges(carte) if carte else [],
            "retour": self.request.session.pop("revision_retour", None),
            "intervalles": INTERVALLES_LEITNER,
        })
        return context


class RepondreRevisionView(EtudiantQuizMixin, View):
    def post(self, request, pk):
        carte = get_object_or_404(CarteRevision.objects.select_related("question").prefetch_related("question__choix"),
                                  pk=pk, etudiant=request.user, maitrisee=False)
        if carte.prochaine_revision > timezone.localdate():
            messages.info(request, "Cette fiche n'est pas encore à réviser.")
            return redirect("revision")
        choix = {str(c.pk): c for c in carte.question.choix.all()}.get(request.POST.get("choix", ""))
        if choix is None:
            messages.error(request, "Choisis une réponse avant de valider.")
            return redirect("revision")
        carte.enregistrer_reponse(choix.est_correct)
        bonne = next((c.texte for c in carte.question.choix.all() if c.est_correct), "")
        request.session["revision_retour"] = {
            "juste": choix.est_correct, "enonce": carte.question.enonce, "choisi": choix.texte, "bonne": bonne,
            "explication": carte.question.explication, "boite": carte.boite, "maitrisee": carte.maitrisee,
            "prochaine": carte.prochaine_revision.strftime("%d/%m/%Y"),
        }
        return redirect("revision")


# ---------- Intégrité (signaux envoyés par le lecteur de quiz) ----------

class IntegriteView(EtudiantQuizMixin, View):
    def post(self, request, pk):
        with transaction.atomic():
            tentative = get_object_or_404(TentativeQuiz.objects.select_for_update(), pk=pk, etudiant=request.user)
            if tentative.statut != TentativeQuiz.Statut.EN_COURS:
                return JsonResponse({"erreur": "Tentative déjà soumise."}, status=400)
            tentative.integrite = fusionner(tentative.integrite, lire_compteur(request.POST.get("sorties")),
                                            lire_compteur(request.POST.get("collages")))
            tentative.save(update_fields=["integrite"])
        return JsonResponse({"ok": True})


# ---------- Import intelligent de questions (enseignant) ----------

class ImporterQuestionsView(EnseignantQuizMixin, LayoutMixin, TemplateView):
    template_name = "quizzes/importer.html"

    def cle_session(self, quiz):
        return f"import_questions_{quiz.pk}"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and est_enseignant(request.user):
            self.quiz = self.get_quiz()
            if not self.quiz.est_modifiable:
                messages.error(request, message_non_modifiable(self.quiz))
                return redirect("detail-quiz", pk=self.quiz.pk)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({"quiz": self.quiz, "ia_disponible": gemini_configure(), "texte_max": TEXTE_IMPORT_MAX,
                        "places": QUESTIONS_MAX - self.quiz.questions.count()})
        return context

    def post(self, request, pk):
        if request.POST.get("action") == "confirmer":
            return self.confirmer(request)
        texte = request.POST.get("texte", "")
        refus = refus_ia_enseignant(request.user)
        if refus:
            messages.error(request, refus)
            return self.render_to_response(self.get_context_data(texte=texte), status=400)
        try:
            valides, rejets, meta = analyser_texte(texte)
        except ErreurIA as erreur:
            if erreur.code != "source":
                journaliser(AppelIA.Type.IMPORT, self.quiz, request.user, erreur)
            messages.error(request, erreur.message)
            return self.render_to_response(self.get_context_data(texte=texte), status=400)
        journaliser(AppelIA.Type.IMPORT, self.quiz, request.user, meta=meta,
                    reconnues=len(valides), rejetees=len(rejets))
        request.session[self.cle_session(self.quiz)] = valides
        return self.render_to_response(self.get_context_data(texte=texte, apercu=valides, rejets=rejets,
                                                             duree_ms=meta["duree_ms"]))

    def confirmer(self, request):
        apercu = request.session.get(self.cle_session(self.quiz)) or []
        indices = {int(i) for i in request.POST.getlist("selection") if i.isdigit() and int(i) < len(apercu)}
        choisies = [apercu[i] for i in sorted(indices)]
        if not choisies:
            messages.error(request, "Sélectionnez au moins une question à ajouter.")
            return redirect("importer-questions", pk=self.quiz.pk)
        if self.quiz.questions.count() + len(choisies) > QUESTIONS_MAX:
            messages.error(request, f"Un quiz ne peut pas dépasser {QUESTIONS_MAX} questions.")
            return redirect("importer-questions", pk=self.quiz.pk)
        with transaction.atomic():
            ordre = self.quiz.prochain_ordre()
            for donnees in choisies:
                question = Question.objects.create(
                    quiz=self.quiz, ordre=ordre, type_question=donnees["type_question"], enonce=donnees["enonce"],
                    points=Decimal(donnees["points"]), explication=donnees["explication"],
                    reponse_attendue=donnees["reponse_attendue"],
                )
                Choix.objects.bulk_create(Choix(question=question, texte=c["texte"], est_correct=c["est_correct"], ordre=i)
                                          for i, c in enumerate(donnees["choix"], start=1))
                ordre += 1
        request.session.pop(self.cle_session(self.quiz), None)
        a_verifier = sum(1 for q in choisies if not q["reponse_indiquee"])
        messages.success(request, f"{len(choisies)} question(s) importée(s)."
                         + (f" {a_verifier} bonne(s) réponse(s) déduite(s) par l'IA : vérifiez-les avant de publier."
                            if a_verifier else ""))
        return redirect("detail-quiz", pk=self.quiz.pk)


# ---------- Certificat vérifiable et export ----------

class CertificatView(EtudiantQuizMixin, View):
    def get(self, request, pk):
        tentative = get_object_or_404(TentativeQuiz.objects.select_related("quiz__cours", "quiz__enseignant__utilisateur"),
                                      pk=pk, etudiant=request.user)
        if not certificats.peut_obtenir(tentative):
            messages.error(request, f"Le certificat est délivré à partir de {SEUIL_CERTIFICAT} % sur une tentative corrigée.")
            return redirect("tentative", pk=tentative.pk)
        lien = request.build_absolute_uri(reverse("verifier-certificat", args=[certificats.jeton(tentative)]))
        html = render_to_string("quizzes/certificat_pdf.html", {
            "tentative": tentative, "quiz": tentative.quiz, "etudiant": request.user, "lien": lien,
            "qr": certificats.qr_base64(lien), "code": certificats.code_court(tentative),
            "mention": certificats.mention(tentative.pourcentage),
        })
        tampon = io.BytesIO()
        if pisa.CreatePDF(html, dest=tampon, encoding="utf-8").err:
            logger.error("Échec de la génération du certificat de la tentative %s", tentative.pk)
            messages.error(request, "Le certificat n'a pas pu être généré. Réessayez plus tard.")
            return redirect("tentative", pk=tentative.pk)
        nom = f"certificat-{slugify(tentative.quiz.titre) or tentative.quiz.pk}.pdf"
        return HttpResponse(tampon.getvalue(), content_type="application/pdf",
                            headers={"Content-Disposition": f'attachment; filename="{nom}"'})


@method_decorator(login_not_required, name="dispatch")
class VerifierCertificatView(TemplateView):
    """Page publique ouverte par le QR code : confirme (ou non) l'authenticité du certificat."""

    template_name = "quizzes/verifier_certificat.html"

    def get_context_data(self, **kwargs):
        context = layout_sans_menu(self, super().get_context_data(**kwargs))
        tentative = certificats.verifier(self.kwargs["jeton"])
        context.update({"tentative": tentative, "valide": tentative is not None,
                        "code": certificats.code_court(tentative) if tentative else "",
                        "mention": certificats.mention(tentative.pourcentage) if tentative else ""})
        return context


class ExportResultatsView(View):
    def get(self, request, pk):
        quiz = quiz_pour_resultats(request.user, pk)
        tentatives = (quiz.tentatives.exclude(statut=TentativeQuiz.Statut.EN_COURS)
                      .select_related("etudiant").order_by("etudiant__username", "numero"))
        nom = f"resultats-{slugify(quiz.titre) or quiz.pk}.csv"
        return HttpResponse(certificats.export_csv(quiz, tentatives), content_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": f'attachment; filename="{nom}"'})
