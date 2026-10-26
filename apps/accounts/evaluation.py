"""Évaluation des deux IA du module : reconnaissance faciale et détection des connexions suspectes.

Utilisé par la commande `python manage.py evaluer_ia` et par la tâche Celery `lancer_evaluation_ia`.
Seuls les résultats (EvaluationIA) sont enregistrés : aucune donnée de test n'est écrite en base,
les connexions simulées sont des objets JournalConnexion jamais sauvegardés.
"""

import random
import time
from datetime import datetime, timedelta
from itertools import combinations
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from ai_services import VisageErreur, analyser_visage, similarite
from ai_services.visage import DOSSIER_MODELES, MODELES

from .metriques import courbe_seuils, resume_metriques, resume_scores, stats_temps, taux_egal_erreur
from .models import EvaluationIA, JournalConnexion, ProfilFacial, Utilisateur
from .securite import SEUIL_SUSPECT, cache_modeles, cle_modele, evaluer_risque, preparer_modele_heures
from .views_visage import MARGE_IDENTIFICATION, NB_CAPTURES, seuil_identification

SEUILS_COMPARES = [0.30, 0.363, 0.40, 0.45]
SEUILS_COURBE = [0.20, 0.25, 0.30, 0.35, 0.363, 0.40, 0.45, 0.50, 0.55, 0.60]
EXTENSIONS_IMAGES = {".jpg", ".jpeg", ".png"}
MAX_IMAGES_PAR_PERSONNE = 10        # 3 pour la galerie (comme l'enregistrement) + 7 photos de test au plus
DOSSIER_INCONNUS = "inconnus"
DOSSIER_TEST_PAR_DEFAUT = Path(settings.BASE_DIR) / "ai_services" / "donnees_test"
TAILLE_GALERIE_TEMPS = 50           # profils comparés pour mesurer le temps d'une identification 1:N


class EvaluationImpossible(Exception):
    """L'évaluation ne peut pas être faite (modèles absents, pas assez de visages…). Message affichable."""


def seuil_visage():
    return getattr(settings, "FACE_SEUIL", 0.363)


def modeles_presents():
    """Les modèles ONNX sont-ils téléchargés ? (on ne les télécharge pas pendant une évaluation)"""
    return all((DOSSIER_MODELES / nom).exists() and (DOSSIER_MODELES / nom).stat().st_size > 10_000
               for nom, _ in MODELES.values())


def meilleur_score(encodage, galerie_personne):
    """Comme ProfilFacial.score : meilleure similarité avec les captures d'une personne."""
    return max(similarite(encodage, reference) for reference in galerie_personne)


def identifier_en_memoire(encodage, galerie, seuil, scores=None):
    """Identification « 1 parmi N », même règle que la connexion : seuil + marge avec le 2e plus proche.

    Renvoie le nom reconnu, ou None si le visage est refusé.
    """
    scores = scores or {nom: meilleur_score(encodage, refs) for nom, refs in galerie.items()}
    classes = sorted(scores.items(), key=lambda s: s[1], reverse=True)
    premier, score = classes[0]
    deuxieme = classes[1][1] if len(classes) > 1 else 0.0
    if score < seuil or score - deuxieme < MARGE_IDENTIFICATION:
        return None
    return premier


# =====================================================================
# 1. Reconnaissance faciale
# =====================================================================

def _resultats_visage(scores_meme, scores_differents, seuils):
    seuil = seuil_visage()
    return {
        "seuil": seuil,
        "libelle_positif": "Même personne",
        "libelle_negatif": "Personnes différentes",
        "nb_paires_meme": len(scores_meme),
        "nb_paires_differentes": len(scores_differents),
        "score_moyen_meme": round(sum(scores_meme) / len(scores_meme), 3) if scores_meme else None,
        "score_moyen_differentes": round(sum(scores_differents) / len(scores_differents), 3) if scores_differents else None,
        "verification": resume_scores(scores_meme, scores_differents, seuil),
        "seuils": courbe_seuils(scores_meme, scores_differents, seuils),
        "eer": taux_egal_erreur(scores_meme, scores_differents),
    }


