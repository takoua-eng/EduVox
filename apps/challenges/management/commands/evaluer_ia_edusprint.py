"""Protocole d'évaluation reproductible de l'IA EduSprint.

    python manage.py evaluer_ia_edusprint --stats                # mesures réelles déjà en base (lecture seule)
    python manage.py evaluer_ia_edusprint --plan                 # affiche le jeu de cas, sans appel
    python manage.py evaluer_ia_edusprint --reel --sortie rapport.json [--limite 3]   # appels Gemini PAYANTS

Mesures automatiques : taux de JSON valide (sortie acceptée par la validation serveur), temps de réponse
(médiane, p95), taux et codes d'échec, diversité des imprévus (types distincts, similarité des titres),
exactitude de la couverture des exigences par rapport aux annotations manuelles, fidélité des extraits.
Mesures humaines : pertinence et faisabilité des briefs et imprévus → grille exportée à remplir.
"""

import json
import statistics
from difflib import SequenceMatcher
from pathlib import Path
from types import SimpleNamespace

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from apps.challenges.models import DemandeIA
from apps.challenges.services.brief import SCHEMA_BRIEF, construire_prompt as prompt_brief, valider_brief
from apps.challenges.services.gemini import SYSTEME_COMMUN, ErreurIA, appeler_json, gemini_configure
from apps.challenges.services.scenario import SCHEMA_EVENEMENT, prompt_depuis_contexte, valider_evenement
from apps.challenges.services.verification import SCHEMA_VERIFICATION, construire_prompt as prompt_verif, valider_resultat

CAS = Path(__file__).resolve().parents[2] / "evaluation" / "cas.json"


def percentile(valeurs, p):
    if not valeurs:
        return None
    valeurs = sorted(valeurs)
    return valeurs[min(len(valeurs) - 1, round(p / 100 * (len(valeurs) - 1)))]


def resume_durees(durees):
    return {"n": len(durees), "mediane_ms": statistics.median(durees) if durees else None, "p95_ms": percentile(durees, 95)}


class Command(BaseCommand):
    help = "Évalue l'IA EduSprint sur un jeu de cas annotés (appels réels uniquement avec --reel)."

    def add_arguments(self, parser):
        parser.add_argument("--stats", action="store_true", help="Mesures issues des demandes IA réelles en base")
        parser.add_argument("--plan", action="store_true", help="Affiche le protocole et le jeu de cas")
        parser.add_argument("--reel", action="store_true", help="Exécute les cas avec la vraie API Gemini (payant)")
        parser.add_argument("--cas", default=str(CAS), help="Fichier de cas JSON")
        parser.add_argument("--limite", type=int, default=0, help="Nombre maximal de cas par famille")
        parser.add_argument("--sortie", default="", help="Fichier JSON du rapport")

    def handle(self, *args, **options):
        if options["stats"]:
            return self.stats()
        cas = json.loads(Path(options["cas"]).read_text(encoding="utf-8"))
        if options["limite"]:
            cas = {k: (v[:options["limite"]] if isinstance(v, list) else v) for k, v in cas.items()}
        if not options["reel"]:
            self.stdout.write("Protocole (aucun appel effectué) :")
            for famille in ("briefs", "evenements", "dossiers"):
                self.stdout.write(f"- {famille} : {len(cas[famille])} cas ({', '.join(c['id'] for c in cas[famille])})")
            self.stdout.write("Relancez avec --reel pour mesurer avec la vraie API (appels facturés).")
            return
        if not gemini_configure():
            raise CommandError("GEMINI_API_KEY absente : impossible de mesurer avec la vraie API.")
        rapport = {"briefs": self.briefs(cas["briefs"]), "evenements": self.evenements(cas["evenements"]),
                   "dossiers": self.dossiers(cas["dossiers"])}
        rapport["avertissement"] = "Mesures obtenues avec la vraie API sur ce jeu de cas ; pertinence/faisabilité à noter à la main."
        texte = json.dumps(rapport, ensure_ascii=False, indent=2, default=str)
        if options["sortie"]:
            Path(options["sortie"]).write_text(texte, encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"Rapport écrit dans {options['sortie']}"))
        self.stdout.write(json.dumps({k: v.get("synthese") for k, v in rapport.items() if isinstance(v, dict)},
                                     ensure_ascii=False, indent=2))

    # ---------- Mesures réelles en base ----------
    def stats(self):
        resultat = {}
        for type_demande, libelle in DemandeIA.Type.choices:
            demandes = DemandeIA.objects.filter(type_demande=type_demande).exclude(statut__in=DemandeIA.ACTIFS)
            total = demandes.count()
            durees = list(demandes.filter(duree_ms__isnull=False, statut="termine").values_list("duree_ms", flat=True))
            resultat[libelle] = {
                "terminees": demandes.filter(statut="termine").count(), "echouees": demandes.filter(statut="echoue").count(),
                "total": total, "codes": dict(demandes.exclude(code_erreur="").values_list("code_erreur").annotate(n=Count("pk"))),
                **resume_durees(durees),
            }
        self.stdout.write(json.dumps(resultat, ensure_ascii=False, indent=2))
        if not any(v["total"] for v in resultat.values()):
            self.stdout.write("Aucune demande IA terminée en base : aucune mesure réelle disponible.")

    # ---------- Exécution des cas ----------
    def executer(self, prompt, schema, valider, temperature):
        try:
            resultat, meta = appeler_json(prompt, schema, SYSTEME_COMMUN, valider, temperature=temperature)
            return {"ok": True, "resultat": resultat, "duree_ms": meta["duree_ms"], "essais": meta["essais"]}
        except ErreurIA as erreur:
            return {"ok": False, "code": erreur.code, "message": erreur.message, "duree_ms": erreur.duree_ms,
                    "essais": erreur.essais}

    def synthese(self, resultats):
        valides = [r for r in resultats if r["ok"]]
        codes = {}
        for r in resultats:
            if not r["ok"]:
                codes[r["code"]] = codes.get(r["code"], 0) + 1
        return {"cas": len(resultats), "json_valide": len(valides),
                "taux_json_valide": round(len(valides) / len(resultats), 3) if resultats else None,
                "echecs": codes, **resume_durees([r["duree_ms"] for r in resultats if r.get("duree_ms") is not None])}

    def briefs(self, cas):
        resultats = []
        for c in cas:
            r = self.executer(prompt_brief(c), SCHEMA_BRIEF, lambda d, c=c: valider_brief(d, c["duree_jours"]), 0.7)
            r.update({"id": c["id"], "grille_humaine": {"pertinence_1_5": None, "faisabilite_1_5": None, "commentaire": ""}})
            resultats.append(r)
        return {"synthese": self.synthese(resultats), "cas": resultats}

    def evenements(self, cas):
        resultats = []
        for c in cas:
            defi = SimpleNamespace(budget=c["budget"])
            r = self.executer(prompt_depuis_contexte(c["contexte"], c["precedents"]), SCHEMA_EVENEMENT,
                              lambda d, defi=defi, c=c: valider_evenement(d, defi, c["precedents"]), 0.8)
            r.update({"id": c["id"], "attendu": c["attendu"],
                      "grille_humaine": {"coherence_1_5": None, "faisabilite_1_5": None, "commentaire": ""}})
            resultats.append(r)
        titres = [r["resultat"]["titre"] for r in resultats if r["ok"]]
        similarites = [SequenceMatcher(None, a.casefold(), b.casefold()).ratio()
                       for i, a in enumerate(titres) for b in titres[i + 1:]]
        synthese = self.synthese(resultats)
        synthese.update({"types_distincts": len({r["resultat"]["type"] for r in resultats if r["ok"]}),
                         "similarite_max_titres": round(max(similarites), 3) if similarites else None})
        return {"synthese": synthese, "cas": resultats}

    def dossiers(self, cas):
        resultats, correct, total, binaires, extraits_total, extraits_ok = [], 0, 0, 0, 0, 0
        for c in cas:
            r = self.executer(prompt_verif(c["exigences"], c["texte"]), SCHEMA_VERIFICATION,
                              lambda d, c=c: valider_resultat(d, c["exigences"], c["texte"]), 0.1)
            r["id"] = c["id"]
            if r["ok"]:
                obtenus = {e["id"]: e["statut"] for e in r["resultat"]["exigences"]}
                comparaison = {i: {"attendu": a, "obtenu": obtenus.get(i)} for i, a in c["attendu"].items()}
                r["comparaison"] = comparaison
                total += len(comparaison)
                correct += sum(v["attendu"] == v["obtenu"] for v in comparaison.values())
                binaires += sum((v["attendu"] == "couverte") == (v["obtenu"] == "couverte") for v in comparaison.values())
                extraits_total += r["resultat"]["extraits"]["total"]
                extraits_ok += r["resultat"]["extraits"]["retrouves"]
            resultats.append(r)
        synthese = self.synthese(resultats)
        synthese.update({
            "exactitude_statut_exact": round(correct / total, 3) if total else None,
            "exactitude_couvert_ou_non": round(binaires / total, 3) if total else None,
            "fidelite_extraits": round(extraits_ok / extraits_total, 3) if extraits_total else None,
            "exigences_comparees": total,
        })
        return {"synthese": synthese, "cas": resultats}
