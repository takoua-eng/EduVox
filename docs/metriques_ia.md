# Évaluation des IA du module Utilisateurs

Ce document présente la méthode, les résultats et les limites de l'évaluation des deux IA du module
« accounts » d'EduVox : la reconnaissance faciale et la détection des connexions suspectes.

Pour lancer l'évaluation :

```bash
python manage.py evaluer_ia                                   # base enregistrée + connexions + temps
python manage.py evaluer_ia --images ~/scikit_learn_data/lfw_home/lfw_funneled --max-personnes 50 --repetitions 100
```

Les résultats sont enregistrés (modèle `EvaluationIA`) et affichés sur la page
**Administration › Performances de l'IA**. Cette page a aussi un bouton qui relance l'évaluation via une tâche Celery.

## 1. Les métriques utilisées

|                   | Prédit positif           | Prédit négatif           |
|-------------------|--------------------------|--------------------------|
| **Réel positif**  | VP (vrai positif)        | FN (faux négatif)        |
| **Réel négatif**  | FP (faux positif)        | VN (vrai négatif)        |

- **Précision** = VP / (VP + FP) : quand l'IA dit « oui », a-t-elle raison ?
- **Rappel** = VP / (VP + FN) : parmi les vrais « oui », combien l'IA en trouve-t-elle ?
- **F1** = moyenne harmonique de la précision et du rappel.
- **FAR** (taux de fausses acceptations) = FP / (FP + VN) : imposteurs acceptés. C'est le risque de **sécurité**.
- **FRR** (taux de faux rejets) = FN / (FN + VP) : vraies personnes refusées. C'est la **gêne** pour l'utilisateur.
- **EER** : le seuil où FAR = FRR. C'est un point de comparaison classique entre systèmes biométriques.

Les calculs sont faits dans `apps/accounts/metriques.py`. Les tests automatiques vérifient
qu'ils donnent exactement les mêmes valeurs que scikit-learn.

## 2. Reconnaissance faciale (OpenCV YuNet + SFace)

**Positif = « même personne »** : le visage doit être accepté. EduVox utilise deux seuils de similarité cosinus :

- `FACE_SEUIL` = **0,363** (seuil recommandé par OpenCV pour SFace) pour la vérification 1:1, c'est-à-dire la connexion
  avec nom d'utilisateur, et pour l'anti-doublon à l'enregistrement ;
- `FACE_SEUIL_IDENTIFICATION` = **0,45** pour l'identification 1 parmi N (connexion sans nom d'utilisateur),
  avec en plus une marge de 0,05 entre les deux visages les plus proches. Le choix de 0,45 est justifié plus bas.

### Méthode

- **Jeu de données** : LFW (*Labeled Faces in the Wild*), version « funneled » (photos 250 × 250).
  C'est un jeu public de référence, avec un dossier par personne. 50 personnes ayant au moins 2 photos sont tirées au sort
  (graine 42). Les personnes de LFW qui n'ont qu'une seule photo jouent le rôle d'**inconnus** (imposteurs).
