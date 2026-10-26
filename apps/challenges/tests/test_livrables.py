"""Versionnement des livrables, validation des fichiers, retard, double soumission."""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.challenges.models import Jalon, VersionLivrable
from apps.challenges.services import livrables as regles
from apps.challenges.stockage import nom_affichable, valider_fichier

from .base import BaseEduSprint

Etat = VersionLivrable.Etat


class ValidationFichierTests(BaseEduSprint):
    def test_formats_et_signatures(self):
        valides = [("rapport.pdf", b"%PDF-1.7 ..."), ("notes.txt", "Texte accentué é".encode()),
                   ("maquette.png", b"\x89PNG\r\n\x1a\n...."), ("budget.xlsx", b"PK\x03\x04....")]
        for nom, contenu in valides:
            with self.subTest(nom):
                valider_fichier(SimpleUploadedFile(nom, contenu))
        invalides = [("script.exe", b"MZ..."), ("faux.pdf", b"<html>pas un pdf"), ("image.png", b"GIF89a"),
                     ("binaire.txt", b"\x00\x01\x02"), ("latin1.txt", "é".encode("latin-1") * 20), ("vide.pdf", b""),
                     ("page.html", b"<script>alert(1)</script>")]
        for nom, contenu in invalides:
            with self.subTest(nom), self.assertRaises(ValidationError):
                valider_fichier(SimpleUploadedFile(nom, contenu))

    @override_settings(EDUSPRINT_FICHIER_MAX_MO=1)
    def test_taille_maximale(self):
        with self.assertRaises(ValidationError):
            valider_fichier(SimpleUploadedFile("gros.txt", b"a" * (1024 * 1024 + 1)))

    def test_noms_surs(self):
        self.assertEqual(nom_affichable("../../etc/passwd"), "passwd")
        self.assertEqual(nom_affichable("C:\\Users\\x\\mon rapport final.pdf"), "mon_rapport_final.pdf")
        self.assertNotIn("/", nom_affichable("a/b/<script>.pdf"))

    def test_chemin_stocke_aleatoire(self):
        _, equipe = self.defi_en_cours()
        version = regles.creer_livrable(equipe, self.alice, "Doc", fichier=SimpleUploadedFile(
            "../../rapport secret.pdf", b"%PDF-1.4 x"))
        self.assertNotIn("secret", version.fichier.name)
        self.assertTrue(version.fichier.name.startswith(f"livrables/defi_{equipe.defi_id}/equipe_{equipe.pk}/"))
        self.assertEqual(version.nom_fichier, "rapport_secret.pdf")

    def test_formulaire_refuse_un_lien_non_http(self):
        _, equipe = self.defi_en_cours()
        self.connecter(self.alice)
        self.client.post(reverse("creer-livrable", args=[equipe.pk]), {
            "titre": "Lien", "description": "", "jalon": "", "lien": "file:///etc/passwd", "texte": "", "note_equipe": ""})
        self.assertFalse(equipe.livrables.exists())


class VersionnementTests(BaseEduSprint):
    def setUp(self):
        self.defi, self.equipe = self.defi_en_cours()
        self.v1 = regles.creer_livrable(self.equipe, self.bob, "Proposition", texte="Version 1")

    def test_soumission_reservee_au_coordinateur(self):
        with self.assertRaises(ValidationError):
            regles.soumettre(self.v1, self.bob)
        regles.soumettre(self.v1, self.alice)
        self.v1.refresh_from_db()
        self.assertEqual(self.v1.etat, Etat.SOUMIS)

    def test_double_soumission_sans_effet(self):
        regles.soumettre(self.v1, self.alice)
        date = VersionLivrable.objects.get(pk=self.v1.pk).date_soumission
        with self.assertRaises(ValidationError):
            regles.soumettre(self.v1, self.alice)
        self.assertEqual(VersionLivrable.objects.get(pk=self.v1.pk).date_soumission, date)

    def test_double_clic_par_l_interface(self):
        self.connecter(self.alice)
        url = reverse("soumettre-version", args=[self.v1.pk])
        self.client.post(url)
        self.client.post(url)
        self.assertEqual(VersionLivrable.objects.filter(livrable=self.v1.livrable, etat=Etat.SOUMIS).count(), 1)

    def test_nouvelle_version_seulement_apres_demande_de_complement(self):
        with self.assertRaises(ValidationError):
            regles.nouvelle_version(self.v1.livrable, self.bob, texte="v2")  # un brouillon existe déjà
        regles.soumettre(self.v1, self.alice)
        with self.assertRaises(ValidationError):
            regles.nouvelle_version(self.v1.livrable, self.bob, texte="v2")  # en attente de retour
        regles.evaluer(self.v1, self.prof, Etat.A_COMPLETER, "Ajoutez le budget.")
        v2 = regles.nouvelle_version(self.v1.livrable, self.bob, texte="v2")
        self.assertEqual(v2.numero, 2)
        self.v1.refresh_from_db()
        self.assertEqual((self.v1.texte, self.v1.retour_enseignant), ("Version 1", "Ajoutez le budget."))

    def test_brouillon_modifiable_par_son_auteur_seulement(self):
        with self.assertRaises(ValidationError):
            regles.modifier_brouillon(self.v1, self.alice, texte="Réécrit par la coordinatrice")
        regles.modifier_brouillon(self.v1, self.bob, texte="Corrigé")
        self.v1.refresh_from_db()
        self.assertEqual(self.v1.texte, "Corrigé")

    def test_suppression_du_brouillon_et_du_fichier(self):
        version = regles.creer_livrable(self.equipe, self.alice, "Annexe",
                                        fichier=SimpleUploadedFile("a.txt", b"annexe"))
        stockage, nom = version.fichier.storage, version.fichier.name
        self.assertTrue(stockage.exists(nom))
        with self.assertRaises(ValidationError):
            regles.supprimer_brouillon(version, self.bob)
        self.assertIsNone(regles.supprimer_brouillon(version, self.alice))
        self.assertFalse(stockage.exists(nom))

    def test_retard_calcule_par_rapport_au_jalon(self):
        jalon = Jalon.objects.get(defi=self.defi, ordre=1)
        Jalon.objects.filter(pk=jalon.pk).update(echeance=timezone.now() - timedelta(hours=1))
        version = regles.creer_livrable(self.equipe, self.alice, "Diagnostic", jalon=Jalon.objects.get(pk=jalon.pk),
                                        texte="En retard")
        version = regles.soumettre(version, self.alice)
        self.assertTrue(version.en_retard)
        self.assertFalse(regles.soumettre(self.v1, self.alice).en_retard)

    def test_retour_obligatoire_pour_un_complement_et_enseignant_requis(self):
        regles.soumettre(self.v1, self.alice)
        with self.assertRaises(ValidationError):
            regles.evaluer(self.v1, self.prof, Etat.A_COMPLETER, " ")
        with self.assertRaises(ValidationError):
            regles.evaluer(self.v1, self.autre_prof, Etat.ACCEPTE, "")
        regles.evaluer(self.v1, self.prof, Etat.ACCEPTE, "")
        with self.assertRaises(ValidationError):
            regles.evaluer(self.v1, self.prof, Etat.A_COMPLETER, "Finalement non")
