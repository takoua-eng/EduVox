from django.core.management.base import BaseCommand

from ai_services.visage import DOSSIER_MODELES, telecharger_modeles


class Command(BaseCommand):
    help = "Télécharge les modèles OpenCV de reconnaissance faciale (environ 39 Mo, une seule fois)."

    def handle(self, *args, **options):
        self.stdout.write("Téléchargement des modèles… (peut prendre une minute)")
        telecharger_modeles()
        self.stdout.write(self.style.SUCCESS(f"Modèles prêts dans {DOSSIER_MODELES}"))