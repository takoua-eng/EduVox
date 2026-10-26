import io
import json
import math
import os
import shutil
import struct
import tempfile
import wave
from types import SimpleNamespace
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.accounts.models import Enseignant, Etudiant, Utilisateur
from apps.assistant.models import SessionAssistant
from apps.courses.models import Cours, Resume

MEDIA_TEST = tempfile.mkdtemp()
TEXTE_COURS = "Une matrice est inversible si et seulement si son déterminant est non nul. " * 10


def creer_utilisateur(username, role):
    user = Utilisateur.objects.create_user(username=username, email=f"{username}@eduvox.test",
                                           password="motdepasse-test", role=role)
    if role == Utilisateur.Role.ENSEIGNANT:
        Enseignant.objects.create(utilisateur=user)
    elif role == Utilisateur.Role.ETUDIANT:
        Etudiant.objects.create(utilisateur=user)
    return user


def creer_cours(enseignant, titre="Algèbre linéaire", publie=True):
    cours = Cours.objects.create(
        titre=titre, fichier=SimpleUploadedFile("cours.pdf", b"%PDF-1.4 test", content_type="application/pdf"),
        type_fichier=Cours.TypeFichier.PDF, enseignant=enseignant,
        publication=Cours.Publication.PUBLIE if publie else Cours.Publication.BROUILLON,
    )
    Resume.objects.create(cours=cours, texte=TEXTE_COURS)
    return cours


def wav_bytes(secondes=1.0, frequence=16000, canaux=1):
    """Petit WAV PCM 16 bits (sinusoïde 440 Hz)."""
    tampon = io.BytesIO()
    with wave.open(tampon, "wb") as f:
        f.setnchannels(canaux)
        f.setsampwidth(2)
        f.setframerate(frequence)
        n = int(secondes * frequence)
        echantillons = (int(12000 * math.sin(2 * math.pi * 440 * i / frequence)) for i in range(n))
        f.writeframes(b"".join(struct.pack("<h", e) * canaux for e in echantillons))
    return tampon.getvalue()


def faux_client(reponses=None, morceaux=None, erreur_flux=None):
    """Faux client Gemini.

    `reponses` : valeur(s) renvoyée(s) par generate_content (dict → JSON, str, ou Exception levée).
    `morceaux` : textes renvoyés un à un par generate_content_stream ; `erreur_flux` levée après eux.
    """
    file_reponses = list(reponses) if isinstance(reponses, list) else [reponses]

    def generate_content(**kwargs):
        valeur = file_reponses.pop(0) if len(file_reponses) > 1 else file_reponses[0]
        if isinstance(valeur, Exception):
            raise valeur
        return SimpleNamespace(text=valeur if isinstance(valeur, str) else json.dumps(valeur))

    async def generate_content_stream(**kwargs):
        async def flux():
            for morceau in morceaux or []:
                yield SimpleNamespace(text=morceau)
            if erreur_flux:
                raise erreur_flux
        return flux()

    return SimpleNamespace(models=SimpleNamespace(generate_content=generate_content),
                           aio=SimpleNamespace(models=SimpleNamespace(generate_content_stream=generate_content_stream)))


def patch_gemini(**kwargs):
    return mock.patch("apps.assistant.services._client", return_value=faux_client(**kwargs))


@override_settings(MEDIA_ROOT=MEDIA_TEST)
@mock.patch.dict(os.environ, {"GEMINI_API_KEY": "cle-de-test", "GEMINI_MODEL": "gemini-test"})
class BaseAssistantTestCase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_TEST, ignore_errors=True)

    @classmethod
    def setUpTestData(cls):
        call_command("init_roles", stdout=io.StringIO())  # groupes et permissions de roles.py
        cls.etudiant = creer_utilisateur("etudiant", Utilisateur.Role.ETUDIANT)
        cls.autre_etudiant = creer_utilisateur("autre_etudiant", Utilisateur.Role.ETUDIANT)
        cls.prof = creer_utilisateur("prof", Utilisateur.Role.ENSEIGNANT)
        cls.admin = creer_utilisateur("admin", Utilisateur.Role.ADMIN)
        cls.cours = creer_cours(cls.prof.enseignant)
        cls.cours_brouillon = creer_cours(cls.prof.enseignant, titre="Brouillon secret", publie=False)

    def setUp(self):
        self.session = SessionAssistant.objects.create(utilisateur=self.etudiant, titre="Révisions", cours=self.cours)

    def connecter(self, user=None):
        self.client.force_login(user or self.etudiant)
