"""Langues de l'assistant : détection sur le texte écrit et consignes pour Gemini.

À l'oral, la langue est détectée par Whisper (ai_services.transcrire_question) ; à l'écrit, une heuristique
légère suffit pour les trois langues prises en charge (alphabet arabe, mots courants français / anglais).
"""

import re

from .models import Langue

NOMS_POUR_PROMPT = {Langue.FR: "français", Langue.EN: "anglais", Langue.AR: "arabe (standard moderne)"}

_MOTS_FR = set("""le la les un une des du de et est sont que qui quoi pour dans sur avec pas ne je tu il elle nous vous
ils elles mon ma mes ton ta tes son sa ses ce cette ces comment pourquoi quel quelle quels quelles explique expliquer
donne moi peux peut faire fait entre aussi mais ou où donc car très plus moins exemple qu est-ce c'est j'ai""".split())
_MOTS_EN = set("""the a an of and is are that what which who for in on with not do does did i you he she we they my your
his her its this these those how why when where explain give me can could make between also but or so because very
more less example please tell difference about from""".split())


def detecter_langue_texte(texte, defaut=Langue.FR):
    """Code de langue (fr, en, ar) d'un texte écrit."""
    lettres = [c for c in texte or "" if c.isalpha()]
    if not lettres:
        return defaut
    if sum("؀" <= c <= "ۿ" or "ݐ" <= c <= "ݿ" for c in lettres) / len(lettres) > 0.3:
        return Langue.AR
    mots = re.findall(r"[a-zàâäçéèêëîïôöùûüÿœ']+", texte.lower())
    francais = sum(m in _MOTS_FR for m in mots) + 2 * len(re.findall(r"[àâçéèêëîïôùûœ]", texte.lower()))
    anglais = sum(m in _MOTS_EN for m in mots)
    if anglais > francais:
        return Langue.EN
    if francais > anglais:
        return Langue.FR
    return defaut


def consigne_langue(code, imposee):
    """Phrase ajoutée au prompt système pour la langue de la réponse."""
    nom = NOMS_POUR_PROMPT.get(code, "français")
    if imposee:
        return f"Réponds toujours en {nom}, quelle que soit la langue de la question."
    return f"La question est en {nom} : réponds en {nom}."
