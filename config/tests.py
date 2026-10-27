"""Tests de l'infrastructure : sonde de santé et service des fichiers envoyés en production."""

import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import clear_url_caches, reverse


class SondeSanteTests(TestCase):
    def test_repond_ok_sans_connexion(self):
        reponse = self.client.get(reverse("sante"))
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse.json(), {"statut": "ok", "base": "ok"})
        self.assertIn("no-cache", reponse["Cache-Control"])

    def test_repond_503_si_la_base_ne_repond_pas(self):
        with mock.patch("config.sante.connection.cursor", side_effect=Exception("base arrêtée")):
            reponse = self.client.get(reverse("sante"))
        self.assertEqual(reponse.status_code, 503)
        self.assertEqual(reponse.json()["base"], "indisponible")

    def test_refuse_les_autres_methodes(self):
        self.assertEqual(self.client.post(reverse("sante")).status_code, 405)


class ServiceMediasTests(TestCase):
    """En production (DEBUG=False), /media/ n'est servi que si SERVIR_MEDIAS=True, et seulement aux connectés."""

    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        Path(self.dossier.name, "photo.txt").write_text("contenu", encoding="utf-8")

    def tearDown(self):
        self.dossier.cleanup()
        clear_url_caches()

    def _recharger_urls(self):
        import importlib

        import config.urls
        clear_url_caches()
        importlib.reload(config.urls)

    def test_fichier_servi_aux_utilisateurs_connectes(self):
        with override_settings(DEBUG=False, SERVIR_MEDIAS=True, MEDIA_ROOT=self.dossier.name):
            self._recharger_urls()
            utilisateur = get_user_model().objects.create_user("medias", "medias@exemple.tn", "MotDePasse2026!x",
                                                               email_verifie=True)
            self.assertEqual(self.client.get("/media/photo.txt").status_code, 302)  # anonyme : vers la connexion
            self.client.force_login(utilisateur)
            reponse = self.client.get("/media/photo.txt")
            self.assertEqual(reponse.status_code, 200)
            self.assertEqual(b"".join(reponse.streaming_content), b"contenu")
        self._recharger_urls()

    def test_pas_de_medias_sans_le_reglage(self):
        with override_settings(DEBUG=False, SERVIR_MEDIAS=False, MEDIA_ROOT=self.dossier.name):
            self._recharger_urls()
            utilisateur = get_user_model().objects.create_user("medias2", "medias2@exemple.tn", "MotDePasse2026!x",
                                                               email_verifie=True)
            self.client.force_login(utilisateur)
            self.assertEqual(self.client.get("/media/photo.txt").status_code, 404)
        self._recharger_urls()