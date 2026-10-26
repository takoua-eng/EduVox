"""Analytique d'évaluation (psychométrie classique) calculée sur les tentatives soumises.

- Indice de difficulté p : part moyenne des points obtenus sur la question (0 = personne, 1 = tout le monde).
- Indice de discrimination D : p du groupe fort (27 % meilleurs scores) − p du groupe faible (27 % plus faibles).
  D ≥ 0,3 : bonne question ; 0,2–0,3 : acceptable ; < 0,2 : à revoir ; < 0 : suspecte (corrigé à vérifier).
- Fiabilité α de Cronbach (égal au KR-20 pour des questions justes/fausses) : cohérence globale du quiz.
- Analyse des distracteurs : part des étudiants qui choisissent chaque réponse.
Les seuils sont ceux de l'analyse d'items classique (Ebel & Frisbie) ; avec peu de tentatives, les
indicateurs sont signalés comme « peu fiables » plutôt que masqués.
"""

from statistics import mean, median, pstdev, pvariance

from django.db.models import Count, Q

from ..models import Question, ReponseEtudiant, TentativeQuiz

TENTATIVES_MIN_FIABLE = 10
GROUPE_EXTREME = 0.27


def _ratio(reponse):
    if reponse.points_obtenus is None or not reponse.question.points:
        return None
    return float(reponse.points_obtenus) / float(reponse.question.points)


def cronbach_alpha(matrice):
    """α de Cronbach sur une matrice étudiants × questions (valeurs entre 0 et 1). None si incalculable."""
    if len(matrice) < 2 or len(matrice[0]) < 2:
        return None
    k = len(matrice[0])
    variance_totale = pvariance([sum(ligne) for ligne in matrice])
    if variance_totale == 0:
        return None
    somme_variances = sum(pvariance([ligne[j] for ligne in matrice]) for j in range(k))
    return round(k / (k - 1) * (1 - somme_variances / variance_totale), 2)


def discrimination(scores_par_etudiant, valeurs_question):
    """D = p(groupe fort) − p(groupe faible), groupes de 27 % selon le score total. None si moins de 4 étudiants."""
    paires = [(scores_par_etudiant[e], v) for e, v in valeurs_question.items() if e in scores_par_etudiant]
    if len(paires) < 4:
        return None
    paires.sort(key=lambda p: p[0])
    taille = max(1, round(len(paires) * GROUPE_EXTREME))
    faible = [v for _, v in paires[:taille]]
    fort = [v for _, v in paires[-taille:]]
    return round(mean(fort) - mean(faible), 2)


def diagnostic(p, d, n):
    """Alertes lisibles pour l'enseignant (texte, niveau)."""
    alertes = []
    if p is None:
        return alertes
    if p >= 0.9:
        alertes.append(("Très facile : presque tout le monde réussit.", "info"))
    elif p <= 0.3:
        alertes.append(("Très difficile : notion à retravailler ou énoncé à clarifier.", "attente"))
    if d is not None:
        if d < 0:
            alertes.append(("Discrimination négative : les meilleurs échouent plus que les autres. "
                            "Vérifiez la bonne réponse.", "erreur"))
        elif d < 0.2 and n >= TENTATIVES_MIN_FIABLE:
            alertes.append(("Peu discriminante : ne distingue pas les étudiants qui maîtrisent la notion.", "attente"))
    return alertes


def niveau_discrimination(d):
    if d is None:
        return None
    if d >= 0.3:
        return "Bonne"
    if d >= 0.2:
        return "Acceptable"
    return "À revoir" if d >= 0 else "Suspecte"


