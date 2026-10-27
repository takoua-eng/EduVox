# Plan : métriques IA et accessibilité du module Cours (`apps/courses`)

Ce plan a été préparé après lecture du code du module Cours. Il ne contient aucun code dans `apps/courses` :
c'est la responsable du module qui l'implémente. Les décisions déjà prises sont indiquées au §8.

## 0. Déjà fait (hors `apps/courses`) : les tests ne peuvent plus envoyer de tâche au broker

**Incident du 27/10/2026.** Le `.env` contenait `CELERY_TASK_ALWAYS_EAGER=False` avec le broker `filesystem://`.
Pendant une exécution des tests de `courses`, les tâches `transcrire_cours` et `generer_resume_cours` sont donc
parties dans `.celery_broker/`. Un worker branché sur Neon les a exécutées avec des identifiants de la base de test.
Le cours n° 2 « test 2 » est passé en statut `erreur`. Il a été restauré (`termine`, progression 100, message vidé),
c'est la seule écriture faite sur Neon.

**Correctif dans `config/settings.py`** : le bloc `if sys.argv[1] == "test"` est maintenant à la **fin** du fichier.
Avant, il était placé avant les réglages Celery, qui l'écrasaient. Il force :

- `CELERY_TASK_ALWAYS_EAGER = True` et `CELERY_TASK_EAGER_PROPAGATES = True` : les tâches s'exécutent directement
  et leurs erreurs font échouer le test ;
- `CELERY_BROKER_URL = "memory://"`, aussi dans `os.environ`, car Celery lit d'abord la variable d'environnement ;
- la base SQLite de test et le cache `modeles_ia` en mémoire.

Vérification : `python manage.py test` (604 tests) ne modifie pas le dossier `.celery_broker/`.

À savoir : dans les tests, `celery_app.conf.task_always_eager = True` est **ignoré**, car Celery donne la priorité à
la clé `CELERY_TASK_ALWAYS_EAGER`. Ce motif de `TachesCeleryTests` fonctionnait seulement parce que la valeur était
déjà `True`. Il n'est plus nécessaire et peut être supprimé.

## 1. Corriger le test restant

`PublicationProgrammeeTests` échoue encore. Le test avance l'heure de 2 h, alors que `SESSION_COOKIE_AGE` vaut
30 min : la session expire et la page redirige (302 au lieu de 200).
→ Ajouter `@override_settings(SESSION_COOKIE_AGE=24 * 3600)` sur la classe.

Les 4 autres échecs observés (`BoutonsTraitementTests`, `TachesCeleryTests`, `EnseignantAmeliorationsTests`)
venaient du mode eager et sont corrigés par le §0.

## 2. Métriques IA : `apps/courses/metriques.py` (fonctions pures)

