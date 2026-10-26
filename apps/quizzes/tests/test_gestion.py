"""CRUD enseignant, publication, archivage, versions et permissions de gestion."""

from django.urls import reverse

from apps.quizzes.models import Choix, Question, Quiz, TentativeQuiz

from .base import BaseQuizTestCase, creer_cours


class CrudQuizTests(BaseQuizTestCase):
    def setUp(self):
        super().setUp()
        self.connecter(self.prof)
        self.brouillon = self.creer_quiz(statut=Quiz.Statut.BROUILLON)

    def test_liste_ne_montre_que_ses_quiz(self):
        autre_cours = creer_cours(self.autre_prof.enseignant, titre="Cours de l'autre")
        Quiz.objects.create(titre="Quiz de l'autre", cours=autre_cours, enseignant=self.autre_prof.enseignant)
        reponse = self.client.get(reverse("liste-quiz"))
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "Quiz matrices")
        self.assertNotContains(reponse, "Quiz de l&#x27;autre")

    def test_creer_quiz_sur_son_cours(self):
        reponse = self.client.post(reverse("creer-quiz"), {
            "titre": "Nouveau quiz", "cours": self.cours.pk, "difficulte": "facile", "tentatives_max": 2,
            "afficher_corrige": "on",
        })
        quiz = Quiz.objects.get(titre="Nouveau quiz")
        self.assertRedirects(reponse, reverse("detail-quiz", args=[quiz.pk]))
        self.assertEqual(quiz.statut, Quiz.Statut.BROUILLON)
        self.assertEqual(quiz.enseignant, self.prof.enseignant)
        self.assertEqual(quiz.titre_cours, self.cours.titre)

    def test_creer_quiz_sur_le_cours_d_un_autre_refuse(self):
        autre_cours = creer_cours(self.autre_prof.enseignant, titre="Cours de l'autre")
        reponse = self.client.post(reverse("creer-quiz"), {
            "titre": "Pirate", "cours": autre_cours.pk, "difficulte": "facile", "tentatives_max": 1,
        })
        self.assertEqual(reponse.status_code, 400)
        self.assertFalse(Quiz.objects.filter(titre="Pirate").exists())

    def test_modifier_un_brouillon(self):
        reponse = self.client.post(reverse("modifier-quiz", args=[self.brouillon.pk]), {
            "titre": "Titre modifié", "cours": self.cours.pk, "difficulte": "difficile", "tentatives_max": 3,
        })
        self.assertRedirects(reponse, reverse("detail-quiz", args=[self.brouillon.pk]))
        self.brouillon.refresh_from_db()
        self.assertEqual(self.brouillon.titre, "Titre modifié")
        self.assertFalse(self.brouillon.afficher_corrige)

    def test_supprimer_un_brouillon(self):
        reponse = self.client.post(reverse("supprimer-quiz", args=[self.brouillon.pk]))
        self.assertRedirects(reponse, reverse("liste-quiz"))
        self.assertFalse(Quiz.objects.filter(pk=self.brouillon.pk).exists())

    def test_suppression_par_get_impossible(self):
        reponse = self.client.get(reverse("supprimer-quiz", args=[self.brouillon.pk]))
        self.assertEqual(reponse.status_code, 405)
        self.assertTrue(Quiz.objects.filter(pk=self.brouillon.pk).exists())

    def test_quiz_avec_tentatives_non_supprimable_mais_archivable(self):
        TentativeQuiz.objects.create(quiz=self.quiz, etudiant=self.etudiant)
        self.client.post(reverse("supprimer-quiz", args=[self.quiz.pk]))
        self.assertTrue(Quiz.objects.filter(pk=self.quiz.pk).exists())
        self.client.post(reverse("archiver-quiz", args=[self.quiz.pk]))
        self.quiz.refresh_from_db()
        self.assertEqual(self.quiz.statut, Quiz.Statut.ARCHIVE)
        self.assertEqual(self.quiz.tentatives.count(), 1)

    def test_pages_de_gestion_s_affichent(self):
        for url in [reverse("liste-quiz"), reverse("creer-quiz"), reverse("generer-quiz"),
                    reverse("detail-quiz", args=[self.brouillon.pk]), reverse("modifier-quiz", args=[self.brouillon.pk]),
                    reverse("ajouter-question", args=[self.brouillon.pk]),
                    reverse("modifier-question", args=[self.brouillon.questions.first().pk]),
                    reverse("resultats-quiz", args=[self.quiz.pk])]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)


