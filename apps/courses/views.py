import base64
import csv
import io
import logging
from collections import Counter
from urllib.parse import urlencode

import qrcode
from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.db.models import Avg, Count, Max, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.text import slugify
from django.views import View
from django.views.generic import CreateView, DeleteView, ListView, TemplateView, UpdateView
from django.views.generic.detail import SingleObjectMixin
from xhtml2pdf import pisa

from ai_services import duree_audio, traduire_texte
from apps.accounts.models import Enseignant
from web_project import TemplateLayout

from .forms import (
    EXTENSIONS_ACCEPTEES,
    AvisForm,
    CoursForm,
    GenerationResumeForm,
    MatiereForm,
    NotePersonnelleForm,
    ReponseAvisForm,
    SignalementForm,
    TraductionForm,
)
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
    annee_universitaire,
    bornes_annee_universitaire,
)
from .permissions import (
    AdminRequisMixin,
    ConsultationCoursMixin,
    EnseignantRequisMixin,
    EtudiantRequisMixin,
    ProprietaireCoursMixin,
    cours_publics,
    cours_visibles,
    est_admin,
    est_enseignant,
    est_etudiant,
    est_proprietaire,
)
from .recommandations import cours_similaires
from .statistiques import avec_note_moyenne, avec_statistiques
from .tasks import generer_resume_cours, transcrire_cours

logger = logging.getLogger(__name__)


def lancer_traitement(tache, cours, **options):
    """Envoie la tâche Celery pour ce cours une fois la transaction validée."""

    def envoyer():
        try:
            tache.delay(cours.pk, **options)
        except Exception:
            logger.exception("Impossible d'envoyer le cours %s à Celery", cours.pk)
            Cours.objects.filter(pk=cours.pk).update(
                statut=Cours.Statut.ERREUR,
                message_erreur="Le service de traitement est injoignable (Celery / Redis). Réessayez plus tard.",
            )

    transaction.on_commit(envoyer)


def rediriger_vers_suivant(request, defaut):
    """Redirige vers le champ « suivant » du formulaire s'il pointe vers EduVox, sinon vers `defaut`."""
    suivant = request.POST.get("suivant", "")
    if not url_has_allowed_host_and_scheme(suivant, allowed_hosts={request.get_host()}):
        suivant = defaut
    return redirect(suivant)


def qr_code_base64(texte):
    """Image PNG d'un QR code, encodée en base64 pour une balise <img>."""
    tampon = io.BytesIO()
    qrcode.make(texte, box_size=6, border=2).save(tampon, format="PNG")
    return base64.b64encode(tampon.getvalue()).decode()


def reponse_fichier(contenu, type_contenu, nom_fichier):
    return HttpResponse(
        contenu, content_type=type_contenu,
        headers={"Content-Disposition": f'attachment; filename="{nom_fichier}"'},
    )


def enregistrer_telechargement(user, cours, format_fichier):
    # Seuls les téléchargements des étudiants comptent dans les statistiques
    if est_etudiant(user):
        Telechargement.objects.create(utilisateur=user, cours=cours, format=format_fichier)


class LayoutMixin:
    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))


# ---------- Consultation ----------

