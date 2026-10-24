import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("eduvox")
# Toutes les options Celery sont lues dans settings.py avec le préfixe CELERY_
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
