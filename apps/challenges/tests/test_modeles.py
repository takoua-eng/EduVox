"""Validations des modèles, transitions de statut et verrous métier (y compris contre un contournement)."""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.challenges.models import (
    Defi,
    EvenementScenario,
    Jalon,
    Livrable,
    ReponseEvenement,
    Tache,
    VersionLivrable,
    lignes,
)
from apps.challenges.services import defis as regles

from .base import BaseEduSprint


class DefiValidationTests(BaseEduSprint):
    def test_dates_incoherentes(self):
        defi = self.creer_defi()
        cas = {
            "inscriptions_fin": {"inscriptions_fin": defi.inscriptions_debut - timedelta(hours=1)},
            "date_debut": {"date_debut": defi.inscriptions_fin - timedelta(hours=1)},
            "date_fin": {"date_fin": defi.date_debut - timedelta(hours=1)},
        }
        for champ, valeurs in cas.items():
            with self.subTest(champ):
                for cle, valeur in valeurs.items():
                    setattr(defi, cle, valeur)
                with self.assertRaises(ValidationError) as ctx:
                    defi.full_clean()
                self.assertIn(champ, ctx.exception.message_dict)
                defi.refresh_from_db()

    def test_tailles_et_budget(self):
        defi = self.creer_defi()
        defi.taille_min, defi.taille_max = 4, 2
        with self.assertRaises(ValidationError):
            defi.full_clean()
        defi.refresh_from_db()
        defi.budget = -5
        with self.assertRaises(ValidationError):
            defi.full_clean()

    def test_contraintes_en_base(self):
        """Même sans full_clean (accès direct), la base refuse des données incohérentes."""
        defi = self.creer_defi()
        for valeurs in [{"taille_min": 5, "taille_max": 2}, {"budget": -1},
                        {"date_fin": defi.date_debut - timedelta(days=1)}]:
            with self.subTest(valeurs), self.assertRaises(IntegrityError), transaction.atomic():
                Defi.objects.filter(pk=defi.pk).update(**valeurs)

    def test_taille_max_inferieure_a_une_equipe(self):
        defi = self.creer_defi(Defi.Statut.INSCRIPTIONS)
        self.creer_equipe(defi, self.alice, self.bob, self.carla)
        defi.taille_max = 2
        with self.assertRaises(ValidationError) as ctx:
            defi.full_clean()
        self.assertIn("taille_max", ctx.exception.message_dict)

    def test_jalon_hors_periode(self):
        defi = self.creer_defi()
        jalon = Jalon(defi=defi, ordre=3, titre="Trop tard", echeance=defi.date_fin + timedelta(days=1))
        with self.assertRaises(ValidationError):
            jalon.full_clean()

    def test_lignes(self):
        self.assertEqual(lignes("- un\n\n• deux \n* trois"), ["un", "deux", "trois"])


class TransitionsTests(BaseEduSprint):
    def test_transitions_autorisees(self):
        defi = self.creer_defi()
        regles.ouvrir_inscriptions(defi)
        defi.refresh_from_db()
        self.assertEqual(defi.statut, Defi.Statut.INSCRIPTIONS)
        regles.repasser_brouillon(defi)
        defi.refresh_from_db()
        self.assertEqual(defi.statut, Defi.Statut.BROUILLON)

    def test_transition_interdite_meme_par_save(self):
        defi = self.creer_defi()
        for statut in [Defi.Statut.EN_COURS, Defi.Statut.TERMINE]:
            defi.statut = statut
            with self.subTest(statut), self.assertRaises(ValidationError):
                defi.save()
        defi.refresh_from_db()
        self.assertEqual(defi.statut, Defi.Statut.BROUILLON)

    def test_archive_definitif(self):
        defi = self.creer_defi()
        regles.archiver(defi)
        with self.assertRaises(ValidationError):
            regles.ouvrir_inscriptions(defi)

    def test_ouverture_refusee_sans_jalon(self):
        defi = self.creer_defi()
        defi.jalons.all().delete()
        with self.assertRaises(ValidationError):
            regles.ouvrir_inscriptions(defi)

    def test_brief_fige_apres_demarrage(self):
        defi, _ = self.defi_en_cours()
        defi.objectifs = "Nouvel objectif silencieux"
        with self.assertRaises(ValidationError):
            defi.save()
        jalon = defi.jalons.first()
        jalon.echeance = jalon.echeance + timedelta(hours=1)
        with self.assertRaises(ValidationError):
            jalon.save()

    def test_suppression_reservee_aux_brouillons_sans_contribution(self):
        defi = self.creer_defi(Defi.Statut.INSCRIPTIONS)
        self.creer_equipe(defi, self.alice)
        with self.assertRaises(ValidationError):
            defi.delete()
        brouillon = self.creer_defi()
        brouillon.delete()
        self.assertFalse(Defi.objects.filter(pk=brouillon.pk).exists())