class ListeCoursView(LayoutMixin, ListView):
    """Liste des cours avec recherche (titre, description, mots-clés, transcription) et filtres."""

    template_name = "courses/liste_cours.html"
    context_object_name = "liste_cours"
    paginate_by = 20
    noms_filtres = ["q", "matiere", "niveau", "enseignant"]

    def get_queryset(self):
        self.filtres = {nom: self.request.GET.get(nom, "").strip() for nom in self.noms_filtres}
        cours = cours_visibles(self.request.user)

        if self.filtres["q"]:
            q = self.filtres["q"]
            cours = cours.filter(
                Q(titre__icontains=q) | Q(description__icontains=q) | Q(mots_cles__icontains=q)
                | Q(transcription__texte__icontains=q)
            )
        if self.filtres["matiere"].isdigit():
            cours = cours.filter(matiere_id=self.filtres["matiere"])
        if self.filtres["niveau"] in Cours.Niveau.values:
            cours = cours.filter(niveau=self.filtres["niveau"])
        if self.filtres["enseignant"].isdigit():
            cours = cours.filter(enseignant_id=self.filtres["enseignant"])
        return avec_note_moyenne(cours).select_related("enseignant__utilisateur", "matiere")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        visibles = cours_visibles(user)
        context.update({
            "est_enseignant": est_enseignant(user),
            "est_etudiant": est_etudiant(user),
            "filtres": self.filtres,
            "filtre_actif": any(self.filtres.values()),
            # Paramètres à conserver dans les liens de pagination
            "parametres": urlencode({nom: valeur for nom, valeur in self.filtres.items() if valeur}),
            "matieres": Matiere.objects.filter(cours__in=visibles).distinct(),
            "niveaux": Cours.Niveau.choices,
            "enseignants": Enseignant.objects.filter(cours__in=visibles).distinct().select_related("utilisateur"),
            "favoris": set(Favori.objects.filter(utilisateur=user).values_list("cours_id", flat=True)),
        })
        return context


class DetailCoursView(ConsultationCoursMixin, LayoutMixin, TemplateView):
    template_name = "courses/detail_cours.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        cours = self.get_cours()
        resume = Resume.objects.filter(cours=cours).first()
        autorises = cours_visibles(user)
        liste_avis = cours.avis.select_related("utilisateur")
        lien_partage = self.request.build_absolute_uri(reverse("detail-cours", args=[cours.pk]))

        context.update({
            "cours": cours,
            "transcription": Transcription.objects.filter(cours=cours).first(),
            "resume": resume,
            "traductions": {t.langue: t for t in resume.traductions.all()} if resume else {},
            "langues_traduction": TraductionResume.Langue.choices,
            "langue_active": self.request.GET.get("langue", "fr"),
            "est_proprietaire": est_proprietaire(user, cours),
            "est_etudiant": est_etudiant(user),
            "est_admin": est_admin(user),
            "form_resume": GenerationResumeForm(initial={
                "type_resume": resume.type_resume if resume else Resume.Type.DETAILLE,
            }),
            # Prérequis et recommandations : seulement les cours que l'utilisateur peut voir
            "prerequis": cours.prerequis.filter(pk__in=autorises).select_related("matiere"),
            "similaires": cours_similaires(cours, autorises),
            "liste_avis": liste_avis,
            "note_moyenne": liste_avis.aggregate(moyenne=Avg("note"))["moyenne"],
            "lien_partage": lien_partage,
            "qr_code": qr_code_base64(lien_partage),
        })

        if est_etudiant(user):
            Consultation.objects.create(utilisateur=user, cours=cours)
            mon_avis = liste_avis.filter(utilisateur=user).first()
            note = NotePersonnelle.objects.filter(utilisateur=user, cours=cours).first()
            context.update({
                "est_favori": Favori.objects.filter(utilisateur=user, cours=cours).exists(),
                "mon_avis": mon_avis,
                "form_avis": AvisForm(instance=mon_avis),
                "note_personnelle": note,
                "form_signalement": SignalementForm(),
            })
        return context


class ProgressionCoursView(ConsultationCoursMixin, View):
    """Avancement du traitement IA, interrogé régulièrement par la page détail."""

    def get(self, request, pk):
        cours = self.get_cours()
        return JsonResponse({
            "statut": cours.statut,
            "statut_libelle": cours.get_statut_display(),
            "progression": cours.progression,
            "message_erreur": cours.message_erreur,
            "fini": cours.statut in [Cours.Statut.TERMINE, Cours.Statut.ERREUR],
        })


class TelechargerResumeView(ConsultationCoursMixin, View):
    def get(self, request, pk):
        cours = self.get_cours()
        resume = get_object_or_404(Resume, cours=cours)
        contenu = f"{cours.titre}\nRésumé généré le {resume.date_generation:%d/%m/%Y}\n\n{resume.texte}\n"
        enregistrer_telechargement(request.user, cours, Telechargement.Format.TXT)
        return reponse_fichier(contenu, "text/plain; charset=utf-8", f"resume-{slugify(cours.titre) or cours.pk}.txt")


