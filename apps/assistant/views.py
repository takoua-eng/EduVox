import logging

from django.contrib import messages
from django.db.models import Count, Max, Q
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views import View
from django.views.generic import ListView, TemplateView

from web_project import TemplateLayout

from .forms import IllustrationForm, QuestionForm, SessionForm
from .models import Illustration, MessageVocal, SessionAssistant
from .permissions import AssistantRequisMixin, StatistiquesRequisMixin, sessions_de
from .reglages import reglage
from .services import (
    ErreurAssistant,
    gemini_configure,
    hugging_face_configure,
    metriques,
    preparer_question,
    rendre_markdown,
    repondre,
    verifier_quota_illustrations,
    verifier_quota_questions,
)
from .tasks import generer_illustration, lancer_apres_validation, transcrire_message

logger = logging.getLogger(__name__)


class LayoutMixin:
    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))


def attend_json(request):
    return request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in request.headers.get(
        "accept", "")


# ---------- Sessions (CRUD) ----------

class ListeSessionsView(AssistantRequisMixin, LayoutMixin, ListView):
    template_name = "assistant/liste.html"
    context_object_name = "liste_sessions"
    paginate_by = 12

    def get_queryset(self):
        self.recherche = self.request.GET.get("q", "").strip()
        self.filtre = self.request.GET.get("etat", "")
        sessions = sessions_de(self.request.user).select_related("cours").annotate(
            nb_messages=Count("messages", filter=Q(messages__role=MessageVocal.Role.UTILISATEUR), distinct=True),
            nb_illustrations=Count("illustrations", distinct=True),
            dernier_message=Max("messages__date"),
        ).order_by("-date_maj", "-pk")
        if self.filtre == "ouvertes":
            sessions = sessions.filter(active=True)
        elif self.filtre == "fermees":
            sessions = sessions.filter(active=False)
        if self.recherche:
            sessions = sessions.filter(Q(titre__icontains=self.recherche) | Q(cours__titre__icontains=self.recherche)
                                       | Q(messages__texte__icontains=self.recherche)).distinct()
        return sessions

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        mes_sessions = sessions_de(self.request.user)
        context.update({
            "recherche": self.recherche, "filtre": self.filtre,
            "bandeau": {
                "sessions": mes_sessions.count(),
                "ouvertes": mes_sessions.filter(active=True).count(),
                "questions": MessageVocal.objects.filter(session__utilisateur=self.request.user,
                                                         role=MessageVocal.Role.UTILISATEUR).count(),
                "illustrations": Illustration.objects.filter(session__utilisateur=self.request.user,
                                                             statut=Illustration.Statut.TERMINEE).count(),
            },
            "form": SessionForm(user=self.request.user),
            "gemini_configure": gemini_configure(),
        })
        return context


class CreerSessionView(AssistantRequisMixin, LayoutMixin, TemplateView):
    template_name = "assistant/form_session.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("form", SessionForm(user=self.request.user, initial={"cours": self.request.GET.get("cours")}))
        context["titre_page"] = "Nouvelle session"
        return context

    def post(self, request):
        form = SessionForm(request.POST, user=request.user)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form), status=400)
        session = form.save(commit=False)
        session.utilisateur = request.user
        session.save()
        messages.success(request, "Session ouverte. Posez votre première question à l'écrit ou à l'oral.")
        return redirect("session-assistant", pk=session.pk)


class ModifierSessionView(AssistantRequisMixin, LayoutMixin, TemplateView):
    template_name = "assistant/form_session.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["session"] = session = kwargs.get("session") or self.get_session()
        context.setdefault("form", SessionForm(instance=session, user=self.request.user))
        context["titre_page"] = "Modifier la session"
        return context

    def post(self, request, pk):
        session = self.get_session()
        form = SessionForm(request.POST, instance=session, user=request.user)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form, session=session), status=400)
        form.save()
        messages.success(request, "Session mise à jour.")
        return redirect("session-assistant", pk=session.pk)


