"""Outils communs aux vues EduSprint."""

import logging

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View

from web_project import TemplateLayout

from ..permissions import est_enseignant, role

logger = logging.getLogger(__name__)


class LayoutMixin:
    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        context["role"] = role(self.request.user)
        return context


def messages_erreur(erreur):
    return " ".join(erreur.messages) if isinstance(erreur, ValidationError) else str(erreur)


def veut_json(request):
    return "application/json" in request.headers.get("Accept", "")


class ActionView(View):
    """Action de modification : POST uniquement (CSRF actif), objet vérifié, message de succès ou d'erreur."""

    http_method_names = ["post"]

    def get_objet(self):
        raise NotImplementedError

    def executer(self, objet):
        """Renvoie le message de succès ; lève ValidationError pour refuser l'action."""
        raise NotImplementedError

    def url_retour(self, objet):
        raise NotImplementedError

    def post(self, request, *args, **kwargs):
        objet = self.get_objet()
        try:
            message = self.executer(objet)
        except ValidationError as erreur:
            if veut_json(request):
                return JsonResponse({"ok": False, "message": messages_erreur(erreur)}, status=400)
            messages.error(request, messages_erreur(erreur))
        else:
            if veut_json(request):
                return JsonResponse({"ok": True, "message": message})
            if message:
                messages.success(request, message)
        suivant = request.POST.get("suivant")
        if suivant and url_has_allowed_host_and_scheme(suivant, allowed_hosts={request.get_host()},
                                                       require_https=request.is_secure()):
            return redirect(suivant)
        return redirect(self.url_retour(objet))


class EnseignantRequisMixin:
    def dispatch(self, request, *args, **kwargs):
        if not est_enseignant(request.user):
            raise PermissionDenied("Réservé aux enseignants.")
        return super().dispatch(request, *args, **kwargs)


def nom(user):
    if user is None:
        return "Compte supprimé"
    return user.get_full_name() or user.username
