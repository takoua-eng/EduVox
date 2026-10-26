"""Catalogue, détail, gestion des défis et des jalons, suivi des équipes par l'enseignant."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import FormView, ListView, TemplateView

from ..forms import DefiForm, JalonForm
from ..models import Defi, Equipe, EvenementScenario, Jalon, ParticipationEquipe, ReponseEvenement, Tache, VersionLivrable
from ..permissions import (
    defis_visibles,
    est_admin,
    est_enseignant,
    est_etudiant,
    est_proprietaire,
    get_defi_gere,
    get_defi_visible,
    participation_acceptee,
    peut_gerer_defi,
)
from ..services import defis as regles
from ..services.gemini import gemini_configure
from .base import ActionView, EnseignantRequisMixin, LayoutMixin, messages_erreur

Etat = ParticipationEquipe.Etat


class CatalogueView(LayoutMixin, ListView):
    template_name = "challenges/catalogue.html"
    context_object_name = "defis"
    paginate_by = 9

    def get_queryset(self):
        user = self.request.user
        self.recherche = self.request.GET.get("q", "").strip()[:100]
        self.filtre_statut = self.request.GET.get("statut", "")
        self.filtre_niveau = self.request.GET.get("niveau", "")
        self.mes = self.request.GET.get("mes") == "1"
        defis = defis_visibles(user)
        if self.mes:
            if est_enseignant(user):
                defis = defis.filter(Q(enseignant=user) | Q(co_enseignants=user))
            elif est_etudiant(user):
                defis = defis.filter(participations__etudiant=user, participations__etat=Etat.ACCEPTEE)
        if self.filtre_statut in Defi.Statut.values:
            defis = defis.filter(statut=self.filtre_statut)
        if self.filtre_niveau in Defi.Niveau.values:
            defis = defis.filter(niveau=self.filtre_niveau)
        if self.recherche:
            defis = defis.filter(Q(titre__icontains=self.recherche) | Q(description__icontains=self.recherche)
                                 | Q(competences__icontains=self.recherche))
        ids = defis.values("pk")
        return (Defi.objects.filter(pk__in=ids).select_related("enseignant")
                .annotate(nb_equipes=Count("equipes", filter=Q(equipes__statut=Equipe.Statut.ACTIVE), distinct=True),
                          nb_jalons=Count("jalons", distinct=True))
                .order_by("-date_creation"))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        statuts = Defi.Statut.choices
        if est_etudiant(user):
            statuts = [s for s in statuts if s[0] not in (Defi.Statut.BROUILLON, Defi.Statut.ARCHIVE)]
        mes_equipes = {}
        if est_etudiant(user):
            for p in ParticipationEquipe.objects.filter(etudiant=user, etat=Etat.ACCEPTEE).select_related("equipe"):
                mes_equipes[p.defi_id] = p.equipe
        for defi in context["defis"]:
            defi.mon_equipe = mes_equipes.get(defi.pk)
        params = self.request.GET.copy()
        params.pop("page", None)
        context.update({
            "recherche": self.recherche, "filtre_statut": self.filtre_statut, "filtre_niveau": self.filtre_niveau,
            "mes": self.mes, "statuts": statuts, "niveaux": Defi.Niveau.choices, "params": params.urlencode(),
            "ia_disponible": gemini_configure(),
        })
        return context


class DetailDefiView(LayoutMixin, TemplateView):
    template_name = "challenges/detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        defi = get_defi_visible(user, self.kwargs["pk"])
        gestion = peut_gerer_defi(user, defi)
        equipes = list(defi.equipes.select_related("coordinateur")
                       .annotate(nb=Count("participations", filter=Q(participations__etat=Etat.ACCEPTEE)))
                       .order_by("statut", "nom"))
        context.update({
            "defi": defi, "jalons": defi.jalons.all(), "gestion": gestion,
            "proprietaire": est_proprietaire(user, defi), "admin": est_admin(user), "equipes": equipes,
            "evenements_publies": defi.evenements.filter(etat=EvenementScenario.Etat.PUBLIE),
        })
        if est_etudiant(user):
            participation = participation_acceptee(user, defi)
            demandes = {p.equipe_id: p for p in ParticipationEquipe.objects.filter(
                defi=defi, etudiant=user, etat=Etat.EN_ATTENTE)}
            for equipe in equipes:
                equipe.ma_demande = demandes.get(equipe.pk)
            context.update({
                "ma_participation": participation,
                "peut_s_inscrire": defi.inscriptions_ouvertes and participation is None,
            })
        if gestion:
            context["problemes_publication"] = (regles.problemes_publication(defi)
                                                if defi.statut == Defi.Statut.BROUILLON else [])
        return context


class FormulaireDefiMixin(EnseignantRequisMixin, LayoutMixin):
    form_class = DefiForm
    template_name = "challenges/form_defi.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs


class CreerDefiView(FormulaireDefiMixin, FormView):
    def form_valid(self, form):
        form.instance.enseignant = self.request.user
        defi = form.save()
        messages.success(self.request, "Défi créé en brouillon. Ajoutez ses jalons puis ouvrez les inscriptions.")
        return redirect("detail-defi", pk=defi.pk)


class ModifierDefiView(FormulaireDefiMixin, FormView):
    def dispatch(self, request, *args, **kwargs):
        if not est_enseignant(request.user):
            raise PermissionDenied
        self.defi = get_defi_gere(request.user, kwargs["pk"])
        if not self.defi.brief_modifiable:
            messages.error(request, "Le défi a commencé : ses exigences ne se modifient plus que par un imprévu publié.")
            return redirect("detail-defi", pk=self.defi.pk)
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.defi
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["defi"] = self.defi
        return context

    def form_valid(self, form):
        try:
            form.save()
        except ValidationError as erreur:
            form.add_error(None, erreur)
            return self.form_invalid(form)
        messages.success(self.request, "Défi enregistré.")
        return redirect("detail-defi", pk=self.defi.pk)


class SupprimerDefiView(ActionView):
    def get_objet(self):
        return get_defi_gere(self.request.user, self.kwargs["pk"])

    def executer(self, defi):
        if not est_proprietaire(self.request.user, defi):
            raise ValidationError("Seul le propriétaire peut supprimer ce défi.")
        regles.supprimer(defi)
        self.supprime = True
        return "Brouillon supprimé."

    def url_retour(self, defi):
        return reverse("catalogue-defis") if getattr(self, "supprime", False) else reverse("detail-defi", args=[defi.pk])


class ChangerStatutDefiView(ActionView):
    MESSAGES = {
        "publier": "Inscriptions ouvertes : le défi est visible des étudiants.",
        "brouillon": "Le défi est redevenu un brouillon.",
        "cloturer": "Défi clôturé : l'historique reste consultable, les équipes ne peuvent plus contribuer.",
        "archiver": "Défi archivé.",
    }

    def get_objet(self):
        user = self.request.user
        if est_admin(user) and self.kwargs["action"] == "archiver":
            return get_object_or_404(Defi, pk=self.kwargs["pk"])  # modération par l'administrateur
        return get_defi_gere(user, self.kwargs["pk"])

    def executer(self, defi):
        action = regles.ACTIONS.get(self.kwargs["action"])
        if action is None:
            raise Http404
        action(defi)
        return self.MESSAGES[self.kwargs["action"]]

    def url_retour(self, defi):
        return reverse("detail-defi", args=[defi.pk])


class DemarrerDefiView(LayoutMixin, TemplateView):
    template_name = "challenges/demarrer.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        defi = get_defi_gere(self.request.user, self.kwargs["pk"])
        retenues, incompletes, attente = regles.bilan_equipes(defi)
        context.update({"defi": defi, "retenues": retenues, "incompletes": incompletes, "attente": attente})
        return context

    def post(self, request, pk):
        defi = get_defi_gere(request.user, pk)
        try:
            regles.demarrer(defi, request.user)
        except ValidationError as erreur:
            messages.error(request, messages_erreur(erreur))
            return redirect("demarrer-defi", pk=pk)
        messages.success(request, "Le défi a démarré. Les équipes incomplètes ont été écartées.")
        return redirect("suivi-defi", pk=pk)


# ---------- Jalons ----------

class JalonFormMixin(LayoutMixin):
    template_name = "challenges/form_jalon.html"
    form_class = JalonForm

    def verifier(self, defi):
        if not defi.brief_modifiable:
            raise PermissionDenied("Le défi a commencé : ses jalons ne se modifient plus que par un imprévu publié.")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["defi"] = self.defi
        return context

    def form_valid(self, form):
        try:
            form.save()
        except ValidationError as erreur:
            form.add_error(None, erreur)
            return self.form_invalid(form)
        messages.success(self.request, "Jalon enregistré.")
        return redirect("detail-defi", pk=self.defi.pk)


class CreerJalonView(JalonFormMixin, FormView):
    def dispatch(self, request, *args, **kwargs):
        self.defi = get_defi_gere(request.user, kwargs["pk"])
        self.verifier(self.defi)
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        suivant = (self.defi.jalons.order_by("-ordre").values_list("ordre", flat=True).first() or 0) + 1
        kwargs["instance"] = Jalon(defi=self.defi, ordre=suivant)
        return kwargs


class ModifierJalonView(JalonFormMixin, FormView):
    def dispatch(self, request, *args, **kwargs):
        self.jalon = get_object_or_404(Jalon.objects.select_related("defi"), pk=kwargs["pk"])
        self.defi = get_defi_gere(request.user, self.jalon.defi_id)
        self.verifier(self.defi)
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.jalon
        return kwargs


class SupprimerJalonView(ActionView):
    def get_objet(self):
        jalon = get_object_or_404(Jalon, pk=self.kwargs["pk"])
        get_defi_gere(self.request.user, jalon.defi_id)
        return jalon

    def executer(self, jalon):
        jalon.delete()
        return "Jalon supprimé."

    def url_retour(self, jalon):
        return reverse("detail-defi", args=[jalon.defi_id])


# ---------- Suivi enseignant ----------

class SuiviDefiView(LayoutMixin, TemplateView):
    """Suivi par l'enseignant (ou l'administrateur, en lecture) : indicateurs propres au défi uniquement."""

    template_name = "challenges/suivi.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        if est_admin(user):
            defi = get_object_or_404(Defi, pk=self.kwargs["pk"])
        else:
            defi = get_defi_gere(user, self.kwargs["pk"])
        evenements = list(defi.evenements.filter(etat=EvenementScenario.Etat.PUBLIE))
        equipes = list(defi.equipes.select_related("coordinateur").annotate(
            nb=Count("participations", filter=Q(participations__etat=Etat.ACCEPTEE), distinct=True),
            attente=Count("participations", filter=Q(participations__etat=Etat.EN_ATTENTE), distinct=True),
        ).order_by("statut", "nom"))
        for equipe in equipes:
            taches = Tache.objects.filter(equipe=equipe).aggregate(
                total=Count("pk"), terminees=Count("pk", filter=Q(statut=Tache.Statut.TERMINE)))
            equipe.taches_total, equipe.taches_terminees = taches["total"], taches["terminees"]
            equipe.progression = round(100 * taches["terminees"] / taches["total"]) if taches["total"] else 0
            versions = VersionLivrable.objects.filter(livrable__equipe=equipe).exclude(etat=VersionLivrable.Etat.BROUILLON)
            equipe.a_relire = versions.filter(etat=VersionLivrable.Etat.SOUMIS).count()
            equipe.acceptes = versions.filter(etat=VersionLivrable.Etat.ACCEPTE).count()
            equipe.retards = versions.filter(en_retard=True).count()
            equipe.membres_liste = equipe.membres()
            reponses = {r.evenement_id: r for r in ReponseEvenement.objects.filter(
                equipe=equipe, etat=ReponseEvenement.Etat.SOUMISE)}
            equipe.reponses = [(e, reponses.get(e.pk)) for e in evenements]
        a_relire = (VersionLivrable.objects.filter(livrable__equipe__defi=defi, etat=VersionLivrable.Etat.SOUMIS)
                    .select_related("livrable__equipe", "livrable__jalon").order_by("date_soumission"))
        context.update({"defi": defi, "equipes": equipes, "evenements": evenements, "a_relire": a_relire,
                        "jalons": defi.jalons.all(), "gestion": peut_gerer_defi(user, defi)})
        return context
