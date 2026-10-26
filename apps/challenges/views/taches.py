"""Tâches de l'équipe (Kanban) et commentaires."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import FormView

from ..forms import CommentaireForm, TacheForm
from ..models import Tache
from ..permissions import (
    get_equipe_accessible,
    peut_contribuer,
    peut_modifier_tache,
    peut_supprimer_tache,
    responsables_autorises,
)
from .base import ActionView, LayoutMixin
from .equipes import EspaceEquipeMixin


def get_tache_accessible(user, pk):
    tache = get_object_or_404(Tache.objects.select_related("equipe__defi", "responsable", "jalon", "cree_par"), pk=pk)
    equipe, acces = get_equipe_accessible(user, tache.equipe_id)  # 404 pour une autre équipe
    return tache, acces


class CreerTacheView(EspaceEquipeMixin, FormView):
    template_name = "challenges/form_tache.html"
    form_class = TacheForm
    onglet = "taches"

    def get(self, request, *args, **kwargs):
        self._verifier()
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        self._verifier()
        return super().post(request, *args, **kwargs)

    def _verifier(self):
        if not peut_contribuer(self.request.user, self.equipe):
            raise PermissionDenied("Votre équipe ne peut plus créer de tâches.")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.update({"equipe": self.equipe, "responsables": responsables_autorises(self.request.user, self.equipe),
                       "instance": Tache(equipe=self.equipe)})
        return kwargs

    def form_valid(self, form):
        form.instance.cree_par = self.request.user
        form.save()
        messages.success(self.request, "Tâche créée.")
        return redirect("taches-equipe", pk=self.equipe.pk)


class ModifierTacheView(LayoutMixin, FormView):
    template_name = "challenges/form_tache.html"
    form_class = TacheForm

    def dispatch(self, request, *args, **kwargs):
        self.tache, self.acces = get_tache_accessible(request.user, kwargs["pk"])
        self.equipe = self.tache.equipe
        if not peut_modifier_tache(request.user, self.tache):
            raise PermissionDenied("Seuls le créateur, le responsable ou le coordinateur modifient cette tâche.")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.update({"equipe": self.equipe, "responsables": responsables_autorises(self.request.user, self.equipe),
                       "instance": self.tache})
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({"equipe": self.equipe, "defi": self.equipe.defi, "tache": self.tache, "onglet": "taches",
                        "membre": True})
        return context

    def form_valid(self, form):
        form.save()
        messages.success(self.request, "Tâche mise à jour.")
        return redirect("detail-tache", pk=self.tache.pk)


class DetailTacheView(LayoutMixin, FormView):
    template_name = "challenges/tache_detail.html"
    form_class = CommentaireForm

    def dispatch(self, request, *args, **kwargs):
        self.tache, self.acces = get_tache_accessible(request.user, kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        equipe = self.tache.equipe
        context.update({
            "tache": self.tache, "equipe": equipe, "defi": equipe.defi, "acces": self.acces, "onglet": "taches",
            "membre": self.acces == "membre", "commentaires": self.tache.commentaires.select_related("auteur"),
            "peut_commenter": peut_contribuer(user, equipe), "peut_modifier": peut_modifier_tache(user, self.tache),
            "peut_supprimer": peut_supprimer_tache(user, self.tache),
            "precedent": self.tache.statut_voisin(-1), "suivant": self.tache.statut_voisin(1),
            "libelles_statuts": dict(Tache.Statut.choices),
        })
        return context

    def form_valid(self, form):
        if not peut_contribuer(self.request.user, self.tache.equipe):
            raise PermissionDenied("Vous ne pouvez pas commenter cette tâche.")
        form.instance.tache = self.tache
        form.instance.auteur = self.request.user
        form.save()
        messages.success(self.request, "Commentaire ajouté.")
        return redirect(reverse("detail-tache", args=[self.tache.pk]) + "#commentaires")


class DeplacerTacheView(ActionView):
    """Change le statut (bouton « précédent / suivant » ou glisser-déposer). Réponse JSON si demandée."""

    def get_objet(self):
        tache, acces = get_tache_accessible(self.request.user, self.kwargs["pk"])
        if acces != "membre":
            raise Http404
        return tache

    def executer(self, tache):
        statut = self.request.POST.get("statut")
        if statut not in Tache.Statut.values:
            raise ValidationError("Statut inconnu.")
        with transaction.atomic():
            tache = Tache.objects.select_for_update(of=("self",)).select_related("equipe__defi").get(pk=tache.pk)
            if not peut_modifier_tache(self.request.user, tache):
                raise ValidationError("Seuls le créateur, le responsable ou le coordinateur font avancer cette tâche.")
            tache.statut = statut
            tache.save(update_fields=["statut", "date_terminee", "date_modification"])
        return f"« {tache.titre} » déplacée dans « {tache.get_statut_display()} »."

    def url_retour(self, tache):
        return reverse("taches-equipe", args=[tache.equipe_id])


class SupprimerTacheView(ActionView):
    def get_objet(self):
        tache, acces = get_tache_accessible(self.request.user, self.kwargs["pk"])
        if acces != "membre":
            raise Http404
        return tache

    def executer(self, tache):
        if not peut_supprimer_tache(self.request.user, tache):
            raise ValidationError("Seul le créateur peut supprimer sa tâche, si aucun autre membre ne l'a commentée.")
        tache.delete()
        return "Tâche supprimée."

    def url_retour(self, tache):
        return reverse("taches-equipe", args=[tache.equipe_id])
