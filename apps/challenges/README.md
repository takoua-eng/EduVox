# Module 5 — EduSprint : défis et projets collaboratifs (`apps/challenges`)

Responsable : Wale Rezgui · Branche : `gestion-defis-projets-collaboratifs`

EduSprint est un espace de **missions professionnelles réalisées en équipe**. L'enseignant prépare une
mission (« Concevoir une solution pour réduire la consommation d'eau d'une université, en cinq jours, avec un
budget de 2 000 DT. Livrables : proposition, maquette et budget prévisionnel »). Les étudiants forment des
équipes, répartissent leurs tâches, respectent des jalons et déposent leurs réalisations. Une IA (Gemini) joue
le **client virtuel** : elle propose un brief, propose des imprévus que l'enseignant valide, et vérifie la
couverture des exigences dans un dossier textuel.

**Hors périmètre, volontairement** : aucune note, aucune correction de quiz, aucune mesure de présence ou
d'attention, aucun chat généraliste, ni classement ni badges. Le module est indépendant des cours et des quiz ;
il réutilise seulement l'authentification et les rôles du module `accounts`.

---

## 1. Architecture

```
apps/challenges/
├── models.py            11 modèles + contraintes PostgreSQL (CHECK, UNIQUE partiels) et verrous métier dans save()
├── permissions.py       règles d'accès objet par objet (404 hors périmètre)
├── services/
│   ├── defis.py         cycle de vie du défi (transitions verrouillées)
│   ├── equipes.py       adhésions concurrentes sûres, coordination
│   ├── livrables.py     versions, soumission, retours
│   ├── evenements.py    imprévus (valider → publier) et réponses d'adaptation
│   ├── gemini.py        client Gemini JSON : délai max, reprises limitées, erreurs traduites
│   ├── brief.py         schéma + validation du brief, conversion des délais relatifs en dates
│   ├── scenario.py      schéma + validation des imprévus (faisabilité, diversité), contexte anonymisé
│   ├── verification.py  exigences, extraction du texte (PDF/TXT/MD), vérification des extraits cités
│   ├── ia.py            quota, anti-doublon, envoi différé à Celery
│   └── reglages.py      réglages EDUSPRINT_* (settings → variables d'environnement → défaut)
├── tasks.py             tâche Celery idempotente traiter_demande
├── stockage.py          stockage PRIVÉ des fichiers (hors MEDIA_ROOT, sans URL publique), validation des fichiers
├── forms.py · views/ (defis, equipes, taches, livrables, evenements, ia) · urls.py · admin.py
├── templatetags/edusprint.py   badges (icône + texte)
├── templates/challenges/       pages + _styles.html (styles limités à .edusprint)
├── management/commands/evaluer_ia_edusprint.py   protocole d'évaluation de l'IA
├── evaluation/cas.json         jeu de cas annotés à la main
└── tests/                      138 tests (Gemini toujours simulé) + settings_test.py
```

> Pourquoi les styles et scripts sont dans des gabarits (`_styles.html`, blocs `<script>`) et non dans
> `static/` : le `.gitignore` commun ignore tous les dossiers `static/` des applications (même choix que le module quiz).

### Modèles et relations

```
Utilisateur (accounts) ──< Defi (enseignant, co_enseignants) ──< Jalon
                              │
                              ├──< Equipe (coordinateur) ──< ParticipationEquipe >── Utilisateur (étudiant)
                              │        ├──< Tache (jalon?, responsable?) ──< CommentaireTache
                              │        ├──< Livrable (jalon?) ──< VersionLivrable ──< DemandeIA (vérification)
                              │        └──< ReponseEvenement
                              ├──< EvenementScenario (imprévu) ──< ReponseEvenement
                              └──< DemandeIA (brief / imprévu)
```