class TelechargerResumePdfView(ConsultationCoursMixin, View):
    """Résumé mis en page en PDF : titre, enseignant, date, résumé."""

    def get(self, request, pk):
        cours = self.get_cours()
        resume = get_object_or_404(Resume, cours=cours)
        html = render_to_string("courses/resume_pdf.html", {"cours": cours, "resume": resume})
        tampon = io.BytesIO()
        if pisa.CreatePDF(html, dest=tampon, encoding="utf-8").err:
            logger.error("Échec de la génération du PDF du résumé du cours %s", cours.pk)
            messages.error(request, "Le PDF n'a pas pu être généré. Téléchargez la version texte.")
            return redirect("detail-cours", pk=cours.pk)
        enregistrer_telechargement(request.user, cours, Telechargement.Format.PDF)
        return reponse_fichier(tampon.getvalue(), "application/pdf", f"resume-{slugify(cours.titre) or cours.pk}.pdf")


# ---------- Gestion (enseignant) ----------

class FormulaireCoursMixin(LayoutMixin):
    form_class = CoursForm
    template_name = "courses/form_cours.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["extensions_acceptees"] = ",".join(EXTENSIONS_ACCEPTEES)
        context["taille_max_mo"] = settings.COURS_TAILLE_MAX_MO
        return context

    def get_success_url(self):
        return reverse("detail-cours", args=[self.object.pk])

    def mesurer_audio(self):
        if self.object.type_fichier == Cours.TypeFichier.AUDIO:
            self.object.mettre_a_jour(duree_audio=duree_audio(self.object.fichier.path))


class AjouterCoursView(EnseignantRequisMixin, FormulaireCoursMixin, CreateView):
    extra_context = {"titre": "Ajouter un cours"}

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "enseignant": self.get_enseignant()}

    def form_valid(self, form):
        form.instance.enseignant = self.get_enseignant()
        response = super().form_valid(form)
        self.mesurer_audio()
        JournalActivite.enregistrer(self.request.user, JournalActivite.Action.CREATION, self.object)
        if self.object.type_fichier == Cours.TypeFichier.AUDIO:
            messages.success(self.request, "Cours ajouté. Vous pouvez maintenant lancer la transcription de l'audio.")
        else:
            messages.success(self.request, "Cours ajouté. Vous pouvez maintenant générer son résumé.")
        return response


class ModifierCoursView(ProprietaireCoursMixin, FormulaireCoursMixin, UpdateView):
    extra_context = {"titre": "Modifier le cours"}

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "enseignant": self.object.enseignant}

    def form_valid(self, form):
        ancien_fichier = Cours.objects.get(pk=self.object.pk).fichier
        response = super().form_valid(form)
        if form.changed_data:
            libelles = ", ".join(str(form.fields[nom].label or nom).lower() for nom in form.changed_data)
            JournalActivite.enregistrer(
                self.request.user, JournalActivite.Action.MODIFICATION, self.object,
                details=f"Champs : {libelles}"[:255],
            )
        if "fichier" in form.changed_data:
            # La transcription et le résumé de l'ancien fichier ne sont plus valables
            ancien_fichier.delete(save=False)
            Transcription.objects.filter(cours=self.object).delete()
            Resume.objects.filter(cours=self.object).delete()
            self.mesurer_audio()
            messages.success(self.request, "Cours modifié. Relancez le traitement IA pour le nouveau fichier.")
        else:
            messages.success(self.request, "Cours modifié.")
        return response


class SupprimerCoursView(ProprietaireCoursMixin, DeleteView):
    http_method_names = ["post"]
    success_url = reverse_lazy("liste-cours")

    def form_valid(self, form):
        JournalActivite.enregistrer(self.request.user, JournalActivite.Action.SUPPRESSION, self.object)
        self.object.fichier.delete(save=False)
        messages.success(self.request, f"Le cours « {self.object.titre} » a été supprimé.")
        return super().form_valid(form)


