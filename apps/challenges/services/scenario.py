"""Client virtuel : proposition d'un imprévu réaliste à partir du défi et de son avancement.

L'IA ne reçoit que des données agrégées et anonymes (aucun nom d'étudiant ni d'équipe) et ne modifie rien :
sa proposition devient un imprévu BROUILLON (origine IA) que l'enseignant relit, valide puis publie.
Contrôles serveur : type autorisé, longueurs, listes non vides, faisabilité (pas de réduction de budget sans
budget) et différence avec les imprévus précédents (titre trop proche refusé).
"""

from difflib import SequenceMatcher

from django.db.models import Count, Q
from django.utils import timezone

from ..models import EvenementScenario, Equipe, Tache
from .gemini import SYSTEME_COMMUN, ErreurIA, appeler_json, bloc_donnees

TYPES_IA = [EvenementScenario.Type.BUDGET, EvenementScenario.Type.EXIGENCE, EvenementScenario.Type.OUTIL,
            EvenementScenario.Type.PUBLIC, EvenementScenario.Type.DELAI]
SIMILARITE_MAX = 0.8

_LISTE = {"type": "array", "minItems": 1, "maxItems": 6, "items": {"type": "string"}}
SCHEMA_EVENEMENT = {
    "type": "object",
    "properties": {
        "titre": {"type": "string"},
        "type": {"type": "string", "enum": [str(t) for t in TYPES_IA]},
        "description": {"type": "string"},
        "justification_pedagogique": {"type": "string"},
        "impacts_attendus": _LISTE,
        "elements_attendus": _LISTE,
    },
    "required": ["titre", "type", "description", "justification_pedagogique", "impacts_attendus", "elements_attendus"],
}


def _normaliser(texte):
    return " ".join((texte or "").casefold().split())


def _invalide(message):
    return ErreurIA(f"Imprévu proposé inexploitable : {message}", "sortie_invalide", True)


def _liste(valeurs, cle):
    if not isinstance(valeurs, list):
        raise _invalide(f"« {cle} » doit être une liste.")
    propres = [" ".join(v.split()) for v in valeurs if isinstance(v, str) and 3 <= len(v.strip()) <= 300]
    if not 1 <= len(propres) <= 6 or len(propres) != len(valeurs):
        raise _invalide(f"« {cle} » contient des éléments invalides.")
    return propres


def valider_evenement(donnees, defi, precedents):
    type_ = donnees.get("type")
    if type_ not in [str(t) for t in TYPES_IA]:
        raise _invalide("type de contrainte inconnu.")
    resultat = {"type": type_}
    for cle, (mini, maxi) in {"titre": (5, 200), "description": (30, 3000), "justification_pedagogique": (20, 2000)}.items():
        valeur = donnees.get(cle)
        if not isinstance(valeur, str) or not mini <= len(valeur.strip()) <= maxi:
            raise _invalide(f"« {cle} » manquant ou de longueur invalide.")
        resultat[cle] = valeur.strip()
    resultat["impacts_attendus"] = _liste(donnees.get("impacts_attendus"), "impacts_attendus")
    resultat["elements_attendus"] = _liste(donnees.get("elements_attendus"), "elements_attendus")

    if type_ == EvenementScenario.Type.BUDGET and defi.budget is None:
        raise _invalide("réduction de budget proposée alors que le défi n'a pas de budget.")
    titre = _normaliser(resultat["titre"])
    for ancien in precedents:
        if SequenceMatcher(None, titre, _normaliser(ancien["titre"])).ratio() >= SIMILARITE_MAX:
            raise _invalide("trop proche d'un imprévu existant.")
    return resultat


def contexte_anonyme(defi):
    """Avancement agrégé du défi : aucune donnée personnelle."""
    maintenant = timezone.now()
    equipes = Equipe.objects.filter(defi=defi, statut=Equipe.Statut.ACTIVE)
    taches = Tache.objects.filter(equipe__in=equipes).aggregate(
        total=Count("pk"), terminees=Count("pk", filter=Q(statut=Tache.Statut.TERMINE)))
    jalons = [f"- {j.titre} (échéance {'passée' if j.echeance < maintenant else 'à venir'} : "
              f"{timezone.localtime(j.echeance):%d/%m/%Y})" for j in defi.jalons.all()]
    duree = max(1, (defi.date_fin - defi.date_debut).days)
    ecoule = min(100, max(0, round((maintenant - defi.date_debut).total_seconds() / ((defi.date_fin - defi.date_debut).total_seconds()) * 100)))
    lignes = [
        f"Titre : {defi.titre}",
        f"Contexte : {defi.contexte}",
        "Objectifs :\n" + "\n".join(f"- {o}" for o in defi.objectifs_liste()),
        "Livrables :\n" + "\n".join(f"- {l}" for l in defi.livrables_liste()),
        "Contraintes :\n" + ("\n".join(f"- {c}" for c in defi.contraintes_liste()) or "- aucune"),
        f"Budget : {f'{defi.budget} DT' if defi.budget is not None else 'aucun'}",
        f"Durée : {duree} jours, avancement temporel {ecoule} %",
        "Jalons :\n" + ("\n".join(jalons) or "- aucun"),
        f"Équipes actives : {equipes.count()} ; tâches terminées : {taches['terminees']}/{taches['total']}",
    ]
    return "\n".join(lignes)


def construire_prompt(defi, precedents, consigne=""):
    return prompt_depuis_contexte(contexte_anonyme(defi), precedents, consigne)


def prompt_depuis_contexte(contexte, precedents, consigne=""):
    """Prompt à partir d'un contexte texte déjà anonymisé (utilisé aussi par le protocole d'évaluation)."""
    anciens = "\n".join(f"- [{p['type_contrainte']}] {p['titre']}" for p in precedents) or "- aucun"
    return (
        "Joue le client de cette mission et propose UN imprévu réaliste et réalisable dans le temps restant, "
        "pertinent pour les objectifs pédagogiques, et différent des imprévus déjà proposés (privilégie un autre "
        "type). N'impose aucune modification de dates, de budget ou de tâches : décris la situation, l'enseignant "
        "décidera. Précise les éléments que les équipes devront fournir dans leur réponse d'adaptation.\n\n"
        + bloc_donnees("defi", contexte) + "\n\n" + bloc_donnees("imprevus_precedents", anciens)
        + ("\n\n" + bloc_donnees("souhait_enseignant", consigne[:500]) if consigne else "")
    )


def proposer_evenement(defi, consigne=""):
    precedents = list(defi.evenements.values("titre", "type_contrainte"))
    return appeler_json(construire_prompt(defi, precedents, consigne), SCHEMA_EVENEMENT, SYSTEME_COMMUN,
                        lambda donnees: valider_evenement(donnees, defi, precedents), temperature=0.8)
