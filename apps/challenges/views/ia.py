"""Génération IA d'un brief (aperçu modifiable) et état consultable des traitements IA."""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views import View
from django.views.generic import FormView, TemplateView

from ..forms import ApercuJalonFormSet, DefiForm, GenerationBriefForm
from ..models import DemandeIA
from ..permissions import acces_equipe, peut_gerer_defi
from ..services.brief import convertir_dates
from ..services.gemini import ErreurIA, gemini_configure
from ..services.ia import DemandeEnCours, creer_defi_depuis_brief, demander_brief
from .base import EnseignantRequisMixin, LayoutMixin, messages_erreur


class GenererBriefView(EnseignantRequisMixin, LayoutMixin, FormView):
    template_name = "challenges/generer_brief.html"
    form_class = GenerationBriefForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["ia_disponible"] = gemini_configure()
        context["recentes"] = DemandeIA.objects.filter(demandeur=self.request.user, type_demande=DemandeIA.Type.BRIEF)[:5]
        return context

    def form_valid(self, form):
        donnees = form.cleaned_data
        parametres = {**{k: donnees[k] for k in ("theme", "niveau", "duree_jours", "taille_min", "taille_max",
                                                 "contraintes")}, "date_debut": donnees["date_debut"].isoformat()}
        try:
            demande = demander_brief(self.request.user, parametres)
        except DemandeEnCours as en_cours:
            messages.info(self.request, "Une génération est déjà en cours : voici son état.")
            return redirect("apercu-brief", pk=en_cours.demande.pk)
        except ErreurIA as erreur:
            form.add_error(None, erreur.message)
            return self.form_invalid(form)
        return redirect("apercu-brief", pk=demande.pk)


class ApercuBriefView(EnseignantRequisMixin, LayoutMixin, TemplateView):
    """Attente (état consultable) puis aperçu modifiable ; l'enregistrement crée un défi BROUILLON."""

    template_name = "challenges/apercu_brief.html"

    def get_demande(self):
        return get_object_or_404(DemandeIA, pk=self.kwargs["pk"], demandeur=self.request.user,
                                 type_demande=DemandeIA.Type.BRIEF)

    def formulaires(self, demande, data=None):
        brief = demande.resultat
        parametres = demande.parametres
        debut = parse_datetime(parametres["date_debut"])
        date_fin, jalons = convertir_dates(brief, debut, int(parametres["duree_jours"]))
        initial = {
            "titre": brief["titre"], "description": brief["description"], "contexte": brief["contexte"],
            "objectifs": "\n".join(brief["objectifs"]), "competences": "\n".join(brief["competences"]),
            "livrables_attendus": "\n".join(brief["livrables"]), "contraintes": "\n".join(brief["contraintes"]),
            "budget": brief["budget"], "niveau": parametres["niveau"], "taille_min": parametres["taille_min"],
            "taille_max": parametres["taille_max"], "inscriptions_debut": timezone.now(),
            "inscriptions_fin": debut, "date_debut": debut, "date_fin": date_fin,
        }
        form = DefiForm(data, initial=initial, user=self.request.user, prefix="defi")
        formset = ApercuJalonFormSet(data, prefix="jalons", initial=[
            {"titre": j["titre"], "description": j["description"], "echeance": j["echeance"],
             "exigences": "\n".join(j["exigences"])} for j in jalons])
        return form, formset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        demande = kwargs.get("demande") or self.get_demande()
        context["demande"] = demande
        if demande.statut == DemandeIA.Statut.TERMINE and not demande.defi_cree_id and "form" not in kwargs:
            context["form"], context["formset"] = self.formulaires(demande)
        return context

    def post(self, request, pk):
        demande = self.get_demande()
        if demande.defi_cree_id:
            return redirect("detail-defi", pk=demande.defi_cree_id)
        if demande.statut != DemandeIA.Statut.TERMINE:
            raise Http404
        form, formset = self.formulaires(demande, request.POST)
        if form.is_valid() and formset.is_valid():
            donnees = {k: v for k, v in form.cleaned_data.items() if k != "co_enseignants"}
            jalons = [{"titre": f.cleaned_data["titre"], "description": f.cleaned_data["description"],
                       "echeance": f.cleaned_data["echeance"], "exigences": f.cleaned_data["exigences"]}
                      for f in formset.forms if f.cleaned_data and not f.cleaned_data.get("DELETE")]
            try:
                defi, cree = creer_defi_depuis_brief(demande, request.user, donnees, jalons,
                                                     form.cleaned_data.get("co_enseignants", []))
            except ValidationError as erreur:
                form.add_error(None, messages_erreur(erreur))
            else:
                if cree:
                    messages.success(request, "Défi enregistré en BROUILLON à partir du brief. Relisez-le avant "
                                              "d'ouvrir les inscriptions : rien n'est publié automatiquement.")
                return redirect("detail-defi", pk=defi.pk)
        return self.render_to_response(self.get_context_data(demande=demande, form=form, formset=formset))


class EtatDemandeView(View):
    """État consultable d'un traitement IA (JSON, interrogé périodiquement par la page)."""

    http_method_names = ["get"]

    def get(self, request, pk):
        demande = get_object_or_404(DemandeIA.objects.select_related("defi", "version__livrable__equipe__defi"), pk=pk)
        user = request.user
        autorise = demande.demandeur_id == user.pk
        if not autorise and demande.version_id:
            acces = acces_equipe(user, demande.version.livrable.equipe)
            autorise = acces == "membre" or (acces == "enseignant" and not demande.version.est_brouillon)
        if not autorise and demande.type_demande == DemandeIA.Type.EVENEMENT and demande.defi_id:
            autorise = peut_gerer_defi(user, demande.defi)
        if not autorise:
            raise Http404
        return JsonResponse({
            "statut": demande.statut, "libelle": demande.get_statut_display(), "termine": not demande.active,
            "message": demande.message, "url": self._url(demande),
        })

    @staticmethod
    def _url(demande):
        if demande.type_demande == DemandeIA.Type.BRIEF:
            return reverse("apercu-brief", args=[demande.pk])
        if demande.type_demande == DemandeIA.Type.EVENEMENT:
            if demande.evenement_cree_id:
                return reverse("detail-evenement", args=[demande.evenement_cree_id])
            return reverse("evenements-defi", args=[demande.defi_id])
        return reverse("detail-livrable", args=[demande.version.livrable_id]) + f"#version-{demande.version_id}"