def evaluer_visage_base():
    """Paires construites à partir des encodages déjà enregistrés (ProfilFacial). Lecture seule."""
    profils = [(p.utilisateur.username, p.encodages) for p in ProfilFacial.objects.select_related("utilisateur")]
    if len(profils) < 2:
        raise EvaluationImpossible(
            f"{len(profils)} visage(s) enregistré(s) : il en faut au moins 2 pour comparer des personnes différentes. "
            "Utilisez plutôt --images avec un jeu de photos (ex. LFW).")

    scores_meme = [similarite(a, b) for _, encodages in profils for a, b in combinations(encodages, 2)]
    scores_differents = [similarite(a, b) for (_, enc_a), (_, enc_b) in combinations(profils, 2)
                         for a in enc_a for b in enc_b]
    resultats = _resultats_visage(scores_meme, scores_differents, SEUILS_COMPARES)
    resultats.update({
        "source": f"Base enregistrée : {len(profils)} visages (résultat optimiste)",
        "nb_personnes": len(profils),
        "avertissement": "Résultat optimiste : à l'enregistrement, les 3 captures doivent déjà se ressembler "
                         "(similarité >= seuil) et un visage proche d'un autre compte est refusé. "
                         "Les paires de la base ont donc déjà été filtrées par le seuil.",
    })
    return resultats


def lister_images(dossier, max_personnes=50, seed=42):
    """Lit un dossier « une personne = un sous-dossier » (structure de LFW ou ai_services/donnees_test).

    Renvoie (connus, inconnus) : connus = {nom: [chemins]} (au moins 2 photos), inconnus = [chemins].
    Les personnes n'ayant qu'une photo (fréquent dans LFW) servent d'inconnus.
    """
    dossier = Path(dossier)
    if not dossier.is_dir():
        raise EvaluationImpossible(f"Dossier d'images introuvable : {dossier}")
    sous_dossiers = sorted(d for d in dossier.iterdir() if d.is_dir())
    # Archive LFW décompressée avec un dossier englobant (ex. lfw/lfw/<Nom>/) : on descend d'un niveau
    if len(sous_dossiers) == 1 and not _images(sous_dossiers[0]):
        return lister_images(sous_dossiers[0], max_personnes, seed)

    hasard = random.Random(seed)
    personnes, inconnus = {}, []
    for sous_dossier in sous_dossiers:
        images = _images(sous_dossier)
        if sous_dossier.name.lower() == DOSSIER_INCONNUS:
            inconnus.extend(images)
        elif len(images) >= 2:
            personnes[sous_dossier.name] = images[:MAX_IMAGES_PAR_PERSONNE]
        elif images:
            inconnus.append(images[0])
    if len(personnes) > max_personnes:
        noms = hasard.sample(sorted(personnes), max_personnes)
        personnes = {nom: personnes[nom] for nom in sorted(noms)}
    if len(inconnus) > max_personnes:
        inconnus = sorted(hasard.sample(inconnus, max_personnes))
    return personnes, inconnus


def _images(dossier):
    return sorted(f for f in dossier.iterdir() if f.is_file() and f.suffix.lower() in EXTENSIONS_IMAGES)


def _encoder(chemins, ignorees):
    """Encode chaque photo ; les photos refusées (pas de visage, floue…) sont comptées par motif."""
    encodages = []
    for chemin in chemins:
        try:
            encodages.append(analyser_visage(chemin.read_bytes())["encodage"])
        except VisageErreur as e:
            motif = str(e).split(".")[0]
            ignorees[motif] = ignorees.get(motif, 0) + 1
    return encodages


