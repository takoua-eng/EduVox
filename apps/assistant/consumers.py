"""WebSocket de l'assistant : la réponse de Gemini est envoyée morceau par morceau (streaming).

Protocole (JSON) :
  client → serveur  {"action": "question", "texte": "...", "message_id": <id d'une question dictée, facultatif>}
                    {"action": "arreter"}
  serveur → client  {"type": "debut", "question": {...}, "reponse_id": n}
                    {"type": "morceau", "texte": "..."}
                    {"type": "fin", "reponse_id": n, "html": "...", "duree_ms": n}
                    {"type": "erreur", "message": "..."}

Connexion refusée (code 4403) si l'utilisateur n'est pas connecté, n'a pas la permission, ou si la
session ne lui appartient pas : même règle que les vues HTTP.
"""

import asyncio
import logging
import time

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .models import SessionAssistant
from .permissions import peut_utiliser
from .services import (
    ErreurAssistant,
    configuration_reponse,
    construire_contenus,
    echouer_reponse,
    flux_reponse,
    preparer_question,
    rendre_markdown,
    terminer_reponse,
    traduire_erreur,
)

logger = logging.getLogger(__name__)
CODE_REFUS = 4403


class AssistantConsumer(AsyncJsonWebsocketConsumer):
    tache = None

    async def connect(self):
        user = self.scope.get("user")
        self.session = None
        if user is not None and await database_sync_to_async(peut_utiliser)(user):
            self.session = await self._session(user, self.scope["url_route"]["kwargs"]["pk"])
        if self.session is None:
            await self.close(code=CODE_REFUS)
            return
        await self.accept()

    @database_sync_to_async
    def _session(self, user, pk):
        return SessionAssistant.objects.select_related("cours", "utilisateur").filter(pk=pk, utilisateur=user).first()

    async def disconnect(self, code):
        if self.tache and not self.tache.done():
            self.tache.cancel()

    async def receive_json(self, contenu, **kwargs):
        if not isinstance(contenu, dict):
            return
        action = contenu.get("action")
        if action == "arreter":
            if self.tache and not self.tache.done():
                self.tache.cancel()
            return
        if action != "question":
            await self.send_json({"type": "erreur", "message": "Action inconnue."})
            return
        if self.tache and not self.tache.done():
            await self.send_json({"type": "erreur", "message": "Une réponse est déjà en cours."})
            return
        texte = contenu.get("texte") if isinstance(contenu.get("texte"), str) else ""
        message_id = contenu.get("message_id") if isinstance(contenu.get("message_id"), int) else None
        self.tache = asyncio.create_task(self._repondre(texte, message_id))

    @database_sync_to_async
    def _preparer(self, texte, message_id):
        self.session.refresh_from_db(fields=["active", "cours", "langue", "mode_reponse"])
        question, reponse = preparer_question(self.session, texte, message_id)
        return question, reponse, construire_contenus(question), configuration_reponse(self.session, question, reponse)

    async def _repondre(self, texte, message_id):
        try:
            question, reponse, contenus, configuration = await self._preparer(texte, message_id)
        except ErreurAssistant as erreur:
            await self.send_json({"type": "erreur", "message": erreur.message})
            return
        except Exception:
            logger.exception("Préparation de la question impossible (session %s)", self.session.pk)
            await self.send_json({"type": "erreur", "message": "Erreur inattendue. Réessayez."})
            return

        await self.send_json({"type": "debut", "reponse_id": reponse.pk, "langue": reponse.langue, "question": {
            "id": question.pk, "texte": question.texte, "mode": question.mode, "html": rendre_markdown(question.texte),
        }})
        debut = time.monotonic()
        premier, morceaux = None, []
        try:
            async for morceau in flux_reponse(question, reponse, contenus, configuration):
                if premier is None:
                    premier = int((time.monotonic() - debut) * 1000)
                morceaux.append(morceau)
                await self.send_json({"type": "morceau", "texte": morceau})
            texte_complet = "".join(morceaux)
            if not texte_complet.strip():
                raise ErreurAssistant("L'IA a renvoyé une réponse vide. Reformulez votre question.", "vide")
        except asyncio.CancelledError:
            # Arrêt demandé (ou onglet fermé) : on garde ce qui a déjà été reçu
            duree = int((time.monotonic() - debut) * 1000)
            await database_sync_to_async(terminer_reponse)(reponse, "".join(morceaux), duree, premier, True)
            try:
                await self.send_json({"type": "fin", "reponse_id": reponse.pk, "duree_ms": duree, "interrompue": True,
                                      "html": rendre_markdown(reponse.texte)})
            except Exception:
                pass  # client déjà déconnecté
            return
        except Exception as erreur:
            erreur = traduire_erreur(erreur)
            if erreur.code == "service":
                logger.exception("Échec du flux Gemini (message %s)", reponse.pk)
            duree = int((time.monotonic() - debut) * 1000)
            await database_sync_to_async(echouer_reponse)(reponse, erreur, duree, "".join(morceaux))
            await self.send_json({"type": "erreur", "message": erreur.message, "reponse_id": reponse.pk})
            return
        duree = int((time.monotonic() - debut) * 1000)
        await database_sync_to_async(terminer_reponse)(reponse, texte_complet, duree, premier)
        await self.send_json({"type": "fin", "reponse_id": reponse.pk, "duree_ms": duree, "premier_morceau_ms": premier,
                              "html": rendre_markdown(reponse.texte)})
