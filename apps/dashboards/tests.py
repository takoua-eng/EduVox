"""Tableaux de bord : les chiffres affichés viennent des données réelles des modules."""

import io
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Enseignant, Etudiant, JournalConnexion, Utilisateur
from apps.assistant.models import SessionAssistant
from apps.courses.models import Consultation, Cours, Favori
from apps.quizzes.models import Quiz, TentativeQuiz

from .management.commands.peupler_demo import utilisateurs_demo
from .statistiques import activite_par_jour, score_moyen, stats_admin, stats_enseignant, stats_etudiant


def creer_utilisateur(username, role, **extra):
    user = Utilisateur.objects.create_user(username=username, email=f"{username}@eduvox.test",
                                           password="motdepasse-test", role=role, email_verifie=True, **extra)
    if role == Utilisateur.Role.ENSEIGNANT:
        Enseignant.objects.get_or_create(utilisateur=user)
    elif role == Utilisateur.Role.ETUDIANT:
        Etudiant.objects.get_or_create(utilisateur=user)
    return user


@override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
class TableauxDeBordTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = creer_utilisateur("admin", Utilisateur.Role.ADMIN)
        cls.prof = creer_utilisateur("prof", Utilisateur.Role.ENSEIGNANT)
        cls.autre_prof = creer_utilisateur("autre_prof", Utilisateur.Role.ENSEIGNANT)
        cls.etudiant = creer_utilisateur("etudiant", Utilisateur.Role.ETUDIANT, first_name="Amira")
        cls.autre_etudiant = creer_utilisateur("autre_etudiant", Utilisateur.Role.ETUDIANT)

        def cours(titre, prof, **champs):
            return Cours.objects.create(titre=titre, enseignant=prof.enseignant, type_fichier=Cours.TypeFichier.PDF,
                                        fichier=SimpleUploadedFile("c.pdf", b"%PDF-1.4"), **champs)

        cls.algebre = cours("Algèbre", cls.prof, publication=Cours.Publication.PUBLIE)
        cls.brouillon = cours("Brouillon", cls.prof, statut=Cours.Statut.ERREUR)
        cls.autre_cours = cours("Chimie", cls.autre_prof, publication=Cours.Publication.PUBLIE)

        cls.quiz = Quiz.objects.create(titre="Quiz matrices", cours=cls.algebre, enseignant=cls.prof.enseignant,
                                       statut=Quiz.Statut.PUBLIE)
        Quiz.objects.create(titre="Quiz brouillon", enseignant=cls.prof.enseignant)
        cls.quiz_autre = Quiz.objects.create(titre="Quiz chimie", enseignant=cls.autre_prof.enseignant,
                                             statut=Quiz.Statut.PUBLIE)

        maintenant = timezone.now()

        def tentative(quiz, etudiant, statut, score=None, numero=1):
            return TentativeQuiz.objects.create(
                quiz=quiz, etudiant=etudiant, statut=statut, numero=numero, score_max=Decimal("20"),
                score=Decimal(score) if score is not None else None,
                date_soumission=None if statut == TentativeQuiz.Statut.EN_COURS else maintenant)

        tentative(cls.quiz, cls.etudiant, TentativeQuiz.Statut.CORRIGEE, "16")                # 80 %
        tentative(cls.quiz, cls.etudiant, TentativeQuiz.Statut.CORRIGEE, "10", numero=2)      # 50 %
        tentative(cls.quiz, cls.autre_etudiant, TentativeQuiz.Statut.SOUMISE)                 # à corriger
        tentative(cls.quiz_autre, cls.autre_etudiant, TentativeQuiz.Statut.CORRIGEE, "4")     # autre enseignant

        Consultation.objects.create(utilisateur=cls.etudiant, cours=cls.algebre)
        Consultation.objects.create(utilisateur=cls.etudiant, cours=cls.algebre)
        Consultation.objects.create(utilisateur=cls.autre_etudiant, cours=cls.autre_cours)
        Favori.objects.create(utilisateur=cls.etudiant, cours=cls.algebre)
        SessionAssistant.objects.create(utilisateur=cls.etudiant, titre="Révision des matrices")
        JournalConnexion.objects.create(utilisateur=cls.etudiant, succes=True)
        JournalConnexion.objects.create(utilisateur=cls.etudiant, succes=True, suspect=True)
        JournalConnexion.objects.create(nom_saisi="inconnu", succes=False)

    # ---------- Calculs ----------

    def test_activite_par_jour_compte_chaque_jour_et_complete_par_des_zeros(self):
        Consultation.objects.filter(cours=self.autre_cours).update(date=timezone.now() - timedelta(days=3))
        activite = activite_par_jour([("Consultations", Consultation.objects.all(), "date")], jours=7)
        self.assertEqual(len(activite["jours"]), 7)
        self.assertEqual(activite["series"][0]["valeurs"], [0, 0, 0, 1, 0, 0, 2])
        self.assertEqual(activite["total"], 3)
        self.assertEqual(activite["lignes"][-1], (activite["jours"][-1], 2))

    def test_activite_ignore_ce_qui_est_trop_ancien(self):
        Consultation.objects.update(date=timezone.now() - timedelta(days=40))
        self.assertEqual(activite_par_jour([("C", Consultation.objects.all(), "date")])["total"], 0)

    def test_score_moyen(self):
        self.assertEqual(score_moyen(TentativeQuiz.objects.filter(etudiant=self.etudiant)), 65)
        self.assertIsNone(score_moyen(TentativeQuiz.objects.none()))

    def test_stats_admin(self):
        stats = stats_admin()
        self.assertEqual((stats["stat_total"], stats["stat_etudiants"], stats["stat_enseignants"]), (5, 2, 2))
        self.assertEqual((stats["stat_cours"], stats["stat_cours_publies"], stats["stat_quiz_publies"]), (3, 2, 2))
        self.assertEqual(stats["stat_tentatives_semaine"], 4)
        self.assertEqual((stats["stat_connexions_semaine"], stats["stat_suspectes_semaine"],
                          stats["stat_echecs_semaine"]), (2, 1, 1))
        self.assertEqual([s["nom"] for s in stats["activite"]["series"]],
                         ["Connexions réussies", "Consultations de cours", "Quiz commencés"])

    def test_stats_enseignant_limitees_a_ses_cours_et_quiz(self):
        stats = stats_enseignant(self.prof)
        self.assertEqual((stats["stat_cours"], stats["stat_cours_publies"], stats["stat_cours_erreur"]), (2, 1, 1))
        self.assertEqual((stats["stat_quiz"], stats["stat_quiz_publies"]), (2, 1))
        self.assertEqual(stats["stat_etudiants_actifs"], 2)        # l'un a consulté, l'autre a passé le quiz
        self.assertEqual(stats["stat_score_moyen"], 65)            # la copie de l'autre enseignant est exclue
        self.assertEqual(stats["stat_corrections_attente"], 1)
        self.assertEqual(len(stats["dernieres_tentatives"]), 3)
        self.assertEqual([(c.titre, c.nb_consultations) for c in stats["cours_populaires"]], [("Algèbre", 2)])
        self.assertEqual(stats["activite"]["total"], 5)            # 2 consultations + 3 tentatives

    def test_stats_etudiant(self):
        stats = stats_etudiant(self.etudiant)
        self.assertEqual(stats["stat_score_moyen"], 65)
        self.assertEqual((stats["stat_quiz_termines"], stats["stat_quiz_reussis"], stats["stat_taux_reussite"]),
                         (2, 1, 50))
        self.assertEqual((stats["stat_cours_consultes"], stats["stat_favoris"], stats["stat_sessions_assistant"]),
                         (1, 1, 1))
        textes = [e["texte"] for e in stats["activite_recente"]]
        self.assertIn("Assistant : Révision des matrices", textes)
        self.assertIn("Quiz « Quiz matrices » : 80 %", textes)
        self.assertIn("Cours consulté : Algèbre", textes)
        dates = [e["date"] for e in stats["activite_recente"]]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_etudiant_sans_activite(self):
        nouveau = creer_utilisateur("nouveau", Utilisateur.Role.ETUDIANT)
        stats = stats_etudiant(nouveau)
        self.assertIsNone(stats["stat_score_moyen"])
        self.assertIsNone(stats["stat_taux_reussite"])
        self.assertEqual(stats["activite_recente"], [])

    def test_quiz_en_cours_affiche_son_statut(self):
        TentativeQuiz.objects.create(quiz=self.quiz_autre, etudiant=self.etudiant)
        textes = [e["texte"] for e in stats_etudiant(self.etudiant)["activite_recente"]]
        self.assertIn("Quiz « Quiz chimie » : en cours", textes)

    # ---------- Pages ----------

    def page(self, user):
        self.client.force_login(user)
        reponse = self.client.get(reverse("index"))
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "<h1", count=1)
        self.assertNotContains(reponse, 'href="#"')
        return reponse

    def test_page_admin(self):
        reponse = self.page(self.admin)
        for texte in ["Cours publiés", "Connexions suspectes (7 jours)", "Activité de la plateforme",
                      'id="donnees-activite"', "Voir les données (tableau)", reverse("journal-connexions")]:
            self.assertContains(reponse, texte)

    def test_page_enseignant(self):
        reponse = self.page(self.prof)
        for texte in ["1 copie en attente de correction", "1 cours en erreur", "Activité de mes étudiants",
                      "Quiz matrices", "65 %", reverse("ajouter-cours"), reverse("generer-quiz")]:
            self.assertContains(reponse, texte)
        self.assertNotContains(reponse, "Taux de présence")

    def test_page_enseignant_sans_alerte(self):
        self.assertNotContains(self.page(self.autre_prof), "en attente de correction")

    def test_page_etudiant(self):
        reponse = self.page(self.etudiant)
        for texte in ["Bonjour Amira", "65 %", 'aria-valuenow="65"', "Quiz réussis (≥ 70 %)", "Révision des matrices",
                      reverse("assistant"), reverse("catalogue-defis"), reverse("mes-tentatives")]:
            self.assertContains(reponse, texte)
        self.assertNotContains(reponse, "Marquer ma présence")

    def test_page_etudiant_sans_activite(self):
        reponse = self.page(creer_utilisateur("nouveau", Utilisateur.Role.ETUDIANT))
        self.assertContains(reponse, "Aucune activité pour le moment")
        self.assertNotContains(reponse, "Voir mes résultats")


