import csv
import io
import shutil
import tempfile
from datetime import datetime, timedelta
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from ai_services import appeler_gemini, duree_audio, extraire_texte_pdf, resumer_cours, traduire_texte, transcrire
from apps.accounts.models import Enseignant, Etudiant, Utilisateur
from config.celery import app as celery_app

from .forms import CoursForm
from .models import (
    Avis,
    Consultation,
    Cours,
    Favori,
    JournalActivite,
    Matiere,
    NotePersonnelle,
    Resume,
    Signalement,
    Telechargement,
    Transcription,
    TraductionResume,
    annee_universitaire,
    bornes_annee_universitaire,
)
from .recommandations import cours_similaires
from .tasks import generer_resume_cours, transcrire_cours

MEDIA_TEST = tempfile.mkdtemp()


def creer_utilisateur(username, role, **extra):
    user = Utilisateur.objects.create_user(
        username=username, email=f"{username}@eduvox.test", password="motdepasse-test", role=role, **extra
    )
    if role == Utilisateur.Role.ENSEIGNANT:
        Enseignant.objects.create(utilisateur=user)
    elif role == Utilisateur.Role.ETUDIANT:
        Etudiant.objects.create(utilisateur=user)
    return user


def fichier_pdf(nom="cours.pdf"):
    return SimpleUploadedFile(nom, b"%PDF-1.4 contenu de test", content_type="application/pdf")


@override_settings(MEDIA_ROOT=MEDIA_TEST)
class BaseCoursTestCase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_TEST, ignore_errors=True)

    @classmethod
    def setUpTestData(cls):
        cls.prof = creer_utilisateur("prof", Utilisateur.Role.ENSEIGNANT)
        cls.autre_prof = creer_utilisateur("autre_prof", Utilisateur.Role.ENSEIGNANT)
        cls.etudiant = creer_utilisateur("etudiant", Utilisateur.Role.ETUDIANT)
        cls.admin = creer_utilisateur("admin", Utilisateur.Role.ADMIN)
        cls.superuser = creer_utilisateur("super", Utilisateur.Role.ETUDIANT, is_superuser=True, is_staff=True)

    def setUp(self):
        self.cours = Cours.objects.create(
            titre="Algèbre linéaire",
            description="Matrices et vecteurs",
            fichier=fichier_pdf(),
            type_fichier=Cours.TypeFichier.PDF,
            enseignant=self.prof.enseignant,
            publication=Cours.Publication.PUBLIE,
        )


@mock.patch("apps.courses.tasks.resumer_cours", return_value="Résumé du cours")
@mock.patch("apps.courses.tasks.extraire_texte_pdf", return_value="Texte du cours")
class CreationCoursTests(BaseCoursTestCase):
    def test_enseignant_cree_un_cours_sans_lancer_l_ia(self, mock_pdf, mock_resume):
        self.client.force_login(self.prof)
        with self.captureOnCommitCallbacks(execute=True):
            reponse = self.client.post(reverse("ajouter-cours"), {"publication": "brouillon", 
                "titre": "Probabilités", "description": "Intro", "fichier": fichier_pdf("proba.pdf"),
            })

        cours = Cours.objects.get(titre="Probabilités")
        self.assertRedirects(reponse, reverse("detail-cours", args=[cours.pk]))
        self.assertEqual(cours.enseignant, self.prof.enseignant)
        self.assertEqual(cours.type_fichier, Cours.TypeFichier.PDF)
        self.assertEqual(cours.statut, Cours.Statut.EN_ATTENTE)
        self.assertEqual(cours.progression, 0)
        mock_pdf.assert_not_called()
        mock_resume.assert_not_called()

    def test_type_audio_detecte(self, mock_pdf, mock_resume):
        self.client.force_login(self.prof)
        self.client.post(reverse("ajouter-cours"), {"publication": "brouillon", 
            "titre": "Podcast", "fichier": SimpleUploadedFile("seance.mp3", b"ID3 audio", content_type="audio/mpeg"),
        })
        self.assertEqual(Cours.objects.get(titre="Podcast").type_fichier, Cours.TypeFichier.AUDIO)

    def test_nouveau_fichier_efface_transcription_et_resume(self, mock_pdf, mock_resume):
        Transcription.objects.create(cours=self.cours, texte="Ancien texte")
        Resume.objects.create(cours=self.cours, texte="Ancien résumé")
        self.cours.mettre_a_jour(statut=Cours.Statut.TERMINE, progression=100)

        self.client.force_login(self.prof)
        self.client.post(reverse("modifier-cours", args=[self.cours.pk]), {"publication": "publie", 
            "titre": self.cours.titre, "fichier": fichier_pdf("v2.pdf"),
        })
        self.cours.refresh_from_db()
        self.assertEqual(self.cours.statut, Cours.Statut.EN_ATTENTE)
        self.assertFalse(Transcription.objects.filter(cours=self.cours).exists())
        self.assertFalse(Resume.objects.filter(cours=self.cours).exists())

    def test_format_refuse(self, mock_pdf, mock_resume):
        self.client.force_login(self.prof)
        reponse = self.client.post(reverse("ajouter-cours"), {"publication": "brouillon", 
            "titre": "Mauvais", "fichier": SimpleUploadedFile("notes.txt", b"texte"),
        })
        self.assertEqual(reponse.status_code, 200)
        self.assertIn("Format non accepté", reponse.context["form"].errors["fichier"][0])
        self.assertFalse(Cours.objects.filter(titre="Mauvais").exists())

    @override_settings(COURS_TAILLE_MAX_MO=0)
    def test_fichier_trop_volumineux(self, mock_pdf, mock_resume):
        self.client.force_login(self.prof)
        reponse = self.client.post(reverse("ajouter-cours"), {"publication": "brouillon", "titre": "Gros", "fichier": fichier_pdf()})
        self.assertIn("taille maximale", reponse.context["form"].errors["fichier"][0])

    def test_modifier_sans_changer_de_fichier_ne_relance_pas(self, mock_pdf, mock_resume):
        self.client.force_login(self.prof)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("modifier-cours", args=[self.cours.pk]), {"publication": "publie", "titre": "Nouveau titre"})
        self.cours.refresh_from_db()
        self.assertEqual(self.cours.titre, "Nouveau titre")
        self.assertEqual(self.cours.statut, Cours.Statut.EN_ATTENTE)
        mock_pdf.assert_not_called()


