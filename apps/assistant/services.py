"""Logique métier de l'assistant : contexte, appels Gemini (avec flux), illustrations, métriques.

Les services partagés (ai_services) restent la porte d'entrée vers Whisper et Hugging Face ;
ce module y ajoute ce dont l'assistant a besoin : délai maximum, flux de réponse, erreurs lisibles,
journalisation des performances.
"""

import json
import logging
import os
import re
import string
import time
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from markdown_it import MarkdownIt

from ai_services.gemini import MODELE_PAR_DEFAUT

from .forms import nettoyer_espaces, valider_question
from .langues import NOMS_POUR_PROMPT, detecter_langue_texte
from .models import Illustration, JournalIA, MessageVocal
from .permissions import cours_utilisables
from .reglages import reglage

logger = logging.getLogger(__name__)

SYSTEME = """Tu es l'assistant pédagogique d'EduVox, une plateforme d'apprentissage.
- Adopte un ton bienveillant et clair ; entre directement dans le sujet, sans salutation ni compliment
  sur la question (« Bonjour », « Excellente question »…).
- Va à l'essentiel : une explication structurée, des exemples si utile, au plus 300 mots sauf demande contraire.
- Utilise le Markdown simple (titres courts, listes, **gras**, `code`, tableaux) ; jamais de HTML ni de LaTeX :
  écris les formules en texte lisible (A⁻¹, x², √2, a × b, ≤) car elles ne sont pas mises en forme.
- Si un contexte de cours est fourni, appuie-toi d'abord sur lui et signale quand tu en sors.
- Si tu ne sais pas, dis-le honnêtement plutôt que d'inventer.
- La question a pu être dictée et transcrite automatiquement : tolère les petites fautes de transcription."""

TYPES_MERMAID = ("flowchart", "graph", "sequenceDiagram", "classDiagram", "stateDiagram", "stateDiagram-v2",
                 "erDiagram", "mindmap", "timeline", "pie", "gantt", "journey", "quadrantChart")

_markdown = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False}).enable("table")


class ErreurAssistant(Exception):
    """Échec affichable à l'utilisateur ; `code` est enregistré dans le journal des appels IA."""

    def __init__(self, message, code="service"):
        super().__init__(message)
        self.message = message
        self.code = code


def rendre_markdown(texte):
    """Markdown → HTML sûr : le HTML brut est désactivé et les liens javascript: refusés par markdown-it."""
    return _markdown.render(texte or "")


MODELE_SECOURS = "gemini-3.5-flash-lite"


def modele_gemini():
    """Modèle principal : ASSISTANT_GEMINI_MODEL (propre au module), sinon GEMINI_MODEL de l'équipe."""
    return os.environ.get("ASSISTANT_GEMINI_MODEL") or os.environ.get("GEMINI_MODEL") or MODELE_PAR_DEFAUT


def modeles_gemini():
    """Modèles à essayer dans l'ordre : le principal, puis le modèle de secours si le quota du premier est atteint.

    Le quota gratuit de Gemini est compté par modèle (20 requêtes / jour sur gemini-3.5-flash) : basculer sur
    un modèle plus léger garde l'assistant disponible. ASSISTANT_GEMINI_MODELE_SECOURS vide = pas de secours.
    """
    principal = modele_gemini()
    secours = os.environ.get("ASSISTANT_GEMINI_MODELE_SECOURS", MODELE_SECOURS).strip()
    return [principal] + ([secours] if secours and secours != principal else [])


def _bascule(erreur, modele, suivants):
    """Vrai si l'on doit réessayer avec le modèle suivant (quota atteint ou modèle indisponible)."""
    if suivants and erreur.code in ("quota", "indisponible"):
        logger.warning("Gemini %s : %s, bascule sur %s", modele, erreur.code, suivants[0])
        return True
    return False


def gemini_configure():
    return bool(os.environ.get("GEMINI_API_KEY"))


def hugging_face_configure():
    return bool(os.environ.get("HF_TOKEN"))


