"""Fonctions IA ajoutées (indice, explication, bilan, audit, analyse de classe), analytique et pilotage."""

import json
import os
from decimal import Decimal
from unittest import mock

from django.test import SimpleTestCase
from django.urls import reverse

from apps.quizzes.models import AppelIA, Choix, Question, Quiz, ReponseEtudiant, TentativeQuiz, TraitementIA
from apps.quizzes.services.assistant import revele_la_reponse, valider_bilan
from apps.quizzes.services.audit import valider_analyse, valider_audit
from apps.quizzes.services.gemini import ErreurIA
from apps.quizzes.services.statistiques import analyser_quiz, cronbach_alpha, discrimination

from .base import BaseQuizTestCase, creer_utilisateur, reponse_gemini

AVEC_CLE = mock.patch.dict(os.environ, {"GEMINI_API_KEY": "cle-de-test"})
SANS_CLE = mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""})
FETCH = {"HTTP_X_REQUESTED_WITH": "fetch", "HTTP_ACCEPT": "application/json"}
BILAN = {"synthese": "Bon travail sur les matrices.", "points_forts": ["Inversibilité"],
         "a_revoir": [{"notion": "Déterminant", "conseil": "Refais les exercices 3 et 4."}],
         "prochaine_etape": "Relis le chapitre 2."}


def client_gemini(*reponses):
    """Faux client qui renvoie les réponses dans l'ordre et mémorise les prompts reçus."""
    file, prompts = list(reponses), []

    def generate_content(**kwargs):
        prompts.append(kwargs)
        valeur = file.pop(0) if len(file) > 1 else file[0]
        if isinstance(valeur, Exception):
            raise valeur
        return reponse_gemini(valeur).models.generate_content()
    return mock.Mock(models=mock.Mock(generate_content=generate_content)), prompts


class StatistiquesTests(SimpleTestCase):
    def test_alpha_de_cronbach(self):
        # Réponses parfaitement cohérentes : fiabilité maximale
        self.assertEqual(cronbach_alpha([[1, 1, 1], [0, 0, 0], [1, 1, 1], [0, 0, 0]]), 1.0)
        self.assertIsNone(cronbach_alpha([[1, 1]]))           # un seul étudiant
        self.assertIsNone(cronbach_alpha([[1, 1], [1, 1]]))   # variance nulle

    def test_discrimination(self):
        scores = {1: 10, 2: 8, 3: 2, 4: 1}
        self.assertEqual(discrimination(scores, {1: 1, 2: 1, 3: 0, 4: 0}), 1.0)
        self.assertEqual(discrimination(scores, {1: 0, 2: 0, 3: 1, 4: 1}), -1.0)
        self.assertIsNone(discrimination({1: 5}, {1: 1}))


class RevelationIndiceTests(BaseQuizTestCase):
    def test_indice_qui_cite_la_bonne_reponse_bloque(self):
        self.assertTrue(revele_la_reponse("Pense au déterminant non nul !", self.q_qcm))
        self.assertFalse(revele_la_reponse("Pense à ce qui rend une matrice réversible.", self.q_qcm))
        self.assertTrue(revele_la_reponse("Indice : c'est vrai.", self.q_vf))
        self.assertFalse(revele_la_reponse("Relis la définition d'une matrice.", self.q_vf))
        self.assertTrue(revele_la_reponse("Il indique si la matrice est inversible (non nul).", self.q_ouverte))


class ValidationSortiesTests(SimpleTestCase):
    def test_bilan(self):
        self.assertEqual(valider_bilan(BILAN)["a_revoir"][0]["notion"], "Déterminant")
        with self.assertRaises(ErreurIA):
            valider_bilan({**BILAN, "synthese": ""})
        self.assertEqual(valider_bilan({**BILAN, "a_revoir": "pas une liste"})["a_revoir"], [])

    def test_audit(self):
        audit = valider_audit({"score": 72, "synthese": "Quiz correct dans l'ensemble.", "questions": [
            {"ordre": 1, "gravite": "majeur", "problemes": ["Deux réponses possibles"], "suggestion": "Préciser."},
            {"ordre": 99, "gravite": "ok", "problemes": [], "suggestion": ""},       # question inconnue : ignorée
            {"ordre": [1], "gravite": "ok", "problemes": [], "suggestion": ""},       # type invalide : ignoré
        ]}, {1, 2})
        self.assertEqual(len(audit["questions"]), 1)
        self.assertEqual(audit["nb_majeurs"], 1)
        for score in (150, -1, "80", True):
            with self.subTest(score=score), self.assertRaises(ErreurIA):
                valider_audit({"score": score, "synthese": "Synthèse valide.", "questions": []}, {1})

    def test_analyse(self):
        analyse = valider_analyse({"synthese": "La classe confond deux notions.", "idees_fausses": [
            {"question": 2, "constat": "Confusion"}, {"question": 7, "constat": "Hors quiz"}],
            "remediation": ["Exercice guidé"], "questions_a_revoir": [2, 9, {"x": 1}]}, {1, 2})
        self.assertEqual([i["question"] for i in analyse["idees_fausses"]], [2])
        self.assertEqual(analyse["questions_a_revoir"], [2])


