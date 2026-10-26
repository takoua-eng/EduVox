from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import RestrictedError

from apps.quizzes.models import Choix, Question, Quiz, ReponseEtudiant, TentativeQuiz

from .base import BaseQuizTestCase


class ContraintesModelesTests(BaseQuizTestCase):
    def test_ordre_unique_par_quiz(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Question.objects.create(quiz=self.quiz, ordre=1, enonce="Doublon", points=1)

    def test_points_hors_bornes_refuses_en_base(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Question.objects.create(quiz=self.quiz, ordre=9, enonce="Trop", points=Decimal("50"))

    def test_points_valides_par_le_modele(self):
        question = Question(quiz=self.quiz, ordre=9, enonce="Zéro point", points=Decimal("0"))
        with self.assertRaises(ValidationError):
            question.full_clean()

    def test_question_ouverte_sans_reponse_attendue_invalide(self):
        question = Question(quiz=self.quiz, ordre=9, type_question=Question.Type.OUVERTE, enonce="Expliquez", points=1)
        with self.assertRaises(ValidationError):
            question.full_clean()

    def test_tentatives_max_entre_1_et_10(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Quiz.objects.filter(pk=self.quiz.pk).update(tentatives_max=0)

    def test_une_seule_tentative_en_cours_par_etudiant(self):
        TentativeQuiz.objects.create(quiz=self.quiz, etudiant=self.etudiant, numero=1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            TentativeQuiz.objects.create(quiz=self.quiz, etudiant=self.etudiant, numero=2)

    def test_une_reponse_par_question_et_par_tentative(self):
        tentative = TentativeQuiz.objects.create(quiz=self.quiz, etudiant=self.etudiant)
        ReponseEtudiant.objects.create(tentative=tentative, question=self.q_qcm, choix=self.bon)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ReponseEtudiant.objects.create(tentative=tentative, question=self.q_qcm, choix=self.mauvais)

    def test_choix_d_une_autre_question_refuse(self):
        tentative = TentativeQuiz.objects.create(quiz=self.quiz, etudiant=self.etudiant)
        reponse = ReponseEtudiant(tentative=tentative, question=self.q_qcm, choix=self.vrai)
        with self.assertRaises(ValidationError):
            reponse.full_clean()

    def test_question_deja_repondue_non_supprimable(self):
        tentative = TentativeQuiz.objects.create(quiz=self.quiz, etudiant=self.etudiant)
        ReponseEtudiant.objects.create(tentative=tentative, question=self.q_qcm, choix=self.bon)
        with self.assertRaises(RestrictedError):
            self.q_qcm.delete()

    def test_titre_du_cours_conserve_si_le_cours_est_supprime(self):
        self.cours.delete()
        self.quiz.refresh_from_db()
        self.assertIsNone(self.quiz.cours)
        self.assertEqual(self.quiz.titre_cours, "Algèbre linéaire")


class CoherencePublicationTests(BaseQuizTestCase):
    def test_quiz_complet_publiable(self):
        self.assertEqual(self.quiz.erreurs_publication(), [])
        self.assertEqual(self.quiz.points_total(), Decimal("6"))

    def test_qcm_sans_ou_avec_deux_bonnes_reponses(self):
        self.bon.est_correct = False
        self.bon.save()
        self.assertIn("exactement une bonne réponse", " ".join(self.q_qcm.erreurs_coherence()))
        Choix.objects.filter(question=self.q_qcm).update(est_correct=True)
        self.assertIn("exactement une bonne réponse", " ".join(self.q_qcm.erreurs_coherence()))

    def test_qcm_avec_un_seul_choix(self):
        self.mauvais.delete()
        self.assertTrue(self.q_qcm.erreurs_coherence())

    def test_vrai_faux_incoherent(self):
        self.faux.texte = "Peut-être"
        self.faux.save()
        self.assertTrue(self.q_vf.erreurs_coherence())

    def test_quiz_vide_ou_sans_cours_non_publiable(self):
        vide = Quiz.objects.create(titre="Vide", cours=None, enseignant=self.prof.enseignant)
        erreurs = " ".join(vide.erreurs_publication())
        self.assertIn("aucune question", erreurs)
        self.assertIn("rattaché à un cours", erreurs)

    def test_modifiable_seulement_en_brouillon_sans_tentative(self):
        brouillon = self.creer_quiz(statut=Quiz.Statut.BROUILLON)
        self.assertTrue(brouillon.est_modifiable)
        TentativeQuiz.objects.create(quiz=brouillon, etudiant=self.etudiant)
        self.assertFalse(brouillon.est_modifiable)
        self.assertFalse(brouillon.est_supprimable)
        self.assertFalse(self.quiz.est_modifiable)  # publié