- Pour chaque personne, les 3 premières photos forment la « galerie » (comme les 3 captures de
  l'enregistrement) et les autres servent de tests.
- **Vérification 1:1** : chaque photo de test est comparée à chaque galerie.
- **Identification 1 parmi N** : on applique la même règle que la connexion sans nom d'utilisateur
  (seuil + marge de 0,05 avec le deuxième visage le plus proche). Une confusion avec une autre personne compte comme FP.
- Les photos refusées par le contrôle qualité d'EduVox sont ignorées et comptées : **36 photos sur 222**,
  dont 30 avec « plusieurs visages détectés » (beaucoup de photos de presse LFW ont des visages en arrière-plan),
  3 avec un visage trop petit et 3 floues. Au final : 44 personnes utilisables et 40 photos d'inconnus.
- La « base enregistrée » (`ProfilFacial`) est aussi évaluée, mais son résultat est **optimiste par construction** :
  à l'enregistrement, les captures doivent déjà dépasser le seuil, et un visage proche d'un autre compte est refusé.

### Résultats LFW, 50 personnes, seuil 0,363

| | Prédit même personne | Prédit personnes différentes |
|---|---|---|
| **Même personne** (64 paires) | VP = 62 | FN = 2 |
| **Personnes différentes** (4 512 paires) | FP = 9 | VN = 4 503 |

**Précision 87,3 % · Rappel 96,9 % · F1 91,9 % · FAR 0,2 % · FRR 3,1 %**

Similarité moyenne : 0,678 pour la même personne et 0,107 pour des personnes différentes. Les deux groupes sont bien séparés.

| Seuil | FAR | FRR | Précision | Rappel | F1 |
|---|---|---|---|---|---|
| 0,30 | 1,4 % | 1,6 % | 50,8 % | 98,4 % | 67,0 % |
| **0,363** | **0,2 %** | **3,1 %** | **87,3 %** | **96,9 %** | **91,9 %** |
| 0,40 | 0,0 % | 3,1 % | 98,4 % | 96,9 % | 97,6 % |
| 0,45 | 0,0 % | 3,1 % | 100 % | 96,9 % | 98,4 % |
| 0,50 | 0,0 % | 4,7 % | 100 % | 95,3 % | 97,6 % |
| 0,60 | 0,0 % | 21,9 % | 100 % | 78,1 % | 87,7 % |

**EER = 1,6 % au seuil 0,296.**

**Identification 1 parmi N** (104 photos de test : 64 de personnes connues, 40 d'inconnus), au seuil 0,45 avec la marge de 0,05 :
96,9 % des personnes connues sont retrouvées et aucun inconnu n'est accepté.
Matrice : VP = 62, FN = 2, FP = 0, VN = 40, soit précision 100 %, rappel 96,9 %, F1 98,4 %.

**Contrôle sur un échantillon plus grand** (300 personnes demandées, 258 utilisables, 424 paires « même personne »,
175 790 paires « personnes différentes ») : à 0,363, FAR 0,1 % et FRR 0,5 %. **EER = 0,5 % au seuil 0,332.**

### Le seuil 0,363 est-il justifié ?

- **Oui pour la vérification 1:1** (connexion avec nom d'utilisateur). L'EER se situe entre 0,30 et 0,33,
  donc 0,363 est **un peu au-dessus** du point d'équilibre. C'est voulu : à 0,363, le FAR est quasi nul (0,1 à 0,2 %)
  pour un FRR de 0,5 à 3 %. Pour une connexion, mieux vaut refuser parfois un étudiant (il peut réessayer ou utiliser
  son mot de passe) qu'accepter un imposteur. Le seuil d'OpenCV est donc cohérent avec nos mesures.
- **Attention au F1** : il est meilleur à 0,45 (98,4 %) qu'à 0,363 (91,9 %). Ce n'est pas un signe
  que 0,363 est mauvais. Il y a 70 fois plus de paires « personnes différentes » que de paires « même personne »,
  donc quelques FP suffisent à faire baisser la précision. En biométrie, on raisonne plutôt avec le FAR et le FRR.
- **Non pour l'identification 1 parmi N** (connexion sans nom d'utilisateur), d'où le second seuil à 0,45.
  Plus il y a de visages enregistrés, plus un inconnu a de chances de ressembler à quelqu'un. Mesures sur LFW,
  avec exactement la règle de la connexion (seuil, puis marge de 0,05 avec le 2e visage le plus proche) :

  | Galerie | Seuil | Personnes retrouvées | Confondues avec un autre | **Inconnus acceptés** |
  |---|---|---|---|---|
  | 44 personnes (40 inconnus) | 0,363 | 96,9 % | 0 % | **5,0 %** (2 / 40) |
  | 44 personnes (40 inconnus) | **0,45** | 96,9 % | 0 % | **0 %** (0 / 40) |
  | 258 personnes (259 inconnus) | 0,363 | 99,1 % | 0 % | **13,1 %** (34 / 259) |
  | 258 personnes (259 inconnus) | 0,40 | 99,1 % | 0 % | 5,8 % (15 / 259) |
  | 258 personnes (259 inconnus) | **0,45** | 97,9 % | 0 % | **1,5 %** (4 / 259) |

  La marge de 0,05 empêche déjà de confondre deux personnes connues (0 % dans tous les cas). Le risque restant
  vient des inconnus, et le seuil 0,45 le divise par environ 9 sur 258 personnes, pour seulement 1,2 point de personnes
  retrouvées en moins. Une personne refusée peut toujours saisir son nom d'utilisateur : on passe alors en
  vérification 1:1 au seuil 0,363.

- **Taille d'échantillon** : avec 50 personnes, il n'y a que 64 paires « même personne », donc chaque erreur pèse 1,6 point.
  C'est pour cela que l'évaluation a été refaite sur 258 personnes : elle confirme les conclusions.

## 3. Détection des connexions suspectes (Isolation Forest + règles)

**Positif = « connexion suspecte »**. Score de risque = heure inhabituelle (40) + nouvel appareil (35)
+ nouveau réseau (25). La connexion est déclarée suspecte si le score est ≥ 60.

**Jeu de test simulé et reproductible** (graine 42) : 6 utilisateurs fictifs, chacun avec 40 jours
d'historique (heures habituelles, même appareil, même réseau), puis 16 cas par utilisateur, soit **96 cas**.
Rien n'est écrit en base : les connexions sont des objets Python jamais sauvegardés.

Point important : **l'étiquette dépend de *qui* se connecte** (l'étudiant lui-même ou un attaquant),
pas des règles de l'IA. Si l'on avait étiqueté « 2 facteurs = suspect », on aurait obtenu 100 %
sans rien prouver.

| | Prédit suspecte | Prédit normale |
|---|---|---|
| **Réellement suspecte** (attaquant) | VP = 28 | FN = 14 |
| **Réellement normale** (étudiant)  | FP = 12 | VN = 42 |

**Précision 70,0 % · Rappel 66,7 % · F1 68,3 % · Exactitude 72,9 %**

Ce qui marche (100 %) : les connexions habituelles, un seul changement (réseau, téléphone ou heure),
un attaquant avec un autre appareil et un autre réseau, et un attaquant la nuit.

Ce qui échoue, et pourquoi :

- **Faux positifs** : un étudiant avec un nouveau téléphone en 4G, ou en voyage. Deux facteurs changent
  en même temps, donc le score dépasse 60. Dans ce cas, l'IA envoie seulement un e-mail d'alerte et ne bloque pas.
- **Faux négatifs** : un attaquant sur le même Wi-Fi du campus, ou qui utilise le même navigateur
  (« Chrome sur Windows » est très courant). L'appareil n'est décrit que par le navigateur et le système,
  ce qui ne suffit pas à le distinguer.

Pistes d'amélioration : une empreinte d'appareil plus fine (cookie d'appareil de confiance), la géolocalisation de l'IP,
ou la vitesse de déplacement entre deux connexions.

## 4. Temps de réponse

Mesure avec `time.perf_counter` sur 100 répétitions, sur un PC portable Windows (processeur seul, sans GPU) :

| Traitement | Moyenne | Médiane | p95 |
|---|---|---|---|
| Détection + encodage d'un visage (photo LFW 250 × 250) | 13,7 ms | 13,5 ms | 14,9 ms |
| Comparaison 1 parmi N (50 profils) | 2,8 ms | 3,0 ms | 3,1 ms |
| Score de risque, **sans pré-calcul** (modèle entraîné pendant la connexion) | 57,6 ms | 0,7 ms | **471 ms** |
| Score de risque, **après pré-calcul** par le worker Celery | 4,9 ms | 0,8 ms | **35,5 ms** |

Une connexion par visage analyse deux photos (de face, puis tête tournée). Les images de la webcam peuvent aller jusqu'à
800 pixels de large. Il faut donc compter quelques dizaines de millisecondes d'IA par connexion. Les mesures varient
d'environ 20 % d'une exécution à l'autre selon la charge du PC.

### Optimisation du score de risque : pré-calcul par Celery

Le score est quasi instantané dans la plupart des cas (médiane 0,7 ms), car les règles de garde décident seules.
Dans la « zone grise » (heure entre 1 h et 3 h d'écart avec les habitudes), l'Isolation Forest (300 arbres) doit être
entraînée sur l'historique de l'utilisateur. L'entraînement prend environ 500 ms et explique le p95.

Or chaque connexion réussie modifie l'historique : la connexion suivante ne peut pas réutiliser l'ancien modèle.
D'où le **pré-calcul** :

1. Après une connexion réussie (une fois la transaction validée, `transaction.on_commit`), le signal lance la tâche
   `preparer_modele_risque.delay(user_id)`.
2. Le worker Celery relit l'historique à jour, entraîne le modèle et le range dans le cache. La clé contient
   l'identifiant de l'utilisateur, le nombre de connexions et les dates de la dernière et de la première connexion.
3. À la connexion suivante, `evaluer_risque` calcule la même clé à partir du même historique : le modèle est déjà prêt.
   Il ne reste que la lecture du cache et la prédiction (environ 30 ms).

La mesure « après pré-calcul » reproduit exactement ce scénario : pour chaque connexion, le modèle est préparé par la
fonction qu'appelle le worker, puis la connexion est chronométrée. **Le p95 passe de 471 ms à 35,5 ms** (÷ 13).
Les résultats sont strictement identiques (`random_state` fixé, tests à l'appui, métriques du §3 inchangées).

**Cache partagé** : le cache mémoire par défaut de Django n'est pas visible par le worker, qui tourne dans un autre
processus. Les modèles sont donc rangés dans un cache dédié, `CACHES["modeles_ia"]`, choisi selon le broker Celery :

- broker Redis (`redis://…`, worker réel) → **Redis**, le même serveur que le broker ;
- broker `filesystem://` (Windows, démonstration) → **cache fichier** (`.cache_modeles_ia/`). Ce broker impose déjà que
  le serveur web et le worker tournent sur la même machine, donc ils partagent ce dossier.

Le cache en base de données (Neon) a été écarté : un modèle pèse environ 1,6 Mo. Il faudrait écrire 1,6 Mo dans la base
partagée à chaque connexion réussie, puis les relire par le réseau, et jusqu'à 500 modèles pourraient occuper
environ 800 Mo. Une panne du cache ne bloque jamais la connexion : le modèle est alors simplement entraîné sur place.

En mode *eager* (développement sans worker), le pré-calcul est désactivé. Il s'exécuterait dans la requête et
ralentirait **chaque** connexion d'environ 0,5 s, au lieu des seules connexions en zone grise.

**Conditions réelles** : chaque connexion enregistre `temps_ia_ms` dans le journal (analyse du visage + calcul du
risque). La page « Performances de l'IA » affiche la moyenne par méthode de connexion.

### Traitements asynchrones (Celery)

- `lancer_evaluation_ia` : l'évaluation complète, lancée depuis le bouton de la page « Performances de l'IA ».
- `preparer_modele_risque` : le pré-calcul décrit ci-dessus.
- Les **e-mails** du module (vérification de l'adresse, invitation après import, alerte de connexion suspecte, compte
  bloqué) sont envoyés par des tâches. Elles ne reçoivent que des identifiants et relisent les données à jour en base.
  En cas de panne SMTP ou réseau, il y a **3 nouvelles tentatives** (après 30 s, 60 s et 120 s). Si Celery est
  injoignable, l'e-mail part directement, donc aucun message n'est perdu. L'e-mail de réinitialisation du mot de passe
  reste géré par Django (`PasswordResetView`).

En développement, Celery est en mode *eager* et les tâches s'exécutent directement. En production, elles passent par le
broker (Redis ou `filesystem://`) et un worker : `celery -A config worker --pool=solo -l info` sous Windows.

## 5. Limites à connaître

- Les résultats de la base enregistrée sont optimistes par construction (voir §2).
- LFW contient des photos de célébrités prises « dans la nature ». Ce n'est pas exactement une webcam d'étudiant
  (éclairage, angle, qualité). Le contrôle qualité d'EduVox en refuse environ 15 % (36 sur 222, et 196 sur 1 395 pour 300 personnes).
- L'échantillon de 50 personnes est petit (64 paires « même personne »). Le contrôle sur 258 personnes confirme les tendances.
- Le jeu de connexions est **simulé** : il montre le comportement de l'IA sur des scénarios
  réalistes, mais ce ne sont pas des attaques réelles. Les chiffres dépendent des scénarios choisis.
- La détection de vivacité (rotation de la tête) n'est pas évaluée ici. Elle demanderait des vidéos
  d'attaques (photo imprimée, écran).
