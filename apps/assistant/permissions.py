"""Règles d'accès du module assistant.

- Poser des questions : permission « accounts.poser_question » (rôles Étudiant et Administrateur, cf. roles.py).
- Chaque utilisateur ne voit et ne modifie QUE ses propres sessions, messages et illustrations : un objet
  d'un autre utilisateur renvoie 404 (on ne révèle pas son existence).
- Modération : l'administrateur (permission « accounts.gerer_utilisateurs ») consulte toutes les conversations
  en LECTURE SEULE, dans un espace dédié (/assistant/moderation/) ; il ne peut ni y écrire ni les modifier.
  Les étudiants en sont informés sur la page de l'assistant.
"""

from django.contrib.auth.mixins import PermissionRequiredMixin
from django.shortcuts import get_object_or_404

from apps.courses.permissions import cours_visibles

from .models import Illustration, MessageVocal, SessionAssistant

PERMISSION_ASSISTANT = "accounts.poser_question"
PERMISSION_MODERATION = "accounts.gerer_utilisateurs"


def peut_utiliser(user):
    return user.is_authenticated and user.has_perm(PERMISSION_ASSISTANT)


def sessions_de(user):
    return SessionAssistant.objects.filter(utilisateur=user)


def cours_utilisables(user):
    """Cours qu'on peut donner comme contexte : ceux que l'utilisateur a le droit de consulter."""
    return cours_visibles(user)


class AssistantRequisMixin(PermissionRequiredMixin):
    permission_required = PERMISSION_ASSISTANT

    def get_session(self, pk=None):
        return get_object_or_404(sessions_de(self.request.user).select_related("cours"), pk=pk or self.kwargs["pk"])

    def get_message(self, pk=None):
        return get_object_or_404(MessageVocal.objects.filter(session__utilisateur=self.request.user)
                                 .select_related("session"), pk=pk or self.kwargs["pk"])

    def get_illustration(self, pk=None):
        return get_object_or_404(Illustration.objects.filter(session__utilisateur=self.request.user)
                                 .select_related("session"), pk=pk or self.kwargs["pk"])


class ModerationRequisMixin(PermissionRequiredMixin):
    """Consultation en lecture seule de toutes les conversations (administrateur)."""

    permission_required = PERMISSION_MODERATION

    def get_session(self, pk=None):
        return get_object_or_404(SessionAssistant.objects.select_related("cours", "utilisateur"),
                                 pk=pk or self.kwargs["pk"])