class IndiceTests(BaseQuizTestCase):
    def setUp(self):
        super().setUp()
        self.connecter(self.etudiant)
        self.tentative = TentativeQuiz.objects.create(quiz=self.quiz, etudiant=self.etudiant)

    def demander(self, question, **extra):
        return self.client.post(reverse("indice-question", args=[self.tentative.pk, question.pk]), **{**FETCH, **extra})

    @AVEC_CLE
    @mock.patch("apps.quizzes.services.gemini._client")
    def test_indice_puis_points_reduits(self, client):
        client.return_value, _ = client_gemini({"indice": "Pense à ce qui rend une matrice réversible."})
        reponse = self.demander(self.q_qcm)
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.json()["restants"], 2)
        # Deuxième demande sur la même question : même indice, aucun nouvel appel facturé
        self.assertEqual(self.demander(self.q_qcm).json()["restants"], 2)
        self.assertEqual(AppelIA.objects.filter(type_appel=AppelIA.Type.INDICE).count(), 1)
        with SANS_CLE, self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("tentative", args=[self.tentative.pk]), {f"question_{self.q_qcm.pk}": self.bon.pk})
        self.tentative.refresh_from_db()
        self.assertEqual(self.tentative.score, Decimal("1.50"))   # 2 points × 75 %
        self.assertTrue(self.tentative.reponses.get(question=self.q_qcm).indice_utilise)

    @AVEC_CLE
    @mock.patch("apps.quizzes.services.gemini.time.sleep")
    @mock.patch("apps.quizzes.services.gemini._client")
    def test_indice_qui_devoile_la_reponse_refuse(self, client, _sleep):
        client.return_value, _ = client_gemini({"indice": "La réponse est : déterminant non nul."})
        reponse = self.demander(self.q_qcm)
        self.assertEqual(reponse.status_code, 502)
        self.assertIn("dévoilait", reponse.json()["erreur"])
        self.tentative.refresh_from_db()
        self.assertEqual(self.tentative.indices, {})

    @AVEC_CLE
    @mock.patch("apps.quizzes.services.gemini._client")
    def test_limite_de_trois_indices(self, client):
        client.return_value, _ = client_gemini({"indice": "Relis la définition vue en cours."})
        q4 = Question.objects.create(quiz=self.quiz, ordre=4, type_question=Question.Type.OUVERTE,
                                     enonce="Donnez un exemple.", points=1, reponse_attendue="Un exemple.")
        for question in (self.q_qcm, self.q_vf, self.q_ouverte):
            self.assertEqual(self.demander(question).status_code, 200)
        self.assertEqual(self.demander(q4).status_code, 429)

    @AVEC_CLE
    def test_controles_d_acces(self):
        autre = self.creer_quiz()
        self.assertEqual(self.demander(autre.questions.first()).status_code, 404)   # question d'un autre quiz
        self.connecter(self.autre_etudiant)
        self.assertEqual(self.demander(self.q_qcm).status_code, 404)                # tentative d'un autre
        self.connecter(self.prof)
        self.assertEqual(self.demander(self.q_qcm).status_code, 403)                # rôle enseignant
        self.connecter(self.etudiant)
        TentativeQuiz.objects.filter(pk=self.tentative.pk).update(statut=TentativeQuiz.Statut.CORRIGEE)
        self.assertEqual(self.demander(self.q_qcm).status_code, 400)                # déjà soumise

    @SANS_CLE
    def test_sans_ia(self):
        self.assertEqual(self.demander(self.q_qcm).status_code, 503)

    def test_page_de_passage(self):
        with AVEC_CLE:
            reponse = self.client.get(reverse("tentative", args=[self.tentative.pk]))
        self.assertContains(reponse, "Demander un indice")
        self.assertContains(reponse, "Vérification avant l'envoi")
        self.assertNotContains(reponse, "est_correct")


