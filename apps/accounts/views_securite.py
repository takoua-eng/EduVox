"""Pages du journal des connexions (utilisateur et administrateur) et des performances de l'IA."""

import logging
from datetime import timedelta

from django.contrib import messages
from django.db.models import Avg, Count, Max, Q
from django.shortcuts import redirect
from django.utils import timezone
from django.views.generic import ListView, TemplateView

from web_project import TemplateLayout

from .models import EvaluationIA, JournalConnexion
from .tasks import lancer_evaluation_ia
from .views import AdminRequiredMixin

logger = logging.getLogger(__name__)


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


class PerformancesIAView(AdminRequiredMixin, TemplateView):
    """Métriques des deux IA (précision, rappel, F1, matrices de confusion, temps de réponse)."""
    template_name = "accounts/performances_ia.html"

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        dernieres = {t: EvaluationIA.objects.filter(type=t).first() for t in EvaluationIA.Type.values}
        temps = dernieres[EvaluationIA.Type.TEMPS]
        context.update({
            "visage_images": dernieres[EvaluationIA.Type.VISAGE_IMAGES],
            "visage_base": dernieres[EvaluationIA.Type.VISAGE_BASE],
            "connexions": dernieres[EvaluationIA.Type.CONNEXIONS],
            "temps": temps,
            "temps_visage": (temps.resultats.get("detection_encodage") or {}).get("moyenne") if temps else None,
            "temps_risque": (temps.resultats.get("evaluer_risque_apres_precalcul")
                             or temps.resultats.get("evaluer_risque") or {}).get("moyenne") if temps else None,
            "temps_reels": JournalConnexion.objects.filter(temps_ia_ms__isnull=False)
                .values("methode", "succes").annotate(nb=Count("id"), moyenne=Avg("temps_ia_ms"), maximum=Max("temps_ia_ms"))
                .order_by("methode", "-succes"),
            "historique": EvaluationIA.objects.all()[:10],
        })
        return context

    def post(self, request):
        try:
            lancer_evaluation_ia.delay()
        except Exception:
            logger.exception("Évaluation de l'IA non envoyée à Celery")
            messages.error(request, "Le service de traitement est injoignable (Celery / Redis). Réessayez plus tard.")
        else:
            messages.success(request, "Évaluation lancée. Les nouveaux résultats s'affichent ci-dessous dès qu'elle est "
                                      "terminée (actualisez la page si besoin).")
        return redirect("performances-ia")