class SupprimerSessionView(AssistantRequisMixin, LayoutMixin, TemplateView):
    template_name = "assistant/supprimer_session.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["session"] = self.get_session()
        context["nb_messages"] = context["session"].messages.count()
        context["nb_illustrations"] = context["session"].illustrations.count()
        return context

    def post(self, request, pk):
        session = self.get_session()
        titre = session.titre
        session.delete()  # les signaux post_delete suppriment aussi les fichiers audio et images du disque
        messages.success(request, f"La session « {titre} » et son historique ont été supprimés.")
        return redirect("assistant")


class FermerSessionView(AssistantRequisMixin, View):
    def post(self, request, pk):
        session = self.get_session()
        if session.active:
            session.fermer()
            messages.info(request, "Session fermée. Vous pouvez la consulter ou la rouvrir à tout moment.")
        return redirect("session-assistant", pk=pk)


class RouvrirSessionView(AssistantRequisMixin, View):
    def post(self, request, pk):
        session = self.get_session()
        if not session.active:
            session.rouvrir()
            messages.success(request, "Session rouverte.")
        return redirect("session-assistant", pk=pk)


class DetailSessionView(AssistantRequisMixin, LayoutMixin, TemplateView):
    """Page de conversation : historique, saisie écrite ou orale, réponses en streaming, illustrations."""

    template_name = "assistant/detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        session = self.get_session()
        context.update({
            "session": session,
            "liste_messages": session.messages.prefetch_related("illustrations"),
            "illustrations": session.illustrations.select_related("message"),
            "form_question": kwargs.get("form_question") or QuestionForm(),
            "form_illustration": IllustrationForm(),
            "gemini_configure": gemini_configure(),
            "hf_configure": hugging_face_configure(),
            "max_caracteres": reglage("ASSISTANT_QUESTION_MAX_CARACTERES"),
            "audio_max_s": reglage("ASSISTANT_AUDIO_MAX_S"),
        })
        return context


class ExporterSessionView(AssistantRequisMixin, View):
    """Téléchargement de l'historique au format Markdown."""

    def get(self, request, pk):
        session = self.get_session()
        lignes = [f"# {session.titre}", "", f"Ouverte le {timezone.localtime(session.date_ouverture):%d/%m/%Y à %H:%M}"]
        if session.cours_id:
            lignes.append(f"Cours : {session.cours.titre}")
        for message in session.messages.filter(statut=MessageVocal.Statut.PRET):
            auteur = "Vous" + (" (à l'oral)" if message.mode == MessageVocal.Mode.VOCAL else "") \
                if message.est_question else "Assistant"
            lignes += ["", f"## {auteur} – {timezone.localtime(message.date):%d/%m/%Y %H:%M}", "", message.texte]
        reponse = HttpResponse("\n".join(lignes) + "\n", content_type="text/markdown; charset=utf-8")
        nom = slugify(session.titre) or f"session-{session.pk}"
        reponse["Content-Disposition"] = f'attachment; filename="{nom}.md"'
        return reponse


# ---------- Messages ----------

class QuestionView(AssistantRequisMixin, View):
    """Question écrite sans WebSocket (JavaScript désactivé ou connexion temps réel indisponible).

    La réponse est complète d'un coup (pas de streaming)."""

    def post(self, request, pk):
        session = self.get_session()
        form = QuestionForm(request.POST)
        message_id = request.POST.get("message_id")
        message_id = int(message_id) if message_id and message_id.isdigit() else None
        if not form.is_valid() and not message_id:
            if attend_json(request):
                return JsonResponse({"erreur": "Écrivez une question (2000 caractères au maximum)."}, status=400)
            messages.error(request, "Écrivez une question avant de l'envoyer.")
            return redirect("session-assistant", pk=pk)
        texte = form.cleaned_data.get("texte", "") if form.is_valid() else request.POST.get("texte", "")
        try:
            question, reponse = preparer_question(session, texte, message_id)
            repondre(question, reponse)
        except ErreurAssistant as erreur:
            if attend_json(request):
                return JsonResponse({"erreur": erreur.message}, status=400 if erreur.code in (
                    "validation", "session_fermee", "limite") else 503)
            messages.error(request, erreur.message)
            return redirect("session-assistant", pk=pk)
        if attend_json(request):
            return JsonResponse({"question": {"id": question.pk, "html": rendre_markdown(question.texte)},
                                 "reponse_id": reponse.pk, "html": rendre_markdown(reponse.texte),
                                 "duree_ms": reponse.duree_ms})
        return redirect(f"{reverse('session-assistant', args=[pk])}#message-{reponse.pk}")