class QuestionsTests(BaseQuizTestCase):
    def setUp(self):
        super().setUp()
        self.connecter(self.prof)
        self.brouillon = self.creer_quiz(statut=Quiz.Statut.BROUILLON)

    def donnees_qcm(self, **extra):
        donnees = {
            "type_question": "qcm", "enonce": "Combien vaut 2 + 2 ?", "points": "1.5", "explication": "",
            "reponse_attendue": "",
            "choix-TOTAL_FORMS": "4", "choix-INITIAL_FORMS": "0", "choix-MIN_NUM_FORMS": "0", "choix-MAX_NUM_FORMS": "6",
            "choix-0-texte": "3", "choix-1-texte": "4", "choix-1-est_correct": "on", "choix-2-texte": "",
            "choix-3-texte": "",
        }
        donnees.update(extra)
        return donnees

    def test_ajouter_un_qcm(self):
        reponse = self.client.post(reverse("ajouter-question", args=[self.brouillon.pk]), self.donnees_qcm())
        self.assertEqual(reponse.status_code, 302)
        question = self.brouillon.questions.get(ordre=4)
        self.assertEqual(list(question.choix.values_list("texte", "est_correct")), [("3", False), ("4", True)])

    def test_qcm_sans_bonne_reponse_refuse(self):
        donnees = self.donnees_qcm()
        del donnees["choix-1-est_correct"]
        reponse = self.client.post(reverse("ajouter-question", args=[self.brouillon.pk]), donnees)
        self.assertEqual(reponse.status_code, 400)
        self.assertContains(reponse, "Cochez exactement une bonne réponse", status_code=400)
        self.assertEqual(self.brouillon.questions.count(), 3)

    def test_qcm_avec_deux_bonnes_reponses_refuse(self):
        reponse = self.client.post(reverse("ajouter-question", args=[self.brouillon.pk]),
                                   self.donnees_qcm(**{"choix-0-est_correct": "on"}))
        self.assertEqual(reponse.status_code, 400)

    def test_ajouter_vrai_faux(self):
        self.client.post(reverse("ajouter-question", args=[self.brouillon.pk]), {
            "type_question": "vrai_faux", "enonce": "La Terre est plate.", "points": "1", "bonne_reponse_vf": "faux",
            "choix-TOTAL_FORMS": "0", "choix-INITIAL_FORMS": "0",
        })
        question = self.brouillon.questions.get(ordre=4)
        self.assertEqual(question.choix.get(est_correct=True).texte, "Faux")
        self.assertEqual(question.erreurs_coherence(), [])

    def test_ouverte_sans_bareme_refusee(self):
        reponse = self.client.post(reverse("ajouter-question", args=[self.brouillon.pk]), {
            "type_question": "ouverte", "enonce": "Expliquez.", "points": "2", "reponse_attendue": "",
            "choix-TOTAL_FORMS": "0", "choix-INITIAL_FORMS": "0",
        })
        self.assertEqual(reponse.status_code, 400)

    def test_points_hors_bornes_refuses(self):
        reponse = self.client.post(reverse("ajouter-question", args=[self.brouillon.pk]),
                                   self.donnees_qcm(points="100"))
        self.assertEqual(reponse.status_code, 400)

    def test_modifier_question_change_le_type(self):
        question = self.brouillon.questions.get(ordre=1)
        self.client.post(reverse("modifier-question", args=[question.pk]), {
            "type_question": "ouverte", "enonce": "Définissez une matrice.", "points": "2",
            "reponse_attendue": "Un tableau de nombres.", "choix-TOTAL_FORMS": "0", "choix-INITIAL_FORMS": "0",
        })
        question.refresh_from_db()
        self.assertEqual(question.type_question, Question.Type.OUVERTE)
        self.assertFalse(question.choix.exists())

    def test_vrai_faux_devient_qcm_sans_perdre_ses_choix_en_cas_d_erreur(self):
        question = self.brouillon.questions.get(ordre=2)  # Vrai/Faux
        url = reverse("modifier-question", args=[question.pk])
        donnees = self.donnees_qcm()
        del donnees["choix-1-est_correct"]
        self.assertEqual(self.client.post(url, donnees).status_code, 400)
        self.assertEqual(sorted(question.choix.values_list("texte", flat=True)), ["Faux", "Vrai"])
        self.client.post(url, self.donnees_qcm())
        question.refresh_from_db()
        self.assertEqual(question.type_question, Question.Type.QCM)
        self.assertEqual(list(question.choix.values_list("texte", flat=True)), ["3", "4"])
        self.assertEqual(question.erreurs_coherence(), [])

    def test_supprimer_question_renumerote(self):
        premiere = self.brouillon.questions.get(ordre=1)
        self.client.post(reverse("supprimer-question", args=[premiere.pk]))
        self.assertEqual(list(self.brouillon.questions.values_list("ordre", flat=True)), [1, 2])

    def test_deplacer_question(self):
        premiere = self.brouillon.questions.get(ordre=1)
        self.client.post(reverse("deplacer-question", args=[premiere.pk]), {"sens": "bas"})
        premiere.refresh_from_db()
        self.assertEqual(premiere.ordre, 2)

    def test_questions_d_un_quiz_publie_ou_tente_verrouillees(self):
        question = self.quiz.questions.get(ordre=1)  # quiz publié
        reponse = self.client.post(reverse("supprimer-question", args=[question.pk]))
        self.assertRedirects(reponse, reverse("detail-quiz", args=[self.quiz.pk]))
        self.assertTrue(Question.objects.filter(pk=question.pk).exists())
        reponse = self.client.post(reverse("ajouter-question", args=[self.quiz.pk]), self.donnees_qcm())
        self.assertEqual(self.quiz.questions.count(), 3)


