"""Permissions par rôle et par propriétaire, y compris par accès direct aux URL."""

from django.urls import reverse

from apps.courses.models import Cours
from apps.quizzes.models import Quiz, TentativeQuiz

from .base import BaseQuizTestCase


class AccesEnseignantTests(BaseQuizTestCase):
    def test_anonyme_redirige_vers_la_connexion(self):
        reponse = self.client.get(reverse("liste-quiz"))
        self.assertEqual(reponse.status_code, 302)
        self.assertIn(reverse("connexion"), reponse.url)

    def test_autre_enseignant_ne_voit_ni_ne_gere_le_quiz(self):
        self.connecter(self.autre_prof)
        question = self.quiz.questions.first()
        self.assertEqual(self.client.get(reverse("detail-quiz", args=[self.quiz.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("resultats-quiz", args=[self.quiz.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("modifier-quiz", args=[self.quiz.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("modifier-question", args=[question.pk])).status_code, 404)
        for nom in ["supprimer-quiz", "publier-quiz", "depublier-quiz", "archiver-quiz", "nouvelle-version-quiz"]:
            with self.subTest(action=nom):
                self.assertEqual(self.client.post(reverse(nom, args=[self.quiz.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("supprimer-question", args=[question.pk])).status_code, 404)
        self.quiz.refresh_from_db()
        self.assertEqual(self.quiz.statut, Quiz.Statut.PUBLIE)

    def test_enseignant_ne_peut_pas_passer_de_quiz(self):
        self.connecter(self.prof)
        self.assertEqual(self.client.post(reverse("commencer-quiz", args=[self.quiz.pk])).status_code, 403)


class AccesEtudiantTests(BaseQuizTestCase):
    def setUp(self):
        super().setUp()
        self.connecter(self.etudiant)

    def test_etudiant_ne_peut_rien_gerer(self):
        question = self.quiz.questions.first()
        self.assertEqual(self.client.get(reverse("creer-quiz")).status_code, 403)
        self.assertEqual(self.client.get(reverse("generer-quiz")).status_code, 403)
        self.assertEqual(self.client.get(reverse("resultats-quiz", args=[self.quiz.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("publier-quiz", args=[self.quiz.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("supprimer-question", args=[question.pk])).status_code, 403)

    def test_quiz_publie_visible_sans_les_reponses(self):
        reponse = self.client.get(reverse("detail-quiz", args=[self.quiz.pk]))
        self.assertEqual(reponse.status_code, 200)
        self.assertNotContains(reponse, "Déterminant non nul")
        self.assertNotContains(reponse, "(bonne réponse)")
        self.assertNotContains(reponse, "Il indique si la matrice est inversible")

    def test_brouillon_archive_et_cours_non_public_invisibles(self):
        brouillon = self.creer_quiz(statut=Quiz.Statut.BROUILLON)
        archive = self.creer_quiz(statut=Quiz.Statut.ARCHIVE)
        for quiz in (brouillon, archive):
            self.assertEqual(self.client.get(reverse("detail-quiz", args=[quiz.pk])).status_code, 404)
            self.assertEqual(self.client.post(reverse("commencer-quiz", args=[quiz.pk])).status_code, 404)
        Cours.objects.filter(pk=self.cours.pk).update(masque=True)
        self.assertEqual(self.client.get(reverse("detail-quiz", args=[self.quiz.pk])).status_code, 404)
        reponse = self.client.get(reverse("liste-quiz"))
        self.assertNotContains(reponse, "Quiz matrices")

    def test_resultat_d_un_autre_etudiant_inaccessible(self):
        tentative = TentativeQuiz.objects.create(quiz=self.quiz, etudiant=self.autre_etudiant,
                                                 statut=TentativeQuiz.Statut.CORRIGEE, score=3, score_max=6)
        self.assertEqual(self.client.get(reverse("tentative", args=[tentative.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("etat-tentative", args=[tentative.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("tentative", args=[tentative.pk])).status_code, 404)
        reponse = self.client.get(reverse("mes-tentatives"))
        self.assertNotContains(reponse, "3 / 6")


class AccesAdministrateurTests(BaseQuizTestCase):
    def test_admin_consulte_mais_ne_gere_pas(self):
        self.connecter(self.admin)
        self.assertEqual(self.client.get(reverse("detail-quiz", args=[self.quiz.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("resultats-quiz", args=[self.quiz.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("creer-quiz")).status_code, 403)
        self.assertEqual(self.client.post(reverse("publier-quiz", args=[self.quiz.pk])).status_code, 403)

    def test_enseignant_voit_les_tentatives_de_ses_quiz_seulement(self):
        tentative = TentativeQuiz.objects.create(quiz=self.quiz, etudiant=self.etudiant,
                                                 statut=TentativeQuiz.Statut.CORRIGEE, score=0, score_max=6)
        self.connecter(self.prof)
        self.assertEqual(self.client.get(reverse("tentative", args=[tentative.pk])).status_code, 200)
        self.connecter(self.autre_prof)
        self.assertEqual(self.client.get(reverse("tentative", args=[tentative.pk])).status_code, 404)