class AudioView(AssistantRequisMixin, View):
    """Réception d'une question dictée (WAV 16 bits enregistré dans le navigateur) puis transcription Whisper."""

    def post(self, request, pk):
        session = self.get_session()
        if not session.active:
            return JsonResponse({"erreur": "Cette session est fermée."}, status=400)
        fichier = request.FILES.get("audio")
        if fichier is None:
            return JsonResponse({"erreur": "Aucun enregistrement reçu."}, status=400)
        if fichier.size > reglage("ASSISTANT_AUDIO_MAX_MO") * 1024 * 1024:
            return JsonResponse({"erreur": "Enregistrement trop volumineux."}, status=400)
        entete = fichier.read(12)
        fichier.seek(0)
        if entete[:4] != b"RIFF" or entete[8:12] != b"WAVE":
            return JsonResponse({"erreur": "Format audio non reconnu (WAV attendu)."}, status=400)
        try:
            verifier_quota_questions(request.user)
        except ErreurAssistant as erreur:
            return JsonResponse({"erreur": erreur.message}, status=429)

        message = MessageVocal(session=session, role=MessageVocal.Role.UTILISATEUR, mode=MessageVocal.Mode.VOCAL,
                               statut=MessageVocal.Statut.EN_ATTENTE)
        message.audio.save(f"question-{session.pk}.wav", fichier, save=False)
        message.save()
        session.save(update_fields=["date_maj"])

        def echec():
            MessageVocal.objects.filter(pk=message.pk).update(
                statut=MessageVocal.Statut.ERREUR,
                message_erreur="Le service de transcription est injoignable. Écrivez votre question.")

        lancer_apres_validation(transcrire_message, message.pk, echec)
        return JsonResponse({"id": message.pk, "url_etat": reverse("etat-message-assistant", args=[message.pk])},
                            status=202)


class FragmentMessageView(AssistantRequisMixin, View):
    """Rendu HTML d'un message, inséré par la page une fois la réponse terminée (un seul gabarit : _message.html)."""

    def get(self, request, pk):
        message = self.get_message()
        return HttpResponse(render_to_string("assistant/_message.html", {"message": message, "session": message.session},
                                             request=request))


class EtatMessageView(AssistantRequisMixin, View):
    """État d'un message (transcription en cours / prête / en erreur), interrogé par la page."""

    def get(self, request, pk):
        message = self.get_message()
        return JsonResponse({
            "id": message.pk, "statut": message.statut, "fini": message.statut != MessageVocal.Statut.EN_ATTENTE,
            "texte": message.texte, "erreur": message.message_erreur, "duree_ms": message.duree_ms,
            "audio": message.audio.url if message.audio else None,
        })


class EvaluerMessageView(AssistantRequisMixin, View):
    """Avis de l'utilisateur sur une réponse (utile / pas utile) : métrique qualitative du module."""

    def post(self, request, pk):
        message = self.get_message()
        if message.role != MessageVocal.Role.ASSISTANT or message.statut != MessageVocal.Statut.PRET:
            return HttpResponseBadRequest("Seules les réponses de l'assistant peuvent être évaluées.")
        try:
            valeur = int(request.POST.get("evaluation", ""))
        except ValueError:
            valeur = None
        if valeur not in MessageVocal.Evaluation.values and valeur != 0:
            return HttpResponseBadRequest("Évaluation invalide.")
        message.evaluation = valeur or None  # 0 : retire l'avis
        message.save(update_fields=["evaluation"])
        if attend_json(request):
            return JsonResponse({"evaluation": message.evaluation})
        return redirect(f"{reverse('session-assistant', args=[message.session_id])}#message-{message.pk}")