def evaluer_visage_images(dossier, max_personnes=50, seed=42):
    """Évaluation sur de vraies photos : vérification 1:1 (courbe FAR/FRR, EER) et identification 1:N.

    Galerie de chaque personne : son profil enregistré si le dossier porte son nom d'utilisateur,
    sinon ses 3 premières photos (comme à l'enregistrement). Les autres photos servent de tests.
    """
    if not modeles_presents():
        raise EvaluationImpossible("Modèles de reconnaissance faciale absents : lancez « python manage.py modeles_visage ».")
    personnes, chemins_inconnus = lister_images(dossier, max_personnes, seed)
    profils = {p.utilisateur.username: p.encodages
               for p in ProfilFacial.objects.filter(utilisateur__username__in=list(personnes)).select_related("utilisateur")}

    ignorees, galerie, tests = {}, {}, []
    for nom, chemins in personnes.items():
        encodages = _encoder(chemins, ignorees)
        if nom in profils:
            galerie[nom], photos_test = profils[nom], encodages
        else:
            nb_galerie = min(NB_CAPTURES, len(encodages) - 1)
            galerie[nom], photos_test = encodages[:nb_galerie], encodages[nb_galerie:]
        if not galerie[nom] or not photos_test:
            galerie.pop(nom)  # pas assez de photos utilisables pour cette personne
            continue
        tests.extend((nom, e) for e in photos_test)
    tests.extend((None, e) for e in _encoder(chemins_inconnus, ignorees))

    if len(galerie) < 2:
        raise EvaluationImpossible(
            "Moins de 2 personnes utilisables (il faut au moins 2 photos avec un visage détecté par personne).")
    tests = [(nom, e) for nom, e in tests if nom is None or nom in galerie]

    scores_meme, scores_differents, tests_scores = [], [], []
    for nom, encodage in tests:
        scores = {autre: meilleur_score(encodage, refs) for autre, refs in galerie.items()}
        for autre, score in scores.items():
            (scores_meme if autre == nom else scores_differents).append(score)
        tests_scores.append((nom, scores))

    seuils_identification = sorted({seuil_visage(), 0.40, seuil_identification()})
    resultats = _resultats_visage(scores_meme, scores_differents, SEUILS_COURBE)
    resultats.update({
        "source": f"Jeu d'images « {Path(dossier).name} » : {len(galerie)} personnes, "
                  f"{sum(1 for nom, _ in tests if nom is None)} photos d'inconnus",
        "dossier": Path(dossier).name,
        "nb_personnes": len(galerie),
        "nb_photos_test": len(tests),
        "nb_ignorees": sum(ignorees.values()),
        "ignorees_par_motif": ignorees,
        "nb_profils_reels": sum(1 for nom in galerie if nom in profils),
        "identification": evaluer_identification(tests_scores, seuil_identification()),
        "identification_par_seuil": [evaluer_identification(tests_scores, s) for s in seuils_identification],
    })
    return resultats


def evaluer_identification(tests_scores, seuil):
    """Identification 1 parmi N avec la règle de la connexion (seuil + marge de 0,05).

    `tests_scores` : [(nom réel ou None pour un inconnu, {nom de la galerie: score})].
    Positif = accepté. VP = bonne personne, FN = personne connue refusée,
    FP = accepté sous un autre nom (personne connue confondue ou inconnu accepté), VN = inconnu refusé.
    """
    vrais, predits, comptes = [], [], {"connus": 0, "correctes": 0, "confusions": 0, "inconnus": 0, "inconnus_acceptes": 0}
    for nom, scores in tests_scores:
        reconnu = identifier_en_memoire(None, None, seuil, scores)
        predits.append(int(reconnu is not None))
        vrais.append(int(nom is not None and reconnu in (None, nom)))
        if nom is None:
            comptes["inconnus"] += 1
            comptes["inconnus_acceptes"] += reconnu is not None
        else:
            comptes["connus"] += 1
            comptes["correctes"] += reconnu == nom
            comptes["confusions"] += reconnu not in (None, nom)

    def taux(n, total):
        return round(100 * n / total, 1) if total else 0.0

    return {
        "seuil": seuil,
        "marge": MARGE_IDENTIFICATION,
        "nb_connus": comptes["connus"],
        "nb_inconnus": comptes["inconnus"],
        "taux_correct": taux(comptes["correctes"], comptes["connus"]),
        "taux_confusions": taux(comptes["confusions"], comptes["connus"]),
        "taux_inconnus_acceptes": taux(comptes["inconnus_acceptes"], comptes["inconnus"]),
        **resume_metriques(vrais, predits),
    }


# =====================================================================
# 2. Connexions suspectes : jeu simulé reproductible, sans écriture en base
# =====================================================================

