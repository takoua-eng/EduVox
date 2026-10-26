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
from statistics import median

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from markdown_it import MarkdownIt

from ai_services.gemini import MODELE_PAR_DEFAUT

from .models import Illustration, JournalIA, MessageVocal
from .permissions import cours_utilisables
from .reglages import reglage

logger = logging.getLogger(__name__)

SYSTEME = """Tu es l'assistant pédagogique d'EduVox, une plateforme d'apprentissage.
- Réponds dans la langue de la question (français par défaut), avec un ton bienveillant et clair.
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
    """Échec affichable à l'utilisateur ; `code` alimente les statistiques."""

    def __init__(self, message, code="service"):
        super().__init__(message)
        self.message = message
        self.code = code


def rendre_markdown(texte):
    """Markdown → HTML sûr : le HTML brut est désactivé et les liens javascript: refusés par markdown-it."""
    return _markdown.render(texte or "")


def modele_gemini():
    return os.environ.get("GEMINI_MODEL") or MODELE_PAR_DEFAUT


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
    return genai.Client(api_key=cle,
                        http_options=types.HttpOptions(timeout=reglage("ASSISTANT_GEMINI_TIMEOUT_S") * 1000))


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

def contexte_cours(session):
    """Texte du cours rattaché (résumé, sinon transcription), tronqué ; vide si le cours n'est plus accessible."""
    cours = session.cours
    if cours is None or not cours_utilisables(session.utilisateur).filter(pk=cours.pk).exists():
        return ""
    texte = ""
    for relation in ("resume", "transcription"):
        objet = getattr(cours, relation, None)  # RelatedObjectDoesNotExist hérite d'AttributeError
        if objet is not None and objet.texte:
            texte = objet.texte
            break
    texte = texte[:reglage("ASSISTANT_CONTEXTE_MAX_CARACTERES")]
    return f"Cours « {cours.titre} » :\n{texte}" if texte else f"Cours « {cours.titre} » (contenu non disponible)."


def instruction_systeme(session):
    contexte = contexte_cours(session)
    if not contexte:
        return SYSTEME
    return f"{SYSTEME}\n\nContexte du cours choisi par l'étudiant (à utiliser en priorité) :\n<<<\n{contexte}\n>>>"


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


def configuration_reponse(session):
    from google.genai import types

    return types.GenerateContentConfig(system_instruction=instruction_systeme(session), temperature=0.4,
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
    texte = (texte or "").strip()
    maximum = reglage("ASSISTANT_QUESTION_MAX_CARACTERES")
    if len(texte) > maximum:
        raise ErreurAssistant(f"La question est trop longue ({maximum} caractères au maximum).", "validation")

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
            if not texte:
                raise ErreurAssistant("Écrivez ou dictez une question.", "validation")
            verifier_quota_questions(session.utilisateur)
            question = MessageVocal.objects.create(session=session, role=MessageVocal.Role.UTILISATEUR, texte=texte)
        reponse = MessageVocal.objects.create(session=session, role=MessageVocal.Role.ASSISTANT,
                                              statut=MessageVocal.Statut.EN_ATTENTE, modele=modele_gemini())
        session.save(update_fields=["date_maj"])
    return question, reponse


def terminer_reponse(reponse, texte, duree_ms, premier_morceau_ms=None, interrompue=False):
    texte = texte.strip()
    if interrompue:
        texte = (texte + "\n\n*(Réponse interrompue.)*").strip()
    reponse.texte = texte
    reponse.statut = MessageVocal.Statut.PRET
    reponse.duree_ms, reponse.premier_morceau_ms = duree_ms, premier_morceau_ms
    reponse.save(update_fields=["texte", "statut", "duree_ms", "premier_morceau_ms"])
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
        resultat = client.models.generate_content(
            model=reponse.modele, contents=construire_contenus(question),
            config=configuration_reponse(question.session),
        )
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
    flux = await client.aio.models.generate_content_stream(model=reponse.modele, contents=contenus,
                                                              config=configuration)
    async for morceau in flux:
        if morceau.text:
            yield morceau.text


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
    for essai in range(1, essais + 1):
        try:
            client = _client()  # garder une référence : le SDK ferme sa connexion quand le client est libéré
            resultat = client.models.generate_content(model=modele_gemini(), contents=prompt, config=config)
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


# ---------- Métriques ----------

def centile(valeurs, p):
    valeurs = sorted(v for v in valeurs if v is not None)
    if not valeurs:
        return None
    rang = (len(valeurs) - 1) * p / 100
    bas = int(rang)
    haut = min(bas + 1, len(valeurs) - 1)
    return round(valeurs[bas] + (valeurs[haut] - valeurs[bas]) * (rang - bas))


def metriques(jours=30):
    depuis = timezone.now() - timedelta(days=jours)
    appels = JournalIA.objects.filter(date__gte=depuis)
    par_type = []
    for valeur, libelle in JournalIA.Type.choices:
        lignes = list(appels.filter(type_appel=valeur).values("succes", "code", "duree_ms", "premier_morceau_ms"))
        reussis = [ligne for ligne in lignes if ligne["succes"]]
        durees = [ligne["duree_ms"] for ligne in reussis]
        premiers = [ligne["premier_morceau_ms"] for ligne in reussis if ligne["premier_morceau_ms"]]
        erreurs = {}
        for ligne in lignes:
            if not ligne["succes"]:
                erreurs[ligne["code"]] = erreurs.get(ligne["code"], 0) + 1
        par_type.append({
            "type": valeur, "libelle": libelle, "total": len(lignes), "reussis": len(reussis),
            "taux_succes": round(100 * len(reussis) / len(lignes)) if lignes else None,
            "mediane_ms": round(median(durees)) if durees else None, "p95_ms": centile(durees, 95),
            "premier_morceau_ms": round(median(premiers)) if premiers else None,
            "erreurs": sorted(erreurs.items(), key=lambda e: -e[1]),
        })

    reponses = MessageVocal.objects.filter(role=MessageVocal.Role.ASSISTANT, date__gte=depuis)
    avis = reponses.aggregate(utiles=Count("pk", filter=Q(evaluation=1)), inutiles=Count("pk", filter=Q(evaluation=-1)))
    evaluees = avis["utiles"] + avis["inutiles"]

    vocaux = MessageVocal.objects.filter(mode=MessageVocal.Mode.VOCAL, statut=MessageVocal.Statut.PRET,
                                         date__gte=depuis, duree_audio_s__gt=0, duree_ms__isnull=False)
    facteurs = [m.duree_ms / 1000 / m.duree_audio_s for m in vocaux.only("duree_ms", "duree_audio_s")]

    questions = MessageVocal.objects.filter(role=MessageVocal.Role.UTILISATEUR, date__gte=depuis)
    return {
        "jours": jours,
        "par_type": par_type,
        "satisfaction": round(100 * avis["utiles"] / evaluees) if evaluees else None,
        "avis_utiles": avis["utiles"], "avis_inutiles": avis["inutiles"],
        "taux_avis": round(100 * evaluees / reponses.count()) if reponses.exists() else None,
        "facteur_temps_reel": round(median(facteurs), 2) if facteurs else None,
        "questions_ecrites": questions.filter(mode=MessageVocal.Mode.TEXTE).count(),
        "questions_orales": questions.filter(mode=MessageVocal.Mode.VOCAL).count(),
        "illustrations": Illustration.objects.filter(date__gte=depuis).values("type_illustration", "statut")
        .annotate(n=Count("pk")).order_by(),
    }


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
