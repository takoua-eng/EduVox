"""Rendu de toutes les pages pour chaque rôle (états vides et remplis) : aucun gabarit en erreur."""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.challenges.models import DemandeIA, EvenementScenario, ReponseEvenement, Tache
from apps.challenges.services import evenements as regles_evenements
from apps.challenges.services import livrables as regles_livrables

from .base import BaseEduSprint


class PagesTests(BaseEduSprint):
    def setUp(self):
        self.defi, self.equipe = self.defi_en_cours()
        self.tache = Tache.objects.create(equipe=self.equipe, titre="Relever", responsable=self.bob, cree_par=self.alice,
                                          jalon=self.defi.jalons.first())
        self.version = regles_livrables.creer_livrable(
            self.equipe, self.alice, "Proposition", texte="Texte", lien="https://exemple.test",
            fichier=SimpleUploadedFile("p.txt", b"Contenu"))
        regles_livrables.soumettre(self.version, self.alice)
        self.evenement = EvenementScenario.objects.create(defi=self.defi, titre="Outil indisponible", description="D",
                                                          type_contrainte="outil", cree_par=self.prof,
                                                          elements_attendus="Plan B")
        regles_evenements.valider(self.evenement, self.prof)
        self.evenement = regles_evenements.publier(self.evenement, self.prof)
        regles_evenements.enregistrer_reponse(self.evenement, self.equipe, self.bob, "a", "b", "c")
        regles_evenements.soumettre_reponse(ReponseEvenement.objects.get(), self.alice)
        self.brouillon = EvenementScenario.objects.create(defi=self.defi, titre="Brouillon IA", description="D",
                                                          type_contrainte="public", origine="ia", cree_par=self.prof)
        DemandeIA.objects.create(type_demande="verification", version=self.version, demandeur=self.prof,
                                 statut="echoue", message="Quota Gemini atteint.")

    def verifier(self, user, urls):
        self.connecter(user)
        for url in urls:
            with self.subTest(user=user.username, url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_pages_etudiant(self):
        e = self.equipe
        self.verifier(self.alice, [
            reverse("catalogue-defis"), reverse("catalogue-defis") + "?mes=1", reverse("detail-defi", args=[self.defi.pk]),
            reverse("espace-equipe", args=[e.pk]), reverse("taches-equipe", args=[e.pk]),
            reverse("taches-equipe", args=[e.pk]) + "?filtre=moi", reverse("jalons-equipe", args=[e.pk]),
            reverse("imprevus-equipe", args=[e.pk]), reverse("livrables-equipe", args=[e.pk]),
            reverse("creer-tache", args=[e.pk]), reverse("detail-tache", args=[self.tache.pk]),
            reverse("modifier-tache", args=[self.tache.pk]), reverse("creer-livrable", args=[e.pk]),
            reverse("detail-livrable", args=[self.version.livrable_id]), reverse("modifier-equipe", args=[e.pk]),
            reverse("reponse-evenement", args=[self.evenement.pk, e.pk]),
        ])

    def test_pages_enseignant(self):
        self.verifier(self.prof, [
            reverse("catalogue-defis"), reverse("creer-defi"), reverse("generer-brief"),
            reverse("detail-defi", args=[self.defi.pk]), reverse("suivi-defi", args=[self.defi.pk]),
            reverse("evenements-defi", args=[self.defi.pk]), reverse("creer-evenement", args=[self.defi.pk]),
            reverse("detail-evenement", args=[self.evenement.pk]), reverse("detail-evenement", args=[self.brouillon.pk]),
            reverse("modifier-evenement", args=[self.brouillon.pk]), reverse("espace-equipe", args=[self.equipe.pk]),
            reverse("detail-livrable", args=[self.version.livrable_id]),
            reverse("reponse-evenement", args=[self.evenement.pk, self.equipe.pk]),
        ])

    def test_pages_brouillon_et_inscriptions(self):
        brouillon = self.creer_defi(titre="Brouillon à préparer")
        self.verifier(self.prof, [reverse("detail-defi", args=[brouillon.pk]), reverse("modifier-defi", args=[brouillon.pk]),
                                  reverse("creer-jalon", args=[brouillon.pk]),
                                  reverse("modifier-jalon", args=[brouillon.jalons.first().pk])])
        ouvert = self.creer_defi(statut="inscriptions", titre="Ouvert")
        self.creer_equipe(ouvert, self.carla, nom="Solo")
        self.verifier(self.prof, [reverse("demarrer-defi", args=[ouvert.pk])])
        self.verifier(self.dina, [reverse("detail-defi", args=[ouvert.pk]), reverse("creer-equipe", args=[ouvert.pk])])

    def test_pages_admin(self):
        self.verifier(self.admin, [reverse("catalogue-defis"), reverse("detail-defi", args=[self.defi.pk]),
                                   reverse("suivi-defi", args=[self.defi.pk]), reverse("espace-equipe", args=[self.equipe.pk]),
                                   reverse("detail-evenement", args=[self.evenement.pk])])

    def test_menu_contient_edusprint(self):
        self.connecter(self.alice)
        self.assertContains(self.client.get(reverse("catalogue-defis")), "EduSprint")