# (nom, heures habituelles, appareil habituel, réseau habituel)
PROFILS_SIMULES = [
    ("sim_matinal", [8, 9, 10], "Chrome sur Windows", "41.226.12"),
    ("sim_apres_midi", [13, 14, 15, 16], "Firefox sur Linux", "197.15.40"),
    ("sim_soir", [19, 20, 21], "Safari sur Mac", "102.157.8"),
    ("sim_mobile", [12, 18, 22], "Chrome sur Android", "196.203.77"),
    ("sim_journee", [9, 11, 14, 17], "Edge sur Windows", "41.230.5"),
    ("sim_tardif", [21, 22, 23], "Chrome sur Windows", "197.2.110"),
]
APPAREILS = ["Chrome sur Windows", "Firefox sur Windows", "Chrome sur Android", "Safari sur iPhone/iPad",
             "Safari sur Mac", "Firefox sur Linux", "Edge sur Windows", "Opera sur Windows"]

NORMALE, SUSPECTE = 0, 1
# L'étiquette dépend de QUI se connecte (l'utilisateur ou un attaquant), pas des règles de l'IA.
# (scénario, étiquette, heure, appareil, réseau, nombre de cas par utilisateur)
SCENARIOS = [
    ("Habitude : même appareil, même réseau", NORMALE, "habituelle", "meme", "meme", 3),
    ("Utilisateur sur un autre réseau (café, 4G)", NORMALE, "habituelle", "meme", "nouveau", 1),
    ("Utilisateur avec un nouveau téléphone, à la maison", NORMALE, "habituelle", "nouveau", "meme", 1),
    ("Utilisateur qui révise tard, sur son appareil", NORMALE, "inhabituelle", "meme", "meme", 1),
    ("Utilisateur un peu en avance ou en retard", NORMALE, "limite", "meme", "meme", 1),
    ("Utilisateur avec un nouveau téléphone en 4G", NORMALE, "habituelle", "nouveau", "nouveau", 1),
    ("Utilisateur en voyage avec son ordinateur", NORMALE, "inhabituelle", "meme", "nouveau", 1),
    ("Attaquant : autre appareil, autre réseau", SUSPECTE, "habituelle", "nouveau", "nouveau", 2),
    ("Attaquant la nuit, autre appareil et réseau", SUSPECTE, "inhabituelle", "nouveau", "nouveau", 1),
    ("Attaquant sur le même Wi-Fi (campus)", SUSPECTE, "habituelle", "nouveau", "meme", 1),
    ("Attaquant avec le même navigateur, heure habituelle", SUSPECTE, "habituelle", "meme", "nouveau", 1),
    ("Attaquant avec le même navigateur, heure limite", SUSPECTE, "limite", "meme", "nouveau", 1),
    ("Attaquant avec le même navigateur, la nuit", SUSPECTE, "inhabituelle", "meme", "nouveau", 1),
]
JOURS_HISTORIQUE = 40


def _ecart_cercle(a, b):
    difference = abs(a - b) % 24
    return min(difference, 24 - difference)


def _heure(hasard, heures, type_heure):
    """Heure (entière) du cas : habituelle, limite (≈ 2 h d'écart, zone de l'Isolation Forest) ou inhabituelle (≥ 5 h)."""
    if type_heure == "habituelle":
        return hasard.choice(heures)
    ecart_voulu = 2 if type_heure == "limite" else 5
    candidates = [h for h in range(24) if (min(_ecart_cercle(h, x) for x in heures) == ecart_voulu
                                           if type_heure == "limite"
                                           else min(_ecart_cercle(h, x) for x in heures) >= ecart_voulu)]
    return hasard.choice(candidates)


def _nouveau_reseau(hasard, reseau):
    while True:
        candidat = f"{hasard.randint(100, 223)}.{hasard.randint(0, 255)}.{hasard.randint(0, 255)}"
        if candidat != reseau:
            return candidat


