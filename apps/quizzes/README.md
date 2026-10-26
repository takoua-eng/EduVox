# Module 3 — Gestion des quiz et de l'évaluation (`apps/quizzes`)

Responsable : Wale Rezgui · Branche : `wale/quizzes`

L'enseignant crée un quiz à la main ou en fait générer un **brouillon** par Gemini à partir du texte
d'un de ses cours (ou de son résumé). Il le relit, le corrige, puis le **publie lui-même**. L'étudiant
passe le quiz ; QCM et Vrai/Faux sont notés par le serveur, les questions ouvertes par Gemini
(note + explication), et l'enseignant peut réviser chaque note.

## 0. L'IA au cœur du module (version 2)

| Pour qui | Fonction | Où | Garde-fous |
|---|---|---|---|
| Enseignant | **Génération** d'un brouillon depuis le cours ou le résumé | `/quiz/generer/` | Schéma JSON + revalidation serveur, jamais publié automatiquement |
| Enseignant | **Audit qualité** : score /100, problèmes par question (ambiguïté, corrigé faux, plusieurs bonnes réponses, hors cours), suggestions | page du quiz → « Lancer l'audit IA » | Tâche Celery, numéros de question vérifiés, score borné 0–100 |
| Enseignant | **Analyse de la classe** : idées fausses fréquentes + plan de remédiation | Analytics → « Analyser la classe » | Statistiques **anonymisées** (aucun nom, aucun e-mail envoyé) |
| Étudiant | **Indice** pendant le quiz (3 par tentative) | bouton « Demander un indice » (touche `I`) | L'indice qui contient la bonne réponse est **bloqué côté serveur** ; question aidée = 75 % des points |
| Étudiant | **Correction** des réponses rédigées | automatique à la soumission | Note bornée au barème, révisable par l'enseignant |
| Étudiant | **« Pourquoi ma réponse est fausse ? »** | page résultat, sur chaque erreur | Seulement si le corrigé est visible ; mise en cache (un seul appel) |
| Étudiant | **Bilan personnalisé** : synthèse, points forts, notions à revoir + conseils, prochaine étape | page résultat | Généré une fois la tentative entièrement corrigée ; relance possible en cas d'échec |
| Admin | **Pilotage** : activité 30 jours, distribution des scores, performance mesurée de l'IA (succès, latence médiane/p95) | `/quiz/pilotage/` | Lecture seule |

**Analytics enseignant (sans IA)** — psychométrie classique calculée par `services/statistiques.py` :
indice de difficulté *p*, indice de discrimination *D* (groupes 27 %), analyse des distracteurs, fiabilité
α de Cronbach / KR-20, distribution des scores, alertes (« distracteur plus choisi que la bonne réponse »,
« discrimination négative : vérifiez le corrigé »…).

**Expérience étudiant** — lecteur une question par écran, barre de progression, navigateur de questions
(répondue / sans réponse / à revoir), raccourcis clavier (`1`–`6`, `←` `→`, `M`, `I`), sauvegarde
automatique locale, chronomètre, écran de vérification avant l'envoi ; page « Ma progression » avec courbe
et tendance. Tout fonctionne aussi sans JavaScript (formulaire classique).


### Fonctionnalités avancées (version 3)

