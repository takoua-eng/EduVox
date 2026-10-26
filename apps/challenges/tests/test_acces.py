"""Droits par rôle, accès par URL directe et confidentialité entre équipes."""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.challenges.models import (
    Defi,
    EvenementScenario,
    Livrable,
    ReponseEvenement,
    Tache,
    VersionLivrable,
)
from apps.challenges.services import livrables as regles_livrables

from .base import BaseEduSprint


class AccesTests(BaseEduSprint):
    def setUp(self):
        # Équipe d'Alice (+ Bob) et équipe rivale du même défi : Carla (coordinatrice) + Dina
        self.defi, self.equipe = self.defi_en_cours(autres=[(self.carla, (self.dina,), "Rivale")])
        self.rivale = self.defi.equipes.get(nom="Rivale")
        self.tache = Tache.objects.create(equipe=self.equipe, titre="Secret", cree_par=self.alice)
        self.brouillon = regles_livrables.creer_livrable(
            self.equipe, self.alice, "Proposition", texte="Brouillon confidentiel",
            fichier=SimpleUploadedFile("note.txt", b"Contenu prive", content_type="text/plain"))
        self.evenement = EvenementScenario.objects.create(defi=self.defi, titre="Imprévu", description="Desc",
                                                          type_contrainte="exigence", cree_par=self.prof)
        EvenementScenario.objects.filter(pk=self.evenement.pk).update(etat=EvenementScenario.Etat.PUBLIE)
        self.reponse = ReponseEvenement.objects.create(evenement=self.evenement, equipe=self.equipe, auteur=self.alice,
                                                       analyse_impact="a", decisions="b", adaptation_planning="c")

    def urls_privees_equipe(self):
        e, v = self.equipe, self.brouillon
        return [
            reverse("espace-equipe", args=[e.pk]), reverse("taches-equipe", args=[e.pk]),
            reverse("jalons-equipe", args=[e.pk]), reverse("imprevus-equipe", args=[e.pk]),
            reverse("livrables-equipe", args=[e.pk]), reverse("detail-tache", args=[self.tache.pk]),
            reverse("modifier-tache", args=[self.tache.pk]), reverse("detail-livrable", args=[v.livrable_id]),
            reverse("telecharger-version", args=[v.pk]), reverse("modifier-version", args=[v.pk]),
            reverse("reponse-evenement", args=[self.evenement.pk, e.pk]),
        ]

    def test_autre_equipe_ne_voit_rien_en_modifiant_l_url(self):
        self.connecter(self.carla)
        for url in self.urls_privees_equipe():
            with self.subTest(url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_autre_equipe_ne_modifie_rien(self):
        self.connecter(self.carla)
        actions = [
            reverse("deplacer-tache", args=[self.tache.pk]), reverse("supprimer-tache", args=[self.tache.pk]),
            reverse("soumettre-version", args=[self.brouillon.pk]), reverse("supprimer-version", args=[self.brouillon.pk]),
            reverse("verifier-version", args=[self.brouillon.pk]), reverse("quitter-equipe", args=[self.equipe.pk]),
            reverse("soumettre-reponse", args=[self.evenement.pk, self.equipe.pk]),
        ]
        for url in actions:
            with self.subTest(url):
                self.assertEqual(self.client.post(url, {"statut": "termine"}).status_code, 404)
        self.tache.refresh_from_db()
        self.assertEqual(self.tache.statut, Tache.Statut.A_FAIRE)
        self.assertTrue(VersionLivrable.objects.filter(pk=self.brouillon.pk, etat="brouillon").exists())

    def test_membre_accede_a_son_espace(self):
        self.connecter(self.bob)
        for url in self.urls_privees_equipe():
            if "modifier" in url:
                continue  # réservé au créateur / à l'auteur
            with self.subTest(url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_telechargement_protege_et_contenu(self):
        self.connecter(self.bob)
        reponse = self.client.get(reverse("telecharger-version", args=[self.brouillon.pk]))
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(b"".join(reponse.streaming_content), b"Contenu prive")
        self.assertIn("attachment", reponse["Content-Disposition"])
        self.assertEqual(reponse["X-Content-Type-Options"], "nosniff")
        self.client.logout()
        anonyme = self.client.get(reverse("telecharger-version", args=[self.brouillon.pk]))
        self.assertEqual(anonyme.status_code, 302)  # redirection vers la connexion

    def test_fichier_sans_url_publique(self):
        with self.assertRaises(ValueError):
            self.brouillon.fichier.url
        self.assertNotIn(str(self.brouillon.fichier.storage.location), str(__import__("django.conf").conf.settings.MEDIA_ROOT))

    def test_enseignant_ne_voit_pas_les_brouillons(self):
        self.connecter(self.prof)
        self.assertEqual(self.client.get(reverse("espace-equipe", args=[self.equipe.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("telecharger-version", args=[self.brouillon.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("detail-livrable", args=[self.brouillon.livrable_id])).status_code, 404)
        self.assertEqual(self.client.get(reverse("reponse-evenement", args=[self.evenement.pk, self.equipe.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("livrables-equipe", args=[self.equipe.pk])), "Proposition")
        # Après soumission, l'enseignant y accède
        regles_livrables.soumettre(self.brouillon, self.alice)
        self.assertEqual(self.client.get(reverse("telecharger-version", args=[self.brouillon.pk])).status_code, 200)

    def test_autre_enseignant_ne_gere_pas(self):
        self.connecter(self.autre_prof)
        for url in [reverse("modifier-defi", args=[self.defi.pk]), reverse("suivi-defi", args=[self.defi.pk]),
                    reverse("evenements-defi", args=[self.defi.pk]), reverse("espace-equipe", args=[self.equipe.pk]),
                    reverse("detail-evenement", args=[self.evenement.pk])]:
            with self.subTest(url):
                self.assertIn(self.client.get(url).status_code, (403, 404))
        self.assertEqual(self.client.post(reverse("statut-defi", args=[self.defi.pk, "cloturer"])).status_code, 404)
        self.defi.refresh_from_db()
        self.assertEqual(self.defi.statut, Defi.Statut.EN_COURS)

    def test_co_enseignant_autorise_explicitement(self):
        self.defi.co_enseignants.add(self.autre_prof)
        self.connecter(self.autre_prof)
        self.assertEqual(self.client.get(reverse("suivi-defi", args=[self.defi.pk])).status_code, 200)

    def test_etudiant_ne_voit_pas_un_brouillon(self):
        brouillon = self.creer_defi()
        self.connecter(self.alice)
        self.assertEqual(self.client.get(reverse("detail-defi", args=[brouillon.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("catalogue-defis")), f'href="{reverse("detail-defi", args=[brouillon.pk])}"')

    def test_etudiant_ne_cree_pas_de_defi(self):
        self.connecter(self.alice)
        for url in [reverse("creer-defi"), reverse("generer-brief")]:
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_admin_consulte_sans_gerer(self):
        self.connecter(self.admin)
        self.assertEqual(self.client.get(reverse("suivi-defi", args=[self.defi.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("espace-equipe", args=[self.equipe.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("creer-defi")).status_code, 403)
        self.assertEqual(self.client.get(reverse("modifier-defi", args=[self.defi.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("telecharger-version", args=[self.brouillon.pk])).status_code, 404)

    def test_aucune_modification_par_get(self):
        self.connecter(self.alice)
        for url in [reverse("deplacer-tache", args=[self.tache.pk]), reverse("soumettre-version", args=[self.brouillon.pk]),
                    reverse("statut-defi", args=[self.defi.pk, "cloturer"]), reverse("quitter-equipe", args=[self.equipe.pk])]:
            with self.subTest(url):
                self.assertEqual(self.client.get(url).status_code, 405)

    def test_csrf_actif(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.alice)
        reponse = client.post(reverse("deplacer-tache", args=[self.tache.pk]), {"statut": "en_cours"})
        self.assertEqual(reponse.status_code, 403)

    def test_redirection_suivante_limitee_au_site(self):
        self.connecter(self.alice)
        reponse = self.client.post(reverse("deplacer-tache", args=[self.tache.pk]),
                                   {"statut": "en_cours", "suivant": "https://exemple-malveillant.test/"})
        self.assertEqual(reponse["Location"], reverse("taches-equipe", args=[self.equipe.pk]))

    def test_identifiant_inexistant(self):
        self.connecter(self.alice)
        self.assertEqual(self.client.get(reverse("detail-livrable", args=[999999])).status_code, 404)
        self.assertEqual(Livrable.objects.count(), 1)