def generer_cas_connexions(seed=42):
    """Construit l'historique (non sauvegardé) et les cas étiquetés de chaque utilisateur simulé."""
    hasard = random.Random(seed)
    # Date fixe : le jeu est identique d'une exécution à l'autre
    aujourdhui = timezone.make_aware(datetime(2026, 6, 15))
    jeu = []
    for nom, heures, appareil, reseau in PROFILS_SIMULES:
        utilisateur = Utilisateur(username=nom)  # jamais sauvegardé
        historique = sorted((
            JournalConnexion(utilisateur=utilisateur, succes=True, suspect=False, appareil=appareil,
                             ip=f"{reseau}.{hasard.randint(2, 250)}",
                             date=aujourdhui - timedelta(days=jour) + timedelta(hours=hasard.choice(heures),
                                                                                 minutes=hasard.randint(0, 59)))
            for jour in range(1, JOURS_HISTORIQUE + 1)), key=lambda c: c.date, reverse=True)
        for scenario, etiquette, type_heure, type_appareil, type_reseau, nombre in SCENARIOS:
            for _ in range(nombre):
                date = aujourdhui + timedelta(hours=_heure(hasard, heures, type_heure), minutes=hasard.randint(0, 59))
                autre_appareil = hasard.choice([a for a in APPAREILS if a != appareil])
                reseau_cas = reseau if type_reseau == "meme" else _nouveau_reseau(hasard, reseau)
                jeu.append({
                    "utilisateur": utilisateur, "historique": historique, "scenario": scenario,
                    "etiquette": etiquette, "date": date,
                    "appareil": appareil if type_appareil == "meme" else autre_appareil,
                    "ip": f"{reseau_cas}.{hasard.randint(2, 250)}",
                })
    return jeu


def evaluer_connexions(seed=42):
    """Passe chaque cas simulé dans evaluer_risque et compare la décision à l'étiquette."""
    jeu = generer_cas_connexions(seed)
    vrais, predits, par_scenario, erreurs = [], [], {}, []
    for cas in jeu:
        score, raisons = evaluer_risque(cas["utilisateur"], cas["ip"], cas["appareil"], cas["date"],
                                        historique=cas["historique"])
        predit = int(score >= SEUIL_SUSPECT)
        vrais.append(cas["etiquette"])
        predits.append(predit)
        ligne = par_scenario.setdefault(cas["scenario"], {
            "scenario": cas["scenario"], "attendu": "Suspecte" if cas["etiquette"] else "Normale",
            "nb": 0, "corrects": 0, "score_moyen": 0})
        ligne["nb"] += 1
        ligne["corrects"] += predit == cas["etiquette"]
        ligne["score_moyen"] += score
        if predit != cas["etiquette"] and len(erreurs) < 20:
            erreurs.append({"utilisateur": cas["utilisateur"].username, "scenario": cas["scenario"], "score": score,
                            "heure": f"{timezone.localtime(cas['date']):%H:%M}", "raisons": raisons})
    for ligne in par_scenario.values():
        ligne["score_moyen"] = round(ligne["score_moyen"] / ligne["nb"])
        ligne["taux_correct"] = round(100 * ligne["corrects"] / ligne["nb"], 1)
    return {
        "source": f"Jeu simulé de connexions : {len(PROFILS_SIMULES)} utilisateurs, {len(jeu)} cas (graine {seed})",
        "seed": seed,
        "seuil": SEUIL_SUSPECT,
        "libelle_positif": "Suspecte",
        "libelle_negatif": "Normale",
        "nb_utilisateurs": len(PROFILS_SIMULES),
        "nb_cas": len(jeu),
        "metriques": resume_metriques(vrais, predits),
        "par_scenario": list(par_scenario.values()),
        "erreurs": erreurs,
    }


# =====================================================================
# 3. Temps de réponse
# =====================================================================

def _chronometrer(fonction, elements, preparer=None):
    """Durée de fonction(element) pour chaque élément ; preparer(element) est appelé hors chronométrage."""
    durees = []
    for element in elements:
        if preparer:
            preparer(element)
        debut = time.perf_counter()
        fonction(element)
        durees.append((time.perf_counter() - debut) * 1000)
    return stats_temps(durees)


def _image_de_test(dossier):
    """Première photo du dossier où un visage est bien détecté (en octets), sinon None."""
    if not dossier or not Path(dossier).is_dir():
        return None
    for chemin in sorted(Path(dossier).rglob("*"))[:500]:
        if chemin.suffix.lower() in EXTENSIONS_IMAGES:
            try:
                analyser_visage(chemin.read_bytes())  # charge aussi les modèles (hors chronométrage)
                return chemin.read_bytes()
            except VisageErreur:
                continue
    return None


