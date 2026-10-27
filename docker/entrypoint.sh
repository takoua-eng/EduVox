#!/bin/sh
# Point d'entrée de l'image EduVox : choisit le processus à lancer.
set -e

case "$1" in
  web)
    # Daphne (ASGI) : pages HTTP + WebSocket de l'assistant vocal
    exec daphne -b 0.0.0.0 -p 8000 --proxy-headers config.asgi:application
    ;;
  worker)
    # Worker Celery : transcription, résumés, e-mails, pré-calcul des modèles IA
    exec celery -A config worker -l info --concurrency="${CELERY_CONCURRENCY:-2}"
    ;;
  migrate)
    # Migrations de la base (lancé avant le site, par Kubernetes ou docker compose)
    exec python manage.py migrate --noinput
    ;;
  *)
    # Toute autre commande, ex. : docker run eduvox python manage.py createsuperuser
    exec "$@"
    ;;
esac