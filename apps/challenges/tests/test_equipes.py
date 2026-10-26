"""Inscriptions, capacité, appartenance unique par défi, coordination et démarrage."""

from datetime import timedelta
from unittest import mock

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.challenges.models import Defi, Equipe, ParticipationEquipe, Tache
from apps.challenges.services import defis as regles_defis
from apps.challenges.services import equipes as regles

from .base import BaseEduSprint, participation

Etat = ParticipationEquipe.Etat


class InscriptionTests(BaseEduSprint):
    def setUp(self):
        self.defi = self.creer_defi(Defi.Statut.INSCRIPTIONS)

    def test_creation_equipe_coordinateur_membre(self):
        equipe = regles.creer_equipe(self.defi, self.alice, "Les Hydros")
        self.assertEqual(equipe.coordinateur, self.alice)
        self.assertTrue(equipe.est_membre(self.alice))

    def test_inscriptions_fermees(self):
        for statut in [Defi.Statut.BROUILLON, Defi.Statut.EN_COURS]:
            Defi.objects.filter(pk=self.defi.pk).update(statut=statut)
            self.defi.refresh_from_db()
            with self.subTest(statut), self.assertRaises(ValidationError):
                regles.creer_equipe(self.defi, self.alice, f"Equipe {statut}")
        Defi.objects.filter(pk=self.defi.pk).update(statut=Defi.Statut.INSCRIPTIONS,
                                                   inscriptions_fin=timezone.now() - timedelta(minutes=1),
                                                   inscriptions_debut=timezone.now() - timedelta(days=1))
        self.defi.refresh_from_db()
        with self.assertRaises(ValidationError):
            regles.creer_equipe(self.defi, self.alice, "Trop tard")

    def test_nom_unique_insensible_a_la_casse(self):
        regles.creer_equipe(self.defi, self.alice, "Les Hydros")
        with self.assertRaises(ValidationError):
            regles.creer_equipe(self.defi, self.bob, "les hydros")

    def test_un_enseignant_ne_cree_pas_d_equipe(self):
        with self.assertRaises(ValidationError):
            regles.creer_equipe(self.defi, self.prof, "Profs")

    def test_demande_dupliquee(self):
        equipe = regles.creer_equipe(self.defi, self.alice, "A")
        regles.demander_adhesion(equipe, self.bob)
        with self.assertRaises(ValidationError):
            regles.demander_adhesion(equipe, self.bob)
        self.assertEqual(ParticipationEquipe.objects.filter(equipe=equipe, etudiant=self.bob).count(), 1)

    def test_doublon_bloque_par_la_base(self):
        equipe = regles.creer_equipe(self.defi, self.alice, "A")
        ParticipationEquipe.objects.create(equipe=equipe, etudiant=self.bob)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ParticipationEquipe.objects.create(equipe=equipe, etudiant=self.bob)

    def test_retrait_puis_nouvelle_demande(self):
        equipe = regles.creer_equipe(self.defi, self.alice, "A")
        demande = regles.demander_adhesion(equipe, self.bob)
        regles.retirer_demande(demande, self.bob)
        regles.demander_adhesion(equipe, self.bob)  # l'historique est conservé, une nouvelle demande est possible
        self.assertEqual(equipe.participations.filter(etudiant=self.bob).count(), 2)

    def test_retirer_la_demande_d_un_autre(self):
        equipe = regles.creer_equipe(self.defi, self.alice, "A")
        demande = regles.demander_adhesion(equipe, self.bob)
        with self.assertRaises(ValidationError):
            regles.retirer_demande(demande, self.carla)

    def test_capacite_maximale(self):
        equipe = regles.creer_equipe(self.defi, self.alice, "A")  # taille_max = 3
        for etudiant in [self.bob, self.carla]:
            regles.accepter_demande(regles.demander_adhesion(equipe, etudiant), self.alice)
        with self.assertRaises(ValidationError):
            regles.demander_adhesion(equipe, self.dina)

    def test_acceptation_simultanee_capacite(self):
        """Deux demandes en attente pour la dernière place : la seconde acceptation est refusée."""
        equipe = regles.creer_equipe(self.defi, self.alice, "A")
        regles.accepter_demande(regles.demander_adhesion(equipe, self.bob), self.alice)
        d1 = regles.demander_adhesion(equipe, self.carla)
        d2 = regles.demander_adhesion(equipe, self.dina)
        regles.accepter_demande(d1, self.alice)
        with self.assertRaises(ValidationError):
            regles.accepter_demande(d2, self.alice)
        self.assertEqual(equipe.nb_membres(), 3)

    def test_une_seule_equipe_par_defi_et_demandes_incompatibles(self):
        equipe_a = regles.creer_equipe(self.defi, self.alice, "A")
        equipe_b = regles.creer_equipe(self.defi, self.bob, "B")
        demande_a = regles.demander_adhesion(equipe_a, self.carla)
        demande_b = regles.demander_adhesion(equipe_b, self.carla)
        regles.accepter_demande(demande_a, self.alice)
        demande_b.refresh_from_db()
        self.assertEqual(demande_b.etat, Etat.RETIREE)
        self.assertIn("A", demande_b.motif)
        with self.assertRaises(ValidationError):
            regles.accepter_demande(demande_b, self.bob)
        with self.assertRaises(ValidationError):
            regles.creer_equipe(self.defi, self.carla, "C")

    def test_contrainte_base_une_equipe_acceptee(self):
        """Garantie ultime en cas de course : l'index UNIQUE partiel refuse une seconde acceptation."""
        equipe_a = regles.creer_equipe(self.defi, self.alice, "A")
        equipe_b = regles.creer_equipe(self.defi, self.bob, "B")
        ParticipationEquipe.objects.create(equipe=equipe_a, etudiant=self.carla, etat=Etat.ACCEPTEE)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ParticipationEquipe.objects.create(equipe=equipe_b, etudiant=self.carla, etat=Etat.ACCEPTEE)

    def test_course_detectee_traduite_en_message(self):
        equipe_b = regles.creer_equipe(self.defi, self.bob, "B")
        demande = regles.demander_adhesion(equipe_b, self.carla)
        with mock.patch("apps.challenges.services.equipes._deja_membre", return_value=False), \
                mock.patch.object(ParticipationEquipe, "save", side_effect=IntegrityError("unique")):
            with self.assertRaises(ValidationError):
                regles.accepter_demande(demande, self.bob)

    def test_seul_le_coordinateur_accepte(self):
        equipe = regles.creer_equipe(self.defi, self.alice, "A")
        regles.accepter_demande(regles.demander_adhesion(equipe, self.bob), self.alice)
        demande = regles.demander_adhesion(equipe, self.carla)
        with self.assertRaises(ValidationError):
            regles.accepter_demande(demande, self.bob)

    def test_limite_de_demandes_en_attente(self):
        equipes = [regles.creer_equipe(self.defi, u, f"E{i}") for i, u in enumerate([self.alice, self.bob, self.carla])]
        for equipe in equipes:
            regles.demander_adhesion(equipe, self.dina)
        quatrieme = Equipe.objects.create(defi=self.defi, nom="E4", coordinateur=None)
        with self.assertRaises(ValidationError):
            regles.demander_adhesion(quatrieme, self.dina)


