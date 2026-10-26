"""Carnet de révision espacée, intégrité, import intelligent, certificat vérifiable et export CSV."""

import os
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core import signing
from django.urls import reverse
from django.utils import timezone

from apps.quizzes.models import CarteRevision, Question, Quiz, TentativeQuiz
from apps.quizzes.services import certificat as certificats
from apps.quizzes.services.integrite import fusionner, lire_compteur, melanger_choix

from .base import BaseQuizTestCase
from .test_assistant import AVEC_CLE, FETCH, SANS_CLE, client_gemini


class CarnetRevisionTests(BaseQuizTestCase):
    def setUp(self):
        super().setUp()
        self.connecter(self.etudiant)

    def soumettre(self, donnees, quiz=None):
        tentative = TentativeQuiz.objects.create(quiz=quiz or self.quiz, etudiant=self.etudiant)
        with SANS_CLE, self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("tentative", args=[tentative.pk]), donnees)
        return tentative

    def rendre_due(self, carte):
        CarteRevision.objects.filter(pk=carte.pk).update(prochaine_revision=timezone.localdate())

    def test_erreurs_ajoutees_au_carnet(self):
        self.soumettre({f"question_{self.q_qcm.pk}": self.mauvais.pk, f"question_{self.q_vf.pk}": self.vrai.pk})
        carte = CarteRevision.objects.get(etudiant=self.etudiant)
        self.assertEqual(carte.question, self.q_qcm)   # seule la question ratée ; l'ouverte n'entre pas
        self.assertEqual((carte.boite, carte.prochaine_revision), (1, timezone.localdate() + timedelta(days=1)))

    def test_corrige_masque_rien_dans_le_carnet(self):
        Quiz.objects.filter(pk=self.quiz.pk).update(afficher_corrige=False)
        self.soumettre({f"question_{self.q_qcm.pk}": self.mauvais.pk})
        self.assertFalse(CarteRevision.objects.exists())

    def test_methode_de_leitner(self):
        carte = CarteRevision.objects.create(etudiant=self.etudiant, question=self.q_qcm)
        carte.enregistrer_reponse(True)
        self.assertEqual((carte.boite, carte.prochaine_revision), (2, timezone.localdate() + timedelta(days=2)))
        carte.enregistrer_reponse(False)
        self.assertEqual(carte.boite, 1)
        carte.boite = 5
        carte.enregistrer_reponse(True)
        self.assertTrue(carte.maitrisee)
        self.assertEqual((carte.nb_revisions, carte.nb_reussites), (3, 2))

    def test_parcours_de_revision(self):
        self.soumettre({f"question_{self.q_qcm.pk}": self.mauvais.pk})
        # Une question laissée sans réponse (ici le Vrai/Faux) entre aussi dans le carnet
        self.assertEqual(CarteRevision.objects.count(), 2)
        CarteRevision.objects.exclude(question=self.q_qcm).delete()
        carte = CarteRevision.objects.get(question=self.q_qcm)
        self.assertContains(self.client.get(reverse("revision")), "Rien à réviser aujourd'hui")
        self.rendre_due(carte)
        page = self.client.get(reverse("revision"))
        self.assertContains(page, self.q_qcm.enonce)
        self.assertNotIn("est_correct", str(page.context["choix"]))
        self.client.post(reverse("repondre-revision", args=[carte.pk]), {"choix": self.bon.pk})
        carte.refresh_from_db()
        self.assertEqual(carte.boite, 2)
        page = self.client.get(reverse("revision"))
        self.assertContains(page, "Bonne réponse !")
        self.assertContains(page, "boîte 2")

    def test_reponse_invalide_ou_fiche_non_due(self):
        carte = CarteRevision.objects.create(etudiant=self.etudiant, question=self.q_qcm)
        self.client.post(reverse("repondre-revision", args=[carte.pk]), {"choix": self.vrai.pk})  # choix d'une autre question
        carte.refresh_from_db()
        self.assertEqual(carte.nb_revisions, 0)
        CarteRevision.objects.filter(pk=carte.pk).update(prochaine_revision=timezone.localdate() + timedelta(days=3))
        self.client.post(reverse("repondre-revision", args=[carte.pk]), {"choix": self.bon.pk})
        carte.refresh_from_db()
        self.assertEqual(carte.nb_revisions, 0)

    def test_carnet_prive(self):
        carte = CarteRevision.objects.create(etudiant=self.autre_etudiant, question=self.q_qcm)
        self.assertEqual(self.client.post(reverse("repondre-revision", args=[carte.pk]), {"choix": self.bon.pk}).status_code, 404)
        self.connecter(self.prof)
        self.assertEqual(self.client.get(reverse("revision")).status_code, 403)