def mesurer_temps(repetitions=100, dossier_images=None, seed=42):
    hasard = random.Random(seed)
    resultats = {"repetitions": repetitions, "source": f"Mesure sur {repetitions} répétitions (time.perf_counter)"}

    # Détection + encodage d'un visage (OpenCV YuNet + SFace)
    if not modeles_presents():
        resultats["detection_encodage"], resultats["note_detection"] = None, "Modèles ONNX absents."
    else:
        image = _image_de_test(dossier_images or DOSSIER_TEST_PAR_DEFAUT)
        if image is None:
            resultats["detection_encodage"] = None
            resultats["note_detection"] = "Aucune photo de visage disponible : utilisez --images DOSSIER."
        else:
            resultats["detection_encodage"] = _chronometrer(lambda _: analyser_visage(image), range(repetitions))

    # Comparaison 1:N : profils réels (lecture seule) complétés par des encodages aléatoires jusqu'à 50
    galerie = {p.utilisateur.username: p.encodages for p in ProfilFacial.objects.select_related("utilisateur")}
    nb_reels = len(galerie)
    while len(galerie) < TAILLE_GALERIE_TEMPS:
        galerie[f"simule_{len(galerie)}"] = [[hasard.uniform(-1, 1) for _ in range(128)] for _ in range(NB_CAPTURES)]
    sonde = [hasard.uniform(-1, 1) for _ in range(128)]
    resultats["comparaison_1n"] = _chronometrer(lambda _: identifier_en_memoire(sonde, galerie, seuil_visage()),
                                                range(repetitions))
    resultats["taille_galerie"], resultats["nb_profils_reels"] = len(galerie), nb_reels

    # Score de risque d'une connexion (cas du jeu simulé, avec ou sans Isolation Forest selon l'heure).
    jeu = generer_cas_connexions(seed)
    cas = (jeu * (repetitions // len(jeu) + 1))[:repetitions]

    def un_calcul(c):
        evaluer_risque(c["utilisateur"], c["ip"], c["appareil"], c["date"], historique=c["historique"])

    def oublier_modele(c):
        cache_modeles().delete(cle_modele(c["utilisateur"], c["historique"]))

    def simuler_worker(c):
        """Ce que fait la tâche preparer_modele_risque après la connexion précédente (hors chronométrage)."""
        oublier_modele(c)
        preparer_modele_heures(c["utilisateur"], c["historique"])

    # Sans pré-calcul : le modèle est entraîné pendant la connexion (historique qui vient de changer)
    resultats["evaluer_risque_sans_cache"] = _chronometrer(un_calcul, cas, preparer=oublier_modele)
    # Scénario réel : le worker a préparé le modèle juste après la connexion précédente
    resultats["evaluer_risque_apres_precalcul"] = _chronometrer(un_calcul, cas, preparer=simuler_worker)
    return resultats


# =====================================================================
# Lancement complet (commande et tâche Celery)
# =====================================================================

def lancer_evaluation(images=None, max_personnes=50, repetitions=100, seed=42):
    """Lance toutes les évaluations possibles et enregistre leurs résultats.

    Renvoie (liste des EvaluationIA créées, liste des messages pour les évaluations impossibles).
    """
    etapes = [
        (EvaluationIA.Type.VISAGE_BASE, evaluer_visage_base, {}),
        (EvaluationIA.Type.CONNEXIONS, evaluer_connexions, {"seed": seed}),
        (EvaluationIA.Type.TEMPS, mesurer_temps, {"repetitions": repetitions, "dossier_images": images, "seed": seed}),
    ]
    if images:
        etapes.insert(1, (EvaluationIA.Type.VISAGE_IMAGES, evaluer_visage_images,
                          {"dossier": images, "max_personnes": max_personnes, "seed": seed}))

    evaluations, messages = [], []
    for type_evaluation, fonction, parametres in etapes:
        debut = time.perf_counter()
        try:
            resultats = fonction(**parametres)
        except EvaluationImpossible as e:
            messages.append(f"{type_evaluation.label} : {e}")
            continue
        evaluations.append(EvaluationIA.objects.create(
            type=type_evaluation, resultats=resultats, duree_totale_ms=round((time.perf_counter() - debut) * 1000)))
    return evaluations, messages
