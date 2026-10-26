"""CRUD par l'interface et scénario de démonstration complet ; blocage des écritures après clôture."""

from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.challenges.forms import FORMAT_DATETIME
from apps.challenges.models import (
    CommentaireTache,
    Defi,
    EvenementScenario,
    Jalon,
    Livrable,
    ReponseEvenement,
    Tache,
    VersionLivrable,
)

from .base import BaseEduSprint


def dt(valeur):
    return timezone.localtime(valeur).strftime(FORMAT_DATETIME)


class CrudEnseignantTests(BaseEduSprint):
    def donnees_defi(self, **extra):
        maintenant = timezone.now()
        donnees = {
            "titre": "Concevoir une application de covoiturage", "description": "Mission pour une start-up.",
            "contexte": "La start-up veut réduire les trajets en voiture seule.", "objectifs": "Étudier le besoin",
            "competences": "UX", "livrables_attendus": "Maquette", "contraintes": "", "budget": "1500",
            "niveau": "intermediaire", "inscriptions_debut": dt(maintenant), "inscriptions_fin": dt(maintenant + timedelta(days=1)),
            "date_debut": dt(maintenant + timedelta(days=2)), "date_fin": dt(maintenant + timedelta(days=6)),
            "taille_min": "2", "taille_max": "4",
        }
        donnees.update(extra)
        return donnees

    def test_creation_modification_jalon_publication(self):
        self.connecter(self.prof)
        reponse = self.client.post(reverse("creer-defi"), self.donnees_defi())
        defi = Defi.objects.get(titre__startswith="Concevoir")
        self.assertRedirects(reponse, reverse("detail-defi", args=[defi.pk]))
        self.assertEqual((defi.enseignant, defi.statut), (self.prof, Defi.Statut.BROUILLON))

        # Champ sensible ignoré : impossible de se placer directement « en cours »
        self.client.post(reverse("modifier-defi", args=[defi.pk]), self.donnees_defi(titre="Covoiturage v2", statut="en_cours"))
        defi.refresh_from_db()
        self.assertEqual((defi.titre, defi.statut), ("Covoiturage v2", Defi.Statut.BROUILLON))

        self.client.post(reverse("creer-jalon", args=[defi.pk]), {
            "titre": "Maquette", "ordre": "1", "echeance": dt(defi.date_debut + timedelta(days=1)), "exigences": "Écrans"})
        self.assertEqual(defi.jalons.count(), 1)
        doublon = self.client.post(reverse("creer-jalon", args=[defi.pk]), {
            "titre": "Autre", "ordre": "1", "echeance": dt(defi.date_debut + timedelta(days=1))})
        self.assertEqual(doublon.status_code, 200)  # numéro d'ordre déjà pris : erreur affichée, pas de 500
        self.client.post(reverse("statut-defi", args=[defi.pk, "publier"]))
        defi.refresh_from_db()
        self.assertEqual(defi.statut, Defi.Statut.INSCRIPTIONS)

    def test_erreurs_de_formulaire_accessibles(self):
        self.connecter(self.prof)
        maintenant = timezone.now()
        reponse = self.client.post(reverse("creer-defi"), self.donnees_defi(date_fin=dt(maintenant)))
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, 'id="resume-erreurs"')
        self.assertContains(reponse, 'aria-invalid="true"')
        self.assertFalse(Defi.objects.exists())

    def test_modification_bloquee_apres_demarrage(self):
        defi, _ = self.defi_en_cours()
        self.connecter(self.prof)
        self.assertRedirects(self.client.get(reverse("modifier-defi", args=[defi.pk])), reverse("detail-defi", args=[defi.pk]))
        self.assertEqual(self.client.get(reverse("creer-jalon", args=[defi.pk])).status_code, 403)

    def test_catalogue_recherche_filtres_pagination(self):
        for i in range(11):
            self.creer_defi(Defi.Statut.INSCRIPTIONS, titre=f"Mission {i:02d}")
        self.creer_defi(Defi.Statut.INSCRIPTIONS, titre="Énergie solaire", niveau="avance")
        self.connecter(self.alice)
        page1 = self.client.get(reverse("catalogue-defis"))
        self.assertEqual(len(page1.context["defis"]), 9)
        self.assertTrue(page1.context["is_paginated"])
        recherche = self.client.get(reverse("catalogue-defis"), {"q": "solaire"})
        self.assertEqual([d.titre for d in recherche.context["defis"]], ["Énergie solaire"])
        niveau = self.client.get(reverse("catalogue-defis"), {"niveau": "avance"})
        self.assertEqual(len(niveau.context["defis"]), 1)
        vide = self.client.get(reverse("catalogue-defis"), {"q": "introuvable"})
        self.assertContains(vide, "Aucun défi ne correspond")


class ScenarioDemonstrationTests(BaseEduSprint):
    """L'enseignant publie une mission ; deux étudiants forment une équipe et s'organisent ; un imprévu validé est
    publié ; l'équipe répond puis dépose un livrable ; complément demandé ; nouvelle version ; acceptation."""

    def test_scenario_complet(self):
        defi = self.creer_defi()
        self.connecter(self.prof)
        self.client.post(reverse("statut-defi", args=[defi.pk, "publier"]))
        defi.refresh_from_db()
        self.assertEqual(defi.statut, Defi.Statut.INSCRIPTIONS)

        # Alice crée l'équipe, Bob demande à la rejoindre, Alice accepte
        self.connecter(self.alice)
        self.client.post(reverse("creer-equipe", args=[defi.pk]), {"nom": "Les Hydros", "description": ""})
        equipe = defi.equipes.get()
        self.connecter(self.bob)
        self.client.post(reverse("rejoindre-equipe", args=[equipe.pk]), {"message": "Je gère le budget."})
        demande = equipe.participations.get(etudiant=self.bob)
        self.connecter(self.alice)
        self.client.post(reverse("accepter-demande", args=[demande.pk]))
        self.assertEqual(equipe.nb_membres(), 2)

        # L'enseignant démarre le défi
        self.connecter(self.prof)
        self.assertContains(self.client.get(reverse("demarrer-defi", args=[defi.pk])), "Les Hydros")
        self.client.post(reverse("demarrer-defi", args=[defi.pk]))
        defi.refresh_from_db()
        self.assertEqual(defi.statut, Defi.Statut.EN_COURS)

        # Organisation des tâches (Kanban, affectation, commentaire)
        self.connecter(self.alice)
        jalon = defi.jalons.first()
        self.client.post(reverse("creer-tache", args=[equipe.pk]), {
            "titre": "Relever les compteurs", "description": "", "jalon": jalon.pk, "responsable": self.bob.pk,
            "priorite": "haute", "echeance": ""})
        tache = Tache.objects.get(titre="Relever les compteurs")
        self.assertEqual(tache.responsable, self.bob)
        self.connecter(self.bob)
        reponse = self.client.post(reverse("deplacer-tache", args=[tache.pk]), {"statut": "en_cours"},
                                   HTTP_ACCEPT="application/json")
        self.assertEqual(reponse.json()["ok"], True)
        self.client.post(reverse("detail-tache", args=[tache.pk]), {"contenu": "Compteurs du bâtiment A relevés."})
        self.assertEqual(CommentaireTache.objects.filter(tache=tache).count(), 1)
        self.assertContains(self.client.get(reverse("taches-equipe", args=[equipe.pk])), "Relever les compteurs")

        # L'enseignant crée, valide puis publie un imprévu
        self.connecter(self.prof)
        self.client.post(reverse("creer-evenement", args=[defi.pk]), {
            "titre": "Budget réduit de 25 %", "type_contrainte": "budget", "description": "Le client réduit le budget.",
            "justification": "Arbitrer", "impacts_attendus": "Moins d'actions", "elements_attendus": "Nouveau budget",
            "echeance_reponse": "", "jalon_concerne": "", "nouvelle_echeance": "", "nouveau_budget": "1500"})
        evenement = EvenementScenario.objects.get()
        refus = self.client.post(reverse("publier-evenement", args=[evenement.pk]))
        self.assertRedirects(refus, reverse("detail-evenement", args=[evenement.pk]))
        evenement.refresh_from_db()
        self.assertEqual(evenement.etat, EvenementScenario.Etat.BROUILLON)  # pas de publication sans validation
        self.client.post(reverse("valider-evenement", args=[evenement.pk]))
        self.client.post(reverse("publier-evenement", args=[evenement.pk]))
        evenement.refresh_from_db()
        defi.refresh_from_db()
        self.assertEqual(evenement.etat, EvenementScenario.Etat.PUBLIE)
        self.assertEqual(str(defi.budget), "1500.00")
        self.assertEqual(evenement.modifications_appliquees["budget"]["avant"], "2000.00")

        # L'équipe prépare (Bob) et soumet (Alice, coordinatrice) sa réponse d'adaptation
        self.connecter(self.bob)
        url_reponse = reverse("reponse-evenement", args=[evenement.pk, equipe.pk])
        self.client.post(url_reponse, {"analyse_impact": "Deux actions sur trois restent finançables.",
                                       "decisions": "Abandon de la récupération d'eau de pluie.",
                                       "adaptation_planning": "Maquette avancée d'un jour."})
        reponse_equipe = ReponseEvenement.objects.get()
        self.client.post(reverse("soumettre-reponse", args=[evenement.pk, equipe.pk]))
        reponse_equipe.refresh_from_db()
        self.assertEqual(reponse_equipe.etat, ReponseEvenement.Etat.BROUILLON)  # Bob n'est pas coordinateur
        self.connecter(self.alice)
        self.client.post(reverse("soumettre-reponse", args=[evenement.pk, equipe.pk]))
        reponse_equipe.refresh_from_db()
        self.assertEqual(reponse_equipe.etat, ReponseEvenement.Etat.SOUMISE)

        # Dépôt d'un livrable puis soumission
        self.client.post(reverse("creer-livrable", args=[equipe.pk]), {
            "titre": "Proposition", "description": "", "jalon": "", "lien": "https://maquette.exemple.test/v1",
            "texte": "Proposition : trois actions.", "note_equipe": "",
            "fichier": SimpleUploadedFile("proposition.pdf", b"%PDF-1.4 contenu", content_type="application/pdf")})
        livrable = Livrable.objects.get()
        v1 = livrable.versions.get()
        self.client.post(reverse("soumettre-version", args=[v1.pk]))
        v1.refresh_from_db()
        self.assertEqual(v1.etat, VersionLivrable.Etat.SOUMIS)

        # L'enseignant demande un complément
        self.connecter(self.prof)
        self.assertContains(self.client.get(reverse("suivi-defi", args=[defi.pk])), "Proposition")
        self.client.post(reverse("evaluer-version", args=[v1.pk]), {"decision": "a_completer", "retour": ""})
        v1.refresh_from_db()
        self.assertEqual(v1.etat, VersionLivrable.Etat.SOUMIS)  # retour obligatoire pour un complément
        self.client.post(reverse("evaluer-version", args=[v1.pk]), {"decision": "a_completer", "retour": "Chiffrez l'action 2."})
        v1.refresh_from_db()
        self.assertEqual(v1.etat, VersionLivrable.Etat.A_COMPLETER)

        # Nouvelle version (l'historique et le retour sont conservés), puis acceptation
        self.connecter(self.bob)
        self.client.post(reverse("nouvelle-version", args=[livrable.pk]), {
            "lien": "", "texte": "Proposition : trois actions, action 2 chiffrée à 600 DT.", "note_equipe": "Complété"})
        v2 = livrable.versions.get(numero=2)
        self.connecter(self.alice)
        self.client.post(reverse("soumettre-version", args=[v2.pk]))
        self.connecter(self.prof)
        self.client.post(reverse("evaluer-version", args=[v2.pk]), {"decision": "accepte", "retour": "Bravo."})
        v1.refresh_from_db()
        v2.refresh_from_db()
        self.assertEqual((v1.etat, v1.retour_enseignant), (VersionLivrable.Etat.A_COMPLETER, "Chiffrez l'action 2."))
        self.assertEqual(v2.etat, VersionLivrable.Etat.ACCEPTE)
        page = self.client.get(reverse("detail-livrable", args=[livrable.pk]))
        self.assertContains(page, "Version 1")
        self.assertContains(page, "Version 2")
        self.assertContains(page, "Chiffrez l")

        # Plus de nouvelle version une fois accepté
        self.connecter(self.bob)
        self.client.post(reverse("nouvelle-version", args=[livrable.pk]), {"texte": "v3"})
        self.assertEqual(livrable.versions.count(), 2)