# ---------- Traitement IA (lancé par l'enseignant depuis la page du cours) ----------

class LancerTraitementView(ProprietaireCoursMixin, SingleObjectMixin, View):
    tache = None
    message_succes = ""

    def refus(self, cours):
        """Renvoie un message si le traitement ne peut pas être lancé pour ce cours."""
        return None

    def options(self):
        """Paramètres supplémentaires passés à la tâche Celery."""
        return {}

    def post(self, request, pk):
        cours = self.get_object()
        message_refus = (
            "Un traitement est déjà en cours pour ce cours."
            if cours.statut == Cours.Statut.EN_COURS else self.refus(cours)
        )
        if message_refus:
            messages.error(request, message_refus)
            return redirect("detail-cours", pk=cours.pk)

        # Passe tout de suite « en cours » pour que la page affiche la barre de progression
        cours.mettre_a_jour(statut=Cours.Statut.EN_COURS, progression=0, message_erreur="")
        lancer_traitement(self.tache, cours, **self.options())
        messages.success(request, self.message_succes)
        return redirect("detail-cours", pk=cours.pk)


class TranscrireCoursView(LancerTraitementView):
    tache = transcrire_cours
    message_succes = "La transcription de l'audio a démarré."

    def refus(self, cours):
        if cours.type_fichier != Cours.TypeFichier.AUDIO:
            return "Seuls les cours audio peuvent être transcrits."
        return None


class GenererResumeView(LancerTraitementView):
    tache = generer_resume_cours
    message_succes = "La génération du résumé a démarré."

    def refus(self, cours):
        if cours.type_fichier == Cours.TypeFichier.AUDIO and not Transcription.objects.filter(cours=cours).exists():
            return "Transcrivez d'abord l'audio avant de générer le résumé."
        self.formulaire = GenerationResumeForm(self.request.POST)
        if not self.formulaire.is_valid():
            return "Choix du résumé invalide : " + " ".join(
                erreur for erreurs in self.formulaire.errors.values() for erreur in erreurs
            )
        return None

    def options(self):
        return {
            "type_resume": self.formulaire.cleaned_data["type_resume"],
            "consigne": self.formulaire.cleaned_data["consigne"],
        }


class TraduireResumeView(ProprietaireCoursMixin, SingleObjectMixin, View):
    """Traduit le résumé (EN ou AR) avec Gemini et stocke la traduction."""

    def post(self, request, pk):
        cours = self.get_object()
        resume = get_object_or_404(Resume, cours=cours)
        formulaire = TraductionForm(request.POST)
        if not formulaire.is_valid():
            messages.error(request, "Langue de traduction invalide.")
            return redirect("detail-cours", pk=cours.pk)

        langue = formulaire.cleaned_data["langue"]
        try:
            texte = traduire_texte(resume.texte, langue)
        except Exception as erreur:
            logger.exception("Échec de la traduction du résumé du cours %s", cours.pk)
            messages.error(request, f"La traduction a échoué : {erreur}")
        else:
            TraductionResume.objects.update_or_create(resume=resume, langue=langue, defaults={"texte": texte})
            libelle = TraductionResume.Langue(langue).label.lower()
            messages.success(request, f"Traduction en {libelle} enregistrée.")
        return redirect(reverse("detail-cours", args=[cours.pk]) + f"?langue={langue}#resume")


# ---------- Suivi de ses cours (enseignant) ----------

class StatistiquesView(EnseignantRequisMixin, LayoutMixin, ListView):
    template_name = "courses/statistiques.html"
    context_object_name = "liste_cours"

    def get_queryset(self):
        cours = Cours.objects.filter(enseignant__utilisateur=self.request.user)
        return avec_statistiques(cours).order_by("-nb_consultations", "titre")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        liste = context["liste_cours"]
        context["totaux"] = {
            "consultations": sum(c.nb_consultations for c in liste),
            "telechargements": sum(c.nb_telechargements for c in liste),
            "avis": sum(c.nb_avis for c in liste),
        }
        return context


