"""Génération et correction Gemini : sorties valides, invalides, hors barème et erreurs API (Gemini simulé)."""

import os
from decimal import Decimal
from unittest import mock

import httpx
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from google.genai import errors

from apps.quizzes.models import AppelIA, Question, Quiz
from apps.quizzes.services.correction import valider_correction
from apps.quizzes.services.gemini import ErreurIA, appeler_gemini_json, extraire_json
from apps.quizzes.services.generation import TYPES, valider_question, valider_sortie

from .base import BaseQuizTestCase, creer_cours, reponse_gemini

AVEC_CLE = mock.patch.dict(os.environ, {"GEMINI_API_KEY": "cle-de-test"})
QCM = {"type": "qcm", "enonce": "Quand une matrice est-elle inversible ?", "points": 2,
       "choix": [{"texte": "Déterminant non nul", "correct": True}, {"texte": "Déterminant nul", "correct": False}],
       "explication": "Voir le cours.", "reponse_attendue": ""}
VF = {"type": "vrai_faux", "enonce": "Une matrice est un tableau de nombres.", "points": 1,
      "choix": [{"texte": "vrai", "correct": True}, {"texte": "Faux", "correct": False}],
      "explication": "", "reponse_attendue": ""}
OUVERTE = {"type": "ouverte", "enonce": "Expliquez le rôle du déterminant.", "points": 3, "choix": [],
           "explication": "", "reponse_attendue": "Il indique l'inversibilité."}


class ValidationSortieTests(SimpleTestCase):
    def test_questions_valides(self):
        valides, rejets = valider_sortie({"questions": [QCM, VF, OUVERTE]}, 5, TYPES)
        self.assertEqual(len(valides), 3)
        self.assertEqual(rejets, [])
        self.assertEqual(valides[1]["choix"][0], {"texte": "Vrai", "est_correct": True})
        self.assertEqual(valides[0]["points"], Decimal("2.00"))

    def test_rejets_detailles(self):
        cas = {
            "deux bonnes réponses": {**QCM, "choix": [{"texte": "A", "correct": True}, {"texte": "B", "correct": True}]},
            "un seul choix": {**QCM, "choix": [{"texte": "A", "correct": True}]},
            "choix en double": {**QCM, "choix": [{"texte": "A", "correct": True}, {"texte": "a", "correct": False}]},
            "points hors barème": {**QCM, "points": 50},
            "points booléens": {**QCM, "points": True},
            "type inconnu": {**QCM, "type": "dessin"},
            "énoncé vide": {**QCM, "enonce": " "},
            "ouverte sans attendu": {**OUVERTE, "reponse_attendue": ""},
            "vrai faux incohérent": {**VF, "choix": [{"texte": "Oui", "correct": True}, {"texte": "Non", "correct": False}]},
        }
        for nom, question in cas.items():
            with self.subTest(nom), self.assertRaises(ValueError):
                valider_question(question, TYPES)

    def test_type_non_demande_rejete(self):
        with self.assertRaises(ValueError):
            valider_question(OUVERTE, [Question.Type.QCM])

    def test_questions_en_trop_ignorees_et_invalides_ecartees(self):
        valides, rejets = valider_sortie({"questions": [QCM, {**QCM, "points": 99}, VF, OUVERTE]}, 3, TYPES)
        self.assertEqual(len(valides), 2)
        self.assertEqual(len(rejets), 1)

    def test_aucune_question_exploitable(self):
        for donnees in [{}, {"questions": []}, {"questions": "texte"}, {"questions": [{"type": "qcm"}]}]:
            with self.subTest(donnees=donnees), self.assertRaises(ErreurIA):
                valider_sortie(donnees, 5, TYPES)

    def test_lecture_json(self):
        self.assertEqual(extraire_json('```json\n{"a": 1}\n```'), {"a": 1})
        for texte in ["pas du json", "[1, 2]", "", None]:
            with self.subTest(texte=texte), self.assertRaises(ErreurIA):
                extraire_json(texte)

    def test_validation_correction(self):
        self.assertEqual(valider_correction({"note": 1.5, "explication": "Bien"}, Decimal("3")), (Decimal("1.50"), "Bien"))
        for donnees in [{"note": 4, "explication": "x"}, {"note": -1, "explication": "x"}, {"note": "2", "explication": "x"},
                        {"note": True, "explication": "x"}, {"note": 2, "explication": ""}, {"explication": "x"}]:
            with self.subTest(donnees=donnees), self.assertRaises(ErreurIA):
                valider_correction(donnees, Decimal("3"))