def analyser_quiz(quiz):
    """Renvoie un dictionnaire prêt pour le template et les graphiques."""
    tentatives = list(quiz.tentatives.exclude(statut=TentativeQuiz.Statut.EN_COURS))
    questions = list(quiz.questions.prefetch_related("choix"))
    reponses = list(ReponseEtudiant.objects.filter(tentative__in=tentatives)
                    .select_related("question", "choix"))

    pourcentages = [t.pourcentage for t in tentatives if t.pourcentage is not None]
    corrigees = [t for t in tentatives if t.statut == TentativeQuiz.Statut.CORRIGEE]
    scores_totaux = {t.pk: float(t.score or 0) for t in corrigees}

    # Matrice étudiants × questions (tentatives entièrement corrigées seulement)
    valeurs = {q.pk: {} for q in questions}
    for r in reponses:
        ratio = _ratio(r)
        if ratio is not None and r.question_id in valeurs:
            valeurs[r.question_id][r.tentative_id] = ratio
    matrice = [[valeurs[q.pk].get(t.pk, 0.0) for q in questions] for t in corrigees
               if all(t.pk in valeurs[q.pk] for q in questions)]

    distribution = [0] * 10
    for p in pourcentages:
        distribution[min(9, p // 10)] += 1

    items = []
    for q in questions:
        notes = list(valeurs[q.pk].values())
        p = round(mean(notes), 2) if notes else None
        d = discrimination(scores_totaux, valeurs[q.pk])
        item = {
            "question": q, "n": len(notes), "p": p, "d": d, "niveau_d": niveau_discrimination(d),
            "p_pct": round(p * 100) if p is not None else None,
            "alertes": diagnostic(p, d, len(tentatives)),
            "indices": sum(1 for r in reponses if r.question_id == q.pk and r.indice_utilise),
            "distracteurs": [],
        }
        if q.a_des_choix:
            repondues = [r for r in reponses if r.question_id == q.pk]
            total = len(repondues) or 1
            for c in q.choix.all():
                nb = sum(1 for r in repondues if r.choix_id == c.pk)
                item["distracteurs"].append({"texte": c.texte, "correct": c.est_correct, "nb": nb,
                                             "pct": round(nb * 100 / total)})
            sans = sum(1 for r in repondues if r.choix_id is None)
            if sans:
                item["distracteurs"].append({"texte": "Sans réponse", "correct": False, "nb": sans,
                                             "pct": round(sans * 100 / total)})
            faux_max = max((x for x in item["distracteurs"] if not x["correct"]), key=lambda x: x["nb"], default=None)
            bon = next((x for x in item["distracteurs"] if x["correct"]), None)
            if faux_max and bon and faux_max["nb"] > bon["nb"] and len(repondues) >= 3:
                item["alertes"].append((f"Le distracteur « {faux_max['texte']} » attire plus que la bonne réponse.",
                                        "erreur"))
        items.append(item)

    durees = [t.duree.total_seconds() for t in tentatives if t.duree]
    return {
        "n": len(tentatives),
        "n_corrigees": len(corrigees),
        "fiable": len(tentatives) >= TENTATIVES_MIN_FIABLE,
        "moyenne": round(mean(pourcentages)) if pourcentages else None,
        "mediane": round(median(pourcentages)) if pourcentages else None,
        "ecart_type": round(pstdev(pourcentages)) if len(pourcentages) > 1 else None,
        "min": min(pourcentages) if pourcentages else None,
        "max": max(pourcentages) if pourcentages else None,
        "reussite": round(100 * sum(1 for p in pourcentages if p >= 50) / len(pourcentages)) if pourcentages else None,
        "duree_moyenne_min": round(mean(durees) / 60, 1) if durees else None,
        "taux_indices": round(100 * sum(1 for t in tentatives if t.indices) / len(tentatives)) if tentatives else None,
        "alpha": cronbach_alpha(matrice),
        "n_alpha": len(matrice),
        "distribution": distribution,
        "items": items,
        "nb_alertes": sum(1 for i in items for a in i["alertes"] if a[1] in ("erreur", "attente")),
    }


def resume_pour_ia(analyse):
    """Données agrégées et anonymes envoyées à Gemini pour l'analyse de classe (aucun nom d'étudiant)."""
    lignes = []
    for item in analyse["items"]:
        q = item["question"]
        ligne = {"ordre": q.ordre, "type": q.type_question, "enonce": q.enonce[:300],
                 "reussite_pct": item["p_pct"], "discrimination": item["d"]}
        if item["distracteurs"]:
            ligne["choix"] = [{"texte": d["texte"][:120], "correct": d["correct"], "pct": d["pct"]}
                              for d in item["distracteurs"]]
        if q.type_question == Question.Type.OUVERTE:
            ligne["attendu"] = q.reponse_attendue[:400]
            # Quelques réponses courtes, sans identité, pour repérer les idées fausses
            ligne["exemples_reponses"] = list(
                ReponseEtudiant.objects.filter(question=q, tentative__statut__in=["soumise", "corrigee"])
                .exclude(texte="").order_by("points_obtenus").values_list("texte", flat=True)[:5]
            )
            ligne["exemples_reponses"] = [t[:300] for t in ligne["exemples_reponses"]]
        lignes.append(ligne)
    return {"nb_tentatives": analyse["n"], "moyenne_pct": analyse["moyenne"], "questions": lignes}


def centile(valeurs, p):
    valeurs = sorted(valeurs)
    return valeurs[min(len(valeurs) - 1, int(round(p / 100 * (len(valeurs) - 1))))]


def metriques_ia(appels_qs):
    """Performance mesurée de l'IA par type d'appel : volume, succès, latence médiane et p95."""
    appels = list(appels_qs.only("type_appel", "succes", "duree_ms", "code"))
    types = []
    for valeur, libelle in appels_qs.model.Type.choices:
        liste = [a for a in appels if a.type_appel == valeur]
        if not liste:
            continue
        durees = [a.duree_ms for a in liste if a.duree_ms is not None and a.succes]
        succes = sum(1 for a in liste if a.succes)
        types.append({"code": valeur, "libelle": libelle, "n": len(liste), "succes": succes,
                      "taux": round(100 * succes / len(liste)),
                      "mediane_ms": round(median(durees)) if durees else None,
                      "p95_ms": centile(durees, 95) if durees else None})
    total = len(appels)
    reussis = sum(1 for a in appels if a.succes)
    invalides = sum(1 for a in appels if a.code in ("json_invalide", "sortie_invalide", "hors_bareme", "indice_revele"))
    durees = [a.duree_ms for a in appels if a.duree_ms is not None and a.succes]
    return {"total": total, "taux_succes": round(100 * reussis / total) if total else None,
            "taux_invalides": round(100 * invalides / total) if total else None,
            "mediane_ms": round(median(durees)) if durees else None,
            "p95_ms": centile(durees, 95) if durees else None, "types": types}


def tableau_de_bord_global(quiz_qs):
    """Indicateurs pour l'administrateur (tous les quiz)."""
    tentatives = TentativeQuiz.objects.filter(quiz__in=quiz_qs).exclude(statut=TentativeQuiz.Statut.EN_COURS)
    pourcentages = [t.pourcentage for t in tentatives.only("score", "score_max") if t.pourcentage is not None]
    statuts = dict(quiz_qs.values_list("statut").annotate(n=Count("pk")))
    return {
        "nb_quiz": quiz_qs.count(),
        "nb_publies": statuts.get("publie", 0),
        "nb_brouillons": statuts.get("brouillon", 0),
        "nb_archives": statuts.get("archive", 0),
        "nb_ia": quiz_qs.filter(origine="ia").count(),
        "nb_tentatives": tentatives.count(),
        "nb_etudiants": tentatives.values("etudiant").distinct().count(),
        "moyenne": round(mean(pourcentages)) if pourcentages else None,
        "en_attente": tentatives.filter(statut=TentativeQuiz.Statut.SOUMISE).count(),
        "corrections_ia": ReponseEtudiant.objects.filter(tentative__quiz__in=quiz_qs).filter(
            Q(statut_correction="ia") | Q(points_ia__isnull=False)).count(),
    }
