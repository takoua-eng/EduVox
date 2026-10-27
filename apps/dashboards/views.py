from django.views.generic import TemplateView

from apps.accounts.models import Utilisateur
from web_project import TemplateLayout

from .statistiques import stats_admin, stats_enseignant, stats_etudiant


class DashboardsView(TemplateView):
    """Affiche un tableau de bord différent selon le rôle de l'utilisateur."""

    def get_template_names(self):
        user = self.request.user
        if user.is_superuser or user.role == Utilisateur.Role.ADMIN:
            return ["dashboard_admin.html"]
        if user.role == Utilisateur.Role.ENSEIGNANT:
            return ["dashboard_enseignant.html"]
        return ["dashboard_etudiant.html"]

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        user = self.request.user
        if user.is_superuser or user.role == Utilisateur.Role.ADMIN:
            context.update(stats_admin())
        elif user.role == Utilisateur.Role.ENSEIGNANT:
            context.update(stats_enseignant(user))
        else:
            context.update(stats_etudiant(user))
        return context