def _client():
    from google import genai
    from google.genai import types

    cle = os.environ.get("GEMINI_API_KEY")
    if not cle:
        raise ErreurAssistant("L'assistant n'est pas configuré sur ce serveur (clé Gemini absente).", "configuration")
    # Une seule tentative côté SDK : nos propres reprises (et la bascule de modèle) restent maîtrisées ;
    # sinon, après un quota atteint, le SDK peut attendre le délai suggéré par l'API (plusieurs heures).
    return genai.Client(api_key=cle, http_options=types.HttpOptions(
        timeout=reglage("ASSISTANT_GEMINI_TIMEOUT_S") * 1000, retry_options=types.HttpRetryOptions(attempts=1)))


def _nom_dans_mro(erreur, nom):
    return any(cls.__name__ == nom for cls in type(erreur).__mro__)


def traduire_erreur(erreur):
    """Exception du SDK Gemini, de Hugging Face ou du réseau → ErreurAssistant compréhensible."""
    if isinstance(erreur, ErreurAssistant):
        return erreur
    if isinstance(erreur, TimeoutError) or _nom_dans_mro(erreur, "TimeoutException"):
        return ErreurAssistant("Le service d'IA n'a pas répondu à temps. Réessayez dans un instant.", "delai")
    if isinstance(erreur, ConnectionError) or _nom_dans_mro(erreur, "TransportError"):
        return ErreurAssistant("Impossible de joindre le service d'IA (problème réseau).", "reseau")
    code = getattr(erreur, "code", None)
    reponse = getattr(erreur, "response", None)
    if code is None and reponse is not None:
        code = getattr(reponse, "status_code", None)
    if code == 429:
        return ErreurAssistant("Quota du service d'IA atteint. Réessayez plus tard.", "quota")
    if code == 402:  # Hugging Face : crédit Inference Providers épuisé
        return ErreurAssistant("Crédit du service d'images épuisé. Les diagrammes restent disponibles.", "credit")
    if code in (401, 403):
        return ErreurAssistant("Clé d'API refusée par le service d'IA. Contactez l'administrateur.", "configuration")
    if code == 404:
        return ErreurAssistant("Modèle d'IA introuvable (vérifiez la configuration).", "configuration")
    if isinstance(code, int) and code >= 500:
        return ErreurAssistant("Le service d'IA est momentanément indisponible. Réessayez.", "indisponible")
    return ErreurAssistant("Erreur inattendue lors de l'appel à l'IA.", "service")


def journaliser(type_appel, utilisateur=None, erreur=None, modele="", duree_ms=None, premier_morceau_ms=None):
    JournalIA.objects.create(
        type_appel=type_appel, utilisateur=utilisateur, succes=erreur is None,
        code="ok" if erreur is None else erreur.code, modele=modele,
        duree_ms=duree_ms, premier_morceau_ms=premier_morceau_ms,
    )


# ---------- Questions et réponses ----------

def cours_accessible(session):
    """Le cours de la session, s'il existe encore et que l'utilisateur a toujours le droit de le voir."""
    cours = session.cours
    if cours is None or not cours_utilisables(session.utilisateur).filter(pk=cours.pk).exists():
        return None
    return cours


def contexte_cours(session):
    """Repli sans RAG : résumé du cours (sinon sa transcription), tronqué."""
    cours = cours_accessible(session)
    if cours is None:
        return ""
    texte = ""
    for relation in ("resume", "transcription"):
        objet = getattr(cours, relation, None)  # RelatedObjectDoesNotExist hérite d'AttributeError
        if objet is not None and objet.texte:
            texte = objet.texte
            break
    texte = texte[:reglage("ASSISTANT_CONTEXTE_MAX_CARACTERES")]
    return f"Cours « {cours.titre} » :\n{texte}" if texte else f"Cours « {cours.titre} » (contenu non disponible)."


CONSIGNE_SOURCES = """Extraits du cours « {titre} » les plus proches de la question, numérotés :
{extraits}

Règles pour ces extraits :
- Appuie-toi d'abord sur eux et cite ceux que tu utilises par leur numéro entre crochets, juste après
  l'information concernée, par exemple [1] ou [2][3]. N'invente jamais de numéro.
- S'ils ne contiennent pas la réponse, dis-le en une phrase, puis réponds avec tes connaissances générales
  en le signalant (sans citation)."""


