"""Passage d'un quiz : score calculé côté serveur, falsifications, double soumission, historique."""

import os
from decimal import Decimal
from unittest import mock

from django.urls import reverse

from apps.quizzes.models import Quiz, ReponseEtudiant, TentativeQuiz

from .base import BaseQuizTestCase, reponse_gemini

SANS_CLE = mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""})


class PassageQuizTests(BaseQuizTestCase):
    def setUp(self):
        super().setUp()
        self.connecter(self.etudiant)

    def commencer(self):
        self.client.post(reverse("commencer-quiz", args=[self.quiz.pk]))
        return TentativeQuiz.objects.get(quiz=self.quiz, etudiant=self.etudiant, statut="en_cours")

    def soumettre(self, tentative, donnees):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(reverse("tentative", args=[tentative.pk]), donnees)

    def test_page_de_passage_sans_les_bonnes_reponses(self):
        tentative = self.commencer()
        reponse = self.client.get(reverse("tentative", args=[tentative.pk]))
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "Déterminant non nul")  # le choix est proposé…
        self.assertNotContains(reponse, "est_correct")       # …sans aucun indicateur de bonne réponse
        self.assertNotContains(reponse, "Il indique si la matrice est inversible")  # ni le barème de la question ouverte
        self.assertNotIn("est_correct", str(reponse.context["questions"]))

    def test_commencer_reprend_la_tentative_en_cours(self):
        premiere = self.commencer()
        self.client.post(reverse("commencer-quiz", args=[self.quiz.pk]))
        self.assertEqual(TentativeQuiz.objects.filter(quiz=self.quiz, etudiant=self.etudiant).count(), 1)
        self.assertEqual(premiere.numero, 1)

    @SANS_CLE
    def test_score_qcm_et_vrai_faux_calcule_cote_serveur(self):
        tentative = self.commencer()
        self.soumettre(tentative, {
            f"question_{self.q_qcm.pk}": self.bon.pk, f"question_{self.q_vf.pk}": self.faux.pk,
            f"question_{self.q_ouverte.pk}": "", "score": "999", "points": "999",
        })
        tentative.refresh_from_db()
        self.assertEqual(tentative.score, Decimal("2"))       # QCM juste (2) + V/F faux (0) + ouverte vide (0)
        self.assertEqual(tentative.score_max, Decimal("6"))
        self.assertEqual(tentative.statut, TentativeQuiz.Statut.CORRIGEE)
        self.assertEqual(tentative.pourcentage, 33)

    @SANS_CLE
    def test_question_sans_reponse_vaut_zero(self):
        tentative = self.commencer()
        self.soumettre(tentative, {})
        tentative.refresh_from_db()
        self.assertEqual(tentative.score, Decimal("0"))
        self.assertEqual(tentative.reponses.count(), 3)

    def test_choix_d_une_autre_question_refuse(self):
        tentative = self.commencer()
        reponse = self.soumettre(tentative, {f"question_{self.q_qcm.pk}": self.vrai.pk})
        self.assertEqual(reponse.status_code, 400)
        tentative.refresh_from_db()
        self.assertEqual(tentative.statut, TentativeQuiz.Statut.EN_COURS)
        self.assertFalse(tentative.reponses.exists())

    def test_choix_d_un_autre_quiz_et_question_etrangere_refuses_ou_ignores(self):
        autre = self.creer_quiz(statut=Quiz.Statut.PUBLIE)  # redéfinit self.bon, self.q_qcm… sur l'autre quiz
        tentative = TentativeQuiz.objects.create(quiz=self.quiz, etudiant=self.etudiant)
        q_qcm_original = self.quiz.questions.get(ordre=1)
        reponse = self.soumettre(tentative, {f"question_{q_qcm_original.pk}": self.bon.pk})
        self.assertEqual(reponse.status_code, 400)
        # Une question d'un autre quiz est simplement ignorée
        with SANS_CLE:
            self.soumettre(tentative, {f"question_{self.q_qcm.pk}": self.bon.pk})
        tentative.refresh_from_db()
        self.assertFalse(tentative.reponses.filter(question__quiz=autre).exists())
        self.assertEqual(tentative.score, Decimal("0"))

    @SANS_CLE
    def test_double_soumission_sans_effet(self):
        tentative = self.commencer()
        donnees = {f"question_{self.q_qcm.pk}": self.bon.pk}
        self.soumettre(tentative, donnees)
        reponse = self.soumettre(tentative, {f"question_{self.q_qcm.pk}": self.mauvais.pk,
                                             f"question_{self.q_vf.pk}": self.vrai.pk})
        self.assertRedirects(reponse, reverse("tentative", args=[tentative.pk]))
        tentative.refresh_from_db()
        self.assertEqual(tentative.score, Decimal("2"))
        self.assertEqual(tentative.reponses.count(), 3)

    def test_nombre_de_tentatives_limite(self):
        tentative = self.commencer()
        with SANS_CLE:
            self.soumettre(tentative, {})
        self.client.post(reverse("commencer-quiz", args=[self.quiz.pk]))
        self.assertEqual(TentativeQuiz.objects.filter(quiz=self.quiz, etudiant=self.etudiant).count(), 1)

    def test_soumission_refusee_si_le_quiz_n_est_plus_publie(self):
        tentative = self.commencer()
        Quiz.objects.filter(pk=self.quiz.pk).update(statut=Quiz.Statut.ARCHIVE)
        self.soumettre(tentative, {f"question_{self.q_qcm.pk}": self.bon.pk})
        tentative.refresh_from_db()
        self.assertEqual(tentative.statut, TentativeQuiz.Statut.EN_COURS)

    def test_page_resultat_respecte_le_reglage_du_corrige(self):
        tentative = self.commencer()
        with SANS_CLE:
            self.soumettre(tentative, {f"question_{self.q_qcm.pk}": self.mauvais.pk})
        reponse = self.client.get(reverse("tentative", args=[tentative.pk]))
        self.assertContains(reponse, "(bonne réponse)")
        Quiz.objects.filter(pk=self.quiz.pk).update(afficher_corrige=False)
        reponse = self.client.get(reverse("tentative", args=[tentative.pk]))
        self.assertNotContains(reponse, "(bonne réponse)")