class AvisRecusView(EnseignantRequisMixin, LayoutMixin, ListView):
    template_name = "courses/avis_recus.html"
    context_object_name = "liste_avis"
    paginate_by = 20

    def get_queryset(self):
        avis = Avis.objects.filter(cours__enseignant__utilisateur=self.request.user)
        if self.request.GET.get("filtre") == "sans_reponse":
            avis = avis.filter(reponse="")
        return avis.select_related("cours", "utilisateur")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filtre"] = self.request.GET.get("filtre", "")
        return context


class RepondreAvisView(EnseignantRequisMixin, View):
    """L'enseignant répond (ou modifie sa réponse) à un avis laissé sur un de SES cours."""

    def post(self, request, pk):
        avis = get_object_or_404(Avis, pk=pk, cours__enseignant__utilisateur=request.user)
        formulaire = ReponseAvisForm(request.POST)
        if formulaire.is_valid():
            avis.reponse = formulaire.cleaned_data["reponse"]
            avis.date_reponse = timezone.now()
            avis.save(update_fields=["reponse", "date_reponse"])
            messages.success(request, "Votre réponse a été publiée.")
        else:
            messages.error(request, "La réponse ne peut pas être vide.")
        return rediriger_vers_suivant(request, reverse("avis-recus"))


# ---------- Espace étudiant ----------

class ActionEtudiantMixin(EtudiantRequisMixin, ConsultationCoursMixin):
    """Action d'un étudiant sur un cours qu'il peut consulter (sinon 403 ou 404)."""

    def retour(self, cours):
        return rediriger_vers_suivant(self.request, reverse("detail-cours", args=[cours.pk]))


class BasculerFavoriView(ActionEtudiantMixin, View):
    def post(self, request, pk):
        cours = self.get_cours()
        favori, cree = Favori.objects.get_or_create(utilisateur=request.user, cours=cours)
        if cree:
            messages.success(request, f"« {cours.titre} » a été ajouté à vos favoris.")
        else:
            favori.delete()
            messages.success(request, f"« {cours.titre} » a été retiré de vos favoris.")
        return self.retour(cours)


class EnregistrerAvisView(ActionEtudiantMixin, View):
    """Crée ou modifie l'avis de l'étudiant (un seul avis par cours)."""

    def post(self, request, pk):
        cours = self.get_cours()
        avis = Avis.objects.filter(utilisateur=request.user, cours=cours).first()
        formulaire = AvisForm(request.POST, instance=avis)
        if formulaire.is_valid():
            formulaire.instance.utilisateur = request.user
            formulaire.instance.cours = cours
            formulaire.save()
            messages.success(request, "Votre avis a été modifié." if avis else "Merci pour votre avis !")
        else:
            messages.error(request, "Choisissez une note entre 1 et 5 étoiles.")
        return redirect(reverse("detail-cours", args=[cours.pk]) + "#avis")


class EnregistrerNoteView(ActionEtudiantMixin, View):
    """Notes personnelles de l'étudiant sur un cours (visibles par lui seul)."""

    def post(self, request, pk):
        cours = self.get_cours()
        formulaire = NotePersonnelleForm(request.POST)
        if formulaire.is_valid() and formulaire.cleaned_data["texte"].strip():
            NotePersonnelle.objects.update_or_create(
                utilisateur=request.user, cours=cours, defaults={"texte": formulaire.cleaned_data["texte"]}
            )
            messages.success(request, "Vos notes ont été enregistrées.")
        else:
            NotePersonnelle.objects.filter(utilisateur=request.user, cours=cours).delete()
            messages.success(request, "Vos notes ont été effacées.")
        return redirect(reverse("detail-cours", args=[cours.pk]) + "#notes")