| IA | Métrique | Outil |
|---|---|---|
| Transcription (Whisper) | **WER** (taux d'erreur par mot) et **temps par minute d'audio** | `jiwer` |
| Résumé (Gemini) | **ROUGE-1** et **ROUGE-L** (précision, rappel, F1), temps de réponse (moyenne, médiane, p95) | `rouge-score` |
| Recommandations (mots-clés) | **précision@4**, rappel@4, F1@4 | Python pur |

Points d'attention :

- **WER** : normaliser les deux textes avant comparaison, avec `jiwer.Compose` : minuscules, suppression de la
  ponctuation et des espaces multiples. Sinon une virgule compte comme une erreur.
- **ROUGE en français** : le tokenizer par défaut de `rouge-score` ne garde que `[a-z0-9]`, il **supprime les lettres
  accentuées** (« électricité » devient « lectricit »). Il faut passer un tokenizer adapté :
  `RougeScorer(["rouge1", "rougeL"], tokenizer=...)`, par exemple un découpage `re.findall(r"\w+", texte.lower())`.
- **Temps** : utiliser `time.perf_counter()`. Le p95 se calcule par la méthode du rang le plus proche, comme dans
  `apps/accounts/metriques.py::stats_temps`, qui peut servir de modèle.
- Les tests unitaires des métriques doivent utiliser des valeurs connues, par exemple un WER de 1 mot faux sur 4 = 0,25.

## 3. Commande `python manage.py evaluer_ia_cours`

Elle crée `apps/courses/management/commands/evaluer_ia_cours.py`. La logique est dans `apps/courses/evaluation.py`,
pour être partagée avec la tâche Celery.

**Données attendues** (fournies par la responsable du module) :

```
ai_services/donnees_test/audio/<nom>.wav          + <nom>.txt            (texte de référence, transcrit à la main)
ai_services/donnees_test/resumes/<nom>.txt        (texte du cours)
ai_services/donnees_test/resumes/<nom>.reference.txt  (résumé de référence, écrit à la main)
```

1. **Transcription** : pour chaque paire, `transcrire()` puis WER contre la référence, et temps / durée de l'audio.
   Résultats : WER moyen, WER par fichier, secondes de calcul par minute d'audio.
2. **Résumé** : `resumer_cours(texte)` puis ROUGE-1 / ROUGE-L contre la référence, avec le temps de chaque appel.
   Sans `GEMINI_API_KEY` : message clair (« clé Gemini absente : évaluation du résumé ignorée »), pas de plantage.
   Chaque résumé est un **vrai appel** Gemini qui consomme le quota : le nombre de fichiers le limite.
3. **Recommandations** : jeu étiqueté en mémoire (voir §4).
4. Options utiles : `--audio DOSSIER`, `--resumes DOSSIER`, `--sans-transcription` (Whisper est long sur CPU).
5. Si un dossier ou une librairie manque, la partie concernée est ignorée avec un message clair, et les autres continuent.

## 4. Recommandations : évaluation sans écrire en base

`cours_similaires()` interroge la base. Pour ne rien écrire sur Neon :

- **extraire le calcul du score** dans une fonction pure, par exemple `classer_par_similarite(cours, candidats, limite)`.
  Score : 2 points pour la même matière, 1 point par mot-clé commun, puis tri par (score, date) décroissants.
  `cours_similaires()` l'appelle après son filtre en base. Le résultat reste identique : le filtre `icontains`
  ne retire que des candidats de score 0. Les tests existants le vérifient.
- **évaluer sur environ 20 objets `Cours` non sauvegardés** : `Cours(titre=..., matiere_id=1, mots_cles=...,
  date_creation=...)`, sans `save()`.
- Une transaction annulée est **déconseillée** : même annulée, elle consomme les séquences d'identifiants de
  PostgreSQL sur Neon.
- **Étiquettes** : elles disent quels cours sont réellement liés **par leur sujet**, pas par les règles de l'algorithme.
  Sinon le score serait de 100 % par construction. Il faut inclure des cas pièges : même matière mais sujet différent
  (Probabilités / Algèbre), sujets liés sans mot-clé commun (« SQL » / « bases de données »), mots-clés absents.
- Mesures : précision@4, rappel@4 et F1@4, en moyenne sur tous les cours du jeu, avec le détail des erreurs.

## 5. Modèles, page admin et tâche Celery

- **`EvaluationIACours`** : `date`, `type` (TRANSCRIPTION / RESUME / RECOMMANDATIONS), `resultats` (JSONField),
  `duree_totale_ms`. Modèle à suivre : `apps/accounts/models.py::EvaluationIA`.
- **`Cours.duree_transcription_ms`** et **`Cours.duree_resume_ms`** (`PositiveIntegerField`, `null=True`), remplis
  avec `perf_counter` dans `transcrire_cours` et `generer_resume_cours`. Ils permettent de calculer le temps réel par
  minute d'audio (`duree_transcription_ms / duree_audio`).
- Une migration `0004`.
- **Tâche `lancer_evaluation_ia_cours`** dans `apps/courses/tasks.py`. Le bouton de la page l'envoie avec `.delay()`
  dans un `try/except`, sur le modèle de `lancer_traitement`. En mode eager, elle tourne dans la requête : prévoir des
  options légères (sans Whisper) pour le bouton.
- **Page « Performances IA – Cours »** : `AdminRequisMixin` + `LayoutMixin`, même style que `journal.html`. Elle contient :
  - des cartes de chiffres clés (WER, ROUGE-L F1, précision@4, temps moyens) ;
  - les tableaux de détail, avec `caption` et `th scope` ;
  - les temps réels tirés des deux nouveaux champs ;
  - l'historique des évaluations ;
  - le bouton POST avec CSRF.

  Ajouter le lien dans le menu admin. La page `apps/accounts/templates/accounts/performances_ia.html` peut servir de modèle.
- **`docs/metriques_ia_cours.md`** : méthode, chiffres obtenus et limites (petit jeu de test, références écrites par une
  seule personne, ROUGE qui mesure le recouvrement de mots et non la justesse, coût des appels Gemini).

## 6. `TRANSCRIPTION_MODE` (déploiement sans torch)

Nouveau réglage `TRANSCRIPTION_MODE = os.environ.get("TRANSCRIPTION_MODE", "local")` :

| Mode | Comportement |
|---|---|
| `local` | Whisper via transformers/torch, comme aujourd'hui |
| `gemini` | nouvelle fonction `transcrire_gemini(chemin)` dans `ai_services/transcription.py` : envoi du fichier avec `client.files.upload(...)` puis `generate_content([consigne, fichier])` |
| `desactive` | bouton « Transcrire » masqué et message « transcription indisponible sur ce serveur » ; la tâche renvoie une erreur claire |

- Ajouter des fonctions, sans modifier `transcrire_question` / `transcrire_wav` (utilisées par l'assistant).
  Tous les imports de torch et de transformers doivent rester **dans les fonctions** (c'est déjà le cas).
- **Remarque importante** : l'**assistant vocal** (`apps/assistant`) utilise lui aussi Whisper (`transcrire_question`,
  `transcrire_wav`). Ce réglage ne suffit donc pas à retirer torch de la production si l'assistant y est déployé.
  Il faut décider en équipe si l'assistant passe aussi en mode Gemini, ou si la production garde torch.
- Pour réellement gagner de la place en production, il faudra un fichier de dépendances sans torch ni transformers
  (par exemple `requirements-production.txt`), à décider en équipe.

## 7. Accessibilité WCAG AA des gabarits `courses`

Constat : aucune page n'a de `<h1>`. Les titres sont des `h5` et `h6`, sauf `resume_pdf.html`.

| Règle | À faire |
|---|---|
| Titres | Le titre de chaque page devient `<h1 class="h4">`. Les titres de cartes passent en `<h2 class="h5">`, et les sous-titres en `<h3 class="h6">` (même rendu visuel, sans sauter de niveau). Exception : `_carte_cours.html` (h6) est inclus dans des listes. Son niveau dépend de la page : `h2` si la liste est directement sous le `h1`. |
| `select` de filtre | `aria-label` (« Filtrer par matière », « par niveau », « par enseignant ») dans `liste_cours.html`, et sur les autres pages à filtres |
| Barre de progression | `aria-label="Progression du traitement"` + `aria-valuenow/min/max` sur `role="progressbar"` (`detail_cours.html`) |
| Boutons `btn-icon` | `aria-label` reprenant le `title` (« Consulter », « Modifier », « Supprimer », « Ajouter aux favoris »…) |
| Icônes décoratives | `aria-hidden="true"` sur les `<i class="icon-base ri …">`, y compris dans les titres de cartes |
| `<th>` vides | texte en `<span class="visually-hidden">Actions</span>` |
| Contraste `#btnLire` | Texte `#7C3AED` sur fond `btn-label-primary` (16 % de violet sur blanc) : environ **4,5:1** par calcul, pile à la limite. Utiliser `#5b21b6`, la couleur déjà utilisée par `.bg-label-primary` dans `eduvox.css` (environ **7,1:1**), par une règle limitée à la page (pas dans `eduvox.css`, qui est partagé). À vérifier avec l'outil de contraste du navigateur. |

Ajouter des tests de gabarit : un seul `<h1` par page, aucun `<th></th>`, `aria-label` présents.

## 8. Questions posées et décisions

1. **Restaurer le cours n° 2 sur Neon ?** Oui, c'est fait (une seule écriture).
2. **Installer `jiwer` et `rouge-score` ?** Oui, à ajouter à `requirements.txt` **en prévenant l'équipe**, car le fichier est partagé.
3. **Un ou deux champs de durée ?** Deux : `duree_transcription_ms` et `duree_resume_ms`. Avec un seul champ, le résumé
   écraserait la durée de transcription et on ne pourrait plus calculer le temps par minute d'audio.
4. **Données de test dans `ai_services/donnees_test/` (ignoré par git à cause des photos de visages) ?** Ajouter une
   exception dans `.gitignore` pour `donnees_test/audio/` et `donnees_test/resumes/`. Les WAV et les résumés de
   référence sont fournis par la responsable du module.

Exception à mettre dans `.gitignore` (la ligne `ai_services/donnees_test/` devient `ai_services/donnees_test/*`,
sinon git ignore tout le dossier et les exceptions sont sans effet) :

```
ai_services/donnees_test/*
!ai_services/donnees_test/audio/
!ai_services/donnees_test/resumes/
```

## 9. Tests et couverture

- Gemini (`apps.courses.tasks.resumer_cours`, `ai_services.gemini.genai.Client`) et Whisper
  (`apps.courses.tasks.transcrire`, `ai_services.transcription._pipeline_whisper`) doivent être **mockés**, comme
  dans les tests actuels.
- La commande se teste avec un dossier temporaire contenant de faux WAV et un faux transcripteur.
- Tester aussi la page (admin = 200, étudiant = 403), le bouton (tâche mockée, puis exécution eager) et les trois
  valeurs de `TRANSCRIPTION_MODE`.
- Couverture visée ≥ 95 % : `coverage run --source=apps.courses --omit="*/migrations/*,*/tests.py" manage.py test apps.courses`.
  Elle était de 94 % avant le §0, à cause des 5 tests en échec.

## 10. Autre point à signaler à l'équipe

Les tests « sans clé » de `apps/challenges` (`AppelGeminiTests.test_sans_cle`, etc.) échouent quand le `.env`
contient une `GEMINI_API_KEY` : ils font alors un **vrai appel à Gemini**, qui consomme le quota de l'équipe.
Il faudrait les isoler avec `mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""})`.