class QuestionsOuvertesTests(BaseQuizTestCase):
    def setUp(self):
        super().setUp()
        self.connecter(self.etudiant)
        self.tentative = TentativeQuiz.objects.create(quiz=self.quiz, etudiant=self.etudiant)

    def soumettre(self, texte="Le déterminant indique si la matrice est inversible."):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(reverse("tentative", args=[self.tentative.pk]), {
                f"question_{self.q_qcm.pk}": self.bon.pk, f"question_{self.q_ouverte.pk}": texte,
            })

    @mock.patch.dict(os.environ, {"GEMINI_API_KEY": "cle-de-test"})
    @mock.patch("apps.quizzes.services.gemini._client")
    def test_correction_ia_reussie(self, client):
        client.return_value = reponse_gemini({"note": 2.5, "explication": "Réponse presque complète."})
        self.soumettre()
        self.tentative.refresh_from_db()
        ouverte = self.tentative.reponses.get(question=self.q_ouverte)
        self.assertEqual(ouverte.statut_correction, ReponseEtudiant.Correction.IA)
        self.assertEqual(ouverte.points_obtenus, Decimal("2.5"))
        self.assertEqual(ouverte.points_ia, Decimal("2.5"))
        self.assertEqual(self.tentative.score, Decimal("4.5"))
        self.assertEqual(self.tentative.statut, TentativeQuiz.Statut.CORRIGEE)
        reponse = self.client.get(reverse("tentative", args=[self.tentative.pk]))
        self.assertContains(reponse, "Correction assistée par IA")

    @mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""})
    def test_sans_ia_la_reponse_reste_en_attente_sans_zero(self):
        self.soumettre()
        self.tentative.refresh_from_db()
        ouverte = self.tentative.reponses.get(question=self.q_ouverte)
        self.assertIsNone(ouverte.points_obtenus)
        self.assertEqual(ouverte.statut_correction, ReponseEtudiant.Correction.ERREUR)
        self.assertEqual(ouverte.texte, "Le déterminant indique si la matrice est inversible.")
        self.assertEqual(self.tentative.statut, TentativeQuiz.Statut.SOUMISE)
        self.assertEqual(self.tentative.score, Decimal("2"))  # score provisoire : le QCM seulement
        reponse = self.client.get(reverse("tentative", args=[self.tentative.pk]))
        self.assertContains(reponse, "Score provisoire")
        self.assertContains(reponse, "en attente de correction")

    @mock.patch.dict(os.environ, {"GEMINI_API_KEY": "cle-de-test"})
    @mock.patch("apps.quizzes.services.gemini.time.sleep")
    @mock.patch("apps.quizzes.services.gemini._client")
    def test_note_hors_bareme_non_attribuee(self, client, _sleep):
        client.return_value = reponse_gemini({"note": 10, "explication": "Parfait"})
        self.soumettre()
        ouverte = self.tentative.reponses.get(question=self.q_ouverte)
        self.assertIsNone(ouverte.points_obtenus)
        self.assertEqual(ouverte.statut_correction, ReponseEtudiant.Correction.ERREUR)
        self.assertIn("sort du barème", ouverte.message_erreur)

    @mock.patch.dict(os.environ, {"GEMINI_API_KEY": "cle-de-test"})
    @mock.patch("apps.quizzes.services.gemini._client")
    def test_double_soumission_ne_relance_pas_la_correction(self, client):
        appels = []
        def generate_content(**kwargs):
            # On ne compte que les appels de correction (le bilan personnalisé suit la correction)
            if "corriges la réponse" in kwargs["config"].system_instruction:
                appels.append(1)
            return reponse_gemini({"note": 3, "explication": "Très bien."}).models.generate_content()
        client.return_value = mock.Mock(models=mock.Mock(generate_content=generate_content))
        self.soumettre()
        self.soumettre("Autre réponse")
        self.assertEqual(len(appels), 1)
        self.assertEqual(self.tentative.reponses.get(question=self.q_ouverte).texte,
                         "Le déterminant indique si la matrice est inversible.")

    @mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""})
    def test_enseignant_note_puis_revise(self):
        self.soumettre()
        ouverte = self.tentative.reponses.get(question=self.q_ouverte)
        self.connecter(self.prof)
        url = reverse("reviser-reponse", args=[ouverte.pk])
        self.client.post(url, {f"r{ouverte.pk}-points": "5", f"r{ouverte.pk}-commentaire": ""})  # > barème
        ouverte.refresh_from_db()
        self.assertIsNone(ouverte.points_obtenus)
        self.client.post(url, {f"r{ouverte.pk}-points": "1.5", f"r{ouverte.pk}-commentaire": "Incomplet."})
        ouverte.refresh_from_db()
        self.tentative.refresh_from_db()
        self.assertEqual(ouverte.statut_correction, ReponseEtudiant.Correction.ENSEIGNANT)
        self.assertEqual(ouverte.corrige_par, self.prof)
        self.assertEqual(self.tentative.score, Decimal("3.5"))
        self.assertEqual(self.tentative.statut, TentativeQuiz.Statut.CORRIGEE)

    @mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""})
    def test_autre_enseignant_ne_peut_pas_reviser(self):
        self.soumettre()
        ouverte = self.tentative.reponses.get(question=self.q_ouverte)
        self.connecter(self.autre_prof)
        reponse = self.client.post(reverse("reviser-reponse", args=[ouverte.pk]),
                                   {f"r{ouverte.pk}-points": "3", f"r{ouverte.pk}-commentaire": ""})
        self.assertEqual(reponse.status_code, 404)
        self.connecter(self.etudiant)
        reponse = self.client.post(reverse("reviser-reponse", args=[ouverte.pk]),
                                   {f"r{ouverte.pk}-points": "3", f"r{ouverte.pk}-commentaire": ""})
        self.assertEqual(reponse.status_code, 403)
        ouverte.refresh_from_db()
        self.assertIsNone(ouverte.points_obtenus)

    @mock.patch("apps.quizzes.services.gemini._client")
    def test_relance_de_la_correction_par_l_enseignant(self, client):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            self.soumettre()
        client.return_value = reponse_gemini({"note": 3, "explication": "Complet."})
        self.connecter(self.prof)
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "cle-de-test"}), \
                self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("relancer-correction", args=[self.tentative.pk]))
        self.tentative.refresh_from_db()
        self.assertEqual(self.tentative.score, Decimal("5"))
        self.assertEqual(self.tentative.statut, TentativeQuiz.Statut.CORRIGEE)