class SignalerCoursView(ActionEtudiantMixin, View):
    def post(self, request, pk):
        cours = self.get_cours()
        if Signalement.objects.filter(auteur=request.user, cours=cours, statut=Signalement.Statut.NOUVEAU).exists():
            messages.warning(request, "Vous avez déjà signalé ce cours. La modération va l'examiner.")
            return redirect("detail-cours", pk=cours.pk)

        formulaire = SignalementForm(request.POST)
        if formulaire.is_valid():
            formulaire.instance.auteur = request.user
            formulaire.instance.cours = cours
            formulaire.save()
            messages.success(request, "Merci, votre signalement a été transmis à la modération.")
        else:
            messages.error(request, "Choisissez un motif de signalement.")
        return redirect("detail-cours", pk=cours.pk)


class MesFavorisView(EtudiantRequisMixin, LayoutMixin, ListView):
    template_name = "courses/mes_favoris.html"
    context_object_name = "favoris"

    def get_queryset(self):
        # Un favori dont le cours a été masqué ou archivé n'est plus affiché
        return (
            Favori.objects.filter(utilisateur=self.request.user, cours__in=cours_publics())
            .select_related("cours__matiere", "cours__enseignant__utilisateur")
        )


class HistoriqueView(EtudiantRequisMixin, LayoutMixin, TemplateView):
    """Derniers cours consultés (une ligne par cours, le plus récent en premier)."""

    template_name = "courses/historique.html"
    nb_cours = 20

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        dernieres = (
            Consultation.objects.filter(utilisateur=self.request.user, cours__in=cours_publics())
            .values("cours").annotate(derniere=Max("date")).order_by("-derniere")[:self.nb_cours]
        )
        dates = {ligne["cours"]: ligne["derniere"] for ligne in dernieres}
        cours = Cours.objects.filter(pk__in=dates).select_related("matiere", "enseignant__utilisateur")
        context["historique"] = sorted(
            (
                {"cours": c, "date": dates[c.pk], "vu_le": f"vu le {timezone.localtime(dates[c.pk]):%d/%m/%Y}"}
                for c in cours
            ),
            key=lambda ligne: ligne["date"], reverse=True,
        )
        return context


# ---------- Administration (modération, archivage, journal, classement) ----------

class MasquerCoursView(AdminRequisMixin, View):
    """Masque un cours (invisible des étudiants) ou le réaffiche."""

    def post(self, request, pk):
        cours = get_object_or_404(Cours, pk=pk)
        cours.mettre_a_jour(masque=not cours.masque)
        action = JournalActivite.Action.MASQUAGE if cours.masque else JournalActivite.Action.REAFFICHAGE
        JournalActivite.enregistrer(request.user, action, cours)
        if cours.masque:
            messages.success(request, f"« {cours.titre} » est masqué : les étudiants ne le voient plus.")
        else:
            messages.success(request, f"« {cours.titre} » est de nouveau visible.")
        return rediriger_vers_suivant(request, reverse("detail-cours", args=[cours.pk]))


class ArchiverCoursView(AdminRequisMixin, View):
    """Archive un cours (sans le supprimer) ou le désarchive."""

    def post(self, request, pk):
        cours = get_object_or_404(Cours, pk=pk)
        if cours.archive:
            cours.mettre_a_jour(archive=False, date_archivage=None)
            JournalActivite.enregistrer(request.user, JournalActivite.Action.DESARCHIVAGE, cours)
            messages.success(request, f"« {cours.titre} » a été désarchivé.")
        else:
            cours.mettre_a_jour(archive=True, date_archivage=timezone.now())
            JournalActivite.enregistrer(request.user, JournalActivite.Action.ARCHIVAGE, cours)
            messages.success(request, f"« {cours.titre} » a été archivé.")
        return rediriger_vers_suivant(request, reverse("detail-cours", args=[cours.pk]))


class SignalementsView(AdminRequisMixin, LayoutMixin, ListView):
    template_name = "courses/signalements.html"
    context_object_name = "signalements"
    paginate_by = 20

    def get_queryset(self):
        self.statut = self.request.GET.get("statut", Signalement.Statut.NOUVEAU)
        signalements = Signalement.objects.select_related("cours", "auteur", "traite_par")
        if self.statut in Signalement.Statut.values:
            signalements = signalements.filter(statut=self.statut)
        return signalements

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        compteurs = dict(Signalement.objects.values_list("statut").annotate(n=Count("pk")))
        context.update({
            "statut_choisi": self.statut,
            "onglets": [(valeur, libelle, compteurs.get(valeur, 0)) for valeur, libelle in Signalement.Statut.choices],
            "total": sum(compteurs.values()),
        })
        return context


