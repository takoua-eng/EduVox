"""Tâches Celery du module quiz : génération, correction, bilan personnalisé, audit et analyse de classe.

Toutes sont idempotentes : relancées deux fois, elles ne créent pas de doublons et n'appellent pas deux fois
Gemini pour le même travail (état « réservé » posé avant l'appel).
"""

import logging
from decimal import Decimal

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from .models import AppelIA, Choix, Question, Quiz, ReponseEtudiant, TentativeQuiz, TraitementIA
from .services.assistant import generer_bilan
from .services.audit import analyser_classe, auditer
from .services.correction import corriger_reponse_ouverte
from .services.evaluation import points_avec_indice
from .services.gemini import ErreurIA, gemini_configure
from .services.generation import generer_questions, texte_source
from .services.statistiques import analyser_quiz

logger = logging.getLogger(__name__)


def journaliser(type_appel, quiz, utilisateur=None, erreur=None, meta=None, **details):
    meta = meta or {}
    AppelIA.objects.create(
        type_appel=type_appel, quiz=quiz, utilisateur=utilisateur,
        succes=erreur is None, code="ok" if erreur is None else erreur.code,
        modele=meta.get("modele", ""),
        duree_ms=meta.get("duree_ms", getattr(erreur, "duree_ms", None)),
        essais=meta.get("essais", getattr(erreur, "essais", 1)),
        details=details,
    )


@shared_task(soft_time_limit=300, time_limit=330)
def generer_quiz_ia(quiz_id):
    quiz = Quiz.objects.select_related("cours", "enseignant__utilisateur").get(pk=quiz_id)
    if quiz.statut_generation != Quiz.Generation.EN_COURS:
        return
    parametres = quiz.parametres_generation
    enseignant = quiz.enseignant.utilisateur
    try:
        if quiz.cours is None:
            raise ErreurIA("Le cours de ce quiz a été supprimé.", "source")
        texte, tronque = texte_source(quiz.cours, parametres["source"])
        questions, rejets, meta = generer_questions(
            texte, parametres["nb_questions"], parametres["types"], quiz.get_difficulte_display()
        )
    except ErreurIA as erreur:
        if erreur.code != "source":
            journaliser(AppelIA.Type.GENERATION, quiz, enseignant, erreur)
        Quiz.objects.filter(pk=quiz_id, statut_generation=Quiz.Generation.EN_COURS).update(
            statut_generation=Quiz.Generation.ERREUR, message_generation=erreur.message
        )
        return
    except Exception:
        logger.exception("Échec inattendu de la génération du quiz %s", quiz_id)
        Quiz.objects.filter(pk=quiz_id, statut_generation=Quiz.Generation.EN_COURS).update(
            statut_generation=Quiz.Generation.ERREUR,
            message_generation="Erreur inattendue pendant la génération. Réessayez ou créez les questions manuellement.",
        )
        return

    with transaction.atomic():
        quiz = Quiz.objects.select_for_update().get(pk=quiz_id)
        if quiz.statut_generation != Quiz.Generation.EN_COURS or quiz.questions.exists():
            return
        for ordre, donnees in enumerate(questions, start=1):
            question = Question.objects.create(
                quiz=quiz, ordre=ordre, type_question=donnees["type_question"], enonce=donnees["enonce"],
                points=donnees["points"], explication=donnees["explication"],
                reponse_attendue=donnees["reponse_attendue"],
            )
            Choix.objects.bulk_create(
                Choix(question=question, texte=c["texte"], est_correct=c["est_correct"], ordre=i)
                for i, c in enumerate(donnees["choix"], start=1)
            )
        message = f"{len(questions)} question(s) générée(s). Relisez-les avant de publier."
        if rejets:
            message += f" {len(rejets)} question(s) incohérente(s) ont été écartées."
        if tronque:
            message += " Le cours étant long, seul son début a été utilisé."
        quiz.statut_generation = Quiz.Generation.TERMINEE
        quiz.message_generation = message
        quiz.parametres_generation = {**parametres, "rejets": rejets, "texte_tronque": tronque}
        quiz.save(update_fields=["statut_generation", "message_generation", "parametres_generation"])
        journaliser(AppelIA.Type.GENERATION, quiz, enseignant, meta=meta,
                 demandees=parametres["nb_questions"], valides=len(questions), rejetees=len(rejets))