class PagesResultatsTests(BaseQuizTestCase):
    @mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""})
    def test_pages_de_resultats_avec_donnees(self):
        self.connecter(self.etudiant)
        tentative = TentativeQuiz.objects.create(quiz=self.quiz, etudiant=self.etudiant)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("tentative", args=[tentative.pk]), {
                f"question_{self.q_qcm.pk}": self.bon.pk, f"question_{self.q_ouverte.pk}": "Une réponse."})
        self.assertContains(self.client.get(reverse("mes-tentatives")), "Quiz matrices")
        self.assertContains(self.client.get(reverse("liste-quiz")), "Dernier : 33 %")
        self.assertContains(self.client.get(reverse("detail-quiz", args=[self.quiz.pk])), "Mes tentatives")

        self.connecter(self.prof)
        reponse = self.client.get(reverse("resultats-quiz", args=[self.quiz.pk]))
        self.assertContains(reponse, "etudiant")
        self.assertContains(reponse, "provisoire")
        reponse = self.client.get(reverse("tentative", args=[tentative.pk]))
        self.assertContains(reponse, "Relancer la correction IA")
        self.assertContains(reponse, "Noter cette réponse")
        self.assertEqual(self.client.get(reverse("etat-tentative", args=[tentative.pk])).json()["en_attente"], 1)

        self.connecter(self.admin)
        self.assertContains(self.client.get(reverse("liste-quiz")), "Quiz matrices")
        self.assertNotContains(self.client.get(reverse("tentative", args=[tentative.pk])), "Noter cette réponse")


class HistoriqueTests(BaseQuizTestCase):
    @mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""})
    def test_resultats_historiques_intacts_apres_nouvelle_version(self):
        self.connecter(self.etudiant)
        tentative = TentativeQuiz.objects.create(quiz=self.quiz, etudiant=self.etudiant)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("tentative", args=[tentative.pk]), {f"question_{self.q_qcm.pk}": self.bon.pk})
        self.connecter(self.prof)
        self.client.post(reverse("nouvelle-version-quiz", args=[self.quiz.pk]))
        copie = Quiz.objects.get(version_precedente=self.quiz)
        q = copie.questions.get(ordre=1)
        self.client.post(reverse("supprimer-question", args=[q.pk]))
        tentative.refresh_from_db()
        self.assertEqual(tentative.score, Decimal("2"))
        self.assertEqual(self.quiz.questions.count(), 3)
        self.assertEqual(tentative.reponses.get(question=self.q_qcm).choix, self.bon)