class PermissionsEnseignantTests(BaseCoursTestCase):
    def test_liste_ne_contient_que_ses_cours(self):
        Cours.objects.create(titre="Cours d'un autre", fichier=fichier_pdf(), type_fichier="pdf",
                             enseignant=self.autre_prof.enseignant)
        self.client.force_login(self.prof)
        reponse = self.client.get(reverse("liste-cours"))
        self.assertEqual(list(reponse.context["liste_cours"]), [self.cours])
        self.assertContains(reponse, reverse("ajouter-cours"))

    def test_proprietaire_consulte_et_modifie(self):
        self.client.force_login(self.prof)
        self.assertEqual(self.client.get(reverse("detail-cours", args=[self.cours.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("modifier-cours", args=[self.cours.pk])).status_code, 200)

    def test_autre_enseignant_ne_peut_ni_consulter_ni_modifier_ni_supprimer(self):
        self.client.force_login(self.autre_prof)
        self.assertEqual(self.client.get(reverse("detail-cours", args=[self.cours.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("modifier-cours", args=[self.cours.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("supprimer-cours", args=[self.cours.pk])).status_code, 404)
        self.assertTrue(Cours.objects.filter(pk=self.cours.pk).exists())

    def test_proprietaire_supprime_son_cours(self):
        self.client.force_login(self.prof)
        reponse = self.client.post(reverse("supprimer-cours", args=[self.cours.pk]))
        self.assertRedirects(reponse, reverse("liste-cours"))
        self.assertFalse(Cours.objects.filter(pk=self.cours.pk).exists())

    def test_enseignant_sans_profil_est_refuse(self):
        sans_profil = Utilisateur.objects.create_user(
            username="sans_profil", email="sp@eduvox.test", password="x", role=Utilisateur.Role.ENSEIGNANT
        )
        self.client.force_login(sans_profil)
        reponse = self.client.post(reverse("ajouter-cours"), {"publication": "brouillon", "titre": "X", "fichier": fichier_pdf()})
        self.assertEqual(reponse.status_code, 403)


class PermissionsEtudiantTests(BaseCoursTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.etudiant)

    def test_voit_tous_les_cours_et_les_consulte(self):
        reponse = self.client.get(reverse("liste-cours"))
        self.assertIn(self.cours, reponse.context["liste_cours"])
        self.assertNotContains(reponse, reverse("ajouter-cours"))
        self.assertEqual(self.client.get(reverse("detail-cours", args=[self.cours.pk])).status_code, 200)

    def test_ne_peut_ni_creer_ni_modifier_ni_supprimer(self):
        self.assertEqual(self.client.get(reverse("ajouter-cours")).status_code, 403)
        self.assertEqual(self.client.get(reverse("modifier-cours", args=[self.cours.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("supprimer-cours", args=[self.cours.pk])).status_code, 403)
        self.assertTrue(Cours.objects.filter(pk=self.cours.pk).exists())

    def test_telecharge_le_resume(self):
        Resume.objects.create(cours=self.cours, texte="Les matrices, c'est utile.")
        reponse = self.client.get(reverse("telecharger-resume", args=[self.cours.pk]))
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse["Content-Type"], "text/plain; charset=utf-8")
        self.assertIn('attachment; filename="resume-algebre-lineaire.txt"', reponse["Content-Disposition"])
        self.assertIn("Les matrices, c'est utile.", reponse.content.decode())

    def test_telechargement_sans_resume_renvoie_404(self):
        self.assertEqual(self.client.get(reverse("telecharger-resume", args=[self.cours.pk])).status_code, 404)

    def test_endpoint_progression(self):
        self.cours.mettre_a_jour(statut=Cours.Statut.EN_COURS, progression=33)
        donnees = self.client.get(reverse("progression-cours", args=[self.cours.pk])).json()
        self.assertEqual(donnees["progression"], 33)
        self.assertEqual(donnees["statut"], "en_cours")
        self.assertFalse(donnees["fini"])


class PermissionsAdministrateurTests(BaseCoursTestCase):
    def test_admin_et_superuser_consultent_tous_les_cours(self):
        for user in [self.admin, self.superuser]:
            self.client.force_login(user)
            self.assertIn(self.cours, self.client.get(reverse("liste-cours")).context["liste_cours"])
            self.assertEqual(self.client.get(reverse("detail-cours", args=[self.cours.pk])).status_code, 200)

    def test_admin_et_superuser_ne_peuvent_ni_creer_ni_modifier_ni_supprimer(self):
        for user in [self.admin, self.superuser]:
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse("ajouter-cours")).status_code, 403)
            self.assertEqual(self.client.get(reverse("modifier-cours", args=[self.cours.pk])).status_code, 403)
            self.assertEqual(self.client.post(reverse("supprimer-cours", args=[self.cours.pk])).status_code, 403)
            self.assertEqual(self.client.post(reverse("generer-resume", args=[self.cours.pk])).status_code, 403)
            self.assertEqual(self.client.post(reverse("transcrire-cours", args=[self.cours.pk])).status_code, 403)
        self.assertTrue(Cours.objects.filter(pk=self.cours.pk).exists())


@mock.patch("apps.courses.tasks.resumer_cours", return_value="Résumé")
@mock.patch("apps.courses.tasks.transcrire", return_value="Texte transcrit")
@mock.patch("apps.courses.tasks.extraire_texte_pdf", return_value="Texte PDF")
class BoutonsTraitementTests(BaseCoursTestCase):
    """Boutons « Transcrire l'audio » et « Générer le résumé » de la page détail."""

    def setUp(self):
        super().setUp()
        self.audio = Cours.objects.create(
            titre="Séance enregistrée", type_fichier=Cours.TypeFichier.AUDIO, enseignant=self.prof.enseignant,
            fichier=SimpleUploadedFile("seance.mp3", b"ID3 audio", content_type="audio/mpeg"),
        )

    def poster(self, nom_url, cours, user=None, donnees=None):
        self.client.force_login(user or self.prof)
        with self.captureOnCommitCallbacks(execute=True):
            reponse = self.client.post(reverse(nom_url, args=[cours.pk]), donnees or {"type_resume": "detaille"})
        cours.refresh_from_db()
        return reponse

    def test_boutons_affiches_au_proprietaire_seulement(self, *mocks):
        self.client.force_login(self.prof)
        self.assertContains(self.client.get(reverse("detail-cours", args=[self.cours.pk])), reverse("generer-resume", args=[self.cours.pk]))
        page_audio = self.client.get(reverse("detail-cours", args=[self.audio.pk]))
        self.assertContains(page_audio, reverse("transcrire-cours", args=[self.audio.pk]))
        self.assertNotContains(page_audio, reverse("generer-resume", args=[self.audio.pk]))

        self.client.force_login(self.etudiant)
        self.assertNotContains(self.client.get(reverse("detail-cours", args=[self.cours.pk])), reverse("generer-resume", args=[self.cours.pk]))

    def test_generer_resume_pdf(self, mock_pdf, mock_transcrire, mock_resume):
        reponse = self.poster("generer-resume", self.cours)
        self.assertRedirects(reponse, reverse("detail-cours", args=[self.cours.pk]))
        self.assertEqual(self.cours.statut, Cours.Statut.TERMINE)
        self.assertEqual(self.cours.resume.texte, "Résumé")
        mock_transcrire.assert_not_called()

    def test_audio_transcription_puis_resume(self, mock_pdf, mock_transcrire, mock_resume):
        self.poster("transcrire-cours", self.audio)
        self.assertEqual(self.audio.transcription.texte, "Texte transcrit")
        mock_resume.assert_not_called()

        self.poster("generer-resume", self.audio)
        mock_resume.assert_called_once_with("Texte transcrit", type_resume="detaille", consigne="")
        mock_transcrire.assert_called_once()
        self.assertEqual(self.audio.resume.texte, "Résumé")

    def test_resume_audio_refuse_sans_transcription(self, mock_pdf, mock_transcrire, mock_resume):
        self.poster("generer-resume", self.audio)
        self.assertEqual(self.audio.statut, Cours.Statut.EN_ATTENTE)
        mock_resume.assert_not_called()

    def test_transcription_refusee_pour_un_pdf(self, mock_pdf, mock_transcrire, mock_resume):
        self.poster("transcrire-cours", self.cours)
        self.assertEqual(self.cours.statut, Cours.Statut.EN_ATTENTE)
        mock_transcrire.assert_not_called()

    def test_pas_de_double_lancement(self, mock_pdf, mock_transcrire, mock_resume):
        self.cours.mettre_a_jour(statut=Cours.Statut.EN_COURS, progression=33)
        self.poster("generer-resume", self.cours)
        self.assertEqual(self.cours.progression, 33)
        mock_resume.assert_not_called()

    def test_autre_enseignant_et_etudiant_refuses(self, mock_pdf, mock_transcrire, mock_resume):
        self.assertEqual(self.poster("generer-resume", self.cours, self.autre_prof).status_code, 404)
        self.assertEqual(self.poster("generer-resume", self.cours, self.etudiant).status_code, 403)
        mock_resume.assert_not_called()


class TachesCeleryTests(BaseCoursTestCase):
    """Les tâches Celery sont exécutées en mode eager, avec les appels IA simulés."""

    def setUp(self):
        super().setUp()
        eager = celery_app.conf.task_always_eager
        celery_app.conf.task_always_eager = True
        self.addCleanup(setattr, celery_app.conf, "task_always_eager", eager)

    @mock.patch("apps.courses.tasks.resumer_cours")
    @mock.patch("apps.courses.tasks.extraire_texte_pdf", return_value="Texte PDF")
    def test_resume_pdf_progression_0_33_66_100(self, mock_pdf, mock_resume):
        progressions = []

        def resumer(texte, **options):
            progressions.append(Cours.objects.get(pk=self.cours.pk).progression)
            return "Résumé"

        mock_resume.side_effect = resumer
        generer_resume_cours.delay(self.cours.pk)

        self.cours.refresh_from_db()
        self.assertEqual(progressions, [33])
        self.assertEqual(self.cours.statut, Cours.Statut.TERMINE)
        self.assertEqual(self.cours.progression, 100)
        mock_pdf.assert_called_once_with(self.cours.fichier.path)
        mock_resume.assert_called_once_with("Texte PDF", type_resume="detaille", consigne="")
        self.assertEqual(Transcription.objects.get(cours=self.cours).texte, "Texte PDF")
        self.assertEqual(Resume.objects.get(cours=self.cours).texte, "Résumé")

    @mock.patch("apps.courses.tasks.resumer_cours", return_value="Résumé")
    @mock.patch("apps.courses.tasks.extraire_texte_pdf")
    def test_resume_reutilise_le_texte_deja_extrait(self, mock_pdf, mock_resume):
        Transcription.objects.create(cours=self.cours, texte="Texte existant")
        generer_resume_cours.delay(self.cours.pk)
        mock_pdf.assert_not_called()
        mock_resume.assert_called_once_with("Texte existant", type_resume="detaille", consigne="")

    @mock.patch("apps.courses.tasks.resumer_cours")
    @mock.patch("apps.courses.tasks.transcrire", return_value="Texte transcrit")
    def test_transcription_audio_sans_resume(self, mock_transcrire, mock_resume):
        self.cours.mettre_a_jour(type_fichier=Cours.TypeFichier.AUDIO)
        transcrire_cours.delay(self.cours.pk)

        self.cours.refresh_from_db()
        mock_transcrire.assert_called_once_with(self.cours.fichier.path)
        mock_resume.assert_not_called()
        self.assertEqual(self.cours.statut, Cours.Statut.TERMINE)
        self.assertEqual(self.cours.progression, 100)
        self.assertEqual(self.cours.transcription.texte, "Texte transcrit")
        self.assertFalse(Resume.objects.filter(cours=self.cours).exists())

    @mock.patch("apps.courses.tasks.transcrire", side_effect=RuntimeError("FFmpeg introuvable"))
    def test_erreur_transcription(self, mock_transcrire):
        self.cours.mettre_a_jour(type_fichier=Cours.TypeFichier.AUDIO)
        transcrire_cours.delay(self.cours.pk)

        self.cours.refresh_from_db()
        self.assertEqual(self.cours.statut, Cours.Statut.ERREUR)
        self.assertEqual(self.cours.message_erreur, "Échec pendant la transcription audio : FFmpeg introuvable")

    @mock.patch("apps.courses.tasks.resumer_cours")
    def test_resume_audio_sans_transcription_en_erreur(self, mock_resume):
        self.cours.mettre_a_jour(type_fichier=Cours.TypeFichier.AUDIO)
        generer_resume_cours.delay(self.cours.pk)

        self.cours.refresh_from_db()
        self.assertEqual(self.cours.statut, Cours.Statut.ERREUR)
        self.assertIn("l'audio doit d'abord être transcrit", self.cours.message_erreur)
        mock_resume.assert_not_called()

    @mock.patch("apps.courses.tasks.resumer_cours", side_effect=RuntimeError("quota dépassé"))
    @mock.patch("apps.courses.tasks.extraire_texte_pdf", return_value="Texte PDF")
    def test_erreur_ia_donne_le_statut_erreur(self, mock_pdf, mock_resume):
        generer_resume_cours.delay(self.cours.pk)

        self.cours.refresh_from_db()
        self.assertEqual(self.cours.statut, Cours.Statut.ERREUR)
        self.assertEqual(self.cours.message_erreur, "Échec pendant la génération du résumé : quota dépassé")
        self.assertEqual(self.cours.progression, 33)
        self.assertFalse(Resume.objects.filter(cours=self.cours).exists())


class VisibiliteTests(BaseCoursTestCase):
    """Règles de visibilité selon la publication, le masquage et l'archivage."""

    def creer(self, titre, **champs):
        return Cours.objects.create(titre=titre, fichier=fichier_pdf(), type_fichier="pdf",
                                    enseignant=self.prof.enseignant, **champs)

    def setUp(self):
        super().setUp()
        maintenant = timezone.now()
        self.brouillon = self.creer("Brouillon")
        self.programme_futur = self.creer("Futur", publication="programme", date_publication=maintenant + timedelta(days=1))
        self.programme_passe = self.creer("Passé", publication="programme", date_publication=maintenant - timedelta(hours=1))
        self.masque = self.creer("Masqué", publication="publie", masque=True)
        self.archive = self.creer("Archivé", publication="publie", archive=True)

    def liste(self, user):
        self.client.force_login(user)
        return set(self.client.get(reverse("liste-cours")).context["liste_cours"])

    def test_etudiant_ne_voit_que_les_cours_publies(self):
        self.assertEqual(self.liste(self.etudiant), {self.cours, self.programme_passe})
        for cours in [self.brouillon, self.programme_futur, self.masque, self.archive]:
            self.assertEqual(self.client.get(reverse("detail-cours", args=[cours.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("detail-cours", args=[self.programme_passe.pk])).status_code, 200)

    def test_enseignant_voit_tous_ses_cours(self):
        tous = {self.cours, self.brouillon, self.programme_futur, self.programme_passe, self.masque, self.archive}
        self.assertEqual(self.liste(self.prof), tous)
        self.assertEqual(self.client.get(reverse("detail-cours", args=[self.brouillon.pk])).status_code, 200)

    def test_admin_voit_tout_y_compris_masques_et_archives(self):
        self.assertIn(self.masque, self.liste(self.admin))
        self.assertIn(self.archive, self.liste(self.admin))
        self.assertEqual(self.client.get(reverse("detail-cours", args=[self.archive.pk])).status_code, 200)

    def test_publication_programmee_exige_une_date(self):
        self.client.force_login(self.prof)
        reponse = self.client.post(reverse("ajouter-cours"), {
            "titre": "Sans date", "fichier": fichier_pdf(), "publication": "programme",
        })
        self.assertIn("date_publication", reponse.context["form"].errors)

    def test_prerequis_proposes(self):
        brouillon_autre = Cours.objects.create(titre="Brouillon d'un autre", fichier=fichier_pdf(), type_fichier="pdf",
                                               enseignant=self.autre_prof.enseignant)
        self.client.force_login(self.prof)
        choix = set(self.client.get(reverse("modifier-cours", args=[self.cours.pk])).context["form"].fields["prerequis"].queryset)
        self.assertNotIn(self.cours, choix)
        self.assertNotIn(brouillon_autre, choix)
        self.assertIn(self.brouillon, choix)
        self.assertIn(self.programme_passe, choix)


class EnseignantAmeliorationsTests(BaseCoursTestCase):
    def setUp(self):
        super().setUp()
        self.resume = Resume.objects.create(cours=self.cours, texte="Résumé en français")

    @mock.patch("apps.courses.tasks.resumer_cours", return_value="Fiche")
    @mock.patch("apps.courses.tasks.extraire_texte_pdf", return_value="un deux trois")
    def test_regenerer_avec_type_et_consigne(self, mock_pdf, mock_resume):
        TraductionResume.objects.create(resume=self.resume, langue="en", texte="Old summary")
        self.client.force_login(self.prof)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("generer-resume", args=[self.cours.pk]),
                             {"type_resume": "fiche", "consigne": "insiste sur les formules"})

        mock_resume.assert_called_once_with("un deux trois", type_resume="fiche", consigne="insiste sur les formules")
        self.resume.refresh_from_db()
        self.cours.refresh_from_db()
        self.assertEqual((self.resume.texte, self.resume.type_resume), ("Fiche", "fiche"))
        self.assertEqual(self.resume.consigne, "insiste sur les formules")
        self.assertEqual(self.cours.nb_mots, 3)
        self.assertFalse(self.resume.traductions.exists(), "les traductions de l'ancien résumé doivent être effacées")

    def test_type_de_resume_invalide(self):
        self.client.force_login(self.prof)
        self.client.post(reverse("generer-resume", args=[self.cours.pk]), {"type_resume": "roman"})
        self.cours.refresh_from_db()
        self.assertEqual(self.cours.statut, Cours.Statut.EN_ATTENTE)

    @mock.patch("apps.courses.views.traduire_texte", return_value="ملخص الدرس")
    def test_traduction_arabe_stockee_et_affichee_rtl(self, mock_traduire):
        self.client.force_login(self.prof)
        self.client.post(reverse("traduire-resume", args=[self.cours.pk]), {"langue": "ar"})
        mock_traduire.assert_called_once_with("Résumé en français", "ar")
        self.assertEqual(self.resume.traductions.get(langue="ar").texte, "ملخص الدرس")

        # L'étudiant voit la traduction, affichée de droite à gauche, sans nouvel appel à Gemini
        self.client.force_login(self.etudiant)
        reponse = self.client.get(reverse("detail-cours", args=[self.cours.pk]))
        self.assertContains(reponse, 'lang="ar" dir="rtl"')
        self.assertContains(reponse, "ملخص الدرس")
        mock_traduire.assert_called_once()

    @mock.patch("apps.courses.views.traduire_texte", side_effect=RuntimeError("quota"))
    def test_echec_de_traduction(self, mock_traduire):
        self.client.force_login(self.prof)
        reponse = self.client.post(reverse("traduire-resume", args=[self.cours.pk]), {"langue": "en"}, follow=True)
        self.assertContains(reponse, "La traduction a échoué")
        self.assertFalse(self.resume.traductions.exists())

    @mock.patch("apps.courses.views.traduire_texte", return_value="Summary")
    def test_traduction_reservee_au_proprietaire(self, mock_traduire):
        for user, code in [(self.etudiant, 403), (self.admin, 403), (self.autre_prof, 404)]:
            self.client.force_login(user)
            self.assertEqual(self.client.post(reverse("traduire-resume", args=[self.cours.pk]), {"langue": "en"}).status_code, code)
        mock_traduire.assert_not_called()

    def test_statistiques_de_ses_cours(self):
        autre = Cours.objects.create(titre="Autre", fichier=fichier_pdf(), type_fichier="pdf", enseignant=self.autre_prof.enseignant)
        etudiant2 = creer_utilisateur("etudiant2", Utilisateur.Role.ETUDIANT)
        for _ in range(3):
            Consultation.objects.create(utilisateur=self.etudiant, cours=self.cours)
        Telechargement.objects.create(utilisateur=self.etudiant, cours=self.cours, format="pdf")
        Avis.objects.create(utilisateur=self.etudiant, cours=self.cours, note=5)
        Avis.objects.create(utilisateur=etudiant2, cours=self.cours, note=2)
        Consultation.objects.create(utilisateur=self.etudiant, cours=autre)

        self.client.force_login(self.prof)
        reponse = self.client.get(reverse("statistiques-cours"))
        [stats] = reponse.context["liste_cours"]
        self.assertEqual(stats, self.cours)
        self.assertEqual((stats.nb_consultations, stats.nb_telechargements, stats.nb_avis), (3, 1, 2))
        self.assertEqual(stats.note_moyenne, 3.5)
        self.assertEqual(reponse.context["totaux"]["consultations"], 3)

    def test_statistiques_et_avis_reserves_aux_enseignants(self):
        for user in [self.etudiant, self.admin]:
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse("statistiques-cours")).status_code, 403)
            self.assertEqual(self.client.get(reverse("avis-recus")).status_code, 403)

    def test_repondre_a_un_avis(self):
        avis = Avis.objects.create(utilisateur=self.etudiant, cours=self.cours, note=4, commentaire="Clair")
        self.client.force_login(self.prof)
        self.assertContains(self.client.get(reverse("avis-recus")), "Clair")

        reponse = self.client.post(reverse("repondre-avis", args=[avis.pk]),
                                   {"reponse": "Merci !", "suivant": "https://site-malveillant.example/"})
        self.assertRedirects(reponse, reverse("avis-recus"))
        avis.refresh_from_db()
        self.assertEqual(avis.reponse, "Merci !")
        self.assertIsNotNone(avis.date_reponse)
        self.assertNotContains(self.client.get(reverse("avis-recus") + "?filtre=sans_reponse"), "Clair")

    def test_seul_le_proprietaire_repond(self):
        avis = Avis.objects.create(utilisateur=self.etudiant, cours=self.cours, note=4)
        self.client.force_login(self.autre_prof)
        self.assertEqual(self.client.post(reverse("repondre-avis", args=[avis.pk]), {"reponse": "Non"}).status_code, 404)
        self.client.force_login(self.etudiant)
        self.assertEqual(self.client.post(reverse("repondre-avis", args=[avis.pk]), {"reponse": "Moi"}).status_code, 403)
        avis.refresh_from_db()
        self.assertEqual(avis.reponse, "")

    @mock.patch("apps.courses.views.duree_audio", return_value=125)
    def test_journal_et_duree_audio(self, mock_duree):
        self.client.force_login(self.prof)
        self.client.post(reverse("ajouter-cours"), {
            "titre": "Podcast", "publication": "publie",
            "fichier": SimpleUploadedFile("seance.mp3", b"ID3 audio", content_type="audio/mpeg"),
        })
        podcast = Cours.objects.get(titre="Podcast")
        self.assertEqual(podcast.duree_audio, 125)
        self.assertEqual(podcast.minutes_estimees, 3)

        self.client.post(reverse("modifier-cours", args=[podcast.pk]), {"titre": "Podcast v2", "publication": "publie"})
        self.client.post(reverse("supprimer-cours", args=[podcast.pk]))

        actions = list(JournalActivite.objects.filter(titre_cours__startswith="Podcast")
                       .order_by("date", "pk").values_list("action", "utilisateur", "cours"))
        self.assertEqual([a for a, _, _ in actions], ["creation", "modification", "suppression"])
        self.assertTrue(all(u == self.prof.pk for _, u, _ in actions))
        self.assertTrue(all(c is None for _, _, c in actions), "le lien vers le cours supprimé passe à NULL")


class EtudiantAmeliorationsTests(BaseCoursTestCase):
    def setUp(self):
        super().setUp()
        self.maths = Matiere.objects.create(nom="Mathématiques")
        self.physique = Matiere.objects.create(nom="Physique")
        self.cours.matiere = self.maths
        self.cours.niveau = Cours.Niveau.DEBUTANT
        self.cours.mots_cles = "matrices, vecteurs"
        self.cours.save()
        Transcription.objects.create(cours=self.cours, texte="Le déterminant d'une matrice carrée")
        self.resume = Resume.objects.create(cours=self.cours, texte="Résumé des matrices.")
        self.optique = self.creer("Optique", self.autre_prof, matiere=self.physique, niveau=Cours.Niveau.AVANCE)

    def creer(self, titre, prof=None, publication=Cours.Publication.PUBLIE, **champs):
        prof = prof or self.prof
        return Cours.objects.create(titre=titre, fichier=fichier_pdf(), type_fichier="pdf",
                                    enseignant=prof.enseignant, publication=publication, **champs)

    def liste(self, **parametres):
        self.client.force_login(self.etudiant)
        return list(self.client.get(reverse("liste-cours"), parametres).context["liste_cours"])

    # --- Recherche et filtres ---
    def test_recherche_dans_la_transcription(self):
        self.assertEqual(self.liste(q="déterminant"), [self.cours])
        self.assertEqual(self.liste(q="vecteurs"), [self.cours])
        self.assertEqual(self.liste(q="introuvable"), [])

    def test_filtres_matiere_niveau_enseignant(self):
        self.assertEqual(self.liste(matiere=self.physique.pk), [self.optique])
        self.assertEqual(self.liste(niveau="debutant"), [self.cours])
        self.assertEqual(self.liste(enseignant=self.autre_prof.enseignant.pk), [self.optique])
        self.assertEqual(len(self.liste(matiere="pas-un-nombre", niveau="inconnu")), 2)

    def test_la_recherche_respecte_la_visibilite(self):
        self.creer("Matrices avancées (brouillon)", publication=Cours.Publication.BROUILLON, mots_cles="matrices")
        self.assertEqual(self.liste(q="matrices"), [self.cours])

    # --- Consultations, historique, temps estimé ---
    def test_consultation_comptee_pour_les_etudiants_seulement(self):
        for user in [self.etudiant, self.etudiant, self.prof, self.admin]:
            self.client.force_login(user)
            self.client.get(reverse("detail-cours", args=[self.cours.pk]))
        self.assertEqual(Consultation.objects.filter(cours=self.cours).count(), 2)

    def test_historique_recent_sans_doublon(self):
        self.client.force_login(self.etudiant)
        for cours in [self.cours, self.optique, self.cours]:
            self.client.get(reverse("detail-cours", args=[cours.pk]))
        historique = self.client.get(reverse("historique-cours")).context["historique"]
        self.assertEqual([ligne["cours"] for ligne in historique], [self.cours, self.optique])

        self.optique.mettre_a_jour(masque=True)
        historique = self.client.get(reverse("historique-cours")).context["historique"]
        self.assertEqual([ligne["cours"] for ligne in historique], [self.cours])

    def test_temps_estime_affiche(self):
        self.cours.mettre_a_jour(nb_mots=450)
        self.client.force_login(self.etudiant)
        self.assertContains(self.client.get(reverse("detail-cours", args=[self.cours.pk])), "3 min de lecture")
        self.assertContains(self.client.get(reverse("liste-cours")), "3 min")

    # --- Prérequis et recommandations ---
    def test_prerequis_visibles_et_informatifs(self):
        base = self.creer("Bases d'algèbre")
        brouillon = self.creer("Prérequis en brouillon", publication=Cours.Publication.BROUILLON)
        self.cours.prerequis.set([base, brouillon])

        self.client.force_login(self.etudiant)
        reponse = self.client.get(reverse("detail-cours", args=[self.cours.pk]))
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(list(reponse.context["prerequis"]), [base])
        self.assertContains(reponse, "À suivre avant ce cours")

    def test_cours_similaires(self):
        meme_matiere = self.creer("Analyse", matiere=self.maths)
        meme_mot_cle = self.creer("Géométrie", mots_cles="Vecteurs, plans")
        les_deux = self.creer("Algèbre 2", matiere=self.maths, mots_cles="matrices")
        self.creer("Masqué", matiere=self.maths, masque=True)

        self.client.force_login(self.etudiant)
        similaires = self.client.get(reverse("detail-cours", args=[self.cours.pk])).context["similaires"]
        self.assertEqual(similaires[0], les_deux)
        self.assertEqual(set(similaires), {les_deux, meme_matiere, meme_mot_cle})

    # --- Favoris ---
    def test_favoris(self):
        self.client.force_login(self.etudiant)
        self.client.post(reverse("basculer-favori", args=[self.cours.pk]))
        self.assertEqual([f.cours for f in self.client.get(reverse("mes-favoris")).context["favoris"]], [self.cours])

        self.client.post(reverse("basculer-favori", args=[self.cours.pk]))
        self.assertFalse(Favori.objects.filter(utilisateur=self.etudiant).exists())

    def test_favoris_reserves_aux_etudiants_et_cours_visibles(self):
        for user in [self.prof, self.admin]:
            self.client.force_login(user)
            self.assertEqual(self.client.post(reverse("basculer-favori", args=[self.cours.pk])).status_code, 403)
            self.assertEqual(self.client.get(reverse("mes-favoris")).status_code, 403)
        brouillon = self.creer("Brouillon", publication=Cours.Publication.BROUILLON)
        self.client.force_login(self.etudiant)
        self.assertEqual(self.client.post(reverse("basculer-favori", args=[brouillon.pk])).status_code, 404)

    # --- Avis ---
    def test_un_seul_avis_par_etudiant_modifiable(self):
        self.client.force_login(self.etudiant)
        self.client.post(reverse("enregistrer-avis", args=[self.cours.pk]), {"note": 3, "commentaire": "Bien"})
        self.client.post(reverse("enregistrer-avis", args=[self.cours.pk]), {"note": 5, "commentaire": "Très bien"})

        avis = Avis.objects.get(cours=self.cours)
        self.assertEqual((avis.utilisateur, avis.note, avis.commentaire), (self.etudiant, 5, "Très bien"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Avis.objects.create(utilisateur=self.etudiant, cours=self.cours, note=1)

    def test_note_hors_limites_refusee(self):
        self.client.force_login(self.etudiant)
        self.client.post(reverse("enregistrer-avis", args=[self.cours.pk]), {"note": 6})
        self.assertFalse(Avis.objects.exists())

    def test_note_moyenne_affichee(self):
        etudiant2 = creer_utilisateur("etudiant2", Utilisateur.Role.ETUDIANT)
        Avis.objects.create(utilisateur=self.etudiant, cours=self.cours, note=4)
        Avis.objects.create(utilisateur=etudiant2, cours=self.cours, note=5, reponse="Merci")
        self.client.force_login(self.etudiant)
        reponse = self.client.get(reverse("detail-cours", args=[self.cours.pk]))
        self.assertEqual(reponse.context["note_moyenne"], 4.5)
        self.assertContains(reponse, "4,5/5")
        self.assertContains(reponse, "Réponse de l'enseignant")
        self.assertEqual(self.liste(q="matrices")[0].note_moyenne, 4.5)

    def test_avis_reserves_aux_etudiants(self):
        for user in [self.prof, self.admin]:
            self.client.force_login(user)
            self.assertEqual(self.client.post(reverse("enregistrer-avis", args=[self.cours.pk]), {"note": 5}).status_code, 403)
        self.assertFalse(Avis.objects.exists())

    # --- Notes personnelles ---
    def test_notes_personnelles_privees(self):
        self.client.force_login(self.etudiant)
        self.client.post(reverse("enregistrer-note", args=[self.cours.pk]), {"texte": "Revoir le chapitre 2"})
        self.assertContains(self.client.get(reverse("detail-cours", args=[self.cours.pk])), "Revoir le chapitre 2")

        for user in [creer_utilisateur("etudiant2", Utilisateur.Role.ETUDIANT), self.prof, self.admin]:
            self.client.force_login(user)
            self.assertNotContains(self.client.get(reverse("detail-cours", args=[self.cours.pk])), "Revoir le chapitre 2")

        self.client.force_login(self.etudiant)
        self.client.post(reverse("enregistrer-note", args=[self.cours.pk]), {"texte": "  "})
        self.assertFalse(NotePersonnelle.objects.exists())

    # --- Téléchargements ---
    def test_export_pdf(self):
        self.client.force_login(self.etudiant)
        reponse = self.client.get(reverse("telecharger-resume-pdf", args=[self.cours.pk]))
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse["Content-Type"], "application/pdf")
        self.assertIn('filename="resume-algebre-lineaire.pdf"', reponse["Content-Disposition"])
        self.assertTrue(reponse.content.startswith(b"%PDF"))

    def test_pdf_sans_resume_et_cours_invisible(self):
        self.client.force_login(self.etudiant)
        self.assertEqual(self.client.get(reverse("telecharger-resume-pdf", args=[self.optique.pk])).status_code, 404)
        brouillon = self.creer("Brouillon", publication=Cours.Publication.BROUILLON)
        Resume.objects.create(cours=brouillon, texte="Secret")
        self.assertEqual(self.client.get(reverse("telecharger-resume-pdf", args=[brouillon.pk])).status_code, 404)

    def test_telechargements_comptes_pour_les_etudiants(self):
        self.client.force_login(self.etudiant)
        self.client.get(reverse("telecharger-resume", args=[self.cours.pk]))
        self.client.get(reverse("telecharger-resume-pdf", args=[self.cours.pk]))
        self.client.force_login(self.prof)
        self.client.get(reverse("telecharger-resume-pdf", args=[self.cours.pk]))
        self.assertEqual(sorted(Telechargement.objects.values_list("format", flat=True)), ["pdf", "txt"])

    # --- Écoute et partage ---
    def test_mode_ecoute_et_partage(self):
        self.client.force_login(self.etudiant)
        reponse = self.client.get(reverse("detail-cours", args=[self.cours.pk]))
        self.assertContains(reponse, 'id="btnLire"')
        self.assertContains(reponse, "speechSynthesis")
        self.assertContains(reponse, f"http://testserver{reverse('detail-cours', args=[self.cours.pk])}")
        self.assertContains(reponse, "data:image/png;base64,")

    # --- Signalement ---
    def test_signaler_une_seule_fois_tant_que_non_traite(self):
        self.client.force_login(self.etudiant)
        self.client.post(reverse("signaler-cours", args=[self.cours.pk]), {"motif": "errone", "message": "Formule fausse"})
        self.client.post(reverse("signaler-cours", args=[self.cours.pk]), {"motif": "autre"})
        signalement = Signalement.objects.get()
        self.assertEqual((signalement.auteur, signalement.motif, signalement.statut), (self.etudiant, "errone", "nouveau"))

        signalement.statut = Signalement.Statut.TRAITE
        signalement.save()
        self.client.post(reverse("signaler-cours", args=[self.cours.pk]), {"motif": "autre"})
        self.assertEqual(Signalement.objects.count(), 2)

    def test_signalement_motif_obligatoire_et_reserve_aux_etudiants(self):
        self.client.force_login(self.etudiant)
        self.client.post(reverse("signaler-cours", args=[self.cours.pk]), {"message": "Sans motif"})
        self.assertFalse(Signalement.objects.exists())
        self.client.force_login(self.prof)
        self.assertEqual(self.client.post(reverse("signaler-cours", args=[self.cours.pk]), {"motif": "autre"}).status_code, 403)


class AdministrateurAmeliorationsTests(BaseCoursTestCase):
    def creer(self, titre, prof=None, **champs):
        prof = prof or self.prof
        champs.setdefault("publication", Cours.Publication.PUBLIE)
        return Cours.objects.create(titre=titre, fichier=fichier_pdf(), type_fichier="pdf", enseignant=prof.enseignant, **champs)

    def refuse_aux_non_admins(self, methode, url, donnees=None):
        for user in [self.etudiant, self.prof]:
            self.client.force_login(user)
            self.assertEqual(getattr(self.client, methode)(url, donnees or {}).status_code, 403, f"{url} pour {user}")

    # --- Masquage ---
    def test_masquer_puis_reafficher(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("masquer-cours", args=[self.cours.pk]))
        self.cours.refresh_from_db()
        self.assertTrue(self.cours.masque)

        self.client.force_login(self.etudiant)
        self.assertNotIn(self.cours, self.client.get(reverse("liste-cours")).context["liste_cours"])
        self.client.force_login(self.prof)
        self.assertEqual(self.client.get(reverse("detail-cours", args=[self.cours.pk])).status_code, 200)

        self.client.force_login(self.admin)
        self.client.post(reverse("masquer-cours", args=[self.cours.pk]))
        self.cours.refresh_from_db()
        self.assertFalse(self.cours.masque)
        self.assertEqual(list(JournalActivite.objects.order_by("pk").values_list("action", flat=True)), ["masquage", "reaffichage"])

    def test_moderation_reservee_a_l_admin(self):
        self.refuse_aux_non_admins("post", reverse("masquer-cours", args=[self.cours.pk]))
        self.refuse_aux_non_admins("post", reverse("archiver-cours", args=[self.cours.pk]))
        self.cours.refresh_from_db()
        self.assertFalse(self.cours.masque or self.cours.archive)

    def test_boutons_de_moderation_sur_la_page_du_cours(self):
        self.client.force_login(self.admin)
        reponse = self.client.get(reverse("detail-cours", args=[self.cours.pk]))
        self.assertContains(reponse, reverse("masquer-cours", args=[self.cours.pk]))
        self.assertNotContains(reponse, reverse("modifier-cours", args=[self.cours.pk]))
        self.client.force_login(self.prof)
        self.assertNotContains(self.client.get(reverse("detail-cours", args=[self.cours.pk])), reverse("masquer-cours", args=[self.cours.pk]))

    # --- Archivage ---
    def test_archiver_et_desarchiver_un_cours(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("archiver-cours", args=[self.cours.pk]))
        self.cours.refresh_from_db()
        self.assertTrue(self.cours.archive)
        self.assertIsNotNone(self.cours.date_archivage)
        self.assertIn(self.cours, self.client.get(reverse("archivage-cours")).context["archives"])

        self.client.post(reverse("archiver-cours", args=[self.cours.pk]))
        self.cours.refresh_from_db()
        self.assertFalse(self.cours.archive)
        self.assertIsNone(self.cours.date_archivage)
        self.assertTrue(Cours.objects.filter(pk=self.cours.pk).exists(), "l'archivage ne supprime jamais un cours")

    def test_archiver_une_annee_universitaire_passee(self):
        def creer_le(titre, annee, mois, jour):
            cours = self.creer(titre)
            Cours.objects.filter(pk=cours.pk).update(date_creation=timezone.make_aware(datetime(annee, mois, jour, 12)))
            return cours

        annee_courante = annee_universitaire(timezone.now())
        debut_ancienne = creer_le("Rentrée", annee_courante - 2, 9, 15)
        fin_ancienne = creer_le("Printemps", annee_courante - 1, 3, 1)
        annee_precedente = creer_le("Année dernière", annee_courante - 1, 10, 1)

        self.client.force_login(self.admin)
        annees = [ligne["annee"] for ligne in self.client.get(reverse("archivage-cours")).context["annees"]]
        self.assertEqual(annees, [annee_courante - 1, annee_courante - 2])

        self.client.post(reverse("archivage-cours"), {"annee": annee_courante - 2})
        archives = set(Cours.objects.filter(archive=True))
        self.assertEqual(archives, {debut_ancienne, fin_ancienne})
        self.assertNotIn(annee_precedente, archives)
        self.assertEqual(JournalActivite.objects.filter(action="archivage").count(), 2)

        # L'année en cours (ou une valeur invalide) ne peut pas être archivée
        for annee in [annee_courante, "abc"]:
            self.client.post(reverse("archivage-cours"), {"annee": annee})
        self.assertFalse(Cours.objects.get(pk=self.cours.pk).archive)
        self.refuse_aux_non_admins("get", reverse("archivage-cours"))

    # --- Signalements ---
    def test_traitement_des_signalements(self):
        signalement = Signalement.objects.create(auteur=self.etudiant, cours=self.cours, motif="errone", message="Erreur p. 3")
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("signalements")), "Erreur p. 3")

        self.client.post(reverse("traiter-signalement", args=[signalement.pk]), {"statut": "traite"})
        signalement.refresh_from_db()
        self.assertEqual((signalement.statut, signalement.traite_par), ("traite", self.admin))
        self.assertIsNotNone(signalement.date_traitement)
        self.assertNotContains(self.client.get(reverse("signalements")), "Erreur p. 3")
        self.assertContains(self.client.get(reverse("signalements") + "?statut=traite"), "Erreur p. 3")

        self.client.post(reverse("traiter-signalement", args=[signalement.pk]), {"statut": "nouveau"})
        signalement.refresh_from_db()
        self.assertEqual((signalement.statut, signalement.traite_par, signalement.date_traitement), ("nouveau", None, None))

        self.client.post(reverse("traiter-signalement", args=[signalement.pk]), {"statut": "supprime"})
        signalement.refresh_from_db()
        self.assertEqual(signalement.statut, "nouveau")

        self.refuse_aux_non_admins("get", reverse("signalements"))
        self.refuse_aux_non_admins("post", reverse("traiter-signalement", args=[signalement.pk]), {"statut": "rejete"})

    # --- Journal ---
    def test_journal_d_activite_filtrable(self):
        JournalActivite.enregistrer(self.prof, JournalActivite.Action.CREATION, self.cours)
        JournalActivite.enregistrer(self.admin, JournalActivite.Action.MASQUAGE, self.cours)
        self.client.force_login(self.admin)
        self.assertEqual(len(self.client.get(reverse("journal-activite")).context["activites"]), 2)
        filtre = self.client.get(reverse("journal-activite"), {"action": "masquage"}).context["activites"]
        self.assertEqual([a.utilisateur for a in filtre], [self.admin])
        self.refuse_aux_non_admins("get", reverse("journal-activite"))

    # --- Classement et export CSV ---
    def remplir_statistiques(self):
        populaire = self.creer("Populaire", matiere=Matiere.objects.create(nom="Physique"))
        etudiants = [self.etudiant] + [creer_utilisateur(f"etu{i}", Utilisateur.Role.ETUDIANT) for i in range(3)]
        for etudiant in etudiants:
            Consultation.objects.create(utilisateur=etudiant, cours=populaire)
            Avis.objects.create(utilisateur=etudiant, cours=populaire, note=4 if etudiant != self.etudiant else 5)
        Consultation.objects.create(utilisateur=self.etudiant, cours=self.cours)
        Telechargement.objects.create(utilisateur=self.etudiant, cours=populaire, format="pdf")
        Avis.objects.create(utilisateur=self.etudiant, cours=self.cours, note=5)  # 1 seul avis : non classé
        Signalement.objects.create(auteur=self.etudiant, cours=self.cours, motif="autre")
        return populaire

    def test_classement(self):
        populaire = self.remplir_statistiques()
        self.client.force_login(self.admin)
        context = self.client.get(reverse("classement-cours")).context
        self.assertEqual(list(context["plus_consultes"]), [populaire, self.cours])
        self.assertEqual(list(context["mieux_notes"]), [populaire])
        self.assertEqual(context["mieux_notes"][0].note_moyenne, 4.25)
        self.assertEqual(list(context["plus_signales"]), [self.cours])
        self.refuse_aux_non_admins("get", reverse("classement-cours"))

    def test_export_csv(self):
        populaire = self.remplir_statistiques()
        self.client.force_login(self.admin)
        reponse = self.client.get(reverse("export-cours-csv"))
        self.assertEqual(reponse["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment;", reponse["Content-Disposition"])

        contenu = reponse.content.decode("utf-8")
        self.assertTrue(contenu.startswith("﻿"), "BOM UTF-8 pour Excel")
        lignes = list(csv.reader(io.StringIO(contenu.lstrip("﻿")), delimiter=";"))
        self.assertEqual(lignes[0][:2], ["ID", "Titre"])
        self.assertEqual(len(lignes), 3)
        ligne = next(l for l in lignes if l[1] == "Populaire")
        colonnes = dict(zip(lignes[0], ligne))
        self.assertEqual(colonnes["Matière"], "Physique")
        self.assertEqual((colonnes["Consultations"], colonnes["Téléchargements"]), ("4", "1"))
        self.assertEqual((colonnes["Note moyenne"], colonnes["Nombre d'avis"], colonnes["Signalements"]), ("4,25", "4", "0"))
        self.assertEqual(next(l for l in lignes if l[1] == self.cours.titre)[-1], "1")
        self.refuse_aux_non_admins("get", reverse("export-cours-csv"))

    # --- Matières ---
    def test_gestion_des_matieres(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("matieres"), {"nom": "Chimie"})
        chimie = Matiere.objects.get(nom="Chimie")
        reponse = self.client.post(reverse("matieres"), {"nom": "Chimie"})
        self.assertEqual(Matiere.objects.filter(nom="Chimie").count(), 1)
        self.assertTrue(reponse.context["form"].errors)

        self.client.post(reverse("modifier-matiere", args=[chimie.pk]), {"nom": "Chimie organique"})
        chimie.refresh_from_db()
        self.assertEqual(chimie.nom, "Chimie organique")

        self.cours.matiere = chimie
        self.cours.save()
        self.client.post(reverse("supprimer-matiere", args=[chimie.pk]))
        self.cours.refresh_from_db()
        self.assertIsNone(self.cours.matiere)
        self.assertTrue(Cours.objects.filter(pk=self.cours.pk).exists())

        self.refuse_aux_non_admins("get", reverse("matieres"))
        self.refuse_aux_non_admins("post", reverse("matieres"), {"nom": "Piratage"})
        self.assertFalse(Matiere.objects.filter(nom="Piratage").exists())


class NavigationTests(BaseCoursTestCase):
    """Liens du menu latéral selon le rôle."""

    LIENS = {
        "enseignant": ["statistiques-cours", "avis-recus"],
        "etudiant": ["mes-favoris", "historique-cours"],
        "admin": ["signalements", "classement-cours", "journal-activite", "archivage-cours", "matieres"],
    }

    def menu(self, user, url=None):
        self.client.force_login(user)
        reponse = self.client.get(url or reverse("index"))
        self.assertEqual(reponse.status_code, 200)
        return reponse.content.decode()

    def verifier_liens(self, page, role):
        for autre_role, noms in self.LIENS.items():
            for nom in noms:
                lien = f'href="{reverse(nom)}"'
                if autre_role == role:
                    self.assertIn(lien, page, f"{nom} doit apparaître pour {role}")
                else:
                    self.assertNotIn(lien, page, f"{nom} ne doit pas apparaître pour {role}")

    def test_menu_par_role(self):
        self.verifier_liens(self.menu(self.prof), "enseignant")
        self.assertIn("Mes cours", self.menu(self.prof))
        self.verifier_liens(self.menu(self.etudiant), "etudiant")
        for admin in [self.admin, self.superuser]:
            self.verifier_liens(self.menu(admin), "admin")

    def test_badge_des_nouveaux_signalements(self):
        Signalement.objects.create(auteur=self.etudiant, cours=self.cours, motif="autre")
        Signalement.objects.create(auteur=self.etudiant, cours=self.cours, motif="errone", statut="traite")
        self.assertIn('<div class="badge rounded-pill bg-danger ms-auto">1</div>', self.menu(self.admin))

    def test_entree_active(self):
        page = self.menu(self.prof, reverse("statistiques-cours"))
        self.assertRegex(page, r'<li class="menu-item active">\s*<a href="%s"' % reverse("statistiques-cours"))
        page = self.menu(self.prof, reverse("detail-cours", args=[self.cours.pk]))
        self.assertRegex(page, r'<li class="menu-item active">\s*<a href="%s"' % reverse("liste-cours"))


class ServicesIATests(TestCase):
    """Services du dossier ai_services/, sans appel réseau ni modèle réel."""

    @mock.patch("ai_services.pdf.pdfplumber.open")
    def test_extraire_texte_pdf(self, mock_open):
        pages = [mock.Mock(**{"extract_text.return_value": "Page 1"}),
                 mock.Mock(**{"extract_text.return_value": None})]
        mock_open.return_value.__enter__.return_value.pages = pages
        self.assertEqual(extraire_texte_pdf("cours.pdf"), "Page 1")

    @mock.patch("ai_services.pdf.pdfplumber.open")
    def test_pdf_sans_texte(self, mock_open):
        mock_open.return_value.__enter__.return_value.pages = []
        with self.assertRaises(ValueError):
            extraire_texte_pdf("scan.pdf")

    @mock.patch("ai_services.transcription._pipeline_whisper")
    def test_transcrire(self, mock_pipeline):
        mock_pipeline.return_value.return_value = {"text": "  Bonjour à tous  "}
        self.assertEqual(transcrire("cours.mp3"), "Bonjour à tous")

    @mock.patch.dict("os.environ", {"GEMINI_API_KEY": ""})
    def test_gemini_sans_cle(self):
        with self.assertRaisesMessage(RuntimeError, "GEMINI_API_KEY"):
            appeler_gemini("Bonjour")

    @mock.patch.dict("os.environ", {"GEMINI_API_KEY": "cle-de-test", "GEMINI_MODEL": "modele-test"})
    @mock.patch("google.genai.Client")
    def test_gemini_avec_cle(self, mock_client):
        mock_client.return_value.models.generate_content.return_value.text = " Réponse "
        self.assertEqual(appeler_gemini("Bonjour"), "Réponse")
        mock_client.assert_called_once_with(api_key="cle-de-test")
        mock_client.return_value.models.generate_content.assert_called_once_with(model="modele-test", contents="Bonjour")

    @mock.patch("ai_services.resume.appeler_gemini", return_value="Résumé")
    def test_resumer_cours_construit_le_prompt(self, mock_gemini):
        self.assertEqual(resumer_cours("Les vecteurs"), "Résumé")
        prompt = mock_gemini.call_args.args[0]
        self.assertIn("Les vecteurs", prompt)
        self.assertIn("résumé", prompt)

    @mock.patch("ai_services.resume.appeler_gemini", return_value="Résumé")
    def test_prompt_selon_le_type_et_la_consigne(self, mock_gemini):
        attendus = {
            "court": "un seul paragraphe",
            "detaille": "résumé détaillé",
            "points_cles": "uniquement les points clés",
            "fiche": "fiche de révision",
        }
        for type_resume, extrait in attendus.items():
            resumer_cours("Texte", type_resume=type_resume, consigne="insiste sur les formules")
            prompt = mock_gemini.call_args.args[0]
            self.assertIn(extrait, prompt)
            self.assertIn("Consigne supplémentaire de l'enseignant : insiste sur les formules", prompt)

        resumer_cours("Texte", type_resume="inconnu")
        prompt = mock_gemini.call_args.args[0]
        self.assertIn("résumé détaillé", prompt)
        self.assertNotIn("Consigne supplémentaire", prompt)

    @mock.patch("ai_services.traduction.appeler_gemini", return_value="Summary")
    def test_traduire_texte(self, mock_gemini):
        self.assertEqual(traduire_texte("Résumé", "en"), "Summary")
        prompt = mock_gemini.call_args.args[0]
        self.assertIn("anglais", prompt)
        self.assertIn("Résumé", prompt)
        traduire_texte("Résumé", "ar")
        self.assertIn("arabe", mock_gemini.call_args.args[0])
        with self.assertRaises(ValueError):
            traduire_texte("Résumé", "de")

    @mock.patch("ai_services.transcription.subprocess.run")
    def test_duree_audio(self, mock_run):
        mock_run.return_value.stdout = "125.6\n"
        self.assertEqual(duree_audio("cours.mp3"), 126)
        self.assertEqual(mock_run.call_args.args[0][0], "ffprobe")

        mock_run.side_effect = FileNotFoundError("ffprobe introuvable")
        self.assertIsNone(duree_audio("cours.mp3"))
        mock_run.side_effect = None
        mock_run.return_value.stdout = "N/A"
        self.assertIsNone(duree_audio("cours.mp3"))


class UtilitairesTests(TestCase):
    """Fonctions utilitaires du module, sans base de données réelle de cours."""

    def test_annee_universitaire(self):
        self.assertEqual(annee_universitaire(datetime(2025, 8, 31)), 2024)
        self.assertEqual(annee_universitaire(datetime(2025, 9, 1)), 2025)
        self.assertEqual(annee_universitaire(datetime(2026, 1, 15)), 2025)
        debut, fin = bornes_annee_universitaire(2025)
        self.assertEqual((debut.year, debut.month, debut.day), (2025, 9, 1))
        self.assertEqual((fin.year, fin.month, fin.day), (2026, 9, 1))
        self.assertTrue(timezone.is_aware(debut))

    def test_temps_estime_et_mots_cles(self):
        cours = Cours(type_fichier=Cours.TypeFichier.PDF, mots_cles=" algèbre, ,matrices ")
        self.assertIsNone(cours.minutes_estimees)
        cours.nb_mots = 201
        self.assertEqual(cours.minutes_estimees, 2)
        self.assertEqual(cours.liste_mots_cles, ["algèbre", "matrices"])

        audio = Cours(type_fichier=Cours.TypeFichier.AUDIO, duree_audio=30, nb_mots=5000)
        self.assertEqual(audio.minutes_estimees, 1, "pour l'audio, on se fie à la durée d'écoute")

    def test_formulaire_normalise_les_mots_cles(self):
        formulaire = CoursForm()
        formulaire.cleaned_data = {"mots_cles": " a , b,,c "}
        self.assertEqual(formulaire.clean_mots_cles(), "a, b, c")

    def test_pas_de_recommandation_sans_matiere_ni_mot_cle(self):
        self.assertEqual(cours_similaires(Cours(pk=1), Cours.objects.all()), [])


class PublicationProgrammeeTests(BaseCoursTestCase):
    def test_le_cours_devient_visible_quand_la_date_est_passee(self):
        maintenant = timezone.now()
        self.cours.mettre_a_jour(publication=Cours.Publication.PROGRAMME, date_publication=maintenant + timedelta(hours=1))
        self.client.force_login(self.etudiant)
        self.assertEqual(self.client.get(reverse("detail-cours", args=[self.cours.pk])).status_code, 404)

        # Aucune tâche planifiée : il suffit que l'heure courante dépasse la date
        with mock.patch("django.utils.timezone.now", return_value=maintenant + timedelta(hours=2)):
            self.assertEqual(self.client.get(reverse("detail-cours", args=[self.cours.pk])).status_code, 200)
            self.assertIn(self.cours, self.client.get(reverse("liste-cours")).context["liste_cours"])


class AdminDjangoTests(BaseCoursTestCase):
    def test_nouveaux_modeles_dans_l_admin_django(self):
        self.client.force_login(self.superuser)
        for modele in ["matiere", "cours", "resume", "consultation", "telechargement", "favori",
                       "avis", "notepersonnelle", "signalement", "journalactivite"]:
            reponse = self.client.get(reverse(f"admin:courses_{modele}_changelist"))
            self.assertEqual(reponse.status_code, 200, modele)
        self.assertEqual(self.client.get(reverse("admin:courses_cours_change", args=[self.cours.pk])).status_code, 200)
