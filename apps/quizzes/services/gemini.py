"""Appel Gemini avec sortie JSON, délai maximum et reprises limitées.

Le service partagé `ai_services.appeler_gemini` renvoie du texte libre, sans délai ni distinction
des erreurs. Plutôt que de le modifier (il sert aux autres modules), ce client utilise les mêmes
variables d'environnement (GEMINI_API_KEY, GEMINI_MODEL) et le même modèle par défaut.
"""

import json
import logging
import os
import time

from ai_services.gemini import MODELE_PAR_DEFAUT

from .reglages import reglage

logger = logging.getLogger(__name__)


class ErreurIA(Exception):
    """Échec d'un appel IA. `message` est affichable à l'utilisateur, `code` sert aux statistiques."""

    def __init__(self, message, code="service", reessayable=False):
        super().__init__(message)
        self.message = message
        self.code = code
        self.reessayable = reessayable


def modele_gemini():
    return os.environ.get("GEMINI_MODEL") or MODELE_PAR_DEFAUT


def gemini_configure():
    return bool(os.environ.get("GEMINI_API_KEY"))


def _client():
    from google import genai
    from google.genai import types

    cle = os.environ.get("GEMINI_API_KEY")
    if not cle:
        raise ErreurIA("L'IA n'est pas configurée sur ce serveur (clé Gemini absente). "
                       "Vous pouvez créer le quiz manuellement.", code="configuration")
    # HttpOptions.timeout est exprimé en millisecondes
    return genai.Client(api_key=cle, http_options=types.HttpOptions(timeout=reglage("QUIZ_GEMINI_TIMEOUT_S") * 1000))


def _erreur_httpx(erreur, nom):
    """Vrai si l'exception hérite de `nom` dans httpx ou httpx2 (le SDK peut utiliser l'un ou l'autre)."""
    return any(cls.__name__ == nom and cls.__module__.startswith("httpx") for cls in type(erreur).__mro__)


def _traduire_erreur(erreur):
    """Transforme une exception du SDK ou du réseau en ErreurIA compréhensible."""
    from google.genai import errors

    if isinstance(erreur, TimeoutError) or _erreur_httpx(erreur, "TimeoutException"):
        return ErreurIA("Gemini n'a pas répondu à temps. Réessayez dans quelques instants.", "delai", True)
    if isinstance(erreur, ConnectionError) or _erreur_httpx(erreur, "TransportError"):
        return ErreurIA("Impossible de joindre Gemini (problème réseau).", "reseau", True)
    if isinstance(erreur, errors.APIError):
        code = getattr(erreur, "code", None)
        if code == 429:
            return ErreurIA("Quota Gemini atteint. Réessayez plus tard.", "quota")
        if code in (401, 403):
            return ErreurIA("Clé Gemini refusée. Contactez l'administrateur.", "configuration")
        if code == 404:
            return ErreurIA("Modèle Gemini introuvable (vérifiez GEMINI_MODEL).", "configuration")
        if code is not None and code >= 500:
            return ErreurIA("Le service Gemini est momentanément indisponible.", "service", True)
        return ErreurIA("La requête a été refusée par Gemini.", "service")
    return ErreurIA("Erreur inattendue lors de l'appel à l'IA.", "service")


def extraire_json(texte):
    """Lit un objet JSON, en tolérant un éventuel bloc ```json … ```."""
    texte = (texte or "").strip()
    if texte.startswith("```"):
        texte = texte.strip("`")
        texte = texte[4:] if texte.lower().startswith("json") else texte
    try:
        donnees = json.loads(texte)
    except (json.JSONDecodeError, TypeError):
        raise ErreurIA("La réponse de l'IA n'est pas un JSON valide.", "json_invalide", True)
    if not isinstance(donnees, dict):
        raise ErreurIA("La réponse de l'IA n'a pas le format attendu.", "json_invalide", True)
    return donnees


def appeler_gemini_json(prompt, schema, systeme, temperature=0.3):
    """Renvoie (données JSON, métadonnées {modele, duree_ms, essais}).

    Les erreurs passagères (délai, réseau, 5xx, JSON illisible) sont retentées au plus
    QUIZ_GEMINI_ESSAIS fois au total ; quota et configuration ne le sont pas.
    """
    from google.genai import types

    client = _client()
    modele = modele_gemini()
    config = types.GenerateContentConfig(
        system_instruction=systeme,
        temperature=temperature,
        response_mime_type="application/json",
        response_json_schema=schema,
    )
    essais_max = reglage("QUIZ_GEMINI_ESSAIS")
    debut = time.monotonic()
    derniere = None
    for essai in range(1, essais_max + 1):
        try:
            reponse = client.models.generate_content(model=modele, contents=prompt, config=config)
            donnees = extraire_json(reponse.text)
            meta = {"modele": modele, "duree_ms": int((time.monotonic() - debut) * 1000), "essais": essai}
            return donnees, meta
        except ErreurIA as erreur:
            derniere = erreur
        except Exception as erreur:  # SDK, httpx...
            logger.warning("Appel Gemini échoué (essai %s/%s) : %s", essai, essais_max, type(erreur).__name__)
            derniere = _traduire_erreur(erreur)
        if not derniere.reessayable:
            break
        if essai < essais_max:
            time.sleep(min(2 ** essai, 5))
    derniere.essais = essai
    derniere.duree_ms = int((time.monotonic() - debut) * 1000)
    raise derniere