@override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class PeuplerDemoTests(TestCase):
    """Commande peupler_demo : données cohérentes, garde-fous, suppression complète."""

    def lancer(self, *arguments):
        sortie = io.StringIO()
        call_command("peupler_demo", "--etudiants", "4", "--mot-de-passe", "Demo-2026", *arguments, stdout=sortie)
        return sortie.getvalue()

    def test_donnees_coherentes(self):
        sortie = self.lancer()
        self.assertIn("Données de démonstration créées", sortie)
        demo = utilisateurs_demo()
        self.assertEqual((demo.filter(role="ENSEIGNANT").count(), demo.filter(role="ETUDIANT").count()), (4, 4))
        self.assertTrue(all(u.email.endswith("@vitrine.eduvox.test") and u.email_verifie for u in demo))
        self.assertTrue(demo.get(username="vitrine_sami.trabelsi").check_password("Demo-2026"))
        self.assertEqual(Cours.objects.count(), 12)
        self.assertEqual(Quiz.objects.filter(statut=Quiz.Statut.PUBLIE).count(), 11)  # un cours est programmé
        for tentative in TentativeQuiz.objects.all():  # score recalculé à partir des vraies réponses
            self.assertEqual(tentative.statut, TentativeQuiz.Statut.CORRIGEE)
            self.assertEqual(tentative.score, sum(r.points_obtenus for r in tentative.reponses.all()))
            self.assertLessEqual(tentative.date_debut, timezone.now())
        self.assertTrue(JournalConnexion.objects.filter(suspect=True).exists())
        self.assertEqual(stats_admin()["stat_cours_publies"], 11)

    def test_reproductible(self):
        self.lancer()
        premier = sorted(TentativeQuiz.objects.values_list("etudiant__username", "quiz__titre", "score"))
        self.lancer("--reinitialiser")
        self.assertEqual(sorted(TentativeQuiz.objects.values_list("etudiant__username", "quiz__titre", "score")), premier)

    def test_pas_de_doublon_sans_reinitialiser(self):
        self.lancer()
        with self.assertRaisesMessage(CommandError, "existent déjà"):
            self.lancer()

    def test_supprimer_ne_touche_que_la_demo(self):
        vrai = creer_utilisateur("vrai_prof", Utilisateur.Role.ENSEIGNANT)
        # Comptes de la démo EduSprint (module défis) : ni bloquants, ni supprimés
        edusprint = Utilisateur.objects.create_user(username="demo_prof", email="demo_prof@demo.eduvox.test",
                                                    password="x", role=Utilisateur.Role.ENSEIGNANT)
        self.lancer()
        self.assertIn("8 comptes, 12 cours", self.lancer("--supprimer"))
        self.assertFalse(utilisateurs_demo().exists())
        self.assertEqual((Cours.objects.count(), TentativeQuiz.objects.count(), Consultation.objects.count()), (0, 0, 0))
        self.assertTrue(Utilisateur.objects.filter(pk__in=[vrai.pk, edusprint.pk]).count() == 2)

    def test_base_partagee_exige_oui(self):
        from django.db import connection

        with mock.patch.object(connection, "vendor", "postgresql"):
            with self.assertRaisesMessage(CommandError, "--oui"):
                self.lancer()
        self.assertFalse(utilisateurs_demo().exists())

    def test_migrations_en_attente(self):
        migration = mock.Mock(app_label="accounts", name="0099_test")
        migration.name = "0099_test"
        with mock.patch("apps.dashboards.management.commands.peupler_demo.MigrationExecutor") as executeur:
            executeur.return_value.migration_plan.return_value = [(migration, False)]
            with self.assertRaisesMessage(CommandError, "accounts.0099_test"):
                self.lancer()

    def test_erreur_annule_tout_et_retire_les_pdf(self):
        from django.core.files.storage import default_storage

        def fichiers():
            return sorted(default_storage.listdir("cours")[1]) if default_storage.exists("cours") else []

        avant = fichiers()  # le stockage en mémoire garde les fichiers des autres tests
        with mock.patch("apps.dashboards.management.commands.peupler_demo.Command.creer_tentative",
                        side_effect=RuntimeError("panne")):
            with self.assertRaises(RuntimeError):
                self.lancer()
        self.assertFalse(utilisateurs_demo().exists())
        self.assertEqual(fichiers(), avant)

    def test_mot_de_passe_aleatoire_affiche_une_fois(self):
        sortie = io.StringIO()
        with mock.patch.dict("os.environ", {"DEMO_MOT_DE_PASSE": ""}):
            call_command("peupler_demo", "--etudiants", "2", stdout=sortie)
        self.assertIn("affiché une seule fois", sortie.getvalue())

    def test_tableaux_de_bord_avec_la_demo(self):
        self.lancer()
        for username in ["vitrine_sami.trabelsi", utilisateurs_demo().filter(role="ETUDIANT").first().username]:
            self.client.force_login(Utilisateur.objects.get(username=username))
            self.assertEqual(self.client.get(reverse("index")).status_code, 200)