| Modèle | Points clés |
|---|---|
| `Defi` | brief (titre, accroche, contexte, objectifs, compétences, livrables, contraintes, budget), niveau, dates d'inscription et du défi, tailles d'équipe, statut, origine (manuelle / IA), `co_enseignants`. CHECK : `1 ≤ min ≤ max ≤ 12`, budget ≥ 0, `ouverture < clôture ≤ début < fin`. |
| `Jalon` | ordre (UNIQUE par défi), échéance comprise dans la période du défi, exigences (une par ligne). |
| `Equipe` | nom (UNIQUE insensible à la casse par défi), coordinateur (membre accepté obligatoire), statut active / écartée / dissoute. Jamais supprimée. |
| `ParticipationEquipe` | état en attente / acceptée / refusée / retirée, rôle, motif, dates. Copie de `defi` pour l'index **UNIQUE partiel « une équipe acceptée par étudiant et par défi »** et l'index « pas de demande dupliquée ». |
| `Tache` | jalon facultatif (même défi), responsable facultatif (membre accepté), priorité, échéance (≤ fin du défi), 4 statuts. |
| `CommentaireTache` | auteur, contenu (2 000 caractères). |
| `Livrable` / `VersionLivrable` | identité du livrable + versions numérotées (fichier, lien, texte saisi, message). État brouillon / soumis / à compléter / accepté, retour enseignant, retard. UNIQUE partiels : un seul brouillon et une seule soumission en attente par livrable ; CHECK : au moins un contenu. |
| `EvenementScenario` | imprévu : type, origine (manuelle / IA), état brouillon / validé / publié, validateur, justification, impacts, éléments attendus, changement réel facultatif (échéance d'un jalon, budget) appliqué et tracé à la publication. |
| `ReponseEvenement` | analyse d'impact, décisions, adaptation du planning, brouillon / soumise. UNIQUE (imprévu, équipe). |
| `DemandeIA` | état consultable (en attente / en cours / terminé / échoué), résultat validé, mesures (modèle, durée, essais, code d'erreur), empreinte SHA-256 du texte analysé. UNIQUE partiels anti-doublon. **Ni clé, ni prompt complet, ni nom d'étudiant.** |

**Suppression de comptes** : le module `accounts` supprime un utilisateur avec `user.delete()`. Les liens
vers les utilisateurs sont donc en `SET_NULL` (auteurs, coordinateur, enseignant), ou en `CASCADE` pour la
seule participation personnelle. La suppression d'un compte n'échoue jamais et n'efface pas le travail de
l'équipe. Une coordination devenue vacante peut être reprise par un membre.

---

## 2. Règles métier

### Cycle de vie d'un défi

```
brouillon ──publier──▶ inscriptions ──démarrer──▶ en cours ──clôturer──▶ terminé ──▶ archivé
    ▲  └──archiver──▶ archivé      │  └──archiver (annulation)──▶ archivé
    └──── repasser en brouillon ───┘  (seulement sans équipe)
```

- Les transitions sont définies dans `Defi.TRANSITIONS` et revérifiées dans `Defi.save()`. Elles sont donc
  impossibles à contourner, y compris par l'admin. Chaque transition verrouille la ligne (`select_for_update`).
- **Publication** : refusée tant que le brief est incomplet, qu'il n'y a aucun jalon ou que la clôture des inscriptions est passée.
- **Démarrage** : au moins une équipe doit atteindre la taille minimale. **Règle pour les équipes incomplètes** :
  elles sont **écartées** (historique consultable, plus aucune contribution). Les demandes encore en attente
  sont refusées (inscriptions closes). Une page de confirmation montre ce bilan avant le démarrage.
- **Après le démarrage**, le brief (exigences, dates, budget, tailles) et les jalons sont **figés** dans `save()`.
  Tout changement passe par un **imprévu publié**, qui trace l'avant et l'après dans `modifications_appliquees`.
- **Suppression** : seul un brouillon sans contribution est supprimable ; sinon on archive.
- **Après clôture ou archivage** : consultation de l'historique uniquement. *Exception métier explicite* :
  après la clôture, l'enseignant peut encore rendre son retour sur une version déjà soumise.

### Équipes et adhésions (sûres en accès concurrent)

- Un étudiant crée son équipe (il en devient coordinateur et membre accepté) ou demande à en rejoindre une
  pendant les inscriptions. Il peut avoir au plus 3 demandes en attente par défi, et retirer une demande en attente.
- **Capacité** : `accepter_demande` verrouille la ligne de l'équipe, puis recompte les membres. Deux acceptations
  simultanées de la dernière place sont donc sérialisées par PostgreSQL, et la seconde voit l'équipe pleine.
- **Une seule équipe acceptée par défi** : vérifié dans le service et **garanti par l'index UNIQUE partiel**.
  En cas de course entre deux équipes, la base refuse la seconde acceptation (`IntegrityError` traduite en message).
- **Demandes devenues incompatibles** : dès qu'un étudiant est accepté (ou crée une équipe), ses autres demandes
  en attente pour ce défi passent à « retirée », avec le motif « membre de l'équipe X ».
- **Coordination** : le coordinateur doit transférer la coordination avant de partir. Dernier membre pendant
  les inscriptions : l'équipe est dissoute. Pendant le défi, le seul membre ne peut pas l'abandonner.
  Un membre qui part libère ses tâches ; ses contributions restent.

### Droits

| Action | Membre | Coordinateur | Enseignant du défi | Admin |
|---|---|---|---|---|
| Voir l'espace d'équipe | ✔ | ✔ | ✔ (sans les brouillons) | ✔ lecture (sans les brouillons) |
| Créer une tâche, commenter | ✔ | ✔ | — | — |
| Affecter une tâche | à soi-même | à tout membre | — | — |
| Modifier / faire avancer une tâche | si créateur ou responsable | ✔ | — | — |
| Supprimer une tâche | créateur, si personne d'autre ne l'a commentée | idem | — | — |
| Préparer un livrable / une réponse (brouillon) | ✔ | ✔ | — | — |
| Modifier / supprimer un brouillon de version | son auteur | son auteur | — | — |
| **Soumettre** une version ou une réponse | — | ✔ | — | — |
| Accepter / refuser une adhésion, retirer un membre (inscriptions), transférer | — | ✔ | — | — |
| Retour sur une version (complément / acceptation) | — | — | ✔ | — |
| Gérer défi, jalons, imprévus | — | — | ✔ (propriétaire ou co-enseignant nommé) | archivage (modération) |

Aucune équipe n'accède aux tâches, fichiers, réponses ou brouillons d'une autre : chaque objet est recherché
**à travers l'équipe de l'utilisateur**, et un objet hors périmètre renvoie **404** (pas d'énumération d'identifiants).
Un enseignant ne gère un défi d'un autre que s'il a été **nommé co-enseignant** par le propriétaire.

### Livrables versionnés

`brouillon → soumis → (à compléter | accepté)`. Une version soumise est **immuable** : `save()` refuse toute
modification et `delete()` toute suppression. Une nouvelle version n'est possible qu'après une demande de
complément ; le numéro est incrémenté sous verrou. Les versions et retours précédents restent visibles. Une
soumission en retard (après l'échéance du jalon, ou de la fin du défi) est signalée. Double clic : la ligne
est verrouillée et l'état revérifié, donc la seconde requête est sans effet.

### Imprévus

`brouillon → validé → publié`. Un imprévu est créé en brouillon (manuel ou proposé par l'IA). Toute
modification de son contenu après validation le **ramène en brouillon** (nouvelle validation obligatoire).
Publié, il est commun à toutes les équipes actives, figé et non supprimable. Une équipe ne peut répondre
qu'à un imprévu publié de **son** défi (vérifié dans le service et dans `save()`).

---

## 3. Sécurité

- `LoginRequiredMiddleware` du projet, plus des contrôles de rôle et d'objet côté serveur sur chaque vue.
- Toutes les modifications se font en POST, avec CSRF actif (testé avec `enforce_csrf_checks`) ; un GET sur une action renvoie 405.
- Formulaires à liste blanche de champs : statut, propriétaire, équipe, auteur et état ne sont jamais modifiables par formulaire (testé).
- Gabarits Django à échappement automatique pour tous les contenus utilisateur et IA (aucun `|safe`).
- **Fichiers** : extensions autorisées (`pdf, txt, md, docx, pptx, xlsx, odt, png, jpg, jpeg`), taille limitée
  (`EDUSPRINT_FICHIER_MAX_MO`), **signature réelle vérifiée** (un `.pdf` qui n'est pas un PDF est refusé), texte en UTF-8.
- **Stockage privé** : fichiers rangés **hors de `MEDIA_ROOT`** (servi publiquement en DEBUG), sous un nom
  aléatoire (UUID), dans `stockage_prive/edusprint/` (ignoré par Git). `StockagePrive.url()` lève une erreur :
  aucune URL publique n'existe. Le seul accès passe par `/defis/versions/<id>/telecharger/` : droits vérifiés,
  pièce jointe, `X-Content-Type-Options: nosniff`, `Cache-Control: private, no-store`.
- **Liens externes** : conservés tels quels (http/https uniquement), jamais ouverts par le serveur (pas de risque SSRF), affichés avec `rel="noopener noreferrer nofollow"`.
- Redirection `suivant` limitée au site (`url_has_allowed_host_and_scheme`).

---

## 4. Intelligence artificielle (Gemini)

**Client** : `services/gemini.py`. Le service partagé `ai_services.appeler_gemini` renvoie du texte libre, sans
délai maximum ni distinction des erreurs. Pour ne pas le modifier, EduSprint utilise les **mêmes variables**
(`GEMINI_API_KEY`, `GEMINI_MODEL`), le même modèle par défaut et le SDK `google-genai` 2.33.0 installé :
`response_mime_type="application/json"`, `response_json_schema`, `HttpOptions(timeout=…)` en millisecondes
(vérifié sur la version installée). Les schémas n'utilisent que des mots-clés JSON Schema sûrs. Toutes les
longueurs et quantités sont **revalidées côté serveur**.

| Fonction | Entrée envoyée | Sortie validée | Garde-fous |
|---|---|---|---|
| **A. Brief** (`/defis/generer/`) | thème, niveau, durée, tailles, contraintes (rien d'autre) | titre, accroche, contexte, 2–8 objectifs, compétences, contraintes, budget, 1–6 jalons à délai relatif, livrables | délais 1..durée et croissants ; dernier jalon = fin ; conversion en dates ; **aperçu modifiable puis défi BROUILLON** ; double envoi sans doublon |
| **B. Imprévu** (page Imprévus) | brief + avancement **agrégé et anonyme** (nb d'équipes, tâches terminées, jalons passés), titres des imprévus précédents | titre, type (budget / exigence / outil / public / délai), description, justification, impacts, éléments attendus | refus si trop proche d'un imprévu existant (similarité ≥ 0,8), si « réduction de budget » sans budget ; créé en **brouillon** ; l'IA ne modifie jamais dates, budget ni tâches |
| **C. Vérification** (page d'un livrable) | exigences construites par le serveur + texte de la version | par exigence : couverte / partielle / non couverte / non vérifiable, extraits, commentaire ; incohérences, améliorations, limites | exigences inventées ignorées, oubliées → « non vérifiable » ; **chaque extrait est recherché dans le texte** ; « couverte » sans extrait retrouvé → « non vérifiable » ; aucune note |

**Vérification des livrables : ce qui est analysé.** Texte saisi, fichiers TXT/MD (UTF-8), PDF dont le texte
est extractible (`ai_services.pdf.extraire_texte_pdf`, réutilisé en lecture seule). Un **PDF scanné** est marqué
« non analysable » **sans appel à Gemini** : ce n'est ni un dossier vide ni un dossier non conforme. Les
formats DOCX, PPTX, XLSX, ODT et images ne sont pas analysés (c'est indiqué). Les liens ne sont jamais ouverts.
L'analyse est **liée à la version exacte** et à l'empreinte SHA-256 du texte : une nouvelle version n'hérite
jamais de l'analyse précédente, et un brouillon modifié depuis l'analyse affiche « analyse obsolète ».

**Robustesse et confidentialité**
- Contenu utilisateur placé entre balises (`<dossier>…</dossier>`) avec une consigne système de ne suivre
  aucune instruction qu'il contient ; une balise fermante injectée est neutralisée (testé).
- Délai maximum `EDUSPRINT_GEMINI_TIMEOUT_S` ; reprises limitées (`EDUSPRINT_GEMINI_ESSAIS`) uniquement pour les
  erreurs passagères (délai, réseau, 5xx, JSON invalide ou hors schéma) ; quota (429) et clé refusée : pas de reprise.
- Messages d'erreur clairs (« Quota Gemini atteint », « n'a pas répondu à temps »…). Les brouillons et dépôts ne sont jamais modifiés en cas d'échec.
- Quota : `EDUSPRINT_IA_APPELS_PAR_HEURE` demandes par utilisateur et par heure ; un seul traitement actif par version, par défi (imprévu) et par enseignant (brief), garanti par des index UNIQUE partiels.
- Mesures (durée, essais, code d'erreur, modèle) enregistrées dans `DemandeIA` ; journaux sans clé ni contenu.
- Chaque bouton IA indique ce qui est envoyé à Google Gemini.
- **Sans clé Gemini**, tout le parcours manuel fonctionne et les boutons IA l'indiquent. Les réponses simulées n'existent que dans les tests (aucun mode démo).

**Traitement différé** : la demande est créée en « en attente » puis envoyée à Celery **après validation de la
transaction** (`transaction.on_commit`). La page interroge `/defis/ia/<id>/etat/` toutes les 3 s (zone
`aria-live`) puis se recharge. La tâche réserve la demande par un UPDATE conditionnel : une double exécution
ne refait pas l'appel. Si Redis est injoignable, la demande passe « échouée » avec un message clair.
> **Important** : avec `CELERY_TASK_ALWAYS_EAGER=True` (valeur du `.env.example` de l'équipe), la tâche
> s'exécute **dans la requête** : ce n'est **pas** du vrai asynchrone. Pour l'asynchrone réel :
> `CELERY_TASK_ALWAYS_EAGER=False`, un serveur Redis et un worker (voir §6).

---

## 5. Installation (Windows / Git Bash)

```bash
cd /c/Users/<vous>/EduVox-main-new
py -3.13 -m venv venv
./venv/Scripts/python.exe -m pip install -r requirements.txt
(cd src && npm install --legacy-peer-deps && npm run build)     # ressources du thème (src/assets/vendor)
cp .env.example .env   # seulement si .env n'existe pas encore, puis renseigner les valeurs privées
```

### Variables d'environnement

Communes au projet : `SECRET_KEY`, `DEBUG`, `DB_*`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `CELERY_BROKER_URL`,
`CELERY_TASK_ALWAYS_EAGER`. Spécifiques et **facultatives** (valeurs par défaut) :

| Variable | Défaut | Rôle |
|---|---|---|
| `EDUSPRINT_GEMINI_TIMEOUT_S` | 60 | délai maximum d'un appel |
| `EDUSPRINT_GEMINI_ESSAIS` | 2 | essais au total (erreurs passagères) |
| `EDUSPRINT_IA_APPELS_PAR_HEURE` | 10 | quota par utilisateur |
| `EDUSPRINT_TEXTE_MAX_CARACTERES` | 40000 | texte de livrable envoyé (au-delà : tronqué et signalé) |
| `EDUSPRINT_FICHIER_MAX_MO` | 20 | taille maximale d'un fichier |
| `EDUSPRINT_DEMANDES_EN_ATTENTE_MAX` | 3 | demandes d'adhésion en attente par étudiant et par défi |
| `EDUSPRINT_STOCKAGE_PRIVE` | `<projet>/stockage_prive/edusprint` | dossier privé des livrables (hors `MEDIA_ROOT`) |

`config/settings.py` appelle `load_dotenv()` sans chemin : le fichier `.env` est cherché en remontant depuis
`config/`, donc celui **du projet** est trouvé en premier. Gardez-le à la racine du projet, sinon un `.env`
placé dans un dossier parent pourrait être chargé.

---

## 6. Migrations, serveur et worker

Migration du module : `apps/challenges/migrations/0001_initial.py`. Elle ne dépend que du modèle utilisateur et
**ne modifie aucune table existante**. Elle crée 12 tables (`challenges_defi`, `challenges_defi_co_enseignants`,
`challenges_jalon`, `challenges_equipe`, `challenges_participationequipe`, `challenges_tache`,
`challenges_commentairetache`, `challenges_livrable`, `challenges_versionlivrable`,
`challenges_evenementscenario`, `challenges_reponseevenement`, `challenges_demandeia`), leurs index et contraintes.
Aucune opération destructive ; retour arrière possible avec `migrate challenges zero` (ne supprime que ces tables).

```bash
./venv/Scripts/python.exe manage.py migrate challenges        # à lancer après accord sur la base commune
./venv/Scripts/python.exe manage.py runserver
# Asynchrone réel (CELERY_TASK_ALWAYS_EAGER=False + Redis démarré) :
./venv/Scripts/celery.exe -A config worker -l info --pool=solo  # --pool=solo : nécessaire sous Windows
```

## 7. URL des parcours (préfixe `/defis/`)

| Parcours | URL |
|---|---|
| Catalogue (recherche, filtres, pagination) | `/defis/` |
| Créer / modifier un défi | `/defis/creer/`, `/defis/<id>/modifier/` |
| Brief IA → aperçu modifiable | `/defis/generer/` → `/defis/generer/<demande>/` |
| Détail du défi, jalons | `/defis/<id>/`, `/defis/<id>/jalons/ajouter/` |
| Démarrage (bilan des équipes) | `/defis/<id>/demarrer/` |
| Suivi des équipes (enseignant) | `/defis/<id>/suivi/` |
| Imprévus (préparation, IA, validation, publication) | `/defis/<id>/imprevus/`, `/defis/imprevus/<id>/` |
| Espace d'équipe : vue, tâches (Kanban), jalons, imprévus, livrables | `/defis/equipes/<id>/`, `…/taches/`, `…/jalons/`, `…/imprevus/`, `…/livrables/` |
| Réponse d'adaptation | `/defis/imprevus/<id>/equipes/<équipe>/reponse/` |
| Historique d'un livrable, retours, vérification IA | `/defis/livrables/<id>/` |
| Administration (consultation, modération) | `/admin/challenges/` |

---

## 8. Tests et couverture

Base **SQLite en mémoire**, stockage privé **temporaire**, clé Gemini retirée de l'environnement de test et
client Gemini **toujours simulé** : aucune donnée de la base commune n'est touchée et aucun appel payant n'est fait.

```bash
./venv/Scripts/python.exe manage.py test apps.challenges --settings=apps.challenges.tests.settings_test
./venv/Scripts/python.exe -m coverage run --source=apps/challenges --omit="apps/challenges/tests/*,apps/challenges/migrations/*" \
    manage.py test apps.challenges --settings=apps.challenges.tests.settings_test && ./venv/Scripts/python.exe -m coverage report
```

| Fichier | Couvre |
|---|---|
| `test_modeles.py` | dates, tailles, budget, contraintes en base, transitions, brief figé, verrous des versions et imprévus |
| `test_equipes.py` | inscriptions, doublons, capacité, appartenance unique, demandes incompatibles, course traduite en message, coordination, démarrage |
| `test_acces.py` | URL directes entre équipes, brouillons invisibles de l'enseignant, autre enseignant, co-enseignant, admin, GET → 405, CSRF, redirection |
| `test_parcours.py` | CRUD par l'interface, **scénario de démonstration complet**, blocage après clôture, droits sur les tâches, imprévu de délai appliqué |
| `test_livrables.py` | formats, signatures, taille, noms sûrs, versionnement, double soumission, retard, retours |
| `test_ia.py` | validation des briefs et imprévus, JSON invalide, quota 429, délai, clé refusée, sans clé, quota local, doublons, Celery injoignable, idempotence, brouillon obligatoire, extraits, PDF scanné, injection, protocole |
| `test_admin.py` · `test_pages.py` | admin non contournable ; rendu de toutes les pages pour chaque rôle |

**Limite** : le verrouillage `select_for_update` n'a d'effet réel que sous PostgreSQL (SQLite sérialise toutes
les écritures). Les tests prouvent la logique et la garantie par contrainte UNIQUE. Le comportement sous accès
**réellement simultanés** reste à vérifier sur PostgreSQL (§11).

---

## 9. Protocole d'évaluation de l'IA

Jeu de cas : `evaluation/cas.json`. Il contient 4 briefs (thèmes et niveaux variés), 3 contextes d'imprévus
(dont un défi **sans budget**, où une réduction de budget est attendue comme incohérente et doit être rejetée)
et 4 dossiers **annotés à la main** (complets, incomplets, un avec une incohérence de total).

```bash
./venv/Scripts/python.exe manage.py evaluer_ia_edusprint --plan        # affiche le protocole, aucun appel
./venv/Scripts/python.exe manage.py evaluer_ia_edusprint --reel --sortie rapport.json   # vraie API (payant)
./venv/Scripts/python.exe manage.py evaluer_ia_edusprint --stats       # mesures issues de l'usage réel en base
```

| Mesure | Calcul |
|---|---|
| Taux de JSON valide | sorties acceptées par la validation serveur / cas |
| Temps de réponse, taux d'échec | médiane, p95 ; échecs par code (`delai`, `quota`, `json_invalide`…) |
| Cohérence et diversité des imprévus | validations serveur réussies ; nombre de types distincts ; similarité maximale des titres |
| Exactitude de la couverture | statut exact vs annotation, et version binaire couvert / non couvert |
| Fidélité des extraits | extraits retrouvés mot pour mot / extraits cités |
| Pertinence, faisabilité | **grille humaine** exportée dans le rapport (notes 1–5 à remplir) |

**État des mesures** : la mécanique du protocole est testée avec Gemini simulé (`ProtocoleEvaluationTests`).
**Aucune mesure réelle n'a encore été faite** : les chiffres sont à produire avec `--reel` et la vraie clé. Ce
document ne contient volontairement aucun résultat chiffré.

---

## 10. Intégration et déploiement

Changements communs (minimaux) : `"apps.challenges"` dans `INSTALLED_APPS`, `path("defis/", …)` dans
`config/urls.py`, une entrée « EduSprint » dans le menu vertical, des variables `EDUSPRINT_*` dans `.env.example`,
la ligne `stockage_prive/` dans `.gitignore`. Aucune dépendance ajoutée.

En production : `DEBUG=False`, `SECRET_KEY` fixe, Redis, un worker Celery, `CELERY_TASK_ALWAYS_EAGER=False`,
`EDUSPRINT_STOCKAGE_PRIVE` sur un volume persistant **non servi** par le serveur web, et des sauvegardes de ce
dossier avec la base. Aucune configuration de déploiement n'est ajoutée par ce module.

## 11. Scénario de démonstration

1. **Enseignant** : `/defis/creer/` (ou `/defis/generer/`). Mission « eau du campus », 2 jalons, budget 2 000 DT, puis « Ouvrir les inscriptions ».
2. **Étudiant A** : catalogue → défi → « Créer une équipe ». **Étudiant B** : « Rejoindre ». **A** accepte la demande (vue d'ensemble de l'équipe).
3. **Enseignant** : « Démarrer le défi… » (bilan des équipes) → Démarrer.
4. **A et B** : onglet Tâches, création et affectation, déplacement au clavier (← →) ou par glisser-déposer, commentaires.
5. **Enseignant** : Imprévus → créer (ou « Proposer un imprévu » par l'IA) → Valider → Publier.
6. **B** prépare la réponse d'adaptation ; **A** (coordinatrice) la soumet.
7. **B** dépose le livrable (texte et/ou fichier) ; « Vérifier la couverture des exigences » (IA) ; **A** soumet.
8. **Enseignant** : Suivi → « Relire » → « Demander un complément » avec un retour.
9. **B** dépose la version 2 ; **A** la soumet ; **enseignant** : « Accepter ». L'historique montre v1 + retour et v2 acceptée.

Ce scénario est automatisé dans `ScenarioDemonstrationTests.test_scenario_complet`.

**À vérifier sur l'environnement réel** : PostgreSQL (accès simultanés), Redis et worker, appels Gemini réels
(`--reel`), rendu et navigation au clavier dans un navigateur, audit d'accessibilité avec un outil (axe, WAVE)
et un lecteur d'écran.

---

## 12. Questions de soutenance (réponses courtes)

- **Comment empêchez-vous un étudiant d'être dans deux équipes ?** Index UNIQUE partiel `(defi, etudiant) WHERE etat='acceptee'` sur une copie de `defi` dans la participation. Même en cas de course, PostgreSQL refuse le second.
- **Et le dépassement de capacité ?** `select_for_update` sur l'équipe avant de recompter : les acceptations d'une même équipe sont sérialisées.
- **Pourquoi des verrous dans `save()` ?** Pour que ni un formulaire, ni un script, ni l'admin ne puisse modifier une version soumise, publier un imprévu non validé ou changer le brief après le démarrage.
- **Comment l'enseignant change-t-il une échéance après le démarrage ?** Par un imprévu « modification de délai » : le changement est appliqué à la publication et tracé (avant / après).
- **L'IA peut-elle publier ou modifier quelque chose ?** Non. Le brief devient un aperçu puis un brouillon ; l'imprévu un brouillon à valider ; la vérification n'attribue aucune note.
- **Comment savez-vous que l'IA n'invente pas de citations ?** Chaque extrait est recherché dans le texte normalisé ; s'il est absent, il est signalé et l'exigence ne peut pas être « couverte ».
- **Et un PDF scanné ?** Aucun texte extractible : « non analysable », sans appel IA. Ce n'est pas un dossier vide.
- **Pourquoi ne pas ouvrir les liens ?** Pour éviter le SSRF (requêtes du serveur vers des adresses internes) : le lien est conservé tel quel.
- **Où sont les fichiers ?** Hors de `MEDIA_ROOT`, sous un nom aléatoire, sans URL publique ; téléchargement uniquement par une vue qui vérifie les droits.
- **Quelles données partent chez Google ?** Les paramètres du brief ; le brief et un avancement agrégé anonyme pour les imprévus ; les exigences et le texte de la version pour la vérification. Jamais de nom ni d'e-mail.
- **Que se passe-t-il si Gemini est en panne ?** La demande passe « échouée » avec un message clair ; rien n'est perdu ; le parcours manuel reste disponible.
- **Pourquoi pas le service Gemini partagé ?** Il renvoie du texte libre sans délai ni gestion d'erreurs. Nous ne modifions pas le code des collègues ; nous réutilisons les mêmes variables et le même modèle.
- **C'est vraiment asynchrone ?** Avec Redis et un worker (`ALWAYS_EAGER=False`), oui. En mode `ALWAYS_EAGER=True`, la tâche s'exécute dans la requête : nous ne le présentons pas comme de l'asynchrone.
