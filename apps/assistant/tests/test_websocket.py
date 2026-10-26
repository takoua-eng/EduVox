"""WebSocket : streaming de la réponse Gemini, contrôle d'accès et gestion des erreurs."""

from channels.db import database_sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser

from apps.assistant.models import JournalIA, MessageVocal, SessionAssistant
from apps.assistant.routing import websocket_urlpatterns

from .base import BaseAssistantTestCase, patch_gemini


class WebSocketTests(BaseAssistantTestCase):
    async def connecter_ws(self, user, session=None):
        communicateur = WebsocketCommunicator(URLRouter(websocket_urlpatterns),
                                              f"/ws/assistant/sessions/{(session or self.session).pk}/")
        communicateur.scope["user"] = user
        connecte, code = await communicateur.connect()
        return communicateur, connecte, code

    async def test_reponse_en_streaming(self):
        with patch_gemini(morceaux=["Le déterminant ", "indique si la matrice ", "est **inversible**."]):
            ws, connecte, _ = await self.connecter_ws(self.etudiant)
            self.assertTrue(connecte)
            await ws.send_json_to({"action": "question", "texte": "À quoi sert le déterminant ?"})
            debut = await ws.receive_json_from()
            self.assertEqual(debut["type"], "debut")
            self.assertEqual(debut["question"]["texte"], "À quoi sert le déterminant ?")
            morceaux = [await ws.receive_json_from() for _ in range(3)]
            self.assertEqual([m["type"] for m in morceaux], ["morceau"] * 3)
            fin = await ws.receive_json_from()
            await ws.disconnect()
        self.assertEqual(fin["type"], "fin")
        self.assertIn("<strong>inversible</strong>", fin["html"])
        reponse = await database_sync_to_async(MessageVocal.objects.get)(pk=fin["reponse_id"])
        self.assertEqual(reponse.statut, MessageVocal.Statut.PRET)
        self.assertEqual(reponse.texte, "Le déterminant indique si la matrice est **inversible**.")
        self.assertIsNotNone(reponse.premier_morceau_ms)
        journal = await database_sync_to_async(JournalIA.objects.get)(type_appel=JournalIA.Type.REPONSE)
        self.assertTrue(journal.succes)

    async def test_refus_si_session_d_un_autre(self):
        _, connecte, code = await self.connecter_ws(self.autre_etudiant)
        self.assertFalse(connecte)
        self.assertEqual(code, 4403)

    async def test_refus_anonyme_et_sans_permission(self):
        for user in (AnonymousUser(), self.prof):
            _, connecte, code = await self.connecter_ws(user)
            self.assertFalse(connecte)
            self.assertEqual(code, 4403)

    async def test_session_fermee(self):
        await database_sync_to_async(self.session.fermer)()
        ws, connecte, _ = await self.connecter_ws(self.etudiant)
        self.assertTrue(connecte)  # consultation possible, mais pas de nouvelle question
        await ws.send_json_to({"action": "question", "texte": "Bonjour"})
        reponse = await ws.receive_json_from()
        await ws.disconnect()
        self.assertEqual(reponse["type"], "erreur")
        self.assertIn("fermée", reponse["message"])

    async def test_erreur_pendant_le_flux_conserve_le_texte_partiel(self):
        with patch_gemini(morceaux=["Début de réponse"], erreur_flux=ConnectionError()):
            ws, _, _ = await self.connecter_ws(self.etudiant)
            await ws.send_json_to({"action": "question", "texte": "Question ?"})
            types = []
            while True:
                message = await ws.receive_json_from()
                types.append(message["type"])
                if message["type"] in ("fin", "erreur"):
                    break
            await ws.disconnect()
        self.assertEqual(types, ["debut", "morceau", "erreur"])
        self.assertIn("réseau", message["message"])
        reponse = await database_sync_to_async(MessageVocal.objects.get)(pk=message["reponse_id"])
        self.assertEqual(reponse.statut, MessageVocal.Statut.ERREUR)
        self.assertEqual(reponse.texte, "Début de réponse")

    async def test_question_vide_et_action_inconnue(self):
        ws, _, _ = await self.connecter_ws(self.etudiant)
        await ws.send_json_to({"action": "question", "texte": "  "})
        self.assertEqual((await ws.receive_json_from())["type"], "erreur")
        await ws.send_json_to({"action": "pirater"})
        self.assertEqual((await ws.receive_json_from())["message"], "Action inconnue.")
        await ws.send_json_to(["liste", "ignorée"])
        self.assertTrue(await ws.receive_nothing())
        await ws.disconnect()
        self.assertEqual(await database_sync_to_async(MessageVocal.objects.count)(), 0)

    async def test_session_inexistante(self):
        fantome = SessionAssistant(pk=999999)
        _, connecte, code = await self.connecter_ws(self.etudiant, fantome)
        self.assertFalse(connecte)
