"""Réglages des tests EduSprint : base SQLite en mémoire, stockage privé temporaire, aucun appel Gemini réel.

    python manage.py test apps.challenges --settings=apps.challenges.tests.settings_test

La configuration de l'équipe (config/settings.py) reste inchangée et la base commune n'est jamais touchée.
"""

import os
import tempfile

from config.settings import *  # noqa: F401,F403

# La clé lue dans .env ne doit jamais servir pendant les tests : chaque test qui simule Gemini la fournit lui-même
os.environ.pop("GEMINI_API_KEY", None)

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
CELERY_TASK_ALWAYS_EAGER = True
EDUSPRINT_STOCKAGE_PRIVE = tempfile.mkdtemp(prefix="edusprint-tests-")
MEDIA_ROOT = tempfile.mkdtemp(prefix="edusprint-media-")
EDUSPRINT_GEMINI_ESSAIS = 2
