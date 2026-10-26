# Module 4 — Assistant vocal et illustrations (`apps/assistant`)

Responsable : Nourane Lammouchi · Branche : `module-assistant-vocal`

L'étudiant ouvre une **session d'assistant** (éventuellement liée à un cours), pose ses questions **à l'oral ou à
l'écrit**, reçoit la réponse de Gemini **en direct (streaming WebSocket)** et peut faire **illustrer** une
explication par un diagramme ou une image.

## 1. Fonctionnalités

| Fonction | Où | Détails |
|---|---|---|
| Sessions (CRUD) | `/assistant/` | Créer, lister (recherche, filtres ouvertes / fermées), consulter, renommer / changer de cours, **fermer / rouvrir**, supprimer (avec les fichiers audio et images), **exporter en Markdown** |
| Question écrite | page de session | Envoi par Entrée ; réponse **en streaming** (WebSocket), bouton **Arrêter** (la réponse partielle est conservée) |
| Question orale | bouton micro ou **Alt+M** | `MediaRecorder` (indicateur de niveau sonore, minuteur, Annuler / Échap) → conversion **WAV 16 kHz** dans le navigateur → **Whisper** (tâche Celery) → question **envoyée automatiquement** |
| **Réponses citant le cours (RAG)** | choix du cours | Le cours est découpé en passages (~900 caractères, chevauchants) vectorisés avec `gemini-embedding-001` ; à chaque question, les 4 passages les plus proches (similarité cosinus) sont donnés à Gemini, qui les **cite [1] [2]** ; les extraits cités s'affichent sous la réponse. Index refait si le cours change ; repli sur le résumé si les embeddings échouent ; uniquement pour un cours que l'utilisateur a le droit de voir |
| **Multilingue** | langue de la session (Auto / Français / English / العربية) | En automatique, **Whisper détecte la langue** (restreinte à fr / en / ar) et l'écrit est analysé ; Gemini répond dans la même langue, la voix de synthèse et le sens d'écriture (RTL pour l'arabe) suivent |
| **Fiche de révision** | menu ⋮ → Fiche de révision | Gemini transforme la conversation en fiche structurée (synthèse, notions clés, définitions, exemples, à retenir, questions d'autoévaluation) dans la langue de la conversation ; signalée « à régénérer » si la conversation continue ; impression / PDF depuis le navigateur |
| Historique | page de session | Conversation complète, mémoire des 10 derniers messages envoyée à Gemini, suppression d'un échange |
| Mode de réponse | bascule **Écrit / Parole** en haut de la conversation (ou à la création) | Enregistré par session. En « Parole », chaque phrase est **lue à voix haute dès qu'elle arrive** dans le flux (Web Speech API, voix française du navigateur, Markdown retiré) ; la voix se coupe quand on dicte ou qu'on arrête la réponse |
| Lecture à voix haute | bouton haut-parleur sous une réponse | Relire une réponse à la demande |
| Avis utile / pas utile | sous chaque réponse | Sert de **métrique qualitative** du modèle génératif |
| Diagrammes | panneau Illustrations ou « Illustrer » sous une réponse | Gemini écrit du **Mermaid** (sortie JSON structurée, code validé côté serveur), rendu en SVG dans le navigateur (`securityLevel: strict`) |
| Illustrations | idem | Gemini rédige un prompt anglais + un **texte alternatif**, puis **Hugging Face** (FLUX.1-schnell par défaut) génère l'image (tâche Celery) |
| Export PDF | panneau Illustrations, menu de la session, chaque carte, galerie | PDF A4 généré dans le navigateur (jsPDF) : titre, type, date, visuel et description de chaque illustration ou diagramme |
| Galerie | `/assistant/illustrations/` | Toutes mes illustrations, filtrables par type, exportables en PDF |
| Conversations (admin) | `/assistant/moderation/` | Toutes les sessions (recherche par titre, utilisateur, e-mail ou contenu ; filtres utilisateur et état), consultation et export **en lecture seule** |

> **Pourquoi Mermaid pour les diagrammes ?** Les modèles texte → image dessinent des schémas illisibles (texte
> déformé, flèches incohérentes). Un diagramme Mermaid généré par Gemini est exact, net, modifiable et
> accessible (texte alternatif + code source consultable). Hugging Face reste utilisé pour les illustrations.

## 2. Architecture

```
ai_services/
├── transcription.py   + lire_wav(), transcrire_wav(), transcrire_question() (WAV lu en Python, sans FFmpeg ;
│                        détection de langue restreinte, encodeur Whisper exécuté une seule fois)
└── illustration.py    generer_image()                 (Hugging Face InferenceClient, HF_TOKEN)

apps/assistant/
├── models.py          SessionAssistant, MessageVocal, Illustration, FicheRevision, PassageCours (index RAG)
│                      + JournalIA : journal technique des appels
├── permissions.py     permission « accounts.poser_question » + filtrage par propriétaire (404 sinon)
├── services.py        prompt (langue, passages cités), Gemini (flux / non-flux / JSON), fiche, erreurs, WER
├── rag.py             découpage du cours, index d'embeddings (PassageCours), recherche, citations
├── langues.py         détection de la langue d'un texte, consignes de langue pour Gemini
├── consumers.py       WebSocket : streaming de la réponse, arrêt, contrôle d'accès (code 4403)
├── routing.py         ws/assistant/sessions/<id>/
├── tasks.py           Celery : transcrire_message, generer_illustration, indexer_cours, generer_fiche_revision
├── views.py / urls.py CRUD, question (repli HTTP sans streaming), audio, illustrations, modération
├── management/commands/evaluer_whisper.py   mesure du WER sur un jeu de test
└── tests/             139 tests (SQLite en mémoire, IA simulée)
```

- **ASGI** : `config/asgi.py` route HTTP vers Django et WebSocket vers Channels (`AuthMiddlewareStack` +
  `AllowedHostsOriginValidator`). `daphne` en tête d'`INSTALLED_APPS` : `python manage.py runserver` sert les deux.
- **Repli** : si le WebSocket est indisponible, la page bascule sur `POST /question/` (réponse complète) ; sans
  JavaScript, tous les formulaires fonctionnent (envoi, avis, suppression, illustrations).
- **Asynchronisme** : Whisper et Hugging Face tournent dans des tâches Celery ; la page interroge leur état.
  En développement `CELERY_TASK_ALWAYS_EAGER=True` (pas besoin de Redis) ; en production : un worker Celery + Redis.

## 3. Sécurité et accès

| Règle | Mise en œuvre |
|---|---|
| Rôles | `PermissionRequiredMixin` sur `accounts.poser_question` (Étudiant, Admin) ; l'enseignant reçoit 403 |
| Données privées | Toutes les requêtes filtrent `session__utilisateur=request.user` → **404** pour l'objet d'un autre utilisateur |
| Modération | L'admin (`accounts.gerer_utilisateurs`) lit toutes les conversations dans un espace séparé, **en lecture seule** : aucun formulaire, et les URL d'écriture lui renvoient 404. Les étudiants en sont informés sur la page de l'assistant |
| WebSocket | Même contrôle à la connexion (refus code 4403), vérification de l'origine (anti-CSWSH) |
| Contexte de cours | Un cours non visible (brouillon, masqué) n'est jamais envoyé à Gemini |
| Contrôle de saisie | Mêmes règles côté navigateur (message sous le champ, `aria-invalid`, focus sur l'erreur) et côté serveur (`forms.py`, aussi appliqué au WebSocket) : titre 3–150 caractères, au moins une lettre, **unique par utilisateur** ; question 2–2 000 caractères avec au moins une lettre (compteur affiché près de la limite) ; demande d'illustration 3–500 caractères ; espaces superflus supprimés ; audio : en-tête `RIFF/WAVE` vérifié, 0,5 à 120 s, ≤ 8 Mo |
| Abus / coûts | 30 questions, 10 illustrations et 5 fiches par heure et par utilisateur (configurable) |
| Disponibilité | Quota Gemini gratuit = 20 requêtes / jour / modèle : en cas de quota atteint (429) ou de modèle indisponible (5xx), bascule automatique sur le modèle de secours, y compris en streaming (avant le 1er mot). Une seule tentative côté SDK, pour ne jamais rester bloqué sur le délai d'attente suggéré par l'API |
| XSS | Réponses rendues en Markdown **sans HTML brut** (markdown-it, liens `javascript:` refusés) ; Mermaid en mode strict, code validé |
| Secrets | `GEMINI_API_KEY`, `HF_TOKEN` dans `.env` uniquement |
| CSRF | Tous les POST (formulaires et `fetch`) portent le jeton CSRF |

## 4. Métriques et performance (critère IA 5.2)

Chaque appel IA est journalisé (`JournalIA` : succès, code d'erreur, durée, délai du 1er morceau), consultable
dans l'administration Django (`/admin/` → « Journal des appels IA »), avec les avis utile / pas utile des réponses.

**Mesures réalisées pendant le développement** (CPU, gemini-3.5-flash, whisper-small) :

| Mesure | Avant optimisation | Après | Changement |
|---|---|---|---|
| Gemini : délai du 1er morceau | 5,6 s | **0,8 s** | `thinking_level="minimal"` (réglable : `ASSISTANT_GEMINI_REFLEXION`) |
| Gemini : réponse complète (3 phrases) | 6,1 s | **1,7 s** | idem |
| Diagramme (Gemini JSON) | 7,8 s | **3,0 s** | idem |
| Whisper : WER sur 5 questions (voix de synthèse anglaise lisant du français) | 455 % | **40,8 %** | langue imposée + pas d'horodatage < 30 s (supprime une boucle d'hallucination) |
| Whisper : facteur temps réel | ×7,9 | **×1,4** | idem |
| Whisper : transcription d'une question de 5 s | 6–8 s | **~3 s** | encodeur exécuté une seule fois pour la détection de langue et la transcription |
| Indexation d'un cours de 23 000 caractères | — | **4,0 s** (33 passages) | une fois par cours, en tâche de fond dès la création de la session |

Le WER élevé restant vient de la voix de synthèse (accent anglais). Pour la soutenance, mesurer sur de vraies voix :

```bash
# dossier contenant q1.wav + q1.txt (texte exact prononcé), q2.wav + q2.txt, ...
python manage.py evaluer_whisper chemin/vers/dossier --json resultats.json
```

Gemini étant un modèle génératif, sa qualité est évaluée **qualitativement** (avis utile / pas utile des
étudiants) et par sa **latence**.

## 5. Accessibilité

Structure sémantique (`<nav>` fil d'Ariane, un seul `<h1>`, sections titrées), conversation en `role="log"`,
messages d'état en `aria-live="polite"` (annonce « Réponse reçue » plutôt que chaque mot du flux),
`aria-busy` pendant la génération, boutons icônes avec `aria-label`, bouton micro avec `aria-pressed`,
images avec `alt` généré par l'IA, diagrammes en `role="img"` + `aria-label`, focus visible (contour 3 px),
raccourcis clavier (Entrée, Maj+Entrée, Alt+M), `prefers-reduced-motion` respecté, contrastes AA.

## 6. Configuration (`.env`)

| Variable | Défaut | Rôle |
|---|---|---|
| `GEMINI_API_KEY`, `GEMINI_MODEL` | — | Clé et modèle Gemini (partagés avec les autres modules) |
| `HF_TOKEN` | — | Jeton Hugging Face (lecture + « Inference Providers ») pour les illustrations |
| `HF_IMAGE_MODEL` | `black-forest-labs/FLUX.1-schnell` | Modèle texte → image |
| `WHISPER_MODEL` | `openai/whisper-small` | `openai/whisper-base` est 2 à 3 fois plus rapide sur CPU, un peu moins précis |
| `WHISPER_LANGUE` | `french` | Langue de `transcrire_wav()` (enregistrements > 30 s) ; les questions courtes suivent la langue de la session |
| `ASSISTANT_GEMINI_MODEL` | `GEMINI_MODEL` | Modèle principal propre à l'assistant (sinon celui de l'équipe) |
| `ASSISTANT_GEMINI_MODELE_SECOURS` | `gemini-3.5-flash-lite` | Modèle utilisé automatiquement si le quota du principal est atteint (vide = aucun) |
| `ASSISTANT_EMBEDDING_MODEL` | `gemini-embedding-001` | Modèle d'embedding du RAG |
| `ASSISTANT_FICHES_PAR_HEURE` | 5 | Fiches de révision par heure et par utilisateur |
| `ASSISTANT_GEMINI_REFLEXION` | `minimal` | Niveau de réflexion de Gemini (`minimal`, `low`, `medium`, `high`, vide = défaut du modèle) |
| `ASSISTANT_QUESTIONS_PAR_HEURE` / `ASSISTANT_ILLUSTRATIONS_PAR_HEURE` | 30 / 10 | Limites par utilisateur |
| `ASSISTANT_AUDIO_MAX_S` / `ASSISTANT_AUDIO_MAX_MO` | 120 / 8 | Taille maximale d'une question dictée |

## 7. Tests

```bash
python manage.py test apps.assistant --settings=apps.assistant.tests.settings_sqlite
coverage run --source=apps/assistant --omit="*/tests/*,*/migrations/*" manage.py test apps.assistant --settings=apps.assistant.tests.settings_sqlite
coverage report        # 94 % ; coverage html → htmlcov/index.html
```

Modèles et services (WER, Markdown sûr, validation Mermaid, contexte du cours), accès (anonyme, rôle sans
permission, autre étudiant → 404 sur chaque URL, admin), vues CRUD, transcription et illustrations (Whisper,
Gemini et Hugging Face simulés, erreurs, quotas, Celery injoignable), WebSocket (streaming, refus 4403, erreur
au milieu du flux), commande d'évaluation.

## 8. Déploiement

Le WebSocket exige un serveur **ASGI** (Gunicorn en mode WSGI ne le gère pas) :

```bash
daphne -b 0.0.0.0 -p 8000 config.asgi:application
celery -A config worker -l info          # avec CELERY_TASK_ALWAYS_EAGER=False et Redis
```

Avec plusieurs processus Daphne, remplacer `InMemoryChannelLayer` par `channels_redis` (non nécessaire ici :
l'assistant n'envoie de messages qu'à la connexion qui pose la question).