class SupprimerMessageView(AssistantRequisMixin, View):
    """Supprime une question et la réponse qui la suit (ou un message isolé)."""

    def post(self, request, pk):
        message = self.get_message()
        session_id = message.session_id
        a_supprimer = [message]
        if message.est_question:
            suivant = message.session.messages.filter(pk__gt=message.pk).first()
            if suivant and suivant.role == MessageVocal.Role.ASSISTANT:
                a_supprimer.append(suivant)
        for objet in a_supprimer:
            objet.delete()
        if attend_json(request):
            return JsonResponse({"supprime": True})
        messages.success(request, "Échange supprimé de l'historique.")
        return redirect("session-assistant", pk=session_id)


# ---------- Illustrations ----------

class IllustrationView(AssistantRequisMixin, View):
    def post(self, request, pk):
        session = self.get_session()
        form = IllustrationForm(request.POST)
        erreur = None
        if not session.active:
            erreur = "Cette session est fermée."
        elif not form.is_valid():
            erreur = "Décrivez ce que vous voulez visualiser (3 à 500 caractères)."
        else:
            try:
                verifier_quota_illustrations(request.user)
            except ErreurAssistant as e:
                erreur = e.message
        if erreur:
            if attend_json(request):
                return JsonResponse({"erreur": erreur}, status=400)
            messages.error(request, erreur)
            return redirect("session-assistant", pk=pk)

        message = None
        if form.cleaned_data.get("message"):
            message = session.messages.filter(pk=form.cleaned_data["message"]).first()
        illustration = Illustration.objects.create(
            session=session, message=message, demande=form.cleaned_data["demande"].strip(),
            type_illustration=form.cleaned_data["type_illustration"],
        )

        def echec():
            Illustration.objects.filter(pk=illustration.pk).update(
                statut=Illustration.Statut.ERREUR,
                message_erreur="Le service de génération est injoignable. Réessayez plus tard.")

        lancer_apres_validation(generer_illustration, illustration.pk, echec)
        if attend_json(request):
            return JsonResponse({"id": illustration.pk,
                                 "url_etat": reverse("etat-illustration-assistant", args=[illustration.pk])},
                                status=202)
        messages.info(request, "Génération lancée : l'illustration apparaîtra dans quelques secondes.")
        return redirect(f"{reverse('session-assistant', args=[pk])}#illustrations")


class EtatIllustrationView(AssistantRequisMixin, View):
    def get(self, request, pk):
        illustration = self.get_illustration()
        return JsonResponse({
            "id": illustration.pk, "statut": illustration.statut,
            "fini": illustration.statut != Illustration.Statut.EN_COURS,
            "html": render_to_string("assistant/_illustration.html", {"illustration": illustration}, request=request),
        })


class SupprimerIllustrationView(AssistantRequisMixin, View):
    def post(self, request, pk):
        illustration = self.get_illustration()
        session_id = illustration.session_id
        illustration.delete()
        if attend_json(request):
            return JsonResponse({"supprime": True})
        messages.success(request, "Illustration supprimée.")
        return redirect(f"{reverse('session-assistant', args=[session_id])}#illustrations")


class GalerieView(AssistantRequisMixin, LayoutMixin, ListView):
    """Toutes les illustrations de l'utilisateur, toutes sessions confondues."""

    template_name = "assistant/galerie.html"
    context_object_name = "liste_illustrations"
    paginate_by = 18

    def get_queryset(self):
        illustrations = Illustration.objects.filter(session__utilisateur=self.request.user,
                                                    statut=Illustration.Statut.TERMINEE).select_related("session")
        self.filtre = self.request.GET.get("type", "")
        if self.filtre in Illustration.Type.values:
            illustrations = illustrations.filter(type_illustration=self.filtre)
        return illustrations

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filtre"] = self.filtre
        context["types"] = Illustration.Type.choices
        return context


# ---------- Statistiques (administrateur) ----------

class StatistiquesView(StatistiquesRequisMixin, LayoutMixin, TemplateView):
    """Métriques de performance et de qualité de l'IA du module (données agrégées, sans contenu personnel)."""

    template_name = "assistant/statistiques.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        try:
            jours = min(max(int(self.request.GET.get("jours", 30)), 1), 365)
        except ValueError:
            jours = 30
        context["m"] = metriques(jours)
        context["periodes"] = [7, 30, 90]
        return context