class PublicationTests(BaseQuizTestCase):
    def setUp(self):
        super().setUp()
        self.connecter(self.prof)
        self.brouillon = self.creer_quiz(statut=Quiz.Statut.BROUILLON)

    def test_publication_explicite(self):
        self.client.post(reverse("publier-quiz", args=[self.brouillon.pk]))
        self.brouillon.refresh_from_db()
        self.assertEqual(self.brouillon.statut, Quiz.Statut.PUBLIE)
        self.assertIsNotNone(self.brouillon.date_publication)

    def test_publication_refusee_si_incoherent(self):
        Choix.objects.filter(question__quiz=self.brouillon, question__ordre=1).update(est_correct=False)
        self.client.post(reverse("publier-quiz", args=[self.brouillon.pk]))
        self.brouillon.refresh_from_db()
        self.assertEqual(self.brouillon.statut, Quiz.Statut.BROUILLON)

    def test_publication_refusee_pendant_la_generation(self):
        self.brouillon.statut_generation = Quiz.Generation.EN_COURS
        self.brouillon.save()
        self.client.post(reverse("publier-quiz", args=[self.brouillon.pk]))
        self.brouillon.refresh_from_db()
        self.assertEqual(self.brouillon.statut, Quiz.Statut.BROUILLON)

    def test_depublier_sans_tentative(self):
        self.client.post(reverse("depublier-quiz", args=[self.quiz.pk]))
        self.quiz.refresh_from_db()
        self.assertEqual(self.quiz.statut, Quiz.Statut.BROUILLON)

    def test_depublier_refuse_avec_tentatives(self):
        TentativeQuiz.objects.create(quiz=self.quiz, etudiant=self.etudiant)
        self.client.post(reverse("depublier-quiz", args=[self.quiz.pk]))
        self.quiz.refresh_from_db()
        self.assertEqual(self.quiz.statut, Quiz.Statut.PUBLIE)

    def test_nouvelle_version_preserve_l_original(self):
        TentativeQuiz.objects.create(quiz=self.quiz, etudiant=self.etudiant)
        self.client.post(reverse("nouvelle-version-quiz", args=[self.quiz.pk]))
        copie = Quiz.objects.get(version_precedente=self.quiz)
        self.assertEqual(copie.version, 2)
        self.assertEqual(copie.statut, Quiz.Statut.BROUILLON)
        self.assertEqual(copie.questions.count(), 3)
        self.assertEqual(Choix.objects.filter(question__quiz=copie, est_correct=True).count(), 2)
        self.quiz.refresh_from_db()
        self.assertEqual(self.quiz.statut, Quiz.Statut.PUBLIE)  # toujours proposé jusqu'à la publication de la copie

        self.client.post(reverse("publier-quiz", args=[copie.pk]))
        self.quiz.refresh_from_db()
        self.assertEqual(self.quiz.statut, Quiz.Statut.ARCHIVE)
        self.assertEqual(self.quiz.tentatives.count(), 1)
