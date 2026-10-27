"""Sonde de santé pour Docker et Kubernetes : /sante/

Répond 200 si l'application tourne et que la base de données répond, 503 sinon.
Kubernetes n'envoie le trafic vers un conteneur que lorsqu'elle répond 200.
"""

from django.contrib.auth.decorators import login_not_required
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


@login_not_required
@never_cache
@require_GET
def sante(request):
    try:
        with connection.cursor() as curseur:
            curseur.execute("SELECT 1")
    except Exception:
        return JsonResponse({"statut": "erreur", "base": "indisponible"}, status=503)
    return JsonResponse({"statut": "ok", "base": "ok"})