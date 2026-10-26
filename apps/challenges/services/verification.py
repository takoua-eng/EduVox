"""Vérification IA de la couverture des exigences dans un livrable (aucune note n'est attribuée).

- Texte réellement analysé : le contenu saisi dans la version + le texte des fichiers TXT/MD (UTF-8) et PDF
  dont le texte est extractible. Les autres formats et les liens externes ne sont PAS analysés (pas de
  récupération d'URL côté serveur : aucun risque SSRF) ; c'est indiqué dans les limites de l'analyse.
- PDF scanné / aucun texte : l'analyse est marquée « non analysable » sans appel à Gemini — ce n'est ni un
  dossier vide ni un dossier non conforme.
- Chaque analyse est liée à UNE version précise et à l'empreinte SHA-256 du texte analysé : elle n'est jamais
  présentée comme valable pour une autre version (ou pour un brouillon modifié depuis).
- Les extraits cités par l'IA sont recherchés dans le texte : un extrait introuvable est signalé, et une
  exigence déclarée couverte sans aucun extrait retrouvé devient « non vérifiable ».
"""

import hashlib
import logging

from ..models import EvenementScenario
from ..stockage import FORMATS_ANALYSABLES
from .gemini import SYSTEME_COMMUN, ErreurIA, appeler_json, bloc_donnees
from .reglages import reglage

logger = logging.getLogger(__name__)

STATUTS = {
    "couverte": "Couverte",
    "partielle": "Partiellement couverte",
    "non_couverte": "Non couverte",
    "non_verifiable": "Non vérifiable",
}
EXIGENCES_MAX = 25
EXTRAITS_MAX = 3
EXTRAIT_LONGUEUR_MAX = 400

SCHEMA_VERIFICATION = {
    "type": "object",
    "properties": {
        "exigences": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "id": {"type": "string"},
                "statut": {"type": "string", "enum": list(STATUTS)},
                "extraits": {"type": "array", "maxItems": EXTRAITS_MAX, "items": {"type": "string"},
                             "description": "citations EXACTES copiées du dossier"},
                "commentaire": {"type": "string"},
            }, "required": ["id", "statut", "extraits", "commentaire"]},
        },
        "incoherences": {"type": "array", "maxItems": 8, "items": {"type": "string"}},
        "ameliorations": {"type": "array", "maxItems": 8, "items": {"type": "string"}},
        "limites": {"type": "array", "maxItems": 6, "items": {"type": "string"}},
    },
    "required": ["exigences", "incoherences", "ameliorations", "limites"],
}


class NonAnalysable(Exception):
    def __init__(self, limites):
        super().__init__("; ".join(limites))
        self.limites = limites


def normaliser(texte):
    """Minuscule, espaces et guillemets/apostrophes typographiques unifiés (pour retrouver un extrait)."""
    texte = (texte or "").casefold()
    for typographique, simple in {"’": "'", "‘": "'", "«": '"', "»": '"', "“": '"', "”": '"', " ": " "}.items():
        texte = texte.replace(typographique, simple)
    return " ".join(texte.split())


# ---------- Exigences ----------

def exigences_de(version):
    """Exigences vérifiables, construites côté serveur (l'IA ne peut pas en inventer)."""
    livrable = version.livrable
    defi = livrable.equipe.defi
    exigences = []

    def ajouter(prefixe, elements, source):
        for i, libelle in enumerate(elements, start=1):
            exigences.append({"id": f"{prefixe}{i}", "libelle": libelle[:300], "source": source})

    if livrable.jalon_id:
        ajouter("J", livrable.jalon.exigences_liste(), f"Jalon « {livrable.jalon.titre} »")
    else:
        ajouter("O", defi.objectifs_liste(), "Objectif du défi")
        ajouter("L", defi.livrables_liste(), "Livrable attendu")
    ajouter("C", defi.contraintes_liste(), "Contrainte du défi")
    nouvelles = defi.evenements.filter(etat=EvenementScenario.Etat.PUBLIE, type_contrainte__in=[
        EvenementScenario.Type.EXIGENCE, EvenementScenario.Type.PUBLIC, EvenementScenario.Type.BUDGET])
    ajouter("I", [f"{e.titre} : {e.description[:200]}" for e in nouvelles.order_by("date_publication")],
            "Imprévu publié")
    return exigences[:EXIGENCES_MAX]


# ---------- Texte analysable ----------

def texte_analysable(version):
    """Renvoie (texte, sources, limites, tronqué). Lève NonAnalysable si aucun texte n'est exploitable."""
    morceaux, sources, limites = [], [], []
    if (version.texte or "").strip():
        morceaux.append(version.texte.strip())
        sources.append("contenu textuel saisi")
    if version.fichier:
        extension = version.extension
        if extension not in FORMATS_ANALYSABLES:
            limites.append(f"Le fichier .{extension} n'est pas analysé (formats analysables : PDF avec texte, TXT, MD).")
        elif extension == "pdf":
            from ai_services.pdf import extraire_texte_pdf

            try:
                morceaux.append(extraire_texte_pdf(version.fichier.path))
                sources.append(f"texte extrait du PDF « {version.nom_fichier} »")
            except ValueError:
                limites.append("Le PDF ne contient pas de texte extractible (document scanné ?) : il n'a pas pu être analysé.")
            except Exception:
                logger.warning("Lecture PDF impossible (version %s)", version.pk)
                limites.append("Le PDF n'a pas pu être lu (fichier endommagé ou protégé).")
        else:
            with version.fichier.open("rb") as fichier:
                contenu = fichier.read().decode("utf-8", errors="replace").strip()
            if contenu:
                morceaux.append(contenu)
                sources.append(f"fichier « {version.nom_fichier} »")
            else:
                limites.append("Le fichier texte est vide.")
    if version.lien:
        limites.append("Le lien externe n'a pas été ouvert : son contenu n'est pas analysé.")
    texte = "\n\n".join(morceaux).strip()
    if not texte:
        raise NonAnalysable(limites or ["Aucun contenu textuel analysable."])
    maximum = reglage("EDUSPRINT_TEXTE_MAX_CARACTERES")
    tronque = len(texte) > maximum
    if tronque:
        texte = texte[:maximum]
        limites.append(f"Le dossier étant long, seuls ses {maximum} premiers caractères ont été analysés.")
    return texte, sources, limites, tronque


def empreinte(texte):
    return hashlib.sha256((texte or "").encode("utf-8")).hexdigest()


# ---------- Validation de la sortie ----------

def _textes(valeurs, maximum, longueur=400):
    if not isinstance(valeurs, list):
        return []
    return [" ".join(v.split())[:longueur] for v in valeurs if isinstance(v, str) and v.strip()][:maximum]


def valider_resultat(donnees, exigences, texte):
    attendues = {e["id"]: e for e in exigences}
    brutes = donnees.get("exigences")
    if not isinstance(brutes, list):
        raise ErreurIA("Analyse IA mal formée (liste des exigences absente).", "sortie_invalide", True)
    texte_normalise = normaliser(texte)
    resultats, vus = {}, set()
    extraits_total = extraits_trouves = 0
    for brute in brutes:
        if not isinstance(brute, dict) or brute.get("id") not in attendues or brute.get("id") in vus:
            continue  # exigence inventée ou en double : ignorée
        vus.add(brute["id"])
        statut = brute.get("statut") if brute.get("statut") in STATUTS else "non_verifiable"
        extraits = []
        for extrait in _textes(brute.get("extraits"), EXTRAITS_MAX, EXTRAIT_LONGUEUR_MAX):
            trouve = normaliser(extrait.strip("…. \"'")) in texte_normalise
            extraits.append({"texte": extrait, "verifie": trouve})
            extraits_total += 1
            extraits_trouves += trouve
        commentaire = brute.get("commentaire") if isinstance(brute.get("commentaire"), str) else ""
        commentaire = " ".join(commentaire.split())[:600]
        if statut in ("couverte", "partielle") and not any(e["verifie"] for e in extraits):
            statut = "non_verifiable"
            commentaire = ("Aucun extrait cité par l'IA n'a été retrouvé dans le dossier : à vérifier manuellement. "
                           + commentaire).strip()
        resultats[brute["id"]] = {**attendues[brute["id"]], "statut": statut, "extraits": extraits,
                                  "commentaire": commentaire}
    if not resultats:
        raise ErreurIA("L'analyse IA ne correspond à aucune exigence du défi.", "sortie_invalide", True)
    for identifiant, exigence in attendues.items():
        if identifiant not in resultats:
            resultats[identifiant] = {**exigence, "statut": "non_verifiable", "extraits": [],
                                      "commentaire": "Exigence non traitée par l'IA."}
    liste = [resultats[e["id"]] for e in exigences]
    return {
        "exigences": liste,
        "incoherences": _textes(donnees.get("incoherences"), 8),
        "ameliorations": _textes(donnees.get("ameliorations"), 8),
        "limites_ia": _textes(donnees.get("limites"), 6),
        "compteurs": {s: sum(1 for e in liste if e["statut"] == s) for s in STATUTS},
        "extraits": {"total": extraits_total, "retrouves": extraits_trouves},
    }


def construire_prompt(exigences, texte):
    liste = "\n".join(f"{e['id']} ({e['source']}) : {e['libelle']}" for e in exigences)
    return (
        "Compare le dossier de l'équipe aux exigences listées. Pour CHAQUE identifiant, indique s'il est couvert, "
        "partiellement couvert, non couvert ou non vérifiable avec le seul texte fourni, et cite jusqu'à 3 extraits "
        "COPIÉS MOT POUR MOT du dossier (jamais reformulés). Signale les incohérences possibles et propose des "
        "améliorations concrètes. N'attribue aucune note.\n\n"
        + bloc_donnees("exigences", liste) + "\n\n" + bloc_donnees("dossier", texte)
    )


def verifier(version):
    """Renvoie (résultat, métadonnées, empreinte). Lève NonAnalysable ou ErreurIA."""
    exigences = exigences_de(version)
    if not exigences:
        raise NonAnalysable(["Le défi ne définit aucune exigence vérifiable pour ce livrable."])
    texte, sources, limites, tronque = texte_analysable(version)
    resultat, meta = appeler_json(construire_prompt(exigences, texte), SCHEMA_VERIFICATION, SYSTEME_COMMUN,
                                  lambda donnees: valider_resultat(donnees, exigences, texte), temperature=0.1)
    resultat.update({"analysable": True, "sources": sources, "limites": limites, "tronque": tronque,
                     "version": version.numero})
    return resultat, meta, empreinte(texte)


def empreinte_actuelle(version):
    """Empreinte du texte qui serait analysé maintenant (None si non analysable)."""
    try:
        return empreinte(texte_analysable(version)[0])
    except NonAnalysable:
        return None


__all__ = ["STATUTS", "NonAnalysable", "exigences_de", "texte_analysable", "valider_resultat", "verifier"]