class CoordinationTests(BaseEduSprint):
    def setUp(self):
        self.defi = self.creer_defi(Defi.Statut.INSCRIPTIONS)
        self.equipe = self.creer_equipe(self.defi, self.alice, self.bob)

    def test_coordinateur_doit_etre_membre(self):
        self.equipe.coordinateur = self.carla
        with self.assertRaises(ValidationError):
            self.equipe.save()

    def test_depart_du_coordinateur_exige_un_transfert(self):
        with self.assertRaises(ValidationError):
            regles.quitter_equipe(self.equipe, self.alice)
        regles.transferer_coordination(self.equipe, self.alice, self.bob)
        regles.quitter_equipe(self.equipe, self.alice)
        self.equipe.refresh_from_db()
        self.assertEqual(self.equipe.coordinateur, self.bob)
        self.assertEqual(participation(self.alice, self.equipe).etat, Etat.RETIREE)

    def test_transfert_vers_un_non_membre(self):
        with self.assertRaises(ValidationError):
            regles.transferer_coordination(self.equipe, self.alice, self.carla)

    def test_depart_libere_les_taches(self):
        tache = Tache.objects.create(equipe=self.equipe, titre="T", responsable=self.bob, cree_par=self.bob)
        regles.quitter_equipe(self.equipe, self.bob)
        tache.refresh_from_db()
        self.assertIsNone(tache.responsable)
        self.assertTrue(Tache.objects.filter(pk=tache.pk).exists())  # le travail est conservé

    def test_dernier_membre_dissout_l_equipe(self):
        solo = self.creer_equipe(self.defi, self.carla, nom="Solo")
        regles.quitter_equipe(solo, self.carla)
        solo.refresh_from_db()
        self.assertEqual(solo.statut, Equipe.Statut.DISSOUTE)
        self.assertIsNone(solo.coordinateur)

    def test_exclusion_pendant_les_inscriptions_seulement(self):
        p = participation(self.bob, self.equipe)
        Defi.objects.filter(pk=self.defi.pk).update(statut=Defi.Statut.EN_COURS)
        with self.assertRaises(ValidationError):
            regles.exclure_membre(p, self.alice)

    def test_reprise_coordination_vacante(self):
        Equipe.objects.filter(pk=self.equipe.pk).update(coordinateur=None)
        self.equipe.refresh_from_db()
        regles.reprendre_coordination(self.equipe, self.bob)
        self.equipe.refresh_from_db()
        self.assertEqual(self.equipe.coordinateur, self.bob)

    def test_suppression_du_compte_coordinateur(self):
        self.alice.delete()  # comme la vue de suppression du module accounts
        self.equipe.refresh_from_db()
        self.assertIsNone(self.equipe.coordinateur)
        self.assertTrue(self.equipe.est_membre(self.bob))


