# =====================================================================
# EduVox : une seule image pour le site (Daphne, HTTP + WebSocket)
# et pour le worker Celery (traitements IA en arrière-plan).
# La commande lancée est choisie au démarrage : web | worker | migrate
# =====================================================================

# =========================================================
# Étape 1 : fichiers du thème Materio (CSS/JS « vendor »)
# Ils ne sont pas dans Git : on les construit ici avec Node.js
# =========================================================
FROM node:22-alpine AS assets
WORKDIR /src

# Les dépendances d'abord : couche réutilisée tant que package*.json ne change pas
COPY src/package.json src/package-lock.json ./
RUN npm ci --no-audit --no-fund --legacy-peer-deps

COPY src/ ./
RUN npm run build:prod && test -f assets/vendor/css/core.css

# =========================================================
# Étape 2 : application Django
# =========================================================
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/opt/huggingface \
    TZ=Africa/Tunis

# ffmpeg : lecture des fichiers audio par Whisper (cours audio, questions vocales)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

# PyTorch CPU d'abord (même version que requirements.txt) : image bien plus légère que la version CUDA
RUN --mount=type=cache,target=/root/.cache/pip \
    TORCH="$(grep -iE '^torch==' requirements.txt)" \
    && (pip install "$TORCH" --index-url https://download.pytorch.org/whl/cpu \
        || pip install "$TORCH")   # secours : version standard de PyPI (plus lourde)

# Autres dépendances Python, adaptées à Linux :
#  - pywin32 n'existe que sous Windows
#  - OpenCV « headless » : pas besoin d'interface graphique sur un serveur
RUN --mount=type=cache,target=/root/.cache/pip \
    grep -viE '^(torch|pywin32)==' requirements.txt \
      | sed -E 's/^opencv-python==/opencv-python-headless==/' > /tmp/requirements-linux.txt \
    && pip install -r /tmp/requirements-linux.txt

# Modèles d'IA téléchargés pendant la construction : le conteneur démarre sans attendre Internet
#  - reconnaissance faciale OpenCV (YuNet + SFace, ~39 Mo)
#  - Whisper (transcription), désactivable : --build-arg PRECHARGER_WHISPER=0
ARG PRECHARGER_WHISPER=1
ARG WHISPER_MODEL=openai/whisper-small
COPY ai_services/ ai_services/
RUN python -c "from ai_services.visage import telecharger_modeles; telecharger_modeles()" \
    && if [ "$PRECHARGER_WHISPER" = "1" ]; then \
         python -c "from transformers import pipeline; pipeline('automatic-speech-recognition', model='$WHISPER_MODEL')"; \
       fi

# Code de l'application + fichiers du thème construits à l'étape 1
COPY . .
COPY --from=assets /src/assets/vendor/ src/assets/vendor/

# Fichiers statiques rassemblés et compressés (servis par WhiteNoise)
RUN DEBUG=False SECRET_KEY=construction-de-l-image python manage.py collectstatic --noinput -v 0

# Utilisateur sans privilèges ; dossiers d'écriture montés sur des volumes en production
RUN useradd --uid 1000 --create-home eduvox \
    && mkdir -p media stockage_prive .cache_modeles_ia \
    && chown -R eduvox:eduvox /app \
    && sed -i "s/\r$//" docker/entrypoint.sh && chmod +x docker/entrypoint.sh
USER eduvox

EXPOSE 8000
ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["web"]