def requete_de_recherche(question):
    """Texte à chercher dans le cours : la question, précédée de la précédente (relances du type « et pourquoi ? »)."""
    precedente = (question.session.messages.filter(role=MessageVocal.Role.UTILISATEUR, pk__lt=question.pk)
                  .exclude(texte="").order_by("-pk").values_list("texte", flat=True).first())
    if precedente and len(question.texte) < 80:
        return f"{precedente}\n{question.texte}"
    return question.texte


def contexte_avec_sources(question, reponse):
    """Passages pertinents du cours (RAG), enregistrés dans `reponse.sources`. Repli sur le résumé en cas d'échec."""
    from . import rag

    session = question.session
    cours = cours_accessible(session)
    if cours is None:
        return ""
    try:
        trouves = rag.rechercher(cours, requete_de_recherche(question), session.utilisateur)
    except Exception:
        logger.warning("Recherche dans le cours %s impossible : repli sur le résumé", cours.pk, exc_info=True)
        trouves = []
    if not trouves:
        return contexte_cours(session)
    total = cours.passages_assistant.count()
    reponse.sources = [
        {"n": n, "ordre": passage.ordre, "total": total, "score": round(score, 3), "extrait": passage.texte}
        for n, (passage, score) in enumerate(trouves, start=1)
    ]
    extraits = "\n\n".join(f"[{s['n']}] {s['extrait']}" for s in reponse.sources)
    return CONSIGNE_SOURCES.format(titre=cours.titre, extraits=extraits)


def instruction_systeme(session, question=None, reponse=None):
    """Prompt système : rôle, langue de la réponse, puis contexte du cours (passages cités ou résumé)."""
    from .langues import consigne_langue

    morceaux = [SYSTEME]
    if reponse is not None and reponse.langue:
        morceaux.append("- " + consigne_langue(reponse.langue, imposee=bool(session.langue)))
    else:
        morceaux.append("- Réponds dans la langue de la question.")
    if question is not None and reponse is not None:
        contexte = contexte_avec_sources(question, reponse)
    else:
        contexte = contexte_cours(session)
    if contexte:
        morceaux.append(f"\nContexte du cours choisi par l'étudiant (à utiliser en priorité) :\n<<<\n{contexte}\n>>>")
    return "\n".join(morceaux)


def construire_contenus(question):
    """Historique récent de la session (messages prêts) suivi de la question, au format Gemini."""
    from google.genai import types

    precedents = list(
        question.session.messages.filter(statut=MessageVocal.Statut.PRET, pk__lt=question.pk)
        .exclude(texte="").order_by("-date", "-pk")[:reglage("ASSISTANT_HISTORIQUE_MESSAGES")]
    )[::-1]
    contenus = [
        types.Content(role="user" if m.est_question else "model", parts=[types.Part(text=m.texte)])
        for m in precedents
    ]
    contenus.append(types.Content(role="user", parts=[types.Part(text=question.texte)]))
    return contenus


def configuration_thinking():
    """Niveau de « réflexion » de Gemini (ASSISTANT_GEMINI_REFLEXION, « minimal » par défaut ; vide = celui du modèle).

    Mesuré sur gemini-3.5-flash : 1er morceau en 0,8 s en « minimal » contre 5,6 s par défaut. Pour un
    assistant vocal, la réactivité prime ; « low » ou « high » donnent des réponses plus approfondies mais plus lentes.
    """
    from google.genai import types

    niveau = os.environ.get("ASSISTANT_GEMINI_REFLEXION", "minimal").strip()
    return types.ThinkingConfig(thinking_level=niveau) if niveau else None


def configuration_reponse(session, question=None, reponse=None):
    """Configuration Gemini de la réponse ; enregistre au passage les sources et la langue de `reponse`."""
    from google.genai import types

    systeme = instruction_systeme(session, question, reponse)
    if reponse is not None and reponse.pk:
        reponse.save(update_fields=["sources", "langue"])
    return types.GenerateContentConfig(system_instruction=systeme, temperature=0.4,
                                       thinking_config=configuration_thinking())


def verifier_quota_questions(user):
    depuis = timezone.now() - timedelta(hours=1)
    nb = MessageVocal.objects.filter(session__utilisateur=user, role=MessageVocal.Role.UTILISATEUR,
                                     date__gte=depuis).count()
    if nb >= reglage("ASSISTANT_QUESTIONS_PAR_HEURE"):
        raise ErreurAssistant("Vous avez atteint la limite de questions pour cette heure. Réessayez un peu plus tard.",
                              "limite")


