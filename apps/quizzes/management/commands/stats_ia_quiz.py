"""Métriques réelles de l'IA du module quiz, calculées à partir du journal AppelIA et des révisions.

    python manage.py stats_ia_quiz [--jours 30]

N'affiche que des valeurs mesurées : s'il n'y a pas de données, la commande le dit.
"""

from collections import Counter
from datetime import timedelta
from statistics import mean, median

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.quizzes.models import AppelIA, ReponseEtudiant


def centile(valeurs, p):
    valeurs = sorted(valeurs)
    return valeurs[min(len(valeurs) - 1, int(round(p / 100 * (len(valeurs) - 1))))]


class Command(BaseCommand):
    help = "Affiche les métriques mesurées de génération et de correction IA des quiz."

    def add_arguments(self, parser):
        parser.add_argument("--jours", type=int, default=None, help="Limiter aux N derniers jours")

    def handle(self, *args, jours=None, **options):
        appels = AppelIA.objects.all()
        if jours:
            appels = appels.filter(date__gte=timezone.now() - timedelta(days=jours))

        for type_appel, libelle in AppelIA.Type.choices:
            liste = list(appels.filter(type_appel=type_appel))
            self.stdout.write(self.style.MIGRATE_HEADING(f"\n== {libelle} =="))
            if not liste:
                self.stdout.write("Aucun appel enregistré.")
                continue
            reussis = [a for a in liste if a.succes]
            self.stdout.write(f"Appels : {len(liste)} · réussis : {len(reussis)} "
                              f"({100 * len(reussis) / len(liste):.1f} %) · échecs : {len(liste) - len(reussis)}")
            codes = Counter(a.code for a in liste if not a.succes)
            if codes:
                self.stdout.write("Causes d'échec : " + ", ".join(f"{c} = {n}" for c, n in codes.most_common()))
            invalides = sum(n for c, n in codes.items() if c in ("json_invalide", "sortie_invalide", "hors_bareme"))
            self.stdout.write(f"Sorties invalides (JSON illisible, incohérent ou hors barème) : {invalides} "
                              f"({100 * invalides / len(liste):.1f} %)")
            durees = [a.duree_ms for a in liste if a.duree_ms is not None]
            if durees:
                self.stdout.write(f"Durée (ms) : moyenne {mean(durees):.0f} · médiane {median(durees):.0f} · "
                                  f"p95 {centile(durees, 95)} · max {max(durees)}")
            reprises = sum(1 for a in liste if a.essais > 1)
            self.stdout.write(f"Appels ayant nécessité une reprise : {reprises}")

            if type_appel == AppelIA.Type.GENERATION and reussis:
                demandees = sum(a.details.get("demandees", 0) for a in reussis)
                valides = sum(a.details.get("valides", 0) for a in reussis)
                rejetees = sum(a.details.get("rejetees", 0) for a in reussis)
                self.stdout.write(f"Questions demandées : {demandees} · conservées : {valides} · écartées par la "
                                  f"validation serveur : {rejetees}")

        # Cohérence IA / enseignant : réponses notées par l'IA puis révisées par l'enseignant
        revisees = list(ReponseEtudiant.objects.filter(
            statut_correction=ReponseEtudiant.Correction.ENSEIGNANT, points_ia__isnull=False
        ).select_related("question"))
        self.stdout.write(self.style.MIGRATE_HEADING("\n== Correction IA comparée à l'enseignant =="))
        if not revisees:
            self.stdout.write("Aucune réponse corrigée par l'IA puis révisée par un enseignant.")
            return
        ecarts = [abs(float(r.points_obtenus - r.points_ia)) for r in revisees]
        relatifs = [abs(float(r.points_obtenus - r.points_ia)) / float(r.question.points) for r in revisees]
        identiques = sum(1 for e in ecarts if e == 0)
        self.stdout.write(f"Réponses comparées : {len(revisees)} · notes identiques : {identiques} "
                          f"({100 * identiques / len(revisees):.1f} %)")
        self.stdout.write(f"Écart absolu moyen : {mean(ecarts):.2f} point(s) · "
                          f"écart moyen rapporté au barème : {100 * mean(relatifs):.1f} %")
