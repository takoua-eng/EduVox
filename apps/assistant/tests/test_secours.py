"""Bascule automatique sur le modèle de secours quand le quota du modèle principal est atteint."""

import json
import os
from types import SimpleNamespace
from unittest import mock

from channels.db import database_sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.urls import reverse

from apps.assistant.models import Illustration, MessageVocal
from apps.assistant.routing import websocket_urlpatterns
from apps.assistant.services import modeles_gemini

from .base import BaseAssistantTestCase, vecteur_mots


class Quota(Exception):
    code = 429


def client_quota(texte="Réponse du modèle de secours.", tous_epuises=False):
    """Faux client : le modèle principal (gemini-test) répond 429, le modèle de secours répond normalement."""
    appels = []

    def generate_content(model, **kwargs):
        appels.append(model)
        if model == "gemini-test" or tous_epuises:
            raise Quota()
        return SimpleNamespace(text=texte)

    async def generate_content_stream(model, **kwargs):
        appels.append(model)
        if model == "gemini-test" or tous_epuises:
            raise Quota()

        async def flux():
            for mot in texte.split(" "):
                yield SimpleNamespace(text=mot + " ")
        return flux()

    def embed_content(model, contents, config=None):
        return SimpleNamespace(embeddings=[SimpleNamespace(values=vecteur_mots(t)) for t in contents])

    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content, embed_content=embed_content),
                             aio=SimpleNamespace(models=SimpleNamespace(generate_content_stream=generate_content_stream)))
    return client, appels


class SecoursTests(BaseAssistantTestCase):
    def setUp(self):
        super().setUp()
        self.connecter()

    def test_ordre_des_modeles(self):
        self.assertEqual(modeles_gemini(), ["gemini-test", "gemini-3.5-flash-lite"])
        with mock.patch.dict(os.environ, {"ASSISTANT_GEMINI_MODELE_SECOURS": ""}):
            self.assertEqual(modeles_gemini(), ["gemini-test"])
        with mock.patch.dict(os.environ, {"ASSISTANT_GEMINI_MODEL": "gemini-3.5-flash-lite"}):
            self.assertEqual(modeles_gemini(), ["gemini-3.5-flash-lite"])  # pas de doublon

    def test_reponse_via_le_modele_de_secours(self):
        client, appels = client_quota()
        with mock.patch("apps.assistant.services._client", return_value=client), \
                self.assertLogs("apps.assistant.services", "WARNING"):
            donnees = self.client.post(reverse("question-assistant", args=[self.session.pk]), {"texte": "Bonjour ?"},
                                       HTTP_X_REQUESTED_WITH="XMLHttpRequest").json()
        self.assertEqual(appels, ["gemini-test", "gemini-3.5-flash-lite"])
        reponse = MessageVocal.objects.get(pk=donnees["reponse_id"])
        self.assertEqual((reponse.statut, reponse.modele), ("PRET", "gemini-3.5-flash-lite"))

    def test_tous_les_quotas_atteints(self):
        client, appels = client_quota(tous_epuises=True)
        with mock.patch("apps.assistant.services._client", return_value=client), \
                self.assertLogs("apps.assistant.services", "WARNING"):
            reponse = self.client.post(reverse("question-assistant", args=[self.session.pk]), {"texte": "Bonjour ?"},
                                       HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(reponse.status_code, 503)
        self.assertIn("Quota", reponse.json()["erreur"])
        self.assertEqual(len(appels), 2)

    def test_diagramme_via_le_modele_de_secours(self):
        client, _ = client_quota(texte=json.dumps({"code_mermaid": "flowchart TD\nA-->B", "texte_alternatif": "x"}))
        with mock.patch("apps.assistant.services._client", return_value=client), \
                self.assertLogs("apps.assistant.services", "WARNING"), self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("illustrer-assistant", args=[self.session.pk]),
                             {"type_illustration": "DIAGRAMME", "demande": "Étapes du calcul"},
                             HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(Illustration.objects.get().statut, Illustration.Statut.TERMINEE)

    async def test_streaming_via_le_modele_de_secours(self):
        client, appels = client_quota(texte="Un deux trois.")
        ws = WebsocketCommunicator(URLRouter(websocket_urlpatterns), f"/ws/assistant/sessions/{self.session.pk}/")
        ws.scope["user"] = self.etudiant
        with mock.patch("apps.assistant.services._client", return_value=client):
            await ws.connect()
            await ws.send_json_to({"action": "question", "texte": "Compte jusqu'à trois"})
            while (message := await ws.receive_json_from(timeout=5))["type"] not in ("fin", "erreur"):
                pass
            await ws.disconnect()
        self.assertEqual(message["type"], "fin")
        self.assertEqual(appels, ["gemini-test", "gemini-3.5-flash-lite"])
        reponse = await database_sync_to_async(MessageVocal.objects.get)(pk=message["reponse_id"])
        self.assertEqual((reponse.texte, reponse.modele), ("Un deux trois.", "gemini-3.5-flash-lite"))
