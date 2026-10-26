"""Équipes : création, adhésions, coordination et espace privé d'équipe (onglets)."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.generic import FormView, TemplateView

from ..forms import AdhesionForm, EquipeForm, RefusForm, TransfertForm
from ..models import Equipe, EvenementScenario, ParticipationEquipe, ReponseEvenement, Tache, VersionLivrable
from ..permissions import (
    est_etudiant,
    get_defi_visible,
    get_equipe_accessible,
    peut_contribuer,
    peut_deposer,
    peut_modifier_tache,
    peut_soumettre,
    versions_visibles,
)
from ..services import equipes as regles
from ..services.gemini import gemini_configure
from .base import ActionView, LayoutMixin, messages_erreur

Etat = ParticipationEquipe.Etat


class CreerEquipeView(LayoutMixin, FormView):
    template_name = "challenges/form_equipe.html"
    form_class = EquipeForm

    def dispatch(self, request, *args, **kwargs):
        if not est_etudiant(request.user):
            raise PermissionDenied("Seuls les étudiants créent des équipes.")
        self.defi = get_defi_visible(request.user, kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["defi"] = self.defi
        return context

    def form_valid(self, form):
        try:
            equipe = regles.creer_equipe(self.defi, self.request.user, form.cleaned_data["nom"],
                                         form.cleaned_data["description"])
        except ValidationError as erreur:
            form.add_error(None, messages_erreur(erreur))
            return self.form_invalid(form)
        messages.success(self.request, f"Équipe « {equipe.nom} » créée : vous en êtes le coordinateur.")
        return redirect("espace-equipe", pk=equipe.pk)


class ModifierEquipeView(LayoutMixin, FormView):
    template_name = "challenges/form_equipe.html"
    form_class = EquipeForm

    def dispatch(self, request, *args, **kwargs):
        self.equipe, acces = get_equipe_accessible(request.user, kwargs["pk"])
        if not (peut_contribuer(request.user, self.equipe) and self.equipe.est_coordinateur(request.user)):
            raise PermissionDenied("Seul le coordinateur modifie la présentation de l'équipe.")
        self.defi = self.equipe.defi
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.equipe
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({"defi": self.defi, "equipe": self.equipe})
        return context

    def form_valid(self, form):
        nom = form.cleaned_data["nom"].strip()
        if self.defi.equipes.filter(nom__iexact=nom).exclude(pk=self.equipe.pk).exists():
            form.add_error("nom", "Une équipe porte déjà ce nom dans ce défi.")
            return self.form_invalid(form)
        form.save()
        messages.success(self.request, "Équipe mise à jour.")
        return redirect("espace-equipe", pk=self.equipe.pk)


# ---------- Adhésions ----------

class RejoindreEquipeView(ActionView):
    def get_objet(self):
        equipe = get_object_or_404(Equipe.objects.select_related("defi"), pk=self.kwargs["pk"])
        get_defi_visible(self.request.user, equipe.defi_id)
        return equipe

    def executer(self, equipe):
        form = AdhesionForm(self.request.POST)
        if not form.is_valid():
            raise ValidationError("Message trop long (500 caractères maximum).")
        regles.demander_adhesion(equipe, self.request.user, form.cleaned_data["message"])
        return f"Demande envoyée au coordinateur de « {equipe.nom} »."

    def url_retour(self, equipe):
        return reverse("detail-defi", args=[equipe.defi_id])


class ParticipationActionView(ActionView):
    def get_objet(self):
        return get_object_or_404(ParticipationEquipe.objects.select_related("equipe__defi", "etudiant"),
                                 pk=self.kwargs["pk"])

    def url_retour(self, participation):
        if participation.etudiant_id == self.request.user.pk and participation.etat != Etat.ACCEPTEE:
            return reverse("detail-defi", args=[participation.defi_id])
        return reverse("espace-equipe", args=[participation.equipe_id])


class RetirerDemandeView(ParticipationActionView):
    def get_objet(self):
        participation = super().get_objet()
        if participation.etudiant_id != self.request.user.pk:
            raise Http404
        return participation

    def executer(self, participation):
        regles.retirer_demande(participation, self.request.user)
        participation.etat = Etat.RETIREE
        return "Demande retirée."


class CoordinationParticipationMixin:
    def get_objet(self):
        participation = super().get_objet()
        if not participation.equipe.est_coordinateur(self.request.user):
            raise Http404
        return participation


class AccepterDemandeView(CoordinationParticipationMixin, ParticipationActionView):
    def executer(self, participation):
        regles.accepter_demande(participation, self.request.user)
        return f"{participation.etudiant.get_full_name() or participation.etudiant.username} a rejoint l'équipe."


class RefuserDemandeView(CoordinationParticipationMixin, ParticipationActionView):
    def executer(self, participation):
        form = RefusForm(self.request.POST)
        motif = form.cleaned_data["motif"] if form.is_valid() else ""
        regles.refuser_demande(participation, self.request.user, motif)
        return "Demande refusée."


class ExclureMembreView(CoordinationParticipationMixin, ParticipationActionView):
    def executer(self, participation):
        regles.exclure_membre(participation, self.request.user)
        return "Membre retiré de l'équipe. Ses contributions sont conservées."

    def url_retour(self, participation):
        return reverse("espace-equipe", args=[participation.equipe_id])


class EquipeActionView(ActionView):
    def get_objet(self):
        equipe, acces = get_equipe_accessible(self.request.user, self.kwargs["pk"])
        if acces != "membre":
            raise Http404
        return equipe

    def url_retour(self, equipe):
        return reverse("espace-equipe", args=[equipe.pk])


class QuitterEquipeView(EquipeActionView):
    def executer(self, equipe):
        regles.quitter_equipe(equipe, self.request.user)
        self.parti = True
        return f"Vous avez quitté « {equipe.nom} »."

    def url_retour(self, equipe):
        return reverse("detail-defi", args=[equipe.defi_id]) if getattr(self, "parti", False) else super().url_retour(equipe)


class TransfererCoordinationView(EquipeActionView):
    def executer(self, equipe):
        form = TransfertForm(self.request.POST, equipe=equipe, user=self.request.user)
        if not form.is_valid():
            raise ValidationError("Choisissez un membre accepté de l'équipe.")
        regles.transferer_coordination(equipe, self.request.user, form.cleaned_data["nouveau"])
        return "Coordination transférée."


class ReprendreCoordinationView(EquipeActionView):
    def executer(self, equipe):
        regles.reprendre_coordination(equipe, self.request.user)
        return "Vous coordonnez désormais l'équipe."


# ---------- Espace d'équipe ----------

class EspaceEquipeMixin(LayoutMixin):
    onglet = "vue"

    def dispatch(self, request, *args, **kwargs):
        self.equipe, self.acces = get_equipe_accessible(request.user, kwargs["pk"])
        self.defi = self.equipe.defi
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context.update({
            "equipe": self.equipe, "defi": self.defi, "acces": self.acces, "onglet": self.onglet,
            "membre": self.acces == "membre", "coordinateur": self.equipe.est_coordinateur(user),
            "peut_contribuer": peut_contribuer(user, self.equipe), "peut_deposer": peut_deposer(user, self.equipe),
            "peut_soumettre": peut_soumettre(user, self.equipe), "ia_disponible": gemini_configure(),
            "membres": self.equipe.membres(),
        })
        return context


class EspaceEquipeView(EspaceEquipeMixin, TemplateView):
    template_name = "challenges/equipe_vue.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        equipe = self.equipe
        compte = dict(equipe.taches.values_list("statut").annotate(n=Count("pk")))
        total = sum(compte.values())
        maintenant = timezone.now()
        livrables = list(equipe.livrables.select_related("jalon"))
        for livrable in livrables:
            visibles = versions_visibles(self.request.user, self.acces, livrable)
            livrable.derniere = visibles.order_by("-numero").first()
        context.update({
            "colonnes_compte": [(s, l, compte.get(s, 0)) for s, l in Tache.Statut.choices],
            "taches_total": total, "taches_terminees": compte.get(Tache.Statut.TERMINE, 0),
            "progression": round(100 * compte.get(Tache.Statut.TERMINE, 0) / total) if total else 0,
            "taches_en_retard": equipe.taches.exclude(statut=Tache.Statut.TERMINE).filter(
                echeance__lt=timezone.localdate()).count(),
            "prochain_jalon": self.defi.jalons.filter(echeance__gte=maintenant).first(),
            "livrables": livrables,
            "imprevus_sans_reponse": EvenementScenario.objects.filter(
                defi=self.defi, etat=EvenementScenario.Etat.PUBLIE).exclude(pk__in=ReponseEvenement.objects.filter(
                    equipe=equipe, etat=ReponseEvenement.Etat.SOUMISE).values("evenement_id")).count(),
            "demandes": (equipe.participations.filter(etat=Etat.EN_ATTENTE).select_related("etudiant")
                         if context["coordinateur"] else []),
            "transfert_form": TransfertForm(equipe=equipe, user=self.request.user) if context["coordinateur"] else None,
        })
        return context


class TachesEquipeView(EspaceEquipeMixin, TemplateView):
    """Kanban à quatre colonnes ; boutons de déplacement accessibles + glisser-déposer facultatif."""

    template_name = "challenges/equipe_taches.html"
    onglet = "taches"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        filtre = self.request.GET.get("filtre", "")
        taches = (self.equipe.taches.select_related("responsable", "jalon", "cree_par")
                  .annotate(nb_commentaires=Count("commentaires")))
        if filtre == "moi":
            taches = taches.filter(responsable=user)
        elif filtre == "non_affectees":
            taches = taches.filter(responsable__isnull=True)
        taches = list(taches)
        for tache in taches:
            tache.modifiable = peut_modifier_tache(user, tache)
            tache.precedent = tache.statut_voisin(-1)
            tache.suivant = tache.statut_voisin(1)
        libelles = dict(Tache.Statut.choices)
        context.update({
            "colonnes": [(s, libelles[s], [t for t in taches if t.statut == s]) for s in Tache.ORDRE_STATUTS],
            "libelles_statuts": libelles, "filtre": filtre,
        })
        return context


class JalonsEquipeView(EspaceEquipeMixin, TemplateView):
    template_name = "challenges/equipe_jalons.html"
    onglet = "jalons"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        jalons = list(self.defi.jalons.all())
        for jalon in jalons:
            stats = self.equipe.taches.filter(jalon=jalon).aggregate(
                total=Count("pk"), terminees=Count("pk", filter=Q(statut=Tache.Statut.TERMINE)))
            jalon.taches_total, jalon.taches_terminees = stats["total"], stats["terminees"]
            jalon.livrables_equipe = []
            for livrable in self.equipe.livrables.filter(jalon=jalon):
                livrable.derniere = versions_visibles(self.request.user, self.acces, livrable).order_by("-numero").first()
                jalon.livrables_equipe.append(livrable)
        context["jalons"] = jalons
        return context


class ImprevusEquipeView(EspaceEquipeMixin, TemplateView):
    template_name = "challenges/equipe_imprevus.html"
    onglet = "imprevus"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        evenements = list(self.defi.evenements.filter(etat=EvenementScenario.Etat.PUBLIE).order_by("-date_publication"))
        reponses = {r.evenement_id: r for r in ReponseEvenement.objects.filter(equipe=self.equipe)}
        for evenement in evenements:
            reponse = reponses.get(evenement.pk)
            if reponse and self.acces != "membre" and reponse.etat != ReponseEvenement.Etat.SOUMISE:
                reponse = None  # le brouillon de l'équipe reste privé
            evenement.reponse_equipe = reponse
        context["evenements"] = evenements
        return context


class LivrablesEquipeView(EspaceEquipeMixin, TemplateView):
    template_name = "challenges/equipe_livrables.html"
    onglet = "livrables"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        livrables = []
        for livrable in self.equipe.livrables.select_related("jalon", "cree_par"):
            versions = list(versions_visibles(self.request.user, self.acces, livrable).order_by("-numero"))
            if not versions:
                continue  # livrable encore en brouillon : invisible hors de l'équipe
            livrable.versions_visibles = versions
            livrable.derniere = versions[0]
            livrables.append(livrable)
        context.update({"livrables": livrables, "etats": VersionLivrable.Etat})
        return context

