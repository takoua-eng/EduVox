"""Pages du journal des connexions (utilisateur et administrateur)."""

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from django.views.generic import ListView

from web_project import TemplateLayout

from .models import JournalConnexion
from .views import AdminRequiredMixin


class MesConnexionsView(ListView):
    """L'utilisateur voit son propre historique de connexion."""
    template_name = "accounts/mes_connexions.html"
    context_object_name = "connexions"
    paginate_by = 15

    def get_queryset(self):
        return JournalConnexion.objects.filter(utilisateur=self.request.user)

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))


class JournalConnexionsView(AdminRequiredMixin, ListView):
    """L'administrateur voit toutes les connexions, avec filtres et statistiques."""
    template_name = "accounts/journal_connexions.html"
    context_object_name = "connexions"
    paginate_by = 20

    def get_queryset(self):
        qs = JournalConnexion.objects.select_related("utilisateur")
        q = self.request.GET.get("q", "").strip()
        filtre = self.request.GET.get("filtre", "")
        if q:
            qs = qs.filter(Q(nom_saisi__icontains=q) | Q(utilisateur__username__icontains=q) | Q(ip__icontains=q))
        if filtre == "succes":
            qs = qs.filter(succes=True)
        elif filtre == "echec":
            qs = qs.filter(succes=False)
        elif filtre == "suspect":
            qs = qs.filter(suspect=True)
        elif filtre == "visage":
            qs = qs.filter(methode=JournalConnexion.Methode.VISAGE)
        return qs

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        depuis = timezone.now() - timedelta(days=7)
        semaine = JournalConnexion.objects.filter(date__gte=depuis)
        context.update({
            "stat_total": semaine.count(),
            "stat_echecs": semaine.filter(succes=False).count(),
            "stat_suspectes": semaine.filter(suspect=True).count(),
            "stat_visage": semaine.filter(methode=JournalConnexion.Methode.VISAGE, succes=True).count(),
            "q": self.request.GET.get("q", ""),
            "filtre": self.request.GET.get("filtre", ""),
        })
        return context