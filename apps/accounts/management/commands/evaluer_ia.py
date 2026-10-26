from django.core.management.base import BaseCommand

from apps.accounts.evaluation import lancer_evaluation
from apps.accounts.models import EvaluationIA


class Command(BaseCommand):
    help = ("Évalue les IA du module Utilisateurs (reconnaissance faciale, connexions suspectes, temps de réponse) "
            "et enregistre les résultats. Aucune donnée de test n'est écrite en base.")

    def add_arguments(self, parser):
        parser.add_argument("--images", metavar="DOSSIER",
                            help="Dossier « un sous-dossier par personne » (LFW ou ai_services/donnees_test).")
        parser.add_argument("--max-personnes", type=int, default=50, help="Personnes évaluées au maximum (50).")
        parser.add_argument("--repetitions", type=int, default=100, help="Répétitions pour les temps de réponse (100).")
        parser.add_argument("--seed", type=int, default=42, help="Graine du jeu de connexions simulé (42).")

    def handle(self, *args, **options):
        self.stdout.write("Évaluation des IA en cours…")
        evaluations, messages = lancer_evaluation(
            images=options["images"], max_personnes=options["max_personnes"],
            repetitions=options["repetitions"], seed=options["seed"],
        )
        for evaluation in evaluations:
            self.titre(f"{evaluation.get_type_display()} ({evaluation.duree_totale_ms} ms)")
            r = evaluation.resultats
            self.stdout.write(r["source"])
            if evaluation.type == EvaluationIA.Type.TEMPS:
                self.afficher_temps(r)
            elif evaluation.type == EvaluationIA.Type.CONNEXIONS:
                self.afficher_connexions(r)
            else:
                self.afficher_visage(r)
        for message in messages:
            self.stdout.write(self.style.WARNING(f"\nAttention : {message}"))
        self.stdout.write(self.style.SUCCESS(f"\n{len(evaluations)} évaluation(s) enregistrée(s)."))

    # ---------- Mise en forme ----------

    def titre(self, texte):
        self.stdout.write("\n" + self.style.MIGRATE_HEADING(texte))
        self.stdout.write("-" * len(texte))

    def tableau(self, entetes, lignes):
        largeurs = [max(len(str(x)) for x in colonne) for colonne in zip(entetes, *lignes)]
        for ligne in [entetes, ["-" * l for l in largeurs], *lignes]:
            premiere, *autres = [(str(x), l) for x, l in zip(ligne, largeurs)]
            self.stdout.write("  ".join([premiere[0].ljust(premiere[1]), *(x.rjust(l) for x, l in autres)]))

    def metriques(self, m, positif, negatif, visage=False):
        mc = m["matrice"]
        self.tableau(["", f"Prédit {positif}", f"Prédit {negatif}"], [
            [f"Réel {positif}", f"VP = {mc['VP']}", f"FN = {mc['FN']}"],
            [f"Réel {negatif}", f"FP = {mc['FP']}", f"VN = {mc['VN']}"],
        ])
        texte = (f"Précision {m['precision']} %  ·  Rappel {m['rappel']} %  ·  F1 {m['f1']} %  ·  "
                 f"Exactitude {m['exactitude']} %")
        if visage:
            texte += f"  ·  FAR {m['far']} %  ·  FRR {m['frr']} %"
        self.stdout.write(self.style.SUCCESS(texte))

    def afficher_visage(self, r):
        if r.get("avertissement"):
            self.stdout.write(self.style.WARNING(r["avertissement"]))
        if "nb_ignorees" in r:
            details = ", ".join(f"{motif} : {nb}" for motif, nb in r["ignorees_par_motif"].items())
            self.stdout.write(f"Photos ignorées : {r['nb_ignorees']}" + (f" ({details})" if details else ""))
        self.stdout.write(f"Paires même personne : {r['nb_paires_meme']} (similarité moyenne {r['score_moyen_meme']})"
                          f"  ·  personnes différentes : {r['nb_paires_differentes']} "
                          f"(similarité moyenne {r['score_moyen_differentes']})")
        self.stdout.write(f"\nVérification 1:1 au seuil {r['seuil']} :")
        self.metriques(r["verification"], r["libelle_positif"], r["libelle_negatif"], visage=True)
        self.stdout.write("\nComparaison des seuils :")
        self.tableau(["Seuil", "FAR %", "FRR %", "Précision %", "Rappel %", "F1 %"],
                     [[l["seuil"], l["far"], l["frr"], l["precision"], l["rappel"], l["f1"]] for l in r["seuils"]])
        if r["eer"]:
            self.stdout.write(f"EER (FAR = FRR) : {r['eer']['taux']} % au seuil {r['eer']['seuil']}")
        if "identification" in r:
            i = r["identification"]
            self.stdout.write(f"\nIdentification 1:N ({r['nb_photos_test']} photos, seuil {i['seuil']}, "
                              f"marge {i['marge']}) : {i['taux_correct']} % des personnes connues retrouvées")
            self.metriques(i, "accepté", "refusé", visage=True)
            self.stdout.write("\nIdentification 1:N selon le seuil :")
            self.tableau(["Seuil", "Retrouvées %", "Confondues %", "Inconnus acceptés %"],
                         [[l["seuil"], l["taux_correct"], l["taux_confusions"], l["taux_inconnus_acceptes"]]
                          for l in r["identification_par_seuil"]])

    def afficher_connexions(self, r):
        self.stdout.write(f"Suspecte si score de risque >= {r['seuil']}")
        self.metriques(r["metriques"], r["libelle_positif"].lower(), r["libelle_negatif"].lower())
        self.stdout.write("\nDétail par scénario :")
        self.tableau(["Scénario", "Attendu", "Cas", "Corrects %", "Score moyen"],
                     [[l["scenario"], l["attendu"], l["nb"], l["taux_correct"], l["score_moyen"]]
                      for l in r["par_scenario"]])

    def afficher_temps(self, r):
        lignes = []
        for cle, libelle in [("detection_encodage", "Détection + encodage d'un visage"),
                             ("comparaison_1n", f"Comparaison 1:N ({r['taille_galerie']} profils)"),
                             ("evaluer_risque_sans_cache", "Score de risque, sans pré-calcul"),
                             ("evaluer_risque_apres_precalcul", "Score de risque, après pré-calcul")]:
            s = r.get(cle)
            valeurs = [s[k] for k in ("moyenne", "mediane", "p95")] if s else ["-"] * 3
            lignes.append([libelle, *valeurs])
        self.tableau(["Traitement", "Moyenne ms", "Médiane ms", "p95 ms"], lignes)
        if r.get("note_detection"):
            self.stdout.write(self.style.WARNING(r["note_detection"]))
