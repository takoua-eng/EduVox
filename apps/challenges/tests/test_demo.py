"""Commande de données de démonstration (exécutée ici sur la base de test isolée)."""

import io

from django.core.management import CommandError, call_command
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Utilisateur
from apps.challenges.models import Defi, Equipe, EvenementScenario, ReponseEvenement, Tache, VersionLivrable


class DonneesDemoTests(TestCase):
    def test_creation_complete_et_sans_doublon(self):
        with self.assertRaises(CommandError):
            call_command("donnees_demo_edusprint", stdout=io.StringIO())  # --confirmer obligatoire
        sortie = io.StringIO()
        call_command("donnees_demo_edusprint", "--confirmer", stdout=sortie)
        self.assertEqual(Defi.objects.filter(statut=Defi.Statut.EN_COURS, titre__startswith="[DÉMO]").count(), 4)
        self.assertEqual(Equipe.objects.filter(statut=Equipe.Statut.ACTIVE).count(), 8)
        self.assertEqual(Equipe.objects.filter(statut=Equipe.Statut.ECARTEE).count(), 1)
        self.assertEqual(EvenementScenario.objects.filter(etat="publie").count(), 4)
        self.assertEqual(ReponseEvenement.objects.filter(etat="soumise").count(), 4)
        self.assertTrue(VersionLivrable.objects.filter(en_retard=True).exists())
        self.assertTrue(Tache.objects.filter(statut="termine").exists())
        self.assertEqual(Utilisateur.objects.filter(username__startswith="demo_").count(), 7)
        with self.assertRaises(CommandError):
            call_command("donnees_demo_edusprint", "--confirmer", stdout=io.StringIO())
        # Les pages s'affichent pour l'enseignant et un étudiant démo
        for nom in ("demo_prof", "demo_etu1"):
            self.client.force_login(Utilisateur.objects.get(username=nom))
            for defi in Defi.objects.all():
                self.assertEqual(self.client.get(reverse("detail-defi", args=[defi.pk])).status_code, 200)
            for equipe in Equipe.objects.all():
                code = self.client.get(reverse("espace-equipe", args=[equipe.pk])).status_code
                self.assertIn(code, (200, 404))