def preparer_question(session, texte="", message_id=None):
    """Crée (ou reprend, pour une question dictée) la question et la réponse vide à remplir.

    Renvoie (question, reponse). Lève ErreurAssistant si la demande est invalide.
    """
    if not session.active:
        raise ErreurAssistant("Cette session est fermée : rouvrez-la pour poser une question.", "session_fermee")
    if not gemini_configure():
        raise ErreurAssistant("L'assistant n'est pas configuré sur ce serveur (clé Gemini absente).", "configuration")
    texte = nettoyer_espaces(texte)
    if texte or not message_id:  # une question dictée peut arriver sans texte (on garde la transcription)
        try:
            texte = valider_question(texte)
        except ValidationError as erreur:
            raise ErreurAssistant(erreur.messages[0], "validation")

    with transaction.atomic():
        if message_id:
            question = (MessageVocal.objects.select_for_update()
                        .filter(pk=message_id, session=session, role=MessageVocal.Role.UTILISATEUR,
                                mode=MessageVocal.Mode.VOCAL).first())
            if question is None or question.statut != MessageVocal.Statut.PRET:
                raise ErreurAssistant("Cet enregistrement n'est pas (ou plus) prêt à être envoyé.", "validation")
            if session.messages.filter(pk__gt=question.pk, role=MessageVocal.Role.ASSISTANT).exists():
                raise ErreurAssistant("Cette question a déjà reçu une réponse.", "validation")
            if texte and texte != question.texte:  # l'utilisateur a corrigé la transcription
                question.texte = texte
                question.save(update_fields=["texte"])
            if not question.texte:
                raise ErreurAssistant("La question est vide.", "validation")
        else:
            verifier_quota_questions(session.utilisateur)
            question = MessageVocal.objects.create(
                session=session, role=MessageVocal.Role.UTILISATEUR, texte=texte,
                langue=session.langue or detecter_langue_texte(texte))
        if not question.langue:  # question dictée transcrite sans langue (enregistrement long)
            question.langue = session.langue or detecter_langue_texte(question.texte)
            question.save(update_fields=["langue"])
        reponse = MessageVocal.objects.create(session=session, role=MessageVocal.Role.ASSISTANT,
                                              statut=MessageVocal.Statut.EN_ATTENTE, modele=modele_gemini(),
                                              langue=session.langue or question.langue)
        session.save(update_fields=["date_maj"])
    return question, reponse


def terminer_reponse(reponse, texte, duree_ms, premier_morceau_ms=None, interrompue=False):
    texte = texte.strip()
    if interrompue:
        texte = (texte + "\n\n*(Réponse interrompue.)*").strip()
    reponse.texte = texte
    reponse.statut = MessageVocal.Statut.PRET
    reponse.duree_ms, reponse.premier_morceau_ms = duree_ms, premier_morceau_ms
    reponse.save(update_fields=["texte", "statut", "duree_ms", "premier_morceau_ms", "modele"])
    journaliser(JournalIA.Type.REPONSE, reponse.session.utilisateur, modele=reponse.modele,
                erreur=ErreurAssistant("Interrompue", "interrompue") if interrompue else None,
                duree_ms=duree_ms, premier_morceau_ms=premier_morceau_ms)


def echouer_reponse(reponse, erreur, duree_ms=None, texte_partiel=""):
    reponse.statut = MessageVocal.Statut.ERREUR
    reponse.message_erreur = erreur.message[:255]
    reponse.texte = texte_partiel.strip()
    reponse.duree_ms = duree_ms
    reponse.save(update_fields=["statut", "message_erreur", "texte", "duree_ms"])
    journaliser(JournalIA.Type.REPONSE, reponse.session.utilisateur, erreur, reponse.modele, duree_ms)


def repondre(question, reponse):
    """Version sans flux (repli quand le WebSocket n'est pas disponible)."""
    debut = time.monotonic()
    try:
        client = _client()  # garder une référence : le SDK ferme sa connexion quand le client est libéré
        contenus = construire_contenus(question)
        configuration = configuration_reponse(question.session, question, reponse)
        modeles = modeles_gemini()
        for i, modele in enumerate(modeles):
            try:
                resultat = client.models.generate_content(model=modele, contents=contenus, config=configuration)
                reponse.modele = modele
                break
            except Exception as exception:
                if not _bascule(traduire_erreur(exception), modele, modeles[i + 1:]):
                    raise
        texte = (resultat.text or "").strip()
        if not texte:
            raise ErreurAssistant("L'IA a renvoyé une réponse vide. Reformulez votre question.", "vide")
    except Exception as erreur:
        erreur = traduire_erreur(erreur)
        if erreur.code == "service":
            logger.exception("Échec de la réponse Gemini (message %s)", reponse.pk)
        echouer_reponse(reponse, erreur, int((time.monotonic() - debut) * 1000))
        raise erreur
    duree = int((time.monotonic() - debut) * 1000)
    terminer_reponse(reponse, texte, duree, premier_morceau_ms=duree)
    return reponse


async def flux_reponse(question, reponse, contenus, configuration):
    """Générateur asynchrone des morceaux de texte renvoyés par Gemini au fil de l'eau."""
    client = _client()  # référence conservée pendant toute la durée du flux
    modeles = modeles_gemini()
    for i, modele in enumerate(modeles):
        recu = False
        try:
            flux = await client.aio.models.generate_content_stream(model=modele, contents=contenus,
                                                                      config=configuration)
            async for morceau in flux:
                if not recu:
                    reponse.modele, recu = modele, True
                if morceau.text:
                    yield morceau.text
            return
        except Exception as exception:
            # Bascule seulement si rien n'a encore été envoyé à l'étudiant
            if recu or not _bascule(traduire_erreur(exception), modele, modeles[i + 1:]):
                raise


# ---------- Illustrations ----------

SCHEMA_IMAGE = {
    "type": "object",
    "properties": {
        "prompt": {"type": "string", "description": "Prompt en anglais pour un modèle texte → image"},
        "texte_alternatif": {"type": "string", "description": "Description courte de l'image, en français"},
    },
    "required": ["prompt", "texte_alternatif"],
}

SCHEMA_DIAGRAMME = {
    "type": "object",
    "properties": {
        "code_mermaid": {"type": "string", "description": "Code Mermaid valide, sans balises ```"},
        "texte_alternatif": {"type": "string", "description": "Ce que montre le diagramme, en français"},
    },
    "required": ["code_mermaid", "texte_alternatif"],
}


def verifier_quota_illustrations(user):
    depuis = timezone.now() - timedelta(hours=1)
    nb = Illustration.objects.filter(session__utilisateur=user, date__gte=depuis).count()
    if nb >= reglage("ASSISTANT_ILLUSTRATIONS_PAR_HEURE"):
        raise ErreurAssistant("Limite d'illustrations atteinte pour cette heure. Réessayez plus tard.", "limite")


def _contexte_illustration(illustration):
    morceaux = [f"Demande de l'étudiant : {illustration.demande}"]
    if illustration.message_id and illustration.message.texte:
        morceaux.append(f"Explication à illustrer :\n{illustration.message.texte[:3000]}")
    if illustration.session.cours_id:
        morceaux.append(f"Cours : {illustration.session.cours.titre}")
    return "\n\n".join(morceaux)


ERREURS_PASSAGERES = {"delai", "reseau", "indisponible", "json_invalide"}


def appeler_gemini_json(prompt, schema, systeme, essais=2):
    """Appel Gemini à sortie JSON structurée. Renvoie (dict, durée en ms).

    Une erreur passagère (délai, réseau, 5xx, JSON illisible) est retentée une fois.
    """
    from google.genai import types

    debut = time.monotonic()
    config = types.GenerateContentConfig(system_instruction=systeme, temperature=0.3, thinking_config=configuration_thinking(),
                                         response_mime_type="application/json", response_json_schema=schema)
    modeles = modeles_gemini()
    for essai in range(1, essais + 1):
        try:
            client = _client()  # garder une référence : le SDK ferme sa connexion quand le client est libéré
            resultat = None
            for i, modele in enumerate(modeles):
                try:
                    resultat = client.models.generate_content(model=modele, contents=prompt, config=config)
                    break
                except Exception as exception:
                    if not _bascule(traduire_erreur(exception), modele, modeles[i + 1:]):
                        raise
            donnees = json.loads((resultat.text or "").strip())
            if not isinstance(donnees, dict):
                raise json.JSONDecodeError("objet attendu", "", 0)
            return donnees, int((time.monotonic() - debut) * 1000)
        except json.JSONDecodeError:
            erreur = ErreurAssistant("La réponse de l'IA n'a pas le format attendu. Réessayez.", "json_invalide")
        except Exception as exception:
            erreur = traduire_erreur(exception)
            if erreur.code == "service":
                logger.exception("Échec de l'appel Gemini (JSON)")
        if erreur.code not in ERREURS_PASSAGERES or essai == essais:
            raise erreur
        logger.warning("Appel Gemini (JSON) : erreur passagère « %s », nouvel essai", erreur.code)
        time.sleep(1)


def preparer_prompt_image(illustration):
    """Gemini transforme la demande (souvent en français) en prompt anglais précis + texte alternatif."""
    donnees, _ = appeler_gemini_json(
        _contexte_illustration(illustration), SCHEMA_IMAGE,
        "Tu rédiges des prompts pour un générateur d'images pédagogiques. Le prompt, en anglais, décrit une "
        "illustration claire, didactique, sans texte écrit dans l'image, style illustration éducative propre. "
        "Refuse tout contenu violent, sexuel ou haineux en renvoyant un prompt neutre sur le thème du cours. "
        "Le texte alternatif, en français, décrit l'image en une phrase (150 caractères max).",
    )
    prompt = str(donnees.get("prompt", "")).strip()[:1000]
    if not prompt:
        raise ErreurAssistant("L'IA n'a pas pu préparer l'illustration. Reformulez la demande.", "json_invalide")
    return prompt, str(donnees.get("texte_alternatif", "")).strip()[:300]


def nettoyer_mermaid(code):
    code = (code or "").strip()
    code = re.sub(r"^```(?:mermaid)?\s*|\s*```$", "", code).strip()
    premier = code.split(None, 1)[0] if code else ""
    if premier not in TYPES_MERMAID:
        raise ErreurAssistant("Le diagramme généré n'est pas valide. Réessayez en précisant la demande.", "mermaid")
    if len(code) > 5000:
        raise ErreurAssistant("Le diagramme généré est trop complexe. Simplifiez la demande.", "mermaid")
    return code


def generer_diagramme(illustration):
    """Renvoie (code Mermaid validé, texte alternatif, durée en ms)."""
    donnees, duree = appeler_gemini_json(
        _contexte_illustration(illustration), SCHEMA_DIAGRAMME,
        "Tu crées des diagrammes pédagogiques en syntaxe Mermaid (flowchart, sequenceDiagram, classDiagram, "
        "mindmap, timeline...). Choisis le type le plus adapté, au plus 15 nœuds, libellés courts en français "
        "entre guillemets si nécessaire. Renvoie uniquement du Mermaid valide, sans balises ``` ni directive %%{init}.",
    )
    code = nettoyer_mermaid(str(donnees.get("code_mermaid", "")))
    return code, str(donnees.get("texte_alternatif", "")).strip()[:300], duree


# ---------- Fiche de révision ----------

SCHEMA_FICHE = {
    "type": "object",
    "properties": {
        "titre": {"type": "string"},
        "resume": {"type": "string", "description": "Synthèse en 3 à 5 phrases"},
        "notions": {"type": "array", "items": {"type": "object", "properties": {
            "nom": {"type": "string"}, "explication": {"type": "string"}}, "required": ["nom", "explication"]}},
        "definitions": {"type": "array", "items": {"type": "object", "properties": {
            "terme": {"type": "string"}, "definition": {"type": "string"}}, "required": ["terme", "definition"]}},
        "exemples": {"type": "array", "items": {"type": "string"}},
        "a_retenir": {"type": "array", "items": {"type": "string"}},
        "questions": {"type": "array", "items": {"type": "object", "properties": {
            "question": {"type": "string"}, "reponse": {"type": "string"}}, "required": ["question", "reponse"]}},
    },
    "required": ["titre", "resume", "notions", "a_retenir", "questions"],
}
LIMITES_FICHE = {"notions": 8, "definitions": 10, "exemples": 6, "a_retenir": 8, "questions": 6}