class ExplicationEtBilanTests(BaseQuizTestCase):
    def setUp(self):
        super().setUp()
        self.connecter(self.etudiant)
        self.tentative = TentativeQuiz.objects.create(quiz=self.quiz, etudiant=self.etudiant)

    def soumettre(self, donnees):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("tentative", args=[self.tentative.pk]), donnees)
        self.tentative.refresh_from_db()

    @AVEC_CLE
    @mock.patch("apps.quizzes.services.gemini._client")
    def test_bilan_automatique_puis_explication(self, client):
        client.return_value, prompts = client_gemini(
            {"note": 3, "explication": "Complet."}, BILAN, {"explication": "Un déterminant nul rend la matrice non inversible."})
        self.soumettre({f"question_{self.q_qcm.pk}": self.mauvais.pk, f"question_{self.q_vf.pk}": self.vrai.pk,
                        f"question_{self.q_ouverte.pk}": "Il indique l'inversibilité."})
        self.assertEqual(self.tentative.statut, TentativeQuiz.Statut.CORRIGEE)
        self.assertEqual(TraitementIA.etat(self.tentative.bilan_ia), TraitementIA.TERMINE)
        self.assertEqual(self.tentative.bilan_ia["resultat"]["prochaine_etape"], "Relis le chapitre 2.")
        self.assertNotIn("etudiant", json.dumps([str(p["contents"]) for p in prompts]))   # aucune identité envoyée
        page = self.client.get(reverse("tentative", args=[self.tentative.pk]))
        self.assertContains(page, "Relis le chapitre 2.")
        self.assertContains(page, "Pourquoi ma réponse est fausse ?")

        mauvaise = self.tentative.reponses.get(question=self.q_qcm)
        url = reverse("expliquer-erreur", args=[mauvaise.pk])
        self.assertIn("déterminant nul", self.client.post(url, **FETCH).json()["explication"])
        nb_appels = len(prompts)
        self.client.post(url, **FETCH)                        # en cache : pas de nouvel appel
        self.assertEqual(len(prompts), nb_appels)
        juste = self.tentative.reponses.get(question=self.q_vf)
        self.assertEqual(self.client.post(reverse("expliquer-erreur", args=[juste.pk]), **FETCH).status_code, 400)
        self.connecter(self.autre_etudiant)
        self.assertEqual(self.client.post(url, **FETCH).status_code, 404)

    @AVEC_CLE
    @mock.patch("apps.quizzes.services.gemini.time.sleep")
    @mock.patch("apps.quizzes.services.gemini._client")
    def test_bilan_invalide_puis_relance(self, client, _sleep):
        client.return_value, _ = client_gemini({"synthese": ""})
        self.soumettre({f"question_{self.q_qcm.pk}": self.bon.pk})
        self.assertEqual(TraitementIA.etat(self.tentative.bilan_ia), TraitementIA.ERREUR)
        self.assertContains(self.client.get(reverse("tentative", args=[self.tentative.pk])), "Réessayer")
        client.return_value, _ = client_gemini(BILAN)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("relancer-bilan", args=[self.tentative.pk]))
        self.tentative.refresh_from_db()
        self.assertEqual(TraitementIA.etat(self.tentative.bilan_ia), TraitementIA.TERMINE)

    @SANS_CLE
    def test_sans_ia_aucun_bilan_invente(self):
        self.soumettre({f"question_{self.q_qcm.pk}": self.bon.pk})
        self.assertEqual(self.tentative.bilan_ia, {})
        Quiz.objects.filter(pk=self.quiz.pk).update(afficher_corrige=False)
        mauvaise = self.tentative.reponses.get(question=self.q_vf)
        self.assertEqual(self.client.post(reverse("expliquer-erreur", args=[mauvaise.pk]), **FETCH).status_code, 400)


class AuditEtAnalyseTests(BaseQuizTestCase):
    @AVEC_CLE
    @mock.patch("apps.quizzes.services.gemini._client")
    def test_audit_du_quiz(self, client):
        client.return_value, prompts = client_gemini({"score": 64, "synthese": "Une question ambiguë.", "questions": [
            {"ordre": 1, "gravite": "majeur", "problemes": ["Deux réponses défendables"], "suggestion": "Reformuler."}]})
        self.connecter(self.prof)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("audit-quiz", args=[self.quiz.pk]))
        self.quiz.refresh_from_db()
        self.assertEqual(self.quiz.audit_ia["resultat"]["score"], 64)
        self.assertIn("<cours>", prompts[0]["contents"])
        page = self.client.get(reverse("detail-quiz", args=[self.quiz.pk]))
        self.assertContains(page, "Score qualité : 64/100")
        self.assertContains(page, "Deux réponses défendables")
        self.assertEqual(self.client.get(reverse("etat-ia-quiz", args=[self.quiz.pk])).json()["audit"], "termine")

    def test_audit_reserve_au_proprietaire(self):
        self.connecter(self.autre_prof)
        self.assertEqual(self.client.post(reverse("audit-quiz", args=[self.quiz.pk])).status_code, 404)
        self.connecter(self.etudiant)
        self.assertEqual(self.client.post(reverse("audit-quiz", args=[self.quiz.pk])).status_code, 403)

    @SANS_CLE
    def test_audit_sans_ia(self):
        self.connecter(self.prof)
        self.client.post(reverse("audit-quiz", args=[self.quiz.pk]))
        self.quiz.refresh_from_db()
        self.assertEqual(self.quiz.audit_ia, {})

    @AVEC_CLE
    @mock.patch("apps.quizzes.services.gemini._client")
    def test_analyse_de_classe_anonyme(self, client):
        client.return_value, prompts = client_gemini(
            {"synthese": "La classe confond nul et non nul.", "idees_fausses": [{"question": 1, "constat": "Confusion"}],
             "remediation": ["Exemple chiffré au tableau"], "questions_a_revoir": []})
        for etudiant, choix in ((self.etudiant, self.bon), (self.autre_etudiant, self.mauvais)):
            t = TentativeQuiz.objects.create(quiz=self.quiz, etudiant=etudiant, statut=TentativeQuiz.Statut.CORRIGEE,
                                             score=0, score_max=6)
            ReponseEtudiant.objects.create(tentative=t, question=self.q_qcm, choix=choix,
                                           points_obtenus=2 if choix.est_correct else 0, est_correcte=choix.est_correct)
        self.connecter(self.prof)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("analyse-classe", args=[self.quiz.pk]))
        self.quiz.refresh_from_db()
        self.assertEqual(self.quiz.analyse_ia["resultat"]["remediation"], ["Exemple chiffré au tableau"])
        envoye = prompts[0]["contents"]
        self.assertNotIn("autre_etudiant", envoye)
        self.assertNotIn("etudiant@eduvox.test", envoye)
        page = self.client.get(reverse("resultats-quiz", args=[self.quiz.pk]))
        self.assertContains(page, "Exemple chiffré au tableau")
        self.assertContains(page, "Analyse des items")

    def test_analyse_items(self):
        for i, (etudiant, choix) in enumerate([(self.etudiant, self.mauvais), (self.autre_etudiant, self.mauvais)]):
            t = TentativeQuiz.objects.create(quiz=self.quiz, etudiant=etudiant, statut=TentativeQuiz.Statut.CORRIGEE,
                                             score=0, score_max=6)
            ReponseEtudiant.objects.create(tentative=t, question=self.q_qcm, choix=choix, points_obtenus=0, est_correcte=False)
        etudiant3 = creer_utilisateur("etudiant3", "ETUDIANT")
        t = TentativeQuiz.objects.create(quiz=self.quiz, etudiant=etudiant3, statut=TentativeQuiz.Statut.CORRIGEE,
                                         score=2, score_max=6)
        ReponseEtudiant.objects.create(tentative=t, question=self.q_qcm, choix=self.mauvais, points_obtenus=0, est_correcte=False)
        analyse = analyser_quiz(self.quiz)
        item = next(i for i in analyse["items"] if i["question"] == self.q_qcm)
        self.assertEqual(item["p"], 0.0)
        self.assertEqual(next(d for d in item["distracteurs"] if not d["correct"])["pct"], 100)
        self.assertTrue(any("attire plus que la bonne réponse" in texte for texte, _ in item["alertes"]))
        self.assertEqual(analyse["n"], 3)


class PilotageTests(BaseQuizTestCase):
    def test_acces_reserve_a_l_admin(self):
        AppelIA.objects.create(type_appel=AppelIA.Type.GENERATION, succes=True, duree_ms=2500)
        AppelIA.objects.create(type_appel=AppelIA.Type.INDICE, succes=False, code="delai")
        self.connecter(self.admin)
        page = self.client.get(reverse("pilotage-quiz"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Performance de l'IA")
        self.assertEqual(page.context["metriques_ia"]["total"], 2)
        self.assertEqual(page.context["metriques_ia"]["taux_succes"], 50)
        for user in (self.prof, self.etudiant):
            self.connecter(user)
            self.assertEqual(self.client.get(reverse("pilotage-quiz")).status_code, 403)

    def test_progression_etudiant(self):
        for numero, score in ((1, 2), (2, 5)):
            TentativeQuiz.objects.create(quiz=self.quiz, etudiant=self.etudiant, numero=numero,
                                         statut=TentativeQuiz.Statut.CORRIGEE, score=score, score_max=6,
                                         date_soumission="2026-10-0%sT10:00:00Z" % numero)
        self.connecter(self.etudiant)
        page = self.client.get(reverse("mes-tentatives"))
        self.assertEqual(page.context["stats"]["tendance"], 50)   # 33 % puis 83 %
        self.assertEqual(page.context["graphique"]["scores"], [33, 83])