class IntegriteTests(BaseQuizTestCase):
    def setUp(self):
        super().setUp()
        self.connecter(self.etudiant)
        self.client.post(reverse("commencer-quiz", args=[self.quiz.pk]))
        self.tentative = TentativeQuiz.objects.get(etudiant=self.etudiant)

    def envoyer(self, **donnees):
        return self.client.post(reverse("integrite-tentative", args=[self.tentative.pk]), donnees, **FETCH)

    def test_compteurs_croissants_et_bornes(self):
        self.envoyer(sorties=2, collages=1)
        self.envoyer(sorties=0, collages="abc")          # baisse ou valeur falsifiée : ignorées
        self.envoyer(sorties=99999)                       # hors bornes : ignorée
        self.tentative.refresh_from_db()
        self.assertEqual(self.tentative.integrite, {"sorties": 2, "collages": 1})
        self.assertEqual(lire_compteur("-1"), None)
        self.assertEqual(fusionner({"sorties": 5}, 3, None), {"sorties": 5})

    def test_signaux_a_verifier(self):
        self.envoyer(sorties=4, collages=1)
        with SANS_CLE, self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("tentative", args=[self.tentative.pk]), {})
        self.tentative.refresh_from_db()
        signaux = " ".join(self.tentative.signaux_integrite)
        self.assertIn("4 sorties", signaux)
        self.assertIn("collé", signaux)
        self.assertIn("très rapides", signaux)
        self.assertEqual(self.envoyer(sorties=9).status_code, 400)   # tentative soumise : plus de mise à jour
        self.connecter(self.prof)
        self.assertContains(self.client.get(reverse("resultats-quiz", args=[self.quiz.pk])), "À vérifier")
        self.assertContains(self.client.get(reverse("tentative", args=[self.tentative.pk])), "Signaux d'intégrité")

    def test_tentative_d_un_autre(self):
        self.connecter(self.autre_etudiant)
        self.assertEqual(self.envoyer(sorties=1).status_code, 404)

    def test_melange_propre_a_chaque_tentative(self):
        choix = list(range(6))
        self.assertEqual(melanger_choix(choix, 1, 7), melanger_choix(choix, 1, 7))   # stable au rechargement
        self.assertNotEqual([melanger_choix(choix, g, 7) for g in range(5)].count(choix), 5)
        page = self.client.get(reverse("tentative", args=[self.tentative.pk]))
        qcm = next(q for q in page.context["questions"] if q["pk"] == self.q_qcm.pk)
        attendu = melanger_choix([{"pk": c.pk, "texte": c.texte} for c in self.q_qcm.choix.all()],
                                 self.tentative.graine, self.q_qcm.pk)
        self.assertEqual(qcm["choix"], attendu)


class ImportTests(BaseQuizTestCase):
    TEXTE = "1) Quelle commande crée une migration ? a) migrate b) makemigrations (*)\n2) Expliquez le CSRF. (2 points)"
    SORTIE = {"questions": [
        {"type": "qcm", "enonce": "Quelle commande crée une migration ?", "points": 1,
         "choix": [{"texte": "migrate", "correct": False}, {"texte": "makemigrations", "correct": True}],
         "explication": "", "reponse_attendue": "", "reponse_indiquee": True},
        {"type": "qcm", "enonce": "Quel fichier contient les modèles ?", "points": 1,
         "choix": [{"texte": "models.py", "correct": True}, {"texte": "urls.py", "correct": False}],
         "explication": "", "reponse_attendue": "", "reponse_indiquee": False},
        {"type": "qcm", "enonce": "Question invalide sans bonne réponse", "points": 1,
         "choix": [{"texte": "A", "correct": False}, {"texte": "B", "correct": False}],
         "explication": "", "reponse_attendue": "", "reponse_indiquee": True},
    ]}

    def setUp(self):
        super().setUp()
        self.brouillon = self.creer_quiz(statut=Quiz.Statut.BROUILLON)
        self.connecter(self.prof)
        self.url = reverse("importer-questions", args=[self.brouillon.pk])

    @AVEC_CLE
    @mock.patch("apps.quizzes.services.gemini._client")
    def test_apercu_puis_import_selectif(self, client):
        client.return_value, prompts = client_gemini(self.SORTIE)
        page = self.client.post(self.url, {"action": "analyser", "texte": self.TEXTE})
        self.assertContains(page, "Réponse déduite par l'IA")
        self.assertEqual(len(page.context["apercu"]), 2)
        self.assertEqual(len(page.context["rejets"]), 1)
        self.assertIn("<document>", prompts[0]["contents"])
        self.assertEqual(self.brouillon.questions.count(), 3)          # rien n'est ajouté avant confirmation
        self.client.post(self.url, {"action": "confirmer", "selection": ["0", "1", "99"]})
        questions = list(self.brouillon.questions.order_by("ordre"))
        self.assertEqual([q.ordre for q in questions], [1, 2, 3, 4, 5])
        self.assertEqual(questions[3].choix.get(est_correct=True).texte, "makemigrations")
        self.assertEqual(self.brouillon.erreurs_publication(), [])

    @AVEC_CLE
    def test_texte_trop_long_ou_vide(self):
        for texte in ("court", "x" * 15001):
            with self.subTest(longueur=len(texte)):
                self.assertEqual(self.client.post(self.url, {"action": "analyser", "texte": texte}).status_code, 400)

    @SANS_CLE
    def test_sans_ia(self):
        self.assertContains(self.client.post(self.url, {"action": "analyser", "texte": self.TEXTE}), "pas configurée",
                            status_code=400)

    def test_acces_et_quiz_verrouille(self):
        self.assertEqual(self.client.get(reverse("importer-questions", args=[self.quiz.pk])).status_code, 302)  # publié
        self.connecter(self.autre_prof)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.connecter(self.etudiant)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_confirmation_sans_apercu(self):
        self.client.post(self.url, {"action": "confirmer", "selection": ["0"]})
        self.assertEqual(self.brouillon.questions.count(), 3)