class ClotureTests(BaseEduSprint):
    def test_ecritures_bloquees_apres_cloture(self):
        defi, equipe = self.defi_en_cours()
        tache = Tache.objects.create(equipe=equipe, titre="T", cree_par=self.alice)
        livrable = Livrable.objects.create(equipe=equipe, titre="L", cree_par=self.alice)
        version = VersionLivrable.objects.create(livrable=livrable, numero=1, texte="x", auteur=self.alice)
        self.connecter(self.prof)
        self.client.post(reverse("statut-defi", args=[defi.pk, "cloturer"]))
        defi.refresh_from_db()
        self.assertEqual(defi.statut, Defi.Statut.TERMINE)

        self.connecter(self.alice)
        self.assertEqual(self.client.get(reverse("creer-tache", args=[equipe.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("creer-livrable", args=[equipe.pk])).status_code, 403)
        self.client.post(reverse("deplacer-tache", args=[tache.pk]), {"statut": "termine"})
        self.client.post(reverse("soumettre-version", args=[version.pk]))
        self.client.post(reverse("detail-tache", args=[tache.pk]), {"contenu": "Trop tard"})
        tache.refresh_from_db()
        version.refresh_from_db()
        self.assertEqual(tache.statut, Tache.Statut.A_FAIRE)
        self.assertEqual(version.etat, VersionLivrable.Etat.BROUILLON)
        self.assertFalse(CommentaireTache.objects.exists())
        # L'historique reste consultable
        self.assertEqual(self.client.get(reverse("espace-equipe", args=[equipe.pk])).status_code, 200)
        self.assertContains(self.client.get(reverse("espace-equipe", args=[equipe.pk])), "historique reste consultable")

    def test_exception_retour_enseignant_apres_cloture(self):
        defi, equipe = self.defi_en_cours()
        livrable = Livrable.objects.create(equipe=equipe, titre="L", cree_par=self.alice)
        version = VersionLivrable.objects.create(livrable=livrable, numero=1, texte="x", auteur=self.alice)
        self.connecter(self.alice)
        self.client.post(reverse("soumettre-version", args=[version.pk]))
        Defi.objects.filter(pk=defi.pk).update(statut=Defi.Statut.TERMINE)
        self.connecter(self.prof)
        self.client.post(reverse("evaluer-version", args=[version.pk]), {"decision": "accepte", "retour": ""})
        version.refresh_from_db()
        self.assertEqual(version.etat, VersionLivrable.Etat.ACCEPTE)

    def test_archive_consultable_par_les_participants(self):
        defi, equipe = self.defi_en_cours()
        Defi.objects.filter(pk=defi.pk).update(statut=Defi.Statut.ARCHIVE)
        self.connecter(self.alice)
        self.assertEqual(self.client.get(reverse("detail-defi", args=[defi.pk])).status_code, 200)
        self.connecter(self.carla)
        self.assertEqual(self.client.get(reverse("detail-defi", args=[defi.pk])).status_code, 404)


class DroitsTachesTests(BaseEduSprint):
    def setUp(self):
        self.defi, self.equipe = self.defi_en_cours(membres=(self.bob, self.carla))

    def creer(self, auteur, **donnees):
        self.connecter(auteur)
        valeurs = {"titre": "Tâche", "description": "", "jalon": "", "responsable": "", "priorite": "normale", "echeance": ""}
        valeurs.update(donnees)
        return self.client.post(reverse("creer-tache", args=[self.equipe.pk]), valeurs)

    def test_affectation_selon_le_role(self):
        self.creer(self.bob, titre="Pour Carla", responsable=self.carla.pk)  # un membre n'affecte pas à autrui
        self.assertFalse(Tache.objects.filter(titre="Pour Carla").exists())
        self.creer(self.bob, titre="Pour moi", responsable=self.bob.pk)
        self.assertTrue(Tache.objects.filter(titre="Pour moi", responsable=self.bob).exists())
        self.creer(self.alice, titre="Affectée par la coordinatrice", responsable=self.carla.pk)
        self.assertTrue(Tache.objects.filter(responsable=self.carla).exists())
        self.creer(self.alice, titre="Hors équipe", responsable=self.dina.pk)
        self.assertFalse(Tache.objects.filter(titre="Hors équipe").exists())

    def test_pas_de_modification_ni_suppression_arbitraire(self):
        self.creer(self.bob, titre="Tâche de Bob", responsable=self.bob.pk)
        tache = Tache.objects.get(titre="Tâche de Bob")
        self.connecter(self.carla)
        self.assertEqual(self.client.get(reverse("modifier-tache", args=[tache.pk])).status_code, 403)
        self.client.post(reverse("deplacer-tache", args=[tache.pk]), {"statut": "termine"})
        self.client.post(reverse("supprimer-tache", args=[tache.pk]))
        tache.refresh_from_db()
        self.assertEqual(tache.statut, Tache.Statut.A_FAIRE)
        # La coordinatrice fait avancer la tâche mais ne la supprime pas
        self.connecter(self.alice)
        self.client.post(reverse("deplacer-tache", args=[tache.pk]), {"statut": "a_verifier"})
        self.client.post(reverse("supprimer-tache", args=[tache.pk]))
        tache.refresh_from_db()
        self.assertEqual(tache.statut, Tache.Statut.A_VERIFIER)
        # Le créateur ne supprime plus une tâche commentée par un autre membre
        self.client.post(reverse("detail-tache", args=[tache.pk]), {"contenu": "Je relis."})
        self.connecter(self.bob)
        self.client.post(reverse("supprimer-tache", args=[tache.pk]))
        self.assertTrue(Tache.objects.filter(pk=tache.pk).exists())

    def test_statut_inconnu_refuse(self):
        self.creer(self.bob, titre="T", responsable=self.bob.pk)
        tache = Tache.objects.get(titre="T")
        reponse = self.client.post(reverse("deplacer-tache", args=[tache.pk]), {"statut": "pirate"},
                                   HTTP_ACCEPT="application/json")
        self.assertEqual(reponse.status_code, 400)


class ImprevuDelaiTests(BaseEduSprint):
    def test_modification_de_delai_appliquee_et_tracee(self):
        defi, _ = self.defi_en_cours()
        jalon = Jalon.objects.get(defi=defi, ordre=1)
        nouvelle = jalon.echeance + timedelta(days=2)
        self.connecter(self.prof)
        self.client.post(reverse("creer-evenement", args=[defi.pk]), {
            "titre": "Report du diagnostic", "type_contrainte": "delai", "description": "Le client décale la réunion.",
            "justification": "", "impacts_attendus": "", "elements_attendus": "", "echeance_reponse": "",
            "jalon_concerne": jalon.pk, "nouvelle_echeance": dt(nouvelle), "nouveau_budget": ""})
        evenement = EvenementScenario.objects.get()
        jalon.refresh_from_db()
        self.assertNotEqual(jalon.echeance.replace(second=0, microsecond=0), nouvelle.replace(second=0, microsecond=0))
        self.client.post(reverse("valider-evenement", args=[evenement.pk]))
        self.client.post(reverse("publier-evenement", args=[evenement.pk]))
        jalon.refresh_from_db()
        evenement.refresh_from_db()
        self.assertEqual(jalon.echeance.replace(second=0, microsecond=0), nouvelle.replace(second=0, microsecond=0))
        self.assertEqual(evenement.modifications_appliquees["jalon"]["id"], jalon.pk)
