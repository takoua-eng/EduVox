"""Imprévus : préparation, proposition IA, validation, publication ; réponses d'adaptation des équipes."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import FormView, TemplateView

from ..forms import EvenementForm, PropositionEvenementForm, ReponseForm
from ..models import DemandeIA, Defi, Equipe, EvenementScenario, ReponseEvenement
from ..permissions import est_admin, get_defi_gere, get_equipe_accessible, peut_deposer, peut_gerer_defi, peut_soumettre
from ..services import evenements as regles
from ..services.gemini import ErreurIA, gemini_configure
from ..services.ia import DemandeEnCours, demander_evenement
from .base import ActionView, LayoutMixin, messages_erreur


def get_evenement_gere(user, pk):
    evenement = get_object_or_404(EvenementScenario.objects.select_related("defi", "jalon_concerne"), pk=pk)
    if not peut_gerer_defi(user, evenement.defi):
        raise Http404("Imprévu introuvable.")
    return evenement


class EvenementsDefiView(LayoutMixin, TemplateView):
    template_name = "challenges/evenements.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        defi = get_defi_gere(self.request.user, self.kwargs["pk"])
        actives = defi.equipes.filter(statut=Equipe.Statut.ACTIVE).count()
        evenements = defi.evenements.select_related("valide_par", "cree_par").annotate(
            nb_reponses=Count("reponses", filter=Q(reponses__etat=ReponseEvenement.Etat.SOUMISE)))
        demande = defi.demandes_ia.filter(type_demande=DemandeIA.Type.EVENEMENT).first()
        context.update({
            "defi": defi, "evenements": evenements, "equipes_actives": actives, "ia_disponible": gemini_configure(),
            "proposition_form": PropositionEvenementForm(), "demande": demande,
            "peut_proposer": defi.statut in (Defi.Statut.INSCRIPTIONS, Defi.Statut.EN_COURS),
        })
        return context


class EvenementFormMixin(LayoutMixin):
    template_name = "challenges/form_evenement.html"
    form_class = EvenementForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["defi"] = self.defi
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["defi"] = self.defi
        context["evenement"] = getattr(self, "evenement", None)
        return context

    def form_valid(self, form):
        try:
            evenement = form.save()
        except ValidationError as erreur:
            form.add_error(None, erreur)
            return self.form_invalid(form)
        messages.success(self.request, "Imprévu enregistré en brouillon : relisez-le puis validez-le.")
        return redirect("detail-evenement", pk=evenement.pk)


class CreerEvenementView(EvenementFormMixin, FormView):
    def dispatch(self, request, *args, **kwargs):
        self.defi = get_defi_gere(request.user, kwargs["pk"])
        if self.defi.lecture_seule:
            raise PermissionDenied("Le défi est clos.")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.cree_par = self.request.user
        form.instance.origine = EvenementScenario.Origine.MANUELLE
        return super().form_valid(form)


class ModifierEvenementView(EvenementFormMixin, FormView):
    def dispatch(self, request, *args, **kwargs):
        self.evenement = get_evenement_gere(request.user, kwargs["pk"])
        self.defi = self.evenement.defi
        if self.evenement.publie:
            messages.error(request, "Un imprévu publié est conservé tel quel dans l'historique.")
            return redirect("detail-evenement", pk=self.evenement.pk)
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.evenement
        return kwargs


class DetailEvenementView(LayoutMixin, TemplateView):
    template_name = "challenges/evenement_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        evenement = get_object_or_404(EvenementScenario.objects.select_related(
            "defi", "jalon_concerne", "valide_par", "cree_par"), pk=self.kwargs["pk"])
        if not (peut_gerer_defi(user, evenement.defi) or est_admin(user)):
            raise Http404("Imprévu introuvable.")
        reponses = {r.equipe_id: r for r in evenement.reponses.filter(etat=ReponseEvenement.Etat.SOUMISE)
                    .select_related("soumis_par")}
        equipes = [(e, reponses.get(e.pk)) for e in evenement.defi.equipes.filter(statut=Equipe.Statut.ACTIVE)]
        context.update({"evenement": evenement, "defi": evenement.defi, "equipes_reponses": equipes,
                        "gestion": peut_gerer_defi(user, evenement.defi)})
        return context


class EvenementActionView(ActionView):
    def get_objet(self):
        return get_evenement_gere(self.request.user, self.kwargs["pk"])

    def url_retour(self, evenement):
        if getattr(self, "supprime", False):
            return reverse("evenements-defi", args=[evenement.defi_id])
        return reverse("detail-evenement", args=[evenement.pk])


class ValiderEvenementView(EvenementActionView):
    def executer(self, evenement):
        regles.valider(evenement, self.request.user)
        return "Imprévu validé. Vous pouvez maintenant le publier aux équipes."


class PublierEvenementView(EvenementActionView):
    def executer(self, evenement):
        evenement = regles.publier(evenement, self.request.user)
        detail = " Modifications appliquées et tracées." if evenement.modifications_appliquees else ""
        return "Imprévu publié à toutes les équipes actives." + detail


class SupprimerEvenementView(EvenementActionView):
    def executer(self, evenement):
        regles.supprimer(evenement, self.request.user)
        self.supprime = True
        return "Imprévu supprimé."


class ProposerEvenementView(ActionView):
    """Demande à Gemini une proposition d'imprévu (brouillon, origine IA) ; traitement différé."""

    def get_objet(self):
        return get_defi_gere(self.request.user, self.kwargs["pk"])

    def executer(self, defi):
        if defi.statut not in (Defi.Statut.INSCRIPTIONS, Defi.Statut.EN_COURS):
            raise ValidationError("Les imprévus se préparent pendant les inscriptions ou le défi.")
        form = PropositionEvenementForm(self.request.POST)
        consigne = form.cleaned_data["consigne"] if form.is_valid() else ""
        try:
            demander_evenement(self.request.user, defi, consigne)
        except DemandeEnCours:
            raise ValidationError("Une proposition est déjà en cours de préparation.")
        except ErreurIA as erreur:
            raise ValidationError(erreur.message)
        return ("Proposition demandée : le brief du défi et son avancement anonymisé sont envoyés à Google Gemini. "
                "L'imprévu apparaîtra en brouillon.")

    def url_retour(self, defi):
        return reverse("evenements-defi", args=[defi.pk])