| Fonctionnalité | Pour qui | Où | Fonctionnement et garde-fous |
|---|---|---|---|
| **Carnet de révision espacée** (méthode Leitner) | Étudiant | `/quiz/revision/` | Chaque QCM / Vrai-Faux raté (ou sans réponse) devient une fiche, en boîte 1 pour le lendemain. Réussie : boîte suivante, intervalle 1 → 2 → 4 → 8 → 16 jours ; ratée : retour en boîte 1 ; réussie en boîte 5 : notion maîtrisée. Rien n'entre dans le carnet si l'enseignant masque le corrigé. |
| **Import intelligent de questions** | Enseignant | page du quiz → « Importer (IA) » | Texte collé (15 000 caractères max) → Gemini structure les questions sans rien inventer → revalidation serveur → **aperçu** avec cases à cocher → ajout atomique. Une bonne réponse que l'IA a dû deviner est signalée « à vérifier ». |
| **Intégrité des évaluations** | Enseignant, admin | Analytics, page de tentative, Pilotage | Ordre des choix QCM propre à chaque tentative (graine aléatoire, stable au rechargement). Compteurs de sorties de page et de textes collés (aucun contenu envoyé ; un compteur ne peut que croître ; valeurs bornées). Réponses anormalement rapides détectées. Affiché comme « à vérifier », jamais comme une preuve ; l'étudiant en est informé avant de commencer. |
| **Certificat vérifiable + export** | Étudiant, enseignant, admin | page résultat → « Mon certificat (PDF) » ; Analytics → « Exporter (CSV) » | Certificat PDF dès 70 % avec QR code vers `/quiz/certificat/<jeton>/` (page publique). Le jeton est signé avec `django.core.signing` et contient le score : un lien modifié, un faux certificat ou une note révisée depuis sont refusés. Export CSV compatible Excel (BOM UTF-8, séparateur « ; »). |

---

## 1. Règles d'évaluation

| Type | Règle |
|---|---|
| QCM | 2 à 6 choix, **une seule bonne réponse** (boutons radio). Juste = tous les points, sinon 0. |
| Vrai / Faux | Exactement les choix « Vrai » et « Faux », un seul correct. |
| Question ouverte | Réponse attendue / barème obligatoire. Note IA entre 0 et le barème, révisable par l'enseignant. |
| Sans réponse | 0 point, aucun appel IA. |

- **Score calculé côté serveur uniquement** : le navigateur n'envoie que des identifiants de choix et du texte. Tout autre champ (`score`, `points`…) est ignoré.
- **Choix vérifiés** : un choix qui n'appartient pas à la question (ou au quiz) fait refuser la soumission (HTTP 400). Une question d'un autre quiz est ignorée.
- **Bonnes réponses jamais envoyées pendant une tentative** : la vue construit une liste sans `est_correct` ni barème (`questions_sans_corrige`).
- **Double soumission** : verrou `select_for_update` sur la tentative + contrôle de son état ; la 2ᵉ soumission ne change rien. Chaque réponse ouverte est « réservée » (`EN_ATTENTE → EN_COURS`) avant l'appel IA : jamais deux corrections pour la même réponse.
- **IA indisponible ≠ réponse fausse** : en cas d'échec, la réponse est conservée, `points_obtenus` reste vide, l'état affiché est « Correction IA indisponible – en attente ». Le score est alors **provisoire** (somme des points déjà attribués) et devient **final** quand tout est noté.
- **Tentatives** : `tentatives_max` (1 à 10), une seule tentative en cours à la fois (contrainte en base).

## 2. Cycle de vie d'un quiz et protection de l'historique

```
brouillon ──(publier, après validation)──▶ publié ──▶ archivé
    ▲              │ (sans tentative)          │
    └──────────────┘ repasser en brouillon     └─ « nouvelle version » : copie en brouillon
```

- Questions et réglages ne sont modifiables qu'en **brouillon sans aucune tentative**.
- Dès qu'un étudiant a commencé le quiz, il est **verrouillé** : pour le corriger, l'enseignant crée une **nouvelle version** (copie complète en brouillon). L'ancienne version reste proposée jusqu'à la publication de la nouvelle, puis est archivée automatiquement. Les tentatives restent liées aux questions et choix d'origine : leurs scores ne changent jamais.
- **Suppression** : seulement pour un brouillon jamais passé. Sinon → **archivage** (plus proposé aux étudiants, résultats conservés). En base, `RESTRICT` empêche aussi de supprimer une question ou un choix déjà utilisé dans une réponse.
- La publication est refusée tant que le quiz est incohérent (aucune question, QCM sans bonne réponse, Vrai/Faux mal formé, question ouverte sans barème, génération en cours…). **La génération IA ne publie jamais.**
- Si le cours est supprimé par le module 2, le quiz et ses résultats sont conservés (`cours` → `NULL`, titre du cours mémorisé dans `titre_cours`).

## 3. Architecture