class DemarrageTests(BaseEduSprint):
    def test_equipes_incompletes_ecartees_et_demandes_refusees(self):
        defi = self.creer_defi(Defi.Statut.INSCRIPTIONS)
        complete = self.creer_equipe(defi, self.alice, self.bob, nom="Complète")
        solo = self.creer_equipe(defi, self.carla, nom="Solo")
        attente = regles.demander_adhesion(complete, self.dina)
        regles_defis.demarrer(defi, self.prof)
        defi.refresh_from_db()
        solo.refresh_from_db()
        attente.refresh_from_db()
        self.assertEqual(defi.statut, Defi.Statut.EN_COURS)
        self.assertEqual(solo.statut, Equipe.Statut.ECARTEE)
        self.assertEqual(attente.etat, Etat.REFUSEE)
        self.assertEqual(Equipe.objects.get(pk=complete.pk).statut, Equipe.Statut.ACTIVE)

    def test_demarrage_impossible_sans_equipe_complete(self):
        defi = self.creer_defi(Defi.Statut.INSCRIPTIONS)
        self.creer_equipe(defi, self.carla, nom="Solo")
        with self.assertRaises(ValidationError):
            regles_defis.demarrer(defi, self.prof)
        defi.refresh_from_db()
        self.assertEqual(defi.statut, Defi.Statut.INSCRIPTIONS)

    def test_double_demarrage(self):
        defi = self.creer_defi(Defi.Statut.INSCRIPTIONS)
        self.creer_equipe(defi, self.alice, self.bob)
        regles_defis.demarrer(defi, self.prof)
        with self.assertRaises(ValidationError):
            regles_defis.demarrer(defi, self.prof)
