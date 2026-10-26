"""Réglages pour lancer les tests sans PostgreSQL (base SQLite en mémoire) :

    python manage.py test apps.assistant --settings=apps.assistant.tests.settings_sqlite

Ce fichier ne sert qu'aux tests : la configuration de l'équipe (config/settings.py) reste inchangée.
"""

from config.settings import *  # noqa: F401,F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
CELERY_TASK_ALWAYS_EAGER = True
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