```
apps/quizzes/
├── models.py          Quiz, Question, Choix, TentativeQuiz, ReponseEtudiant (+ AppelIA : journal technique)
├── permissions.py     règles d'accès (réutilise apps.courses.permissions et les rôles d'accounts)
├── forms.py           formulaires accessibles (aria-describedby / aria-invalid), formset des choix
├── views.py           vues (CBV), toutes les modifications en POST + CSRF
├── urls.py            préfixe /quiz/
├── tasks.py           tâches Celery : generer_quiz_ia, corriger_tentative (idempotentes)
├── services/
│   ├── gemini.py      appel Gemini JSON : délai max, reprises limitées, erreurs traduites
│   ├── generation.py  prompt, schéma JSON, validation serveur des questions générées
│   ├── correction.py  prompt et validation de la note des questions ouvertes
│   ├── evaluation.py  soumission, calcul du score, nouvelle version, publication
│   └── reglages.py    réglages QUIZ_* (settings → variables d'environnement → défaut)
├── management/commands/stats_ia_quiz.py   métriques IA mesurées
├── templates/quizzes/   pages + _styles.html (styles du module : le .gitignore commun ignore les dossiers static/ des apps)
└── tests/             136 tests (Gemini toujours simulé) + settings_sqlite.py
```

| Classe | Rôle et contraintes principales |
|---|---|
| `Quiz` | `cours` (FK `courses.Cours`, `SET_NULL`), `enseignant` (FK `accounts.Enseignant`), statut, origine (manuel/IA), difficulté, `tentatives_max` (CHECK 1–10), `afficher_corrige`, état de génération, `version` / `version_precedente`. |
| `Question` | `ordre` (UNIQUE par quiz), type, énoncé, `points` (CHECK 0,25–20), explication, réponse attendue. |
| `Choix` | texte, `est_correct`, ordre. |
| `TentativeQuiz` | étudiant, numéro (UNIQUE quiz+étudiant+numéro), état (en cours / soumise / corrigée), `score` et `score_max` calculés par le serveur ; UNIQUE partiel « une tentative en cours ». |
| `ReponseEtudiant` | choix ou texte, `points_obtenus` (vide = en attente), `points_ia` (note IA conservée), état de correction, explication, trace IA (modèle, durée, essais), `corrige_par`. UNIQUE (tentative, question). |
| `AppelIA` | journal technique des appels Gemini (type, succès, code d'erreur, durée, essais). **Ni prompt, ni réponse d'étudiant, ni clé.** Sert à la limite de débit et aux métriques. |

### Intégration avec les autres modules (sans les modifier)

- **accounts** : `Utilisateur.role` et le profil `Enseignant`. Fonctions `est_admin / est_enseignant / est_etudiant` réutilisées depuis `apps.courses.permissions`.
- **courses** : un enseignant ne rattache un quiz qu'à **ses** cours (`cours_visibles`). Un étudiant ne voit un quiz que s'il est publié **et** si son cours est public (`cours_publics()` : publié, non masqué, non archivé). Le texte vient de `Transcription.texte`, de `Resume.texte`, ou (PDF sans transcription) d'une lecture du PDF avec `ai_services.extraire_texte_pdf` — **en lecture seule**, rien n'est écrit dans les modèles du module cours.
- **ai_services** : `ai_services.appeler_gemini()` renvoie du texte libre, sans délai maximum ni distinction des erreurs. Pour ne pas modifier ce service partagé, `services/gemini.py` utilise les **mêmes variables** (`GEMINI_API_KEY`, `GEMINI_MODEL`) et le **même modèle par défaut** (`ai_services.gemini.MODELE_PAR_DEFAUT`) avec le SDK `google-genai` déjà présent (`response_mime_type="application/json"`, `response_json_schema`, `HttpOptions(timeout=…)` en millisecondes — vérifié sur la version 2.33.0 installée). *Proposition pour l'équipe* : ajouter ces options au service partagé, puis faire pointer ce module dessus.
- **Celery** : réutilise `config/celery.py` (autodécouverte de `apps.quizzes.tasks`), comme le module cours.

## 4. IA Gemini

### Génération
1. L'enseignant choisit un de ses cours, la source (contenu complet ou résumé), le nombre de questions (1–15), les types et la difficulté.
2. Un quiz **brouillon** est créé avec l'état « génération en cours », puis la tâche Celery est envoyée après validation de la transaction.
3. Le texte (limité à `QUIZ_TEXTE_MAX_CARACTERES`) est placé entre balises `<cours>…</cours>` ; l'instruction système précise que c'est une **donnée** dont il ne faut suivre aucune instruction (protection contre l'injection de prompt). Les balises fermantes présentes dans le texte sont neutralisées.
4. Gemini répond en JSON suivant un schéma explicite (`SCHEMA_QUIZ`). Le serveur **revalide chaque question** (type demandé, énoncé 10–1000 caractères, points 0,25–20, booléens stricts, 2–6 choix uniques et une seule bonne réponse pour un QCM, Vrai/Faux normalisé, barème pour les questions ouvertes). Les questions invalides sont écartées et comptées ; s'il n'en reste aucune, la génération échoue proprement.
5. Les questions valides sont enregistrées **en une transaction** (verrou sur le quiz : une relance ne crée pas de doublons). La page détail interroge l'état toutes les 3 s (`role="status"`, `aria-live`).

### Correction des questions ouvertes
- Seuls l'énoncé, la réponse attendue/barème, le maximum et la réponse sont envoyés — **aucune donnée sur l'étudiant**. La réponse est entre balises `<reponse>` (une demande « donne-moi 20/20 » est une donnée).
- Sortie JSON `{note, explication}` ; la note doit être un nombre (pas un booléen ni une chaîne) compris entre 0 et le barème, sinon elle est **refusée** (pas de troncature arbitraire) et la réponse reste en attente.
- Affichée comme « Correction assistée par IA (révisable par l'enseignant) ». La note IA est conservée dans `points_ia` même après révision.

### Erreurs, délais, limites
| Situation | Comportement |
|---|---|
| Clé absente | Génération refusée avec un message clair ; CRUD manuel et tests fonctionnent ; les réponses ouvertes attendent l'enseignant. **Aucun faux résultat IA.** |
| Délai dépassé, réseau, erreur 5xx, JSON illisible | Reprise limitée (`QUIZ_GEMINI_ESSAIS`, 2 essais au total par défaut), puis message compréhensible. |
| Quota (429), clé refusée (401/403), modèle introuvable (404) | Pas de reprise, message explicite. |
| Celery/Redis injoignable | Quiz marqué « échec de la génération » / réponses laissées en attente ; relance possible. |
| Abus | 1 génération à la fois par enseignant, `QUIZ_GENERATIONS_PAR_HEURE` (10) par heure, 15 questions max par génération, 30 questions max par quiz, réponse ouverte ≤ 3000 caractères. |

### Asynchronisme : ce qui est vrai aujourd'hui
- Avec `CELERY_TASK_ALWAYS_EAGER=True` (valeur par défaut du projet en développement), Celery exécute la tâche **dans la requête HTTP** : la génération **bloque** la page jusqu'à la réponse de Gemini (au plus ~2 × `QUIZ_GEMINI_TIMEOUT_S`).
- Avec `CELERY_TASK_ALWAYS_EAGER=False`, Redis et un worker lancé, la requête rend la main immédiatement et la page suit l'avancement. Aucune modification d'infrastructure n'a été faite pour cela : c'est la configuration Celery existante de l'équipe.

## 5. Variables d'environnement

| Variable | Rôle | Défaut |
|---|---|---|
| `GEMINI_API_KEY` | Clé Gemini (déjà utilisée par l'équipe). **Jamais dans le code ni dans Git.** | — |
| `GEMINI_MODEL` | Modèle Gemini (partagé avec `ai_services`) | `ai_services.gemini.MODELE_PAR_DEFAUT` |
| `CELERY_BROKER_URL`, `CELERY_TASK_ALWAYS_EAGER` | Configuration Celery existante | voir `config/settings.py` |
| `QUIZ_GEMINI_TIMEOUT_S` | Délai maximum d'un appel (s) | 60 |
| `QUIZ_GEMINI_ESSAIS` | Essais au total en cas d'erreur passagère | 2 |
| `QUIZ_TEXTE_MAX_CARACTERES` | Texte du cours envoyé pour la génération | 30000 |
| `QUIZ_GENERATIONS_PAR_HEURE` | Générations par enseignant et par heure | 10 |

## 6. Installation et lancement (Windows, Git Bash)

```bash
cd ~/EduVox
python -m venv venv                      # si le venv n'existe pas encore
source venv/Scripts/activate             # (PowerShell : venv\Scripts\Activate.ps1)
pip install -r requirements.txt
cp .env.example .env                     # puis remplir .env (base, GEMINI_API_KEY…) — jamais commité
python manage.py migrate quizzes         # n'applique que la migration du module (0001_initial)
python manage.py init_roles              # commande existante du module 1 (groupes et permissions)
python manage.py runserver
```

Worker Celery (facultatif, pour un vrai traitement en arrière-plan) — dans `.env` : `CELERY_TASK_ALWAYS_EAGER=False`, Redis démarré, puis dans un second terminal :

```bash
celery -A config worker -l info --pool=solo   # --pool=solo est nécessaire sous Windows
```

> Remarque locale : `load_dotenv()` remonte les dossiers jusqu'à trouver un `.env`. Sans `.env` dans le dépôt, il peut lire un autre `.env` (par exemple dans le dossier utilisateur). Gardez un `.env` à la racine du projet.

## 7. Démonstration

URL d'entrée : **http://127.0.0.1:8000/quiz/** (entrée « Quiz » du menu latéral).

**Enseignant**
1. Avoir un cours avec un texte disponible (PDF, ou audio transcrit, ou résumé généré) dans `/cours/`.
2. `/quiz/generer/` : choisir le cours, « Résumé du cours », 5 questions, les trois types → le brouillon s'affiche avec l'état de génération.
3. Relire, modifier une question (`Modifier`), changer l'ordre (flèches), ajouter une question à la main.
4. « Valider et publier » (confirmation). Montrer qu'un QCM sans bonne réponse bloque la publication.
5. `/quiz/creer/` : création manuelle complète (sans clé Gemini, tout ce parcours fonctionne).
6. Après le passage d'un étudiant : « Résultats », ouvrir une tentative, réviser la note IA d'une question ouverte, relancer une correction en échec.
7. Montrer le verrouillage : « Créer une nouvelle version », modifier la copie, la publier → l'ancienne est archivée, les anciens scores sont inchangés.

**Étudiant**
1. `/quiz/` : seuls les quiz publiés de cours publics apparaissent.
2. Ouvrir un quiz → « Commencer » → répondre (navigation possible au clavier : Tab, flèches dans les groupes de boutons radio, Entrée).
3. Soumettre → score (provisoire tant qu'une question ouverte est en correction), corrigé selon le réglage du quiz.
4. `/quiz/mes-resultats/` : historique personnel uniquement.

**Métriques IA mesurées** : `python manage.py stats_ia_quiz` (option `--jours 30`).

## 8. Tests et couverture

Gemini est **toujours simulé** dans les tests : aucun appel réel ni payant. Les tests tournent sur PostgreSQL (configuration de l'équipe) ou, sans base locale, sur SQLite en mémoire :

```bash
# Tests du module, sans PostgreSQL
python manage.py test apps.quizzes --settings=apps.quizzes.tests.settings_sqlite
# Avec la base de l'équipe (.env rempli ; Django crée une base de test temporaire)
python manage.py test apps.quizzes

# Couverture (pip install coverage)
python -m coverage run --source=apps/quizzes --omit="apps/quizzes/tests/*,apps/quizzes/migrations/*" \
    manage.py test apps.quizzes --settings=apps.quizzes.tests.settings_sqlite
python -m coverage report -m
python -m coverage html        # rapport dans htmlcov/ (ignoré par Git)

# Vérifications
python manage.py check
python manage.py makemigrations --check --dry-run
```

Ce que couvrent les 136 tests : contraintes des modèles (ordre unique, bornes des points, tentatives, une réponse par question, RESTRICT), cohérence de publication, CRUD et publication, versions et archivage, permissions par rôle et par propriétaire (y compris accès directs aux URL et méthodes GET sur des actions POST), visibilité des quiz, confidentialité des résultats, score QCM/Vrai-Faux, champs de score falsifiés ignorés, choix ou question d'un autre quiz, double soumission, nombre de tentatives, corrections IA réussies / hors barème / indisponibles, révision enseignant, historique intact après nouvelle version, sorties Gemini valides/invalides, délai dépassé, quota, erreurs 5xx et réseau, JSON invalide, clé absente, limite de débit, idempotence des tâches, commande de métriques ; version 2 : indice IA (limite de 3, pénalité 75 %, indice qui dévoile la réponse bloqué, contrôles d'accès), explication d'erreur (cache, droits, corrigé masqué), bilan automatique / invalide / relancé, audit et analyse de classe (anonymisation vérifiée, propriétaire seulement), psychométrie (α de Cronbach, discrimination, distracteurs), pilotage réservé à l'admin, progression étudiant ; version 3 : règles de Leitner, carnet privé, corrigé masqué exclu, compteurs d'intégrité (croissants, bornés, propriétaire seulement), mélange des choix déterministe, import avec aperçu et sélection, certificat PDF et vérification publique (jeton falsifié ou note révisée refusés), export CSV par rôle.

## 9. Protocole d'évaluation de l'IA

Automatique (journal `AppelIA`, commande `stats_ia_quiz`) :
- **Validité du JSON** : part des appels en échec `json_invalide`, `sortie_invalide`, `hors_bareme` ; questions demandées / conservées / écartées par la validation.
- **Temps de réponse** : moyenne, médiane, 95ᵉ centile et maximum par type d'appel (mesurés côté serveur, reprises incluses).
- **Taux d'échec** par cause (délai, quota, réseau…).
- **Cohérence des corrections** : sur les réponses notées par l'IA puis révisées par l'enseignant, part de notes identiques et écart moyen rapporté au barème.

Manuel (à faire avec l'enseignant, sur 3 cours de matières différentes, 10 questions chacun) :
- **Fidélité au cours** : pour chaque question, « la réponse se trouve-t-elle dans le texte ? » (oui / partiellement / non).
- **Pertinence** : la question porte-t-elle sur une notion importante ? (échelle 1–3).
- **Correction** : l'enseignant note à l'aveugle 20 réponses ouvertes, puis on compare avec les notes IA (même calcul que `stats_ia_quiz`).

Mesures réellement effectuées à ce jour : **aucune mesure sur le vrai Gemini** (pas de clé utilisée pendant le développement). Seuls la suite de tests (simulée) et le rapport de couverture ont été exécutés. Les chiffres ci-dessus restent à produire avant la soutenance.

## 10. Accessibilité (mise en œuvre, audit restant à faire)

- HTML sémantique : `section`/`article`, un `h1` par page puis `h2`/`h3`, fil d'Ariane en `nav`, tableaux avec `th scope`.
- Chaque champ a un `label` ; groupes de réponses en `fieldset` + `legend` (énoncé dans la légende) ; erreurs reliées par `aria-describedby` et `aria-invalid`, résumé d'erreurs qui reçoit le focus.
- États de chargement et de correction annoncés (`role="status"`, `aria-live="polite"`), boutons désactivés pendant l'envoi.
- Badges toujours accompagnés d'un **texte** (jamais la couleur seule), couleurs du module choisies pour un contraste élevé ; focus clavier visible renforcé (`templates/quizzes/_styles.html`).
- Boutons-icônes avec `aria-label`, liens « Voir » complétés d'un texte masqué visuellement.

**Non vérifié** : aucun audit Lighthouse/axe ni test au lecteur d'écran n'a encore été mené, et les contrastes du thème Materio partagé (hors module) n'ont pas été mesurés. À faire : `Lighthouse > Accessibility` sur `/quiz/`, une page de passage et une page de résultat.

## 11. Fichiers communs modifiés (additions uniquement)

| Fichier | Modification |
|---|---|
| `config/settings.py` | `"apps.quizzes"` ajouté à `INSTALLED_APPS` |
| `config/urls.py` | `path("quiz/", include("apps.quizzes.urls"))` |
| `templates/layout/partials/menu/vertical/vertical_menu.html` | l'entrée « Quiz » déjà prévue (commentée) est activée |
| `.env.example` | variables `QUIZ_*` facultatives, sans valeur secrète |

Aucune dépendance ajoutée (`google-genai`, `celery`, `redis` sont déjà dans `requirements.txt`). Aucun modèle, migration, vue ou test des modules `accounts`, `courses` ou `dashboards` n'a été modifié.

## 12. Préparation de l'intégration et du déploiement

- Pull request `wale/quizzes → main` après relecture par l'équipe ; seule migration ajoutée : `quizzes/0001_initial` (dépend de `accounts/0006` et `courses/0003`).
- En production (configuration commune, à décider en équipe) : `DEBUG=False`, `SECRET_KEY` fixe, `ALLOWED_HOSTS`, cookies `Secure`, HSTS (`manage.py check --deploy` les signale), `collectstatic`, `CELERY_TASK_ALWAYS_EAGER=False` + Redis + worker, `GEMINI_API_KEY` en variable d'environnement du serveur.
- Les tableaux de bord du module 5 affichent encore « Quiz publiés : 0 » en dur ; ils peuvent lire `Quiz` et `TentativeQuiz` (score, statut) sans modification de ce module.

## 13. Limites connues

- QCM à une seule bonne réponse (choix assumé : notation simple et sans ambiguïté).
- Pas de minuterie de quiz ni de mélange des choix.
- Les quiz ne sont pas réservés à une classe : le module cours n'a pas de notion d'inscription, tout étudiant voit les cours publics.
- Sans worker, la génération bloque la requête (voir §4).
- Le test existant `apps.courses…PublicationProgrammeeTests` échoue aussi sur `main` sans ce module (session de 30 min dépassée par l'heure simulée à +2 h) : à signaler au module 2.

## 14. Questions probables de soutenance

- **Pourquoi l'IA ne publie-t-elle jamais ?** Un modèle génératif peut se tromper (mauvaise réponse marquée juste) : l'enseignant reste responsable ; la publication est une action explicite, validée par `erreurs_publication()`.
- **Comment empêchez-vous la triche sur le score ?** Le navigateur n'envoie que des identifiants de choix ; le serveur recharge les choix de chaque question du quiz, calcule les points et ignore tout autre champ. Les bonnes réponses ne sont jamais envoyées pendant la tentative.
- **Et la double soumission ?** Transaction + `select_for_update` sur la tentative + contrôle de l'état ; côté IA, réservation atomique de chaque réponse (`UPDATE … WHERE statut = en_attente`).
- **Pourquoi ne pas modifier un quiz déjà passé ?** Les scores dépendent des questions ; les modifier fausserait l'historique. On crée une version, l'ancienne reste intacte et est archivée.
- **Pourquoi valider la sortie de Gemini alors qu'on lui donne un schéma ?** Le schéma guide le modèle mais ne garantit pas la cohérence métier (une seule bonne réponse, barème, types demandés). La validation serveur est la seule garantie.
- **Injection de prompt ?** Le cours et la réponse de l'étudiant sont délimités et déclarés comme données dans l'instruction système ; la note est de toute façon bornée par le serveur et révisable.
- **Pourquoi un client Gemini propre au module ?** Le service partagé n'a ni délai ni mode JSON ; le modifier aurait touché les modules 2 et 4. Mêmes variables et même modèle par défaut.
- **Échec de l'IA ?** Message clair, aucune note inventée, réponse conservée « en attente », relance par l'enseignant ou notation manuelle.
- **Asynchrone ?** Tâches Celery idempotentes ; en dev, mode « eager » (bloquant, assumé), en production worker + Redis.
- **Données personnelles ?** Rien d'identifiant n'est envoyé à Gemini ; `AppelIA` ne stocke ni prompt, ni réponse, ni clé.