# ---------- Réponses d'adaptation ----------

class ReponseEvenementView(LayoutMixin, FormView):
    template_name = "challenges/reponse_form.html"
    form_class = ReponseForm

    def dispatch(self, request, *args, **kwargs):
        self.equipe, self.acces = get_equipe_accessible(request.user, kwargs["equipe_pk"])
        # Un imprévu d'un autre défi (ou non publié) n'existe pas pour cette équipe
        self.evenement = get_object_or_404(EvenementScenario, pk=kwargs["pk"], defi_id=self.equipe.defi_id,
                                           etat=EvenementScenario.Etat.PUBLIE)
        self.reponse = ReponseEvenement.objects.filter(evenement=self.evenement, equipe=self.equipe).first()
        if self.acces != "membre" and (self.reponse is None or self.reponse.etat != ReponseEvenement.Etat.SOUMISE):
            raise Http404("Réponse introuvable.")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.reponse or ReponseEvenement(evenement=self.evenement, equipe=self.equipe)
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        modifiable = (self.acces == "membre" and peut_deposer(user, self.equipe)
                      and (self.reponse is None or self.reponse.etat == ReponseEvenement.Etat.BROUILLON))
        context.update({
            "equipe": self.equipe, "defi": self.equipe.defi, "evenement": self.evenement, "reponse": self.reponse,
            "acces": self.acces, "membre": self.acces == "membre", "onglet": "imprevus", "modifiable": modifiable,
            "peut_soumettre": modifiable and self.reponse is not None and peut_soumettre(user, self.equipe),
        })
        return context

    def post(self, request, *args, **kwargs):
        if self.acces != "membre":
            raise PermissionDenied
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        try:
            regles.enregistrer_reponse(self.evenement, self.equipe, self.request.user, **form.cleaned_data)
        except ValidationError as erreur:
            form.add_error(None, messages_erreur(erreur))
            return self.form_invalid(form)
        messages.success(self.request, "Brouillon de réponse enregistré. Le coordinateur peut le soumettre.")
        return redirect("reponse-evenement", pk=self.evenement.pk, equipe_pk=self.equipe.pk)


class SoumettreReponseView(ActionView):
    def get_objet(self):
        equipe, acces = get_equipe_accessible(self.request.user, self.kwargs["equipe_pk"])
        if acces != "membre":
            raise Http404
        return get_object_or_404(ReponseEvenement.objects.select_related("evenement", "equipe__defi"),
                                 evenement_id=self.kwargs["pk"], equipe=equipe)

    def executer(self, reponse):
        reponse = regles.soumettre_reponse(reponse, self.request.user)
        return "Réponse d'adaptation soumise à l'enseignant." + (" (après l'échéance)" if reponse.en_retard else "")

    def url_retour(self, reponse):
        return reverse("reponse-evenement", args=[reponse.evenement_id, reponse.equipe_id])