@shared_task(soft_time_limit=600, time_limit=660)
def corriger_tentative(tentative_id):
    """Corrige chaque réponse ouverte « en attente » de la tentative, puis recalcule le score."""
    ids = list(ReponseEtudiant.objects.filter(
        tentative_id=tentative_id, statut_correction=ReponseEtudiant.Correction.EN_ATTENTE
    ).values_list("pk", flat=True))

    for reponse_id in ids:
        # Réservation : si une autre exécution a déjà pris cette réponse, on passe
        reservee = ReponseEtudiant.objects.filter(
            pk=reponse_id, statut_correction=ReponseEtudiant.Correction.EN_ATTENTE
        ).update(statut_correction=ReponseEtudiant.Correction.EN_COURS)
        if not reservee:
            continue
        reponse = ReponseEtudiant.objects.select_related("question__quiz").get(pk=reponse_id)
        try:
            note, explication, meta = corriger_reponse_ouverte(reponse.question, reponse.texte)
        except ErreurIA as erreur:
            ReponseEtudiant.objects.filter(pk=reponse_id).update(
                statut_correction=ReponseEtudiant.Correction.ERREUR, message_erreur=erreur.message[:255],
                nb_essais_ia=reponse.nb_essais_ia + getattr(erreur, "essais", 1),
            )
            journaliser(AppelIA.Type.CORRECTION, reponse.question.quiz, erreur=erreur)
            continue
        except Exception:
            logger.exception("Échec inattendu de la correction de la réponse %s", reponse_id)
            ReponseEtudiant.objects.filter(pk=reponse_id).update(
                statut_correction=ReponseEtudiant.Correction.ERREUR,
                message_erreur="Erreur inattendue pendant la correction.",
            )
            continue

        maximum = reponse.question.points
        ReponseEtudiant.objects.filter(pk=reponse_id).update(
            statut_correction=ReponseEtudiant.Correction.IA, points_ia=note,
            points_obtenus=points_avec_indice(note, reponse.indice_utilise),
            est_correcte=True if note == maximum else (False if note == Decimal("0") else None),
            explication_correction=explication, message_erreur="", modele_ia=meta["modele"],
            duree_ia_ms=meta["duree_ms"], nb_essais_ia=reponse.nb_essais_ia + meta["essais"],
            date_correction=timezone.now(),
        )
        journaliser(AppelIA.Type.CORRECTION, reponse.question.quiz, meta=meta)

    with transaction.atomic():
        TentativeQuiz.objects.select_for_update().get(pk=tentative_id).recalculer_score()
    declencher_bilan(tentative_id)


# ---------- Bilan personnalisé, audit et analyse de classe ----------

def reserver(modele, pk, champ, relancer=False):
    """Passe le traitement IA `champ` de l'objet à « en cours » s'il ne l'est pas déjà. Vrai si réservé.

    Sans `relancer`, un traitement déjà terminé ou en erreur n'est pas refait (un seul appel IA).
    """
    with transaction.atomic():
        objet = modele.objects.select_for_update().get(pk=pk)
        etat = TraitementIA.etat(getattr(objet, champ))
        autorises = {TraitementIA.AUCUN, TraitementIA.ERREUR, TraitementIA.TERMINE} if relancer else {TraitementIA.AUCUN}
        if etat not in autorises:
            return False
        setattr(objet, champ, TraitementIA.en_cours())
        objet.save(update_fields=[champ])
    return True


def lancer_apres_validation(tache, objet_id, modele, champ):
    """Envoie la tâche après la transaction ; si Celery est injoignable, l'état passe en erreur (pas de blocage)."""
    def envoyer():
        try:
            tache.delay(objet_id)
        except Exception:
            logger.exception("Tâche %s (%s) non envoyée", tache.name, objet_id)
            modele.objects.filter(pk=objet_id).update(**{champ: TraitementIA.erreur(
                "Le service de traitement est injoignable (Celery / Redis). Réessayez plus tard.")})
    transaction.on_commit(envoyer)


def declencher_bilan(tentative_id, relancer=False):
    """Lance le bilan IA d'une tentative entièrement corrigée (une seule fois, sauf relance explicite)."""
    if not gemini_configure():
        return False
    if not TentativeQuiz.objects.filter(pk=tentative_id, statut=TentativeQuiz.Statut.CORRIGEE).exists():
        return False
    if not reserver(TentativeQuiz, tentative_id, "bilan_ia", relancer):
        return False
    lancer_apres_validation(generer_bilan_tentative, tentative_id, TentativeQuiz, "bilan_ia")
    return True


def _executer(modele, pk, champ, type_appel, calcul, utilisateur=None):
    objet = modele.objects.get(pk=pk)
    if TraitementIA.etat(getattr(objet, champ)) != TraitementIA.EN_COURS:
        return
    quiz = objet if isinstance(objet, Quiz) else objet.quiz
    try:
        resultat, meta = calcul(objet)
    except ErreurIA as erreur:
        if erreur.code != "source":
            journaliser(type_appel, quiz, utilisateur, erreur)
        modele.objects.filter(pk=pk).update(**{champ: TraitementIA.erreur(erreur.message)})
        return
    except Exception:
        logger.exception("Échec inattendu du traitement IA %s (%s)", type_appel, pk)
        modele.objects.filter(pk=pk).update(**{champ: TraitementIA.erreur("Erreur inattendue. Réessayez plus tard.")})
        return
    resultat["modele"] = meta["modele"]
    modele.objects.filter(pk=pk).update(**{champ: TraitementIA.termine(resultat)})
    journaliser(type_appel, quiz, utilisateur, meta=meta)


@shared_task(soft_time_limit=180, time_limit=200)
def generer_bilan_tentative(tentative_id):
    _executer(TentativeQuiz, tentative_id, "bilan_ia", AppelIA.Type.BILAN, generer_bilan)


@shared_task(soft_time_limit=180, time_limit=200)
def auditer_quiz_ia(quiz_id):
    quiz = Quiz.objects.select_related("enseignant__utilisateur").get(pk=quiz_id)
    _executer(Quiz, quiz_id, "audit_ia", AppelIA.Type.AUDIT, auditer, quiz.enseignant.utilisateur)


@shared_task(soft_time_limit=180, time_limit=200)
def analyser_classe_ia(quiz_id):
    quiz = Quiz.objects.select_related("enseignant__utilisateur").get(pk=quiz_id)
    _executer(Quiz, quiz_id, "analyse_ia", AppelIA.Type.ANALYSE,
              lambda q: analyser_classe(q, analyser_quiz(q)), quiz.enseignant.utilisateur)