class TraiterSignalementView(AdminRequisMixin, View):
    def post(self, request, pk):
        signalement = get_object_or_404(Signalement, pk=pk)
        statut = request.POST.get("statut")
        if statut not in Signalement.Statut.values:
            messages.error(request, "Statut de signalement invalide.")
        else:
            signalement.statut = statut
            en_attente = statut == Signalement.Statut.NOUVEAU
            signalement.traite_par = None if en_attente else request.user
            signalement.date_traitement = None if en_attente else timezone.now()
            signalement.save(update_fields=["statut", "traite_par", "date_traitement"])
            messages.success(request, f"Signalement marqué « {signalement.get_statut_display().lower()} ».")
        return rediriger_vers_suivant(request, reverse("signalements"))


class ArchivageView(AdminRequisMixin, LayoutMixin, TemplateView):
    """Archivage en masse des cours d'une année universitaire passée, et liste des cours archivés."""

    template_name = "courses/archivage.html"

    def annee_courante(self):
        return annee_universitaire(timezone.now())

    def annees_archivables(self):
        debut_annee_courante, _ = bornes_annee_universitaire(self.annee_courante())
        dates = Cours.objects.filter(archive=False, date_creation__lt=debut_annee_courante).values_list("date_creation", flat=True)
        compteur = Counter(annee_universitaire(date) for date in dates)
        return [{"annee": annee, "libelle": f"{annee}-{annee + 1}", "nb_cours": nb} for annee, nb in sorted(compteur.items(), reverse=True)]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            "annees": self.annees_archivables(),
            "annee_courante": f"{self.annee_courante()}-{self.annee_courante() + 1}",
            "archives": Cours.objects.filter(archive=True).select_related("enseignant__utilisateur", "matiere").order_by("-date_archivage"),
        })
        return context

    def post(self, request):
        try:
            annee = int(request.POST.get("annee", ""))
        except ValueError:
            annee = None
        if annee is None or annee >= self.annee_courante():
            messages.error(request, "Choisissez une année universitaire passée.")
            return redirect("archivage-cours")

        debut, fin = bornes_annee_universitaire(annee)
        with transaction.atomic():
            a_archiver = list(Cours.objects.filter(archive=False, date_creation__gte=debut, date_creation__lt=fin))
            Cours.objects.filter(pk__in=[c.pk for c in a_archiver]).update(archive=True, date_archivage=timezone.now())
            JournalActivite.objects.bulk_create([
                JournalActivite(utilisateur=request.user, action=JournalActivite.Action.ARCHIVAGE, cours=c,
                                titre_cours=c.titre, details=f"Archivage de l'année {annee}-{annee + 1}")
                for c in a_archiver
            ])
        messages.success(request, f"{len(a_archiver)} cours de l'année {annee}-{annee + 1} ont été archivés.")
        return redirect("archivage-cours")


class JournalActiviteView(AdminRequisMixin, LayoutMixin, ListView):
    template_name = "courses/journal.html"
    context_object_name = "activites"
    paginate_by = 30

    def get_queryset(self):
        self.action = self.request.GET.get("action", "")
        self.q = self.request.GET.get("q", "").strip()
        activites = JournalActivite.objects.select_related("utilisateur")
        if self.action in JournalActivite.Action.values:
            activites = activites.filter(action=self.action)
        if self.q:
            activites = activites.filter(
                Q(titre_cours__icontains=self.q) | Q(utilisateur__username__icontains=self.q)
                | Q(utilisateur__first_name__icontains=self.q) | Q(utilisateur__last_name__icontains=self.q)
            )
        return activites

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            "actions": JournalActivite.Action.choices,
            "action_choisie": self.action,
            "q": self.q,
            "parametres": urlencode({nom: valeur for nom, valeur in [("action", self.action), ("q", self.q)] if valeur}),
        })
        return context


