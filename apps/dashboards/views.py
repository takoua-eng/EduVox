from datetime import timedelta

from django.utils import timezone
from django.views.generic import TemplateView

from apps.accounts.models import Utilisateur
from web_project import TemplateLayout


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
            tous = Utilisateur.objects.all()
            il_y_a_7_jours = timezone.now() - timedelta(days=7)
            context.update({
                "stat_total": tous.count(),
                "stat_etudiants": tous.filter(role=Utilisateur.Role.ETUDIANT).count(),
                "stat_enseignants": tous.filter(role=Utilisateur.Role.ENSEIGNANT).count(),
                "stat_admins": tous.filter(role=Utilisateur.Role.ADMIN).count(),
                "stat_nouveaux": tous.filter(date_inscription__gte=il_y_a_7_jours).count(),
                "stat_inactifs": tous.filter(is_active=False).count(),
                "derniers_inscrits": tous.order_by("-date_inscription")[:5],
            })

        return context