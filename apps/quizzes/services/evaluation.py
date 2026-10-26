"""Logique métier : soumission d'une tentative, calcul du score côté serveur et versions de quiz."""

import secrets
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from ..models import COEF_INDICE, REPONSE_OUVERTE_MAX, Choix, Question, Quiz, ReponseEtudiant, TentativeQuiz


class SoumissionInvalide(Exception):
    """Réponses incohérentes avec le quiz (choix étranger, identifiant falsifié...)."""


class DejaSoumise(Exception):
    """La tentative a déjà été soumise : on ne recalcule rien."""


def lire_reponses(quiz, donnees):
    """Lit les réponses envoyées pour CE quiz uniquement.

    Les champs attendus sont `question_<id>` ; les autres clés sont ignorées. Un choix qui
    n'appartient pas à la question (ou au quiz) est refusé. Renvoie {question: (choix, texte)}.
    """
    questions = list(quiz.questions.prefetch_related("choix"))
    reponses, erreurs = {}, {}
    for question in questions:
        valeur = (donnees.get(f"question_{question.pk}") or "").strip()
        if question.type_question == Question.Type.OUVERTE:
            if len(valeur) > REPONSE_OUVERTE_MAX:
                erreurs[question.pk] = f"Réponse trop longue ({REPONSE_OUVERTE_MAX} caractères maximum)."
            reponses[question] = (None, valeur)
            continue
        choix = None
        if valeur:
            choix_de_la_question = {str(c.pk): c for c in question.choix.all()}
            choix = choix_de_la_question.get(valeur)
            if choix is None:
                erreurs[question.pk] = "Choix invalide pour cette question."
        reponses[question] = (choix, "")
    return reponses, erreurs


def points_avec_indice(points, indice_utilise):
    """Une question aidée par un indice IA rapporte COEF_INDICE de ses points (arrondi au centième)."""
    if not indice_utilise:
        return points
    return (Decimal(points) * COEF_INDICE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def soumettre(tentative_id, etudiant, donnees):
    """Enregistre les réponses et calcule le score, une seule fois (verrou sur la tentative).

    Renvoie (tentative, nb de réponses ouvertes à corriger). Lève DejaSoumise ou SoumissionInvalide.
    """
    with transaction.atomic():
        tentative = (TentativeQuiz.objects.select_for_update()
                     .select_related("quiz").get(pk=tentative_id, etudiant=etudiant))
        if tentative.statut != TentativeQuiz.Statut.EN_COURS:
            raise DejaSoumise
        reponses, erreurs = lire_reponses(tentative.quiz, donnees)
        if erreurs:
            raise SoumissionInvalide(erreurs)

        a_corriger = 0
        objets = []
        aidees = set(tentative.indices or {})
        for question, (choix, texte) in reponses.items():
            reponse = ReponseEtudiant(tentative=tentative, question=question, choix=choix, texte=texte,
                                      indice_utilise=str(question.pk) in aidees)
            if question.type_question == Question.Type.OUVERTE:
                if texte:
                    reponse.statut_correction = ReponseEtudiant.Correction.EN_ATTENTE
                    a_corriger += 1
                else:
                    # Pas de réponse : 0 point, sans appel à l'IA
                    reponse.points_obtenus, reponse.est_correcte = 0, False
                    reponse.explication_correction = "Aucune réponse."
            else:
                juste = bool(choix and choix.est_correct)
                reponse.est_correcte = juste
                reponse.points_obtenus = points_avec_indice(question.points, reponse.indice_utilise) if juste else 0
            objets.append(reponse)
        ReponseEtudiant.objects.bulk_create(objets)

        tentative.statut = TentativeQuiz.Statut.SOUMISE
        tentative.date_soumission = timezone.now()
        tentative.score_max = tentative.quiz.points_total()
        tentative.save(update_fields=["statut", "date_soumission", "score_max"])
        tentative.recalculer_score()
    return tentative, a_corriger


def commencer(quiz, etudiant):
    """Reprend la tentative en cours, ou en crée une si le nombre maximal n'est pas atteint (sinon None)."""
    with transaction.atomic():
        # Verrou sur le quiz : deux clics simultanés ne créent pas deux tentatives
        Quiz.objects.select_for_update().get(pk=quiz.pk)
        en_cours = quiz.tentatives.filter(etudiant=etudiant, statut=TentativeQuiz.Statut.EN_COURS).first()
        if en_cours:
            return en_cours
        nb = quiz.tentatives.filter(etudiant=etudiant).count()
        if nb >= quiz.tentatives_max:
            return None
        # Graine propre à la tentative : ordre des choix différent d'un étudiant à l'autre
        return TentativeQuiz.objects.create(quiz=quiz, etudiant=etudiant, numero=nb + 1,
                                            graine=secrets.randbelow(2 ** 31))


def nouvelle_version(quiz):
    """Copie le quiz (questions et choix) dans un nouveau brouillon modifiable. L'original et ses tentatives
    restent intacts : les scores déjà obtenus restent calculés sur les questions d'origine."""
    with transaction.atomic():
        copie = Quiz.objects.create(
            titre=quiz.titre, description=quiz.description, cours=quiz.cours, titre_cours=quiz.titre_cours,
            enseignant=quiz.enseignant, difficulte=quiz.difficulte, tentatives_max=quiz.tentatives_max,
            afficher_corrige=quiz.afficher_corrige, origine=quiz.origine,
            version=quiz.version + 1, version_precedente=quiz,
        )
        for question in quiz.questions.prefetch_related("choix"):
            nouvelle = Question.objects.create(
                quiz=copie, ordre=question.ordre, type_question=question.type_question, enonce=question.enonce,
                points=question.points, explication=question.explication, reponse_attendue=question.reponse_attendue,
            )
            Choix.objects.bulk_create(
                Choix(question=nouvelle, texte=c.texte, est_correct=c.est_correct, ordre=c.ordre)
                for c in question.choix.all()
            )
    return copie


def publier(quiz):
    """Publie le quiz (après validation explicite de l'enseignant) et archive la version qu'il remplace."""
    with transaction.atomic():
        quiz.statut = Quiz.Statut.PUBLIE
        quiz.date_publication = timezone.now()
        quiz.save(update_fields=["statut", "date_publication", "date_modification"])
        if quiz.version_precedente_id:
            Quiz.objects.filter(pk=quiz.version_precedente_id, statut=Quiz.Statut.PUBLIE).update(
                statut=Quiz.Statut.ARCHIVE, date_modification=timezone.now()
            )