class TacheValidationTests(BaseEduSprint):
    def test_responsable_et_jalon_coherents(self):
        defi, equipe = self.defi_en_cours()
        autre_defi = self.creer_defi()
        tache = Tache(equipe=equipe, titre="Relever les compteurs", responsable=self.carla)
        with self.assertRaises(ValidationError) as ctx:
            tache.full_clean()
        self.assertIn("responsable", ctx.exception.message_dict)
        tache = Tache(equipe=equipe, titre="Relever", jalon=autre_defi.jalons.first())
        with self.assertRaises(ValidationError) as ctx:
            tache.full_clean()
        self.assertIn("jalon", ctx.exception.message_dict)
        tache = Tache(equipe=equipe, titre="Relever", responsable=self.bob, jalon=defi.jalons.first(), cree_par=self.alice)
        tache.full_clean()
        tache.save()

    def test_date_terminee(self):
        _, equipe = self.defi_en_cours()
        tache = Tache.objects.create(equipe=equipe, titre="T", cree_par=self.alice)
        tache.statut = Tache.Statut.TERMINE
        tache.save()
        self.assertIsNotNone(tache.date_terminee)
        tache.statut = Tache.Statut.EN_COURS
        tache.save()
        self.assertIsNone(tache.date_terminee)


class VersionVerrouTests(BaseEduSprint):
    def setUp(self):
        self.defi, self.equipe = self.defi_en_cours()
        self.livrable = Livrable.objects.create(equipe=self.equipe, titre="Proposition", cree_par=self.alice)
        self.version = VersionLivrable.objects.create(livrable=self.livrable, numero=1, texte="Texte", auteur=self.alice)

    def test_version_soumise_immuable(self):
        self.version.etat = VersionLivrable.Etat.SOUMIS
        self.version.save()
        self.version.texte = "Texte réécrit"
        with self.assertRaises(ValidationError):
            self.version.save()
        with self.assertRaises(ValidationError):
            self.version.delete()

    def test_transition_de_version_interdite(self):
        self.version.etat = VersionLivrable.Etat.ACCEPTE
        with self.assertRaises(ValidationError):
            self.version.save()

    def test_un_seul_brouillon_par_livrable(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            VersionLivrable.objects.create(livrable=self.livrable, numero=2, texte="Autre", auteur=self.bob)

    def test_contenu_obligatoire(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            VersionLivrable.objects.filter(pk=self.version.pk).update(texte="")


class EvenementVerrouTests(BaseEduSprint):
    def setUp(self):
        self.defi, self.equipe = self.defi_en_cours()
        self.evenement = EvenementScenario.objects.create(
            defi=self.defi, titre="Budget réduit", description="Le client réduit le budget de 30 %.",
            type_contrainte=EvenementScenario.Type.BUDGET, cree_par=self.prof)

    def test_creation_toujours_en_brouillon(self):
        with self.assertRaises(ValidationError):
            EvenementScenario.objects.create(defi=self.defi, titre="X", description="Y", type_contrainte="autre",
                                             etat=EvenementScenario.Etat.PUBLIE)

    def test_publication_sans_validation_interdite(self):
        self.evenement.etat = EvenementScenario.Etat.PUBLIE
        with self.assertRaises(ValidationError):
            self.evenement.save()

    def test_modification_apres_validation_impose_nouvelle_validation(self):
        self.evenement.etat = EvenementScenario.Etat.VALIDE
        self.evenement.valide_par = self.prof
        self.evenement.save()
        self.evenement.description = "Le client réduit le budget de 50 %."
        self.evenement.save()
        self.evenement.refresh_from_db()
        self.assertEqual(self.evenement.etat, EvenementScenario.Etat.BROUILLON)
        self.assertIsNone(self.evenement.valide_par)

    def test_evenement_publie_fige_et_conserve(self):
        EvenementScenario.objects.filter(pk=self.evenement.pk).update(etat=EvenementScenario.Etat.PUBLIE)
        self.evenement.refresh_from_db()
        self.evenement.titre = "Autre"
        with self.assertRaises(ValidationError):
            self.evenement.save()
        with self.assertRaises(ValidationError):
            self.evenement.delete()

    def test_changement_reel_coherent(self):
        self.evenement.nouvelle_echeance = timezone.now()
        with self.assertRaises(ValidationError):
            self.evenement.full_clean()  # nouvelle échéance réservée aux modifications de délai

    def test_reponse_d_une_equipe_d_un_autre_defi(self):
        EvenementScenario.objects.filter(pk=self.evenement.pk).update(etat=EvenementScenario.Etat.PUBLIE)
        autre_defi = self.creer_defi(Defi.Statut.INSCRIPTIONS)
        intruse = self.creer_equipe(autre_defi, self.carla, nom="Intruse")
        with self.assertRaises(ValidationError):
            ReponseEvenement.objects.create(evenement=self.evenement, equipe=intruse, analyse_impact="a",
                                            decisions="b", adaptation_planning="c", auteur=self.carla)
