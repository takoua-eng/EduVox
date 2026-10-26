"""Livrables versionnés : dépôt, historique, soumission, retours enseignant, téléchargement protégé, vérification IA."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import FormView, TemplateView

from ..forms import EvaluationForm, LivrableForm, VersionForm
from ..models import DemandeIA, Livrable, VersionLivrable
from ..permissions import (
    get_equipe_accessible,
    get_version_accessible,
    peut_deposer,
    peut_gerer_defi,
    peut_modifier_version,
    peut_soumettre,
    peut_supprimer_version,
    versions_visibles,
)
from ..services import livrables as regles
from ..services.gemini import ErreurIA, gemini_configure
from ..services.ia import DemandeEnCours, demander_verification
from ..services.verification import STATUTS, empreinte_actuelle
from .base import ActionView, LayoutMixin, messages_erreur
from .equipes import EspaceEquipeMixin


def contenu(form):
    donnees = form.cleaned_data
    return {"fichier": donnees.get("fichier"), "lien": donnees.get("lien", ""), "texte": donnees.get("texte", ""),
            "note_equipe": donnees.get("note_equipe", "")}


class CreerLivrableView(EspaceEquipeMixin, FormView):
    template_name = "challenges/form_livrable.html"
    form_class = LivrableForm
    onglet = "livrables"

    def get(self, request, *args, **kwargs):
        self._verifier()
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        self._verifier()
        return super().post(request, *args, **kwargs)

    def _verifier(self):
        if not peut_deposer(self.request.user, self.equipe):
            raise PermissionDenied("Les livrables se déposent pendant le défi, par les membres d'une équipe active.")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["equipe"] = self.equipe
        initial = self.request.GET.get("jalon")
        if initial and initial.isdigit():
            kwargs["initial"] = {"jalon": int(initial)}
        return kwargs

    def form_valid(self, form):
        donnees = form.cleaned_data
        try:
            version = regles.creer_livrable(self.equipe, self.request.user, donnees["titre"], donnees["description"],
                                            donnees["jalon"], **contenu(form))
        except ValidationError as erreur:
            form.add_error(None, messages_erreur(erreur))
            return self.form_invalid(form)
        messages.success(self.request, "Brouillon enregistré. Le coordinateur peut maintenant le soumettre.")
        return redirect("detail-livrable", pk=version.livrable_id)


class DetailLivrableView(LayoutMixin, TemplateView):
    template_name = "challenges/livrable_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        livrable = get_object_or_404(Livrable.objects.select_related("equipe__defi", "jalon"), pk=self.kwargs["pk"])
        equipe, acces = get_equipe_accessible(user, livrable.equipe_id)
        versions = list(versions_visibles(user, acces, livrable).select_related(
            "auteur", "soumis_par", "retour_par").order_by("-numero"))
        if not versions:
            raise Http404("Livrable introuvable.")
        for version in versions:
            version.modifiable = peut_modifier_version(user, version)
            version.supprimable = peut_supprimer_version(user, version)
            analyses = list(version.analyses.filter(type_demande=DemandeIA.Type.VERIFICATION)[:1])
            version.analyse = analyses[0] if analyses else None
            if version.analyse and version.analyse.statut == DemandeIA.Statut.TERMINE and version.est_brouillon \
                    and version.analyse.empreinte:
                # Brouillon modifié depuis l'analyse : elle ne vaut plus pour ce contenu
                version.analyse_obsolete = empreinte_actuelle(version) != version.analyse.empreinte
        derniere = versions[0]
        context.update({
            "livrable": livrable, "equipe": equipe, "defi": equipe.defi, "acces": acces, "onglet": "livrables",
            "membre": acces == "membre", "versions": versions, "derniere": derniere,
            "peut_nouvelle_version": peut_deposer(user, equipe) and derniere.etat == VersionLivrable.Etat.A_COMPLETER,
            "peut_soumettre": peut_soumettre(user, equipe), "peut_deposer": peut_deposer(user, equipe),
            "peut_evaluer": peut_gerer_defi(user, equipe.defi) and equipe.defi.statut in ("en_cours", "termine"),
            "evaluation_form": EvaluationForm(), "statuts_ia": STATUTS, "ia_disponible": gemini_configure(),
        })
        return context


class NouvelleVersionView(LayoutMixin, FormView):
    template_name = "challenges/form_version.html"
    form_class = VersionForm

    def dispatch(self, request, *args, **kwargs):
        self.livrable = get_object_or_404(Livrable.objects.select_related("equipe__defi", "jalon"), pk=kwargs["pk"])
        self.equipe, acces = get_equipe_accessible(request.user, self.livrable.equipe_id)
        if not peut_deposer(request.user, self.equipe):
            raise PermissionDenied("Vous ne pouvez pas déposer de version pour cette équipe.")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({"livrable": self.livrable, "equipe": self.equipe, "defi": self.equipe.defi,
                        "onglet": "livrables", "membre": True, "precedente": self.livrable.derniere_version()})
        return context

    def form_valid(self, form):
        try:
            version = regles.nouvelle_version(self.livrable, self.request.user, **contenu(form))
        except ValidationError as erreur:
            form.add_error(None, messages_erreur(erreur))
            return self.form_invalid(form)
        messages.success(self.request, f"Version {version.numero} enregistrée en brouillon.")
        return redirect("detail-livrable", pk=self.livrable.pk)


class ModifierVersionView(LayoutMixin, FormView):
    template_name = "challenges/form_version.html"
    form_class = VersionForm

    def dispatch(self, request, *args, **kwargs):
        self.version, acces = get_version_accessible(request.user, kwargs["pk"])
        if not peut_modifier_version(request.user, self.version):
            raise PermissionDenied("Seul l'auteur modifie ce brouillon ; une version soumise n'est jamais modifiée.")
        self.livrable = self.version.livrable
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["version"] = self.version
        if self.request.method == "GET":
            kwargs["initial"] = {"lien": self.version.lien, "texte": self.version.texte,
                                 "note_equipe": self.version.note_equipe}
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({"livrable": self.livrable, "equipe": self.livrable.equipe, "defi": self.livrable.equipe.defi,
                        "version": self.version, "onglet": "livrables", "membre": True})
        return context

    def form_valid(self, form):
        try:
            regles.modifier_brouillon(self.version, self.request.user, retirer_fichier=form.cleaned_data.get(
                "retirer_fichier", False), **contenu(form))
        except ValidationError as erreur:
            form.add_error(None, messages_erreur(erreur))
            return self.form_invalid(form)
        messages.success(self.request, "Brouillon mis à jour.")
        return redirect("detail-livrable", pk=self.livrable.pk)


class VersionActionView(ActionView):
    def get_objet(self):
        version, acces = get_version_accessible(self.request.user, self.kwargs["pk"])
        self.acces = acces
        return version

    def url_retour(self, version):
        if getattr(self, "livrable_supprime", False):
            return reverse("livrables-equipe", args=[version.livrable.equipe_id])
        return reverse("detail-livrable", args=[version.livrable_id])


class SoumettreVersionView(VersionActionView):
    def executer(self, version):
        version = regles.soumettre(version, self.request.user)
        retard = " (soumission tardive)" if version.en_retard else ""
        return f"Version {version.numero} soumise à l'enseignant{retard}. Elle ne peut plus être modifiée."


class SupprimerVersionView(VersionActionView):
    def executer(self, version):
        livrable = regles.supprimer_brouillon(version, self.request.user)
        self.livrable_supprime = livrable is None
        return "Brouillon supprimé."


class EvaluerVersionView(VersionActionView):
    def executer(self, version):
        form = EvaluationForm(self.request.POST)
        if not form.is_valid():
            raise ValidationError(" ".join(e for erreurs in form.errors.values() for e in erreurs))
        regles.evaluer(version, self.request.user, form.cleaned_data["decision"], form.cleaned_data["retour"])
        if form.cleaned_data["decision"] == VersionLivrable.Etat.ACCEPTE:
            return "Livrable accepté."
        return "Complément demandé : l'équipe peut déposer une nouvelle version."


class VerifierVersionView(VersionActionView):
    """Lance la vérification IA de la version : membres de l'équipe, ou enseignant sur une version soumise."""

    def executer(self, version):
        user = self.request.user
        if self.acces not in ("membre", "enseignant"):
            raise ValidationError("Action réservée à l'équipe et à ses enseignants.")
        if self.acces == "membre" and not peut_deposer(user, version.livrable.equipe):
            raise ValidationError("Le défi n'est plus en cours.")
        derniere = version.analyses.filter(type_demande=DemandeIA.Type.VERIFICATION,
                                           statut=DemandeIA.Statut.TERMINE).first()
        if derniere and not version.est_brouillon:
            raise ValidationError("Cette version soumise a déjà été analysée : son contenu ne change plus.")
        if derniere and derniere.empreinte and derniere.empreinte == empreinte_actuelle(version):
            raise ValidationError("Le contenu n'a pas changé depuis la dernière analyse.")
        try:
            demander_verification(user, version)
        except DemandeEnCours:
            raise ValidationError("Une vérification de cette version est déjà en cours.")
        except ErreurIA as erreur:
            raise ValidationError(erreur.message)
        return "Vérification lancée : le texte du dossier est envoyé à Google Gemini. Le résultat s'affichera ici."


class TelechargerVersionView(View):
    """Seul accès aux fichiers déposés : droits vérifiés sur la version, téléchargement en pièce jointe."""

    http_method_names = ["get"]

    def get(self, request, pk):
        version, acces = get_version_accessible(request.user, pk)
        if not version.fichier:
            raise Http404("Aucun fichier.")
        try:
            fichier = version.fichier.open("rb")
        except FileNotFoundError:
            raise Http404("Fichier introuvable sur le serveur.")
        reponse = FileResponse(fichier, as_attachment=True, filename=version.nom_fichier or "livrable")
        reponse["X-Content-Type-Options"] = "nosniff"
        reponse["Cache-Control"] = "private, no-store"
        return reponse