class CertificatEtExportTests(BaseQuizTestCase):
    def setUp(self):
        super().setUp()
        self.tentative = TentativeQuiz.objects.create(
            quiz=self.quiz, etudiant=self.etudiant, statut=TentativeQuiz.Statut.CORRIGEE, score=Decimal("5"),
            score_max=Decimal("6"), date_soumission=timezone.now())

    def test_certificat_pdf_et_verification_publique(self):
        self.connecter(self.etudiant)
        reponse = self.client.get(reverse("certificat", args=[self.tentative.pk]))
        self.assertEqual(reponse["Content-Type"], "application/pdf")
        self.assertTrue(reponse.content.startswith(b"%PDF"))
        jeton = certificats.jeton(self.tentative)
        self.client.logout()                                   # page publique, sans connexion
        page = self.client.get(reverse("verifier-certificat", args=[jeton]))
        self.assertContains(page, "Certificat authentique")
        self.assertContains(page, "83 %")
        self.assertContains(self.client.get(reverse("verifier-certificat", args=[jeton[:-3] + "abc"])),
                            "Certificat non valide")
        faux = signing.dumps({"t": self.tentative.pk, "s": "6.00"}, salt="autre-sel")
        self.assertContains(self.client.get(reverse("verifier-certificat", args=[faux])), "Certificat non valide")
        TentativeQuiz.objects.filter(pk=self.tentative.pk).update(score=Decimal("4.50"))   # note révisée
        self.assertContains(self.client.get(reverse("verifier-certificat", args=[jeton])), "Certificat non valide")

    def test_certificat_refuse(self):
        TentativeQuiz.objects.filter(pk=self.tentative.pk).update(score=Decimal("3"))
        self.connecter(self.etudiant)
        self.assertRedirects(self.client.get(reverse("certificat", args=[self.tentative.pk])),
                             reverse("tentative", args=[self.tentative.pk]))
        self.connecter(self.autre_etudiant)
        self.assertEqual(self.client.get(reverse("certificat", args=[self.tentative.pk])).status_code, 404)

    def test_export_csv(self):
        self.connecter(self.prof)
        reponse = self.client.get(reverse("export-resultats", args=[self.quiz.pk]))
        contenu = reponse.content.decode("utf-8")
        self.assertTrue(contenu.startswith("﻿"))
        self.assertIn("etudiant;", contenu)
        self.assertIn(";83;", contenu)
        self.connecter(self.admin)
        self.assertEqual(self.client.get(reverse("export-resultats", args=[self.quiz.pk])).status_code, 200)
        self.connecter(self.autre_prof)
        self.assertEqual(self.client.get(reverse("export-resultats", args=[self.quiz.pk])).status_code, 404)
        self.connecter(self.etudiant)
        self.assertEqual(self.client.get(reverse("export-resultats", args=[self.quiz.pk])).status_code, 403)

    def test_pilotage_et_resultat(self):
        self.connecter(self.admin)
        self.assertEqual(self.client.get(reverse("pilotage-quiz")).context["certifies"], 1)
        self.connecter(self.etudiant)
        self.assertContains(self.client.get(reverse("tentative", args=[self.tentative.pk])), "Mon certificat (PDF)")
        self.assertContains(self.client.get(reverse("liste-quiz")), "Réviser")
        self.assertEqual(Question.objects.count(), 3)