def langue_de_la_session(session):
    """Langue imposée, sinon la plus fréquente des questions (français par défaut)."""
    if session.langue:
        return session.langue
    langues = list(session.messages.filter(role=MessageVocal.Role.UTILISATEUR).exclude(langue="")
                   .values_list("langue", flat=True))
    return max(set(langues), key=langues.count) if langues else "fr"


def transcription_conversation(session, maximum=20000):
    lignes = []
    for m in session.messages.filter(statut=MessageVocal.Statut.PRET).exclude(texte=""):
        lignes.append(f"{'ÉTUDIANT' if m.est_question else 'ASSISTANT'} : {m.texte}")
    texte = "\n\n".join(lignes)
    return texte[-maximum:]  # les échanges les plus récents si la conversation est très longue


def _texte(valeur, maximum):
    return str(valeur or "").strip()[:maximum]


def valider_fiche(donnees):
    """Nettoie et borne la fiche renvoyée par Gemini ; lève ErreurAssistant si elle est vide."""
    fiche = {"titre": _texte(donnees.get("titre"), 150), "resume": _texte(donnees.get("resume"), 1500)}
    paires = {"notions": ("nom", "explication"), "definitions": ("terme", "definition"),
              "questions": ("question", "reponse")}
    for cle, (a, b) in paires.items():
        elements = donnees.get(cle) if isinstance(donnees.get(cle), list) else []
        fiche[cle] = [{a: _texte(e.get(a), 200), b: _texte(e.get(b), 800)} for e in elements
                      if isinstance(e, dict) and _texte(e.get(a), 200) and _texte(e.get(b), 800)][:LIMITES_FICHE[cle]]
    for cle in ("exemples", "a_retenir"):
        elements = donnees.get(cle) if isinstance(donnees.get(cle), list) else []
        fiche[cle] = [_texte(e, 500) for e in elements if _texte(e, 500)][:LIMITES_FICHE[cle]]
    if not fiche["resume"] or not (fiche["notions"] or fiche["a_retenir"]):
        raise ErreurAssistant("La fiche générée est vide. Posez quelques questions de plus puis réessayez.", "vide")
    return fiche


def generer_fiche(session):
    """Renvoie (contenu de la fiche, langue, durée en ms)."""
    langue = langue_de_la_session(session)
    conversation = transcription_conversation(session)
    if not conversation:
        raise ErreurAssistant("La conversation est vide : posez d'abord une question.", "vide")
    cours = f"Cours : « {session.cours.titre} »\n\n" if session.cours_id else ""
    donnees, duree = appeler_gemini_json(
        f"{cours}Conversation à transformer en fiche de révision :\n<<<\n{conversation}\n>>>", SCHEMA_FICHE,
        f"Tu rédiges une fiche de révision claire et fidèle à partir d'une conversation entre un étudiant et un "
        f"assistant pédagogique. Rédige entièrement en {NOMS_POUR_PROMPT.get(langue, 'français')}. N'ajoute que des "
        f"notions abordées dans la conversation. Phrases courtes, sans Markdown ni HTML. Les questions "
        f"d'autoévaluation (3 à 6) portent sur les points essentiels, avec une réponse courte.",
    )
    return valider_fiche(donnees), langue, duree


# ---------- Évaluation de Whisper ----------

_PONCTUATION = str.maketrans({c: " " for c in string.punctuation + "«»“”’…–—"})


def normaliser_transcription(texte):
    return (texte or "").lower().translate(_PONCTUATION).split()


def taux_erreur_mots(reference, hypothese):
    """Word Error Rate = (substitutions + suppressions + insertions) / nombre de mots de la référence."""
    ref, hyp = normaliser_transcription(reference), normaliser_transcription(hypothese)
    if not ref:
        return 0.0 if not hyp else 1.0
    precedente = list(range(len(hyp) + 1))
    for i, mot_ref in enumerate(ref, start=1):
        courante = [i] + [0] * len(hyp)
        for j, mot_hyp in enumerate(hyp, start=1):
            courante[j] = min(precedente[j] + 1, courante[j - 1] + 1, precedente[j - 1] + (mot_ref != mot_hyp))
        precedente = courante
    return precedente[-1] / len(ref)