class ClassementView(AdminRequisMixin, LayoutMixin, TemplateView):
    template_name = "courses/classement.html"
    taille = 10
    min_avis = 3  # Une note moyenne sur 1 ou 2 avis n'est pas représentative

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cours = avec_statistiques(Cours.objects.select_related("enseignant__utilisateur", "matiere"))
        context.update({
            "min_avis": self.min_avis,
            "plus_consultes": cours.filter(nb_consultations__gt=0).order_by("-nb_consultations", "titre")[:self.taille],
            "mieux_notes": cours.filter(nb_avis__gte=self.min_avis).order_by("-note_moyenne", "-nb_avis")[:self.taille],
            "plus_signales": cours.filter(nb_signalements__gt=0).order_by("-nb_signalements", "titre")[:self.taille],
        })
        return context


class ExportCsvView(AdminRequisMixin, View):
    """Liste des cours avec leurs statistiques, lisible directement dans Excel (UTF-8 avec BOM, séparateur ;)."""

    colonnes = [
        "ID", "Titre", "Enseignant", "Matière", "Niveau", "Type", "Publication", "Masqué", "Archivé",
        "Date de création", "Consultations", "Téléchargements", "Note moyenne", "Nombre d'avis", "Signalements",
    ]

    def get(self, request):
        reponse = HttpResponse(
            content_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="cours-eduvox-{timezone.localdate():%Y-%m-%d}.csv"'},
        )
        reponse.write("\ufeff")
        ecrivain = csv.writer(reponse, delimiter=";")
        ecrivain.writerow(self.colonnes)
        cours = avec_statistiques(Cours.objects.select_related("enseignant__utilisateur", "matiere")).order_by("titre")
        for c in cours:
            ecrivain.writerow([
                c.pk, c.titre, c.enseignant.utilisateur.get_full_name() or c.enseignant.utilisateur.username,
                c.matiere.nom if c.matiere else "", c.get_niveau_display(), c.get_type_fichier_display(),
                "Publié" if c.est_publie else c.get_publication_display(), "oui" if c.masque else "non",
                "oui" if c.archive else "non", f"{timezone.localtime(c.date_creation):%d/%m/%Y}",
                c.nb_consultations, c.nb_telechargements,
                f"{c.note_moyenne:.2f}".replace(".", ",") if c.note_moyenne is not None else "",
                c.nb_avis, c.nb_signalements,
            ])
        return reponse


class MatieresView(AdminRequisMixin, LayoutMixin, ListView):
    """Gestion des matières par l'administrateur (en plus de l'admin Django)."""

    template_name = "courses/matieres.html"
    context_object_name = "matieres"

    def get_queryset(self):
        return Matiere.objects.annotate(nb_cours=Count("cours"))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form"] = kwargs.get("form") or MatiereForm()
        return context

    def post(self, request):
        formulaire = MatiereForm(request.POST)
        if formulaire.is_valid():
            formulaire.save()
            messages.success(request, f"Matière « {formulaire.instance.nom} » ajoutée.")
            return redirect("matieres")
        self.object_list = self.get_queryset()
        return self.render_to_response(self.get_context_data(form=formulaire))


class ModifierMatiereView(AdminRequisMixin, View):
    def post(self, request, pk):
        matiere = get_object_or_404(Matiere, pk=pk)
        formulaire = MatiereForm(request.POST, instance=matiere)
        if formulaire.is_valid():
            formulaire.save()
            messages.success(request, "Matière renommée.")
        else:
            messages.error(request, " ".join(e for erreurs in formulaire.errors.values() for e in erreurs))
        return redirect("matieres")


class SupprimerMatiereView(AdminRequisMixin, View):
    def post(self, request, pk):
        matiere = get_object_or_404(Matiere, pk=pk)
        matiere.delete()  # Les cours de cette matière passent à « aucune matière »
        messages.success(request, f"Matière « {matiere.nom} » supprimée.")
        return redirect("matieres")
