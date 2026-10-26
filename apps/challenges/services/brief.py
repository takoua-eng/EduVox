"""Génération d'un brief de mission par Gemini (le résultat reste TOUJOURS un brouillon à relire).

1. Les paramètres saisis (thème, niveau, durée, taille d'équipe, contraintes) sont envoyés comme données.
2. Gemini répond selon SCHEMA_BRIEF ; `valider_brief` revérifie types, longueurs, quantités et cohérences.
3. `convertir_dates` transforme les délais relatifs (« J+2 ») en dates réelles à partir du début choisi.
Aucune publication automatique : l'enseignant relit l'aperçu modifiable puis enregistre un défi brouillon.
"""

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from ..models import Defi
from .gemini import SYSTEME_COMMUN, ErreurIA, appeler_json, bloc_donnees

LIMITES = {
    "titre": (10, 200), "description": (20, 1000), "contexte": (50, 3000), "element": (3, 300),
    "jalon_titre": (3, 200), "jalon_description": (0, 1000),
}
QUANTITES = {"objectifs": (2, 8), "competences": (1, 8), "contraintes": (0, 8), "livrables": (1, 8),
             "jalons": (1, 6), "exigences": (1, 6)}
BUDGET_MAX = Decimal("1000000")


def _liste(nom, mini, maxi):
    return {"type": "array", "minItems": mini, "maxItems": maxi,
            "items": {"type": "string"},
            "description": nom}


SCHEMA_BRIEF = {
    "type": "object",
    "properties": {
        "titre": {"type": "string"},
        "description": {"type": "string", "description": "accroche de la mission"},
        "contexte": {"type": "string", "description": "situation du client"},
        "objectifs": _liste("objectifs mesurables", *QUANTITES["objectifs"]),
        "competences": _liste("compétences visées", *QUANTITES["competences"]),
        "contraintes": _liste("contraintes du client", *QUANTITES["contraintes"]),
        "avec_budget": {"type": "boolean", "description": "vrai si un budget est pertinent pour la mission"},
        "budget_dt": {"type": "number", "minimum": 0, "description": "budget en dinars tunisiens (0 si avec_budget est faux)"},
        "jalons": {
            "type": "array", "minItems": 1, "maxItems": 6,
            "items": {"type": "object", "properties": {
                "titre": {"type": "string"},
                "description": {"type": "string"},
                "delai_jours": {"type": "integer", "minimum": 1, "description": "jours après le début du défi"},
                "exigences": _liste("exigences vérifiables du jalon", *QUANTITES["exigences"]),
            }, "required": ["titre", "description", "delai_jours", "exigences"]},
        },
        "livrables": _liste("livrables attendus", *QUANTITES["livrables"]),
    },
    "required": ["titre", "description", "contexte", "objectifs", "competences", "contraintes", "avec_budget",
                 "budget_dt", "jalons", "livrables"],
}


def _invalide(message):
    return ErreurIA(f"Brief proposé incohérent : {message}", "sortie_invalide", True)


def _texte(donnees, cle, limites):
    valeur = donnees.get(cle)
    if not isinstance(valeur, str):
        raise _invalide(f"« {cle} » manquant.")
    valeur = " ".join(valeur.split()) if cle in ("titre",) else valeur.strip()
    mini, maxi = limites
    if not mini <= len(valeur) <= maxi:
        raise _invalide(f"longueur de « {cle} » hors limites ({mini}–{maxi}).")
    return valeur


def _textes(valeurs, cle):
    mini, maxi = QUANTITES[cle]
    if not isinstance(valeurs, list):
        raise _invalide(f"« {cle} » doit être une liste.")
    propres, vus = [], set()
    for valeur in valeurs:
        if not isinstance(valeur, str):
            raise _invalide(f"élément non textuel dans « {cle} ».")
        valeur = " ".join(valeur.split())
        if not LIMITES["element"][0] <= len(valeur) <= LIMITES["element"][1]:
            raise _invalide(f"élément de longueur invalide dans « {cle} ».")
        if valeur.casefold() not in vus:
            vus.add(valeur.casefold())
            propres.append(valeur)
    if not mini <= len(propres) <= maxi:
        raise _invalide(f"« {cle} » doit contenir entre {mini} et {maxi} éléments distincts.")
    return propres


def valider_brief(donnees, duree_jours):
    resultat = {
        "titre": _texte(donnees, "titre", LIMITES["titre"]),
        "description": _texte(donnees, "description", LIMITES["description"]),
        "contexte": _texte(donnees, "contexte", LIMITES["contexte"]),
        "objectifs": _textes(donnees.get("objectifs"), "objectifs"),
        "competences": _textes(donnees.get("competences"), "competences"),
        "contraintes": _textes(donnees.get("contraintes", []), "contraintes"),
        "livrables": _textes(donnees.get("livrables"), "livrables"),
    }
    avec_budget = donnees.get("avec_budget")
    if not isinstance(avec_budget, bool):
        raise _invalide("« avec_budget » doit être un booléen.")
    budget = donnees.get("budget_dt")
    if not avec_budget:
        resultat["budget"] = None
    else:
        if isinstance(budget, bool) or not isinstance(budget, (int, float)):
            raise _invalide("budget non numérique.")
        try:
            budget = Decimal(str(budget)).quantize(Decimal("0.01"))
        except InvalidOperation:
            raise _invalide("budget illisible.")
        if not Decimal("0") <= budget <= BUDGET_MAX:
            raise _invalide("budget hors limites.")
        resultat["budget"] = str(budget)

    jalons = donnees.get("jalons")
    mini, maxi = QUANTITES["jalons"]
    if not isinstance(jalons, list) or not mini <= len(jalons) <= maxi:
        raise _invalide(f"il faut entre {mini} et {maxi} jalons.")
    propres = []
    for jalon in jalons:
        if not isinstance(jalon, dict):
            raise _invalide("jalon mal formé.")
        delai = jalon.get("delai_jours")
        if isinstance(delai, bool) or not isinstance(delai, int) or not 1 <= delai <= duree_jours:
            raise _invalide(f"délai de jalon hors de la durée du défi (1 à {duree_jours} jours).")
        propres.append({
            "titre": _texte(jalon, "titre", LIMITES["jalon_titre"]),
            "description": (jalon.get("description") or "").strip()[:LIMITES["jalon_description"][1]]
            if isinstance(jalon.get("description", ""), str) else "",
            "delai_jours": delai,
            "exigences": _textes(jalon.get("exigences"), "exigences"),
        })
    delais = [j["delai_jours"] for j in propres]
    if delais != sorted(delais):
        raise _invalide("les jalons ne sont pas dans l'ordre chronologique.")
    if delais[-1] != duree_jours:
        # Le dernier jalon correspond à la remise finale : on l'aligne sur la fin du défi (cohérence)
        propres[-1]["delai_jours"] = duree_jours
    resultat["jalons"] = propres
    return resultat


def construire_prompt(parametres):
    niveau = dict(Defi.Niveau.choices).get(parametres["niveau"], parametres["niveau"])
    lignes = [
        f"Thème : {parametres['theme']}",
        f"Niveau des étudiants : {niveau}",
        f"Durée du défi : {parametres['duree_jours']} jours",
        f"Taille des équipes : {parametres['taille_min']} à {parametres['taille_max']} étudiants",
        f"Contraintes souhaitées par l'enseignant : {parametres.get('contraintes') or 'aucune'}",
    ]
    return (
        "Propose le brief d'une mission professionnelle réaliste réalisée en équipe, faisable dans la durée "
        "indiquée par des étudiants du niveau indiqué. Les objectifs et exigences doivent être vérifiables dans "
        "un dossier écrit. Les jalons sont donnés en jours après le début (delai_jours croissants, le dernier "
        "égal à la durée). Si un budget est pertinent, mets avec_budget à vrai et indique-le en dinars tunisiens "
        "(budget_dt) ; sinon avec_budget à faux et budget_dt à 0.\n\n"
        + bloc_donnees("parametres", "\n".join(lignes))
    )


def generer_brief(parametres):
    """Renvoie (brief validé, métadonnées). Lève ErreurIA."""
    duree = int(parametres["duree_jours"])
    return appeler_json(construire_prompt(parametres), SCHEMA_BRIEF, SYSTEME_COMMUN,
                        lambda donnees: valider_brief(donnees, duree), temperature=0.7)


def convertir_dates(brief, date_debut, duree_jours):
    """Délais relatifs → échéances réelles (même heure que le début, dernière = fin du défi)."""
    date_fin = date_debut + timedelta(days=duree_jours)
    jalons = []
    for ordre, jalon in enumerate(brief["jalons"], start=1):
        echeance = min(date_debut + timedelta(days=jalon["delai_jours"]), date_fin)
        jalons.append({**jalon, "ordre": ordre, "echeance": echeance})
    return date_fin, jalons
