"""L'admin Django permet consultation et modération, sans contourner les règles métier."""

from django.contrib.admin.sites import site
from django.test import RequestFactory
from django.urls import reverse

from apps.challenges.admin import DefiAdmin
from apps.challenges.models import Defi, VersionLivrable
from apps.challenges.services import livrables as regles

from .base import BaseEduSprint, creer_utilisateur


class AdminTests(BaseEduSprint):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.superadmin = creer_utilisateur("super", "ADMIN", is_staff=True, is_superuser=True)

    def setUp(self):
        self.client.force_login(self.superadmin)

    def test_statut_en_lecture_seule(self):
        defi = self.creer_defi()
        admin = DefiAdmin(Defi, site)
        requete = RequestFactory().get("/")
        requete.user = self.superadmin
        self.assertIn("statut", admin.get_readonly_fields(requete, defi))

    def test_modification_et_suppression_bloquees_apres_demarrage(self):
        defi, _ = self.defi_en_cours()
        admin = DefiAdmin(Defi, site)
        requete = RequestFactory().get("/")
        requete.user = self.superadmin
        self.assertFalse(admin.has_change_permission(requete, defi))
        self.assertFalse(admin.has_delete_permission(requete, defi))
        self.assertFalse(admin.has_add_permission(requete))

    def test_action_d_archivage_respecte_les_transitions(self):
        en_cours, _ = self.defi_en_cours()
        termine = self.creer_defi(Defi.Statut.TERMINE)
        url = reverse("admin:challenges_defi_changelist")
        self.client.post(url, {"action": "archiver", "_selected_action": [en_cours.pk, termine.pk]})
        en_cours.refresh_from_db()
        termine.refresh_from_db()
        self.assertEqual(en_cours.statut, Defi.Statut.EN_COURS)  # transition interdite : refusée
        self.assertEqual(termine.statut, Defi.Statut.ARCHIVE)

    def test_historique_non_modifiable(self):
        _, equipe = self.defi_en_cours()
        version = regles.creer_livrable(equipe, self.alice, "L", texte="x")
        regles.soumettre(version, self.alice)
        livrable_url = reverse("admin:challenges_livrable_change", args=[version.livrable_id])
        self.assertEqual(self.client.get(livrable_url).status_code, 200)  # consultation
        self.assertEqual(self.client.post(livrable_url, {"titre": "Modifié"}).status_code, 403)
        self.assertEqual(self.client.post(reverse("admin:challenges_livrable_delete", args=[version.livrable_id]),
                                          {"post": "yes"}).status_code, 403)
        self.assertTrue(VersionLivrable.objects.filter(pk=version.pk, etat="soumis").exists())

    def test_pages_admin_accessibles(self):
        self.defi_en_cours()
        for modele in ["defi", "equipe", "participationequipe", "tache", "commentairetache", "livrable",
                       "evenementscenario", "reponseevenement", "demandeia"]:
            with self.subTest(modele):
                self.assertEqual(self.client.get(reverse(f"admin:challenges_{modele}_changelist")).status_code, 200)