@AVEC_CLE
@mock.patch("apps.quizzes.services.gemini.time.sleep")
class ErreursApiTests(SimpleTestCase):
    def appeler(self, *reponses):
        with mock.patch("apps.quizzes.services.gemini._client", return_value=reponse_gemini(list(reponses))):
            return appeler_gemini_json("prompt", {}, "systeme")

    def test_reussite_et_metadonnees(self, _sleep):
        donnees, meta = self.appeler({"ok": True})
        self.assertEqual(donnees, {"ok": True})
        self.assertEqual(meta["essais"], 1)

    def test_delai_depasse_retente_une_fois(self, _sleep):
        donnees, meta = self.appeler(httpx.ReadTimeout("lent"), {"ok": True})
        self.assertEqual(meta["essais"], 2)

    def test_delai_depasse_deux_fois(self, _sleep):
        with self.assertRaises(ErreurIA) as ctx:
            self.appeler(httpx.ReadTimeout("lent"), httpx.ReadTimeout("lent"))
        self.assertEqual(ctx.exception.code, "delai")
        self.assertEqual(ctx.exception.essais, 2)

    def test_quota_non_retente(self, _sleep):
        with self.assertRaises(ErreurIA) as ctx:
            self.appeler(errors.ClientError(429, {"error": {"message": "quota"}}), {"ok": True})
        self.assertEqual(ctx.exception.code, "quota")
        self.assertIn("Quota", ctx.exception.message)

    def test_erreur_serveur_et_reseau(self, _sleep):
        with self.assertRaises(ErreurIA) as ctx:
            self.appeler(errors.ServerError(503, {"error": {"message": "indispo"}}),
                         httpx.ConnectError("réseau"))
        self.assertEqual(ctx.exception.code, "reseau")

    def test_json_invalide_retente_puis_echoue(self, _sleep):
        with self.assertRaises(ErreurIA) as ctx:
            self.appeler("pas du json", "toujours pas")
        self.assertEqual(ctx.exception.code, "json_invalide")

    def test_cle_absente(self, _sleep):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""}), self.assertRaises(ErreurIA) as ctx:
            appeler_gemini_json("prompt", {}, "systeme")
        self.assertEqual(ctx.exception.code, "configuration")


class GenerationVueTests(BaseQuizTestCase):
    def setUp(self):
        super().setUp()
        self.connecter(self.prof)

    def generer(self, **extra):
        donnees = {"cours": self.cours.pk, "source": "resume", "nb_questions": 3,
                   "types": ["qcm", "vrai_faux", "ouverte"], "difficulte": "moyen", "titre": ""}
        donnees.update(extra)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(reverse("generer-quiz"), donnees)

    @AVEC_CLE
    @mock.patch("apps.quizzes.services.gemini._client")
    def test_generation_cree_un_brouillon_jamais_publie(self, client):
        client.return_value = reponse_gemini({"questions": [QCM, VF, OUVERTE]})
        reponse = self.generer()
        quiz = Quiz.objects.get(origine=Quiz.Origine.IA)
        self.assertRedirects(reponse, reverse("detail-quiz", args=[quiz.pk]))
        self.assertEqual(quiz.statut, Quiz.Statut.BROUILLON)
        self.assertEqual(quiz.statut_generation, Quiz.Generation.TERMINEE)
        self.assertEqual(quiz.questions.count(), 3)
        self.assertEqual(quiz.erreurs_publication(), [])
        self.assertEqual(quiz.titre, f"Quiz – {self.cours.titre}")
        appel = AppelIA.objects.get(type_appel=AppelIA.Type.GENERATION)
        self.assertTrue(appel.succes)
        self.assertEqual(appel.details["valides"], 3)

    @AVEC_CLE
    @mock.patch("apps.quizzes.services.gemini._client")
    def test_contenu_du_cours_transmis_comme_donnee(self, client):
        capture = {}
        def generate_content(**kwargs):
            capture.update(kwargs)
            return reponse_gemini({"questions": [QCM]}).models.generate_content()
        client.return_value = mock.Mock(models=mock.Mock(generate_content=generate_content))
        self.generer(nb_questions=1)
        self.assertIn("<cours>", capture["contents"])
        self.assertIn("DONNÉE", capture["config"].system_instruction)
        self.assertEqual(capture["config"].response_mime_type, "application/json")

    @AVEC_CLE
    @mock.patch("apps.quizzes.services.gemini.time.sleep")
    @mock.patch("apps.quizzes.services.gemini._client")
    def test_sortie_invalide_aucune_question_creee(self, client, _sleep):
        client.return_value = reponse_gemini({"questions": [{**QCM, "points": 99}]})
        self.generer()
        quiz = Quiz.objects.get(origine=Quiz.Origine.IA)
        self.assertEqual(quiz.statut_generation, Quiz.Generation.ERREUR)
        self.assertFalse(quiz.questions.exists())
        self.assertIn("exploitable", quiz.message_generation)
        self.assertFalse(AppelIA.objects.get().succes)

    @AVEC_CLE
    @mock.patch("apps.quizzes.services.gemini._client")
    def test_quota_atteint_message_clair_puis_relance(self, client):
        client.return_value = reponse_gemini(errors.ClientError(429, {"error": {"message": "quota"}}))
        self.generer()
        quiz = Quiz.objects.get(origine=Quiz.Origine.IA)
        self.assertEqual(quiz.statut_generation, Quiz.Generation.ERREUR)
        self.assertIn("Quota", quiz.message_generation)
        client.return_value = reponse_gemini({"questions": [QCM]})
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("relancer-generation-quiz", args=[quiz.pk]))
        quiz.refresh_from_db()
        self.assertEqual(quiz.statut_generation, Quiz.Generation.TERMINEE)
        self.assertEqual(quiz.questions.count(), 1)

    @mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""})
    def test_sans_cle_aucune_generation_ni_faux_resultat(self):
        reponse = self.generer()
        self.assertEqual(reponse.status_code, 400)
        self.assertContains(reponse, "pas configurée", status_code=400)
        self.assertFalse(Quiz.objects.filter(origine=Quiz.Origine.IA).exists())

    @AVEC_CLE
    def test_cours_d_un_autre_enseignant_refuse(self):
        autre = creer_cours(self.autre_prof.enseignant, titre="Autre")
        self.assertEqual(self.generer(cours=autre.pk).status_code, 400)
        self.assertFalse(Quiz.objects.filter(origine=Quiz.Origine.IA).exists())

    @AVEC_CLE
    def test_source_indisponible_refusee(self):
        sans_resume = creer_cours(self.prof.enseignant, titre="Sans texte", texte=None)
        reponse = self.generer(cours=sans_resume.pk, source="resume")
        self.assertEqual(reponse.status_code, 400)

    @AVEC_CLE
    @override_settings(QUIZ_GENERATIONS_PAR_HEURE=1)
    @mock.patch("apps.quizzes.services.gemini._client")
    def test_limite_de_debit(self, client):
        client.return_value = reponse_gemini({"questions": [QCM]})
        self.generer()
        self.assertEqual(self.generer().status_code, 429)
        self.assertEqual(Quiz.objects.filter(origine=Quiz.Origine.IA).count(), 1)

    @AVEC_CLE
    def test_une_seule_generation_a_la_fois(self):
        Quiz.objects.create(titre="En cours", cours=self.cours, enseignant=self.prof.enseignant,
                            statut_generation=Quiz.Generation.EN_COURS)
        self.assertEqual(self.generer().status_code, 400)

    def test_etat_de_generation_json(self):
        quiz = Quiz.objects.create(titre="En cours", cours=self.cours, enseignant=self.prof.enseignant,
                                   statut_generation=Quiz.Generation.EN_COURS)
        donnees = self.client.get(reverse("etat-generation-quiz", args=[quiz.pk])).json()
        self.assertFalse(donnees["fini"])
        self.connecter(self.autre_prof)
        self.assertEqual(self.client.get(reverse("etat-generation-quiz", args=[quiz.pk])).status_code, 404)

    @AVEC_CLE
    def test_tache_idempotente(self):
        from apps.quizzes.tasks import generer_quiz_ia
        quiz = Quiz.objects.create(titre="Fini", cours=self.cours, enseignant=self.prof.enseignant,
                                   statut_generation=Quiz.Generation.TERMINEE)
        with mock.patch("apps.quizzes.tasks.generer_questions") as generer:
            generer_quiz_ia(quiz.pk)
        generer.assert_not_called()


class StatistiquesIATests(BaseQuizTestCase):
    def test_commande_sans_donnees_et_avec_donnees(self):
        from io import StringIO

        from django.core.management import call_command
        sortie = StringIO()
        call_command("stats_ia_quiz", stdout=sortie)
        self.assertIn("Aucun appel enregistré", sortie.getvalue())
        AppelIA.objects.create(type_appel=AppelIA.Type.GENERATION, succes=True, duree_ms=1200,
                               details={"demandees": 5, "valides": 4, "rejetees": 1})
        AppelIA.objects.create(type_appel=AppelIA.Type.GENERATION, succes=False, code="json_invalide", duree_ms=800)
        sortie = StringIO()
        call_command("stats_ia_quiz", stdout=sortie)
        texte = sortie.getvalue()
        self.assertIn("Appels : 2", texte)
        self.assertIn("50.0 %", texte)
        self.assertIn("conservées : 4", texte)
