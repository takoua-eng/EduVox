import json
import shutil
import tempfile
from decimal import Decimal
from types import SimpleNamespace

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apps.accounts.models import Enseignant, Etudiant, Utilisateur
from apps.courses.models import Cours, Resume, Transcription
from apps.quizzes.models import Choix, Question, Quiz

MEDIA_TEST = tempfile.mkdtemp()
TEXTE_COURS = ("Une matrice est un tableau de nombres. Le déterminant d'une matrice carrée indique si elle est "
               "inversible : une matrice est inversible si et seulement si son déterminant est non nul. ") * 5


def creer_utilisateur(username, role, **extra):
    user = Utilisateur.objects.create_user(
        username=username, email=f"{username}@eduvox.test", password="motdepasse-test", role=role, **extra
    )
    if role == Utilisateur.Role.ENSEIGNANT:
        Enseignant.objects.create(utilisateur=user)
    elif role == Utilisateur.Role.ETUDIANT:
        Etudiant.objects.create(utilisateur=user)
    return user


def creer_cours(enseignant, titre="Algèbre linéaire", publie=True, texte=TEXTE_COURS):
    cours = Cours.objects.create(
        titre=titre, fichier=SimpleUploadedFile("cours.pdf", b"%PDF-1.4 test", content_type="application/pdf"),
        type_fichier=Cours.TypeFichier.PDF, enseignant=enseignant,
        publication=Cours.Publication.PUBLIE if publie else Cours.Publication.BROUILLON,
    )
    if texte:
        Transcription.objects.create(cours=cours, texte=texte)
        Resume.objects.create(cours=cours, texte=texte)
    return cours


def reponse_gemini(donnees):
    """Faux client Gemini : renvoie `donnees` (dict → JSON, str telle quelle, Exception levée)."""
    def generate_content(**kwargs):
        valeur = donnees.pop(0) if isinstance(donnees, list) else donnees
        if isinstance(valeur, Exception):
            raise valeur
        return SimpleNamespace(text=valeur if isinstance(valeur, str) else json.dumps(valeur))
    return SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))


@override_settings(MEDIA_ROOT=MEDIA_TEST)
class BaseQuizTestCase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_TEST, ignore_errors=True)

    @classmethod
    def setUpTestData(cls):
        cls.prof = creer_utilisateur("prof", Utilisateur.Role.ENSEIGNANT)
        cls.autre_prof = creer_utilisateur("autre_prof", Utilisateur.Role.ENSEIGNANT)
        cls.etudiant = creer_utilisateur("etudiant", Utilisateur.Role.ETUDIANT)
        cls.autre_etudiant = creer_utilisateur("autre_etudiant", Utilisateur.Role.ETUDIANT)
        cls.admin = creer_utilisateur("admin", Utilisateur.Role.ADMIN)

    def setUp(self):
        self.cours = creer_cours(self.prof.enseignant)
        self.quiz = self.creer_quiz()

    def creer_quiz(self, statut=Quiz.Statut.PUBLIE, cours=None, **extra):
        """Quiz de 3 questions (QCM 2 pts, Vrai/Faux 1 pt, ouverte 3 pts) = 6 points."""
        quiz = Quiz.objects.create(titre="Quiz matrices", cours=cours or self.cours,
                                   enseignant=(cours or self.cours).enseignant, statut=statut, **extra)
        self.q_qcm = Question.objects.create(quiz=quiz, ordre=1, type_question=Question.Type.QCM,
                                             enonce="Quand une matrice est-elle inversible ?", points=Decimal("2"))
        self.bon = Choix.objects.create(question=self.q_qcm, texte="Déterminant non nul", est_correct=True, ordre=1)
        self.mauvais = Choix.objects.create(question=self.q_qcm, texte="Déterminant nul", ordre=2)
        self.q_vf = Question.objects.create(quiz=quiz, ordre=2, type_question=Question.Type.VRAI_FAUX,
                                            enonce="Une matrice est un tableau de nombres.", points=Decimal("1"))
        self.vrai = Choix.objects.create(question=self.q_vf, texte="Vrai", est_correct=True, ordre=1)
        self.faux = Choix.objects.create(question=self.q_vf, texte="Faux", ordre=2)
        self.q_ouverte = Question.objects.create(
            quiz=quiz, ordre=3, type_question=Question.Type.OUVERTE, enonce="Expliquez le rôle du déterminant.",
            points=Decimal("3"), reponse_attendue="Il indique si la matrice est inversible (non nul).",
        )
        return quiz

    def connecter(self, user):
        self.client.force_login(user)
