"""Tests du module Utilisateurs (apps.accounts).

Lancer :      python manage.py test apps.accounts
Couverture :  coverage run manage.py test apps.accounts
              coverage report      (résumé dans le terminal)
              coverage html        (rapport détaillé : htmlcov/index.html)

Les modèles d'IA (reconnaissance faciale) sont remplacés par des « faux » (mock) dans les tests de vues :
les tests restent rapides, ne téléchargent rien et donnent toujours le même résultat.
"""

import io
import json
import random
import re
from datetime import timedelta
from unittest import mock

import pyotp
from django.contrib.auth.models import Group
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import deux_facteurs
from .forms import AjoutUtilisateurForm, InscriptionForm, creer_profil
from .importation import ImportErreur, importer, lire_fichier
from .models import CodeRecuperation, JournalConnexion, ProfilFacial, Utilisateur
from .securite import (
    MAX_ECHECS_COMPTE,
    _heure_inhabituelle,
    decrire_appareil,
    evaluer_risque,
    prefixe_reseau,
)

MOT_DE_PASSE = "Eduvox2026!test"
CHROME_WINDOWS = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36")
FIREFOX_ANDROID = "Mozilla/5.0 (Android 14; Mobile; rv:140.0) Gecko/140.0 Firefox/140.0"


def creer_utilisateur(username="amira", role=Utilisateur.Role.ETUDIANT, **champs):
    champs.setdefault("email", f"{username}@exemple.com")
    champs.setdefault("email_verifie", True)
    return Utilisateur.objects.create_user(username=username, password=MOT_DE_PASSE, role=role, **champs)


def lien_dans_mail(motif, numero=-1):
    """Extrait le chemin d'un lien (ex. /reinitialiser/...) dans un e-mail envoyé."""
    lien = re.search(rf"https?://\S+{motif}\S+/", mail.outbox[numero].body).group(0)
    return re.sub(r"^https?://[^/]+", "", lien)


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],  # tests plus rapides
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class BaseTest(TestCase):
    """Base commune : groupes et permissions créés avant chaque test."""

    def setUp(self):
        call_command("init_roles", stdout=io.StringIO())

    def connecter(self, user, client=None):
        client = client or self.client
        client.force_login(user)
        return client


# =====================================================================
# TESTS UNITAIRES : modèles, formulaires, logique métier
# =====================================================================

class ModeleUtilisateurTests(BaseTest):
    def test_str_affiche_nom_et_role(self):
        user = creer_utilisateur(first_name="Amira", last_name="Salah")
        self.assertEqual(str(user), "Amira Salah (Étudiant)")

    def test_email_unique(self):
        creer_utilisateur("a1", email="meme@exemple.com")
        with self.assertRaises(Exception):
            creer_utilisateur("a2", email="meme@exemple.com")

    def test_mot_de_passe_hache(self):
        user = creer_utilisateur()
        self.assertNotEqual(user.password, MOT_DE_PASSE)
        self.assertTrue(user.check_password(MOT_DE_PASSE))

    def test_groupe_synchronise_avec_le_role(self):
        user = creer_utilisateur()
        self.assertEqual(list(user.groups.values_list("name", flat=True)), ["Étudiant"])
        user.role = Utilisateur.Role.ENSEIGNANT
        user.save()
        self.assertEqual(list(user.groups.values_list("name", flat=True)), ["Enseignant"])

    def test_est_bloque(self):
        user = creer_utilisateur()
        self.assertFalse(user.est_bloque)
        user.bloque_jusqua = timezone.now() + timedelta(minutes=5)
        self.assertTrue(user.est_bloque)
        user.bloque_jusqua = timezone.now() - timedelta(seconds=1)
        self.assertFalse(user.est_bloque)

    def test_creer_profil_selon_role(self):
        etudiant = creer_utilisateur("etu")
        enseignant = creer_utilisateur("prof", role=Utilisateur.Role.ENSEIGNANT)
        creer_profil(etudiant)
        creer_profil(enseignant)
        self.assertTrue(hasattr(etudiant, "etudiant"))
        self.assertTrue(hasattr(enseignant, "enseignant"))


class RolesPermissionsTests(BaseTest):
    def test_init_roles_cree_les_trois_groupes(self):
        self.assertEqual(
            set(Group.objects.values_list("name", flat=True)), {"Étudiant", "Enseignant", "Administrateur"}
        )
        self.assertEqual(Group.objects.get(name="Administrateur").permissions.filter(content_type__app_label="accounts").count(), 9)

    def test_permissions_par_role(self):
        etudiant = creer_utilisateur("etu")
        enseignant = creer_utilisateur("prof", role=Utilisateur.Role.ENSEIGNANT)
        admin = creer_utilisateur("adm", role=Utilisateur.Role.ADMIN)
        self.assertTrue(etudiant.has_perm("accounts.passer_quiz"))
        self.assertFalse(etudiant.has_perm("accounts.publier_cours"))
        self.assertTrue(enseignant.has_perm("accounts.publier_cours"))
        self.assertFalse(enseignant.has_perm("accounts.gerer_utilisateurs"))
        self.assertTrue(admin.has_perm("accounts.gerer_utilisateurs"))


class FormulairesTests(BaseTest):
    def donnees(self, **autres):
        donnees = {"username": "nouveau", "first_name": "Nour", "last_name": "B", "email": "nouveau@exemple.com",
                   "role": "ETUDIANT", "password1": MOT_DE_PASSE, "password2": MOT_DE_PASSE}
        donnees.update(autres)
        return donnees

    def test_inscription_refuse_le_role_admin(self):
        form = InscriptionForm(data=self.donnees(role="ADMIN"))
        self.assertFalse(form.is_valid())
        self.assertIn("role", form.errors)

    def test_inscription_mots_de_passe_differents(self):
        form = InscriptionForm(data=self.donnees(password2="Autre2026!xyz"))
        self.assertFalse(form.is_valid())

    def test_inscription_cree_le_profil_etudiant(self):
        form = InscriptionForm(data=self.donnees())
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.assertTrue(hasattr(user, "etudiant"))
        self.assertFalse(user.email_verifie)

    def test_ajout_par_admin_verifie_l_email(self):
        form = AjoutUtilisateurForm(data=self.donnees(role="ENSEIGNANT", is_active="on"))
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.assertTrue(user.email_verifie)
        self.assertTrue(hasattr(user, "enseignant"))


class DeuxFacteursTests(BaseTest):
    def test_code_totp(self):
        secret = deux_facteurs.generer_secret()
        self.assertTrue(deux_facteurs.verifier_code_totp(secret, pyotp.TOTP(secret).now()))
        self.assertFalse(deux_facteurs.verifier_code_totp(secret, "000000"))
        self.assertFalse(deux_facteurs.verifier_code_totp("", "123456"))

    def test_codes_de_recuperation_haches_et_a_usage_unique(self):
        user = creer_utilisateur()
        codes = deux_facteurs.generer_codes_recuperation(user)
        self.assertEqual(len(set(codes)), 8)
        self.assertFalse(CodeRecuperation.objects.filter(code_hash=codes[0]).exists())  # jamais stocké en clair
        self.assertTrue(deux_facteurs.utiliser_code_recuperation(user, codes[0]))
        self.assertFalse(deux_facteurs.utiliser_code_recuperation(user, codes[0]))  # déjà utilisé

    def test_qr_code(self):
        user = creer_utilisateur()
        uri = deux_facteurs.uri_totp(user, deux_facteurs.generer_secret())
        self.assertIn("EduVox", uri)
        self.assertTrue(deux_facteurs.qr_code_base64(uri).startswith("iVBOR"))  # image PNG


class SecuriteUnitTests(BaseTest):
    def test_decrire_appareil(self):
        self.assertEqual(decrire_appareil(CHROME_WINDOWS), "Chrome sur Windows")
        self.assertEqual(decrire_appareil(FIREFOX_ANDROID), "Firefox sur Android")
        self.assertEqual(decrire_appareil(""), "Navigateur inconnu sur système inconnu")

    def test_prefixe_reseau(self):
        self.assertEqual(prefixe_reseau("192.168.1.42"), "192.168.1")
        self.assertEqual(prefixe_reseau(None), "")

    def historique(self, heures, jours=25):
        """Connexions passées aux heures indiquées (en heure locale)."""
        base = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
        hasard = random.Random(42)
        return [JournalConnexion(succes=True, date=base - timedelta(days=j) + timedelta(hours=hasard.choice(heures),
                                                                                       minutes=hasard.randint(0, 59)))
                for j in range(1, jours)]

    def test_ia_heure_habituelle_et_inhabituelle(self):
        historique = self.historique([8, 9, 10, 14, 15, 16])
        jour = timezone.localtime().replace(minute=30, second=0, microsecond=0)
        self.assertFalse(_heure_inhabituelle(historique, jour.replace(hour=9)))
        self.assertFalse(_heure_inhabituelle(historique, jour.replace(hour=15)))
        self.assertTrue(_heure_inhabituelle(historique, jour.replace(hour=3)))
        self.assertTrue(_heure_inhabituelle(historique, jour.replace(hour=22)))

    def test_ia_sans_historique(self):
        self.assertFalse(_heure_inhabituelle([], timezone.now()))

    def test_score_de_risque(self):
        user = creer_utilisateur()
        maintenant = timezone.now()
        for jour in range(1, 12):
            entree = JournalConnexion.objects.create(utilisateur=user, succes=True, appareil="Chrome sur Windows", ip="10.0.0.5")
            JournalConnexion.objects.filter(pk=entree.pk).update(date=maintenant - timedelta(days=jour, minutes=10))
        self.assertEqual(evaluer_risque(user, "10.0.0.7", "Chrome sur Windows", maintenant), (0, []))
        score, raisons = evaluer_risque(user, "41.230.1.1", "Firefox sur Android", maintenant)
        self.assertEqual(score, 60)
        self.assertEqual(len(raisons), 2)

    def test_les_connexions_suspectes_n_entrainent_pas_l_ia(self):
        user = creer_utilisateur()
        entree = JournalConnexion.objects.create(utilisateur=user, succes=True, suspect=True, appareil="Firefox sur Android")
        JournalConnexion.objects.filter(pk=entree.pk).update(date=timezone.now() - timedelta(days=1))
        self.assertEqual(evaluer_risque(user, "1.2.3.4", "Firefox sur Android", timezone.now()), (0, []))


class ImportationUnitTests(BaseTest):
    def fichier_csv(self, texte, encodage="utf-8"):
        return SimpleUploadedFile("classe.csv", texte.encode(encodage))

    def test_csv_point_virgule_et_nom_utilisateur_automatique(self):
        lignes = lire_fichier(self.fichier_csv("prenom;nom;email;role\nAmira;Salah;amira@ex.com;Etudiant\n"))
        crees, erreurs = importer(lignes)
        self.assertEqual(erreurs, [])
        self.assertEqual(crees[0].username, "asalah")
        self.assertTrue(crees[0].email_verifie)

    def test_csv_windows_cp1252_avec_virgules(self):
        lignes = lire_fichier(self.fichier_csv("Prénom,Nom,E-mail\nHédi,Zaïri,hedi@ex.com\n", "cp1252"))
        crees, _ = importer(lignes)
        self.assertEqual(crees[0].first_name, "Hédi")

    def test_erreurs_ligne_par_ligne(self):
        creer_utilisateur("existant", email="pris@ex.com")
        texte = ("prenom;nom;email;role\n"
                 "Ali;;ali@ex.com;\n"              # ligne 2 : nom manquant
                 "Sara;K;pas-un-email;\n"          # ligne 3 : e-mail invalide
                 "Nour;M;pris@ex.com;\n"           # ligne 4 : e-mail déjà utilisé
                 "Rim;B;rim@ex.com;Directeur\n"    # ligne 5 : rôle inconnu
                 "Sami;T;sami@ex.com;enseignant\n")  # ligne 6 : valide
        crees, erreurs = importer(lire_fichier(self.fichier_csv(texte)))
        self.assertEqual([numero for numero, _ in erreurs], [2, 3, 4, 5])
        self.assertEqual(crees[0].role, Utilisateur.Role.ENSEIGNANT)

    def test_fichier_excel(self):
        from openpyxl import Workbook

        classeur = Workbook()
        classeur.active.append(["prenom", "nom", "email"])
        classeur.active.append(["Inès", "Gharbi", "ines@ex.com"])
        tampon = io.BytesIO()
        classeur.save(tampon)
        crees, _ = importer(lire_fichier(SimpleUploadedFile("c.xlsx", tampon.getvalue())),
                            role_par_defaut=Utilisateur.Role.ENSEIGNANT)
        self.assertEqual(crees[0].role, Utilisateur.Role.ENSEIGNANT)

    def test_fichiers_refuses(self):
        with self.assertRaisesMessage(ImportErreur, "Format non pris en charge"):
            lire_fichier(SimpleUploadedFile("x.txt", b"abc"))
        with self.assertRaisesMessage(ImportErreur, "Colonnes obligatoires manquantes"):
            lire_fichier(self.fichier_csv("nom;age\nA;3\n"))
        with self.assertRaisesMessage(ImportErreur, "aucune ligne"):
            lire_fichier(self.fichier_csv("prenom;nom;email\n"))


# =====================================================================
# TESTS D'INTÉGRATION : vues, codes HTTP, parcours complets
# =====================================================================

class InscriptionEtVerificationEmailTests(BaseTest):
    def test_parcours_complet(self):
        reponse = self.client.post(reverse("inscription"), {
            "username": "nour", "first_name": "Nour", "last_name": "B", "email": "nour@exemple.com",
            "role": "ETUDIANT", "password1": MOT_DE_PASSE, "password2": MOT_DE_PASSE,
        })
        self.assertRedirects(reponse, reverse("verification-envoyee"))
        self.assertEqual(len(mail.outbox), 1)

        # Connexion refusée tant que l'e-mail n'est pas confirmé
        reponse = self.client.post(reverse("connexion"), {"username": "nour", "password": MOT_DE_PASSE})
        self.assertContains(reponse, "confirmer votre adresse")

        lien = lien_dans_mail("/verifier-email/")
        self.client.get(lien)
        self.assertTrue(Utilisateur.objects.get(username="nour").email_verifie)
        reponse = self.client.get(lien, follow=True)
        self.assertContains(reponse, "invalide")  # le lien ne sert qu'une fois


class ConnexionEtJournalTests(BaseTest):
    def test_page_protegee_redirige_vers_la_connexion(self):
        reponse = self.client.get(reverse("index"))
        self.assertRedirects(reponse, f"{reverse('connexion')}?next=/")

    def test_connexion_reussie_journalisee(self):
        creer_utilisateur()
        client = Client(HTTP_USER_AGENT=CHROME_WINDOWS)
        reponse = client.post(reverse("connexion"), {"username": "amira", "password": MOT_DE_PASSE})
        self.assertEqual(reponse.status_code, 302)
        entree = JournalConnexion.objects.first()
        self.assertTrue(entree.succes)
        self.assertEqual(entree.appareil, "Chrome sur Windows")
        self.assertEqual(entree.score_risque, 0)  # première connexion

    def test_echecs_journalises(self):
        creer_utilisateur()
        self.client.post(reverse("connexion"), {"username": "amira", "password": "faux"})
        self.client.post(reverse("connexion"), {"username": "fantome", "password": "faux"})
        motifs = list(JournalConnexion.objects.order_by("date").values_list("motif", flat=True))
        self.assertEqual(motifs, ["Mot de passe incorrect", "Compte inconnu"])

    def test_connexion_suspecte_declenche_une_alerte(self):
        user = creer_utilisateur()
        for jour in range(1, 12):
            entree = JournalConnexion.objects.create(utilisateur=user, succes=True, appareil="Chrome sur Windows", ip="127.0.0.1")
            JournalConnexion.objects.filter(pk=entree.pk).update(date=timezone.now() - timedelta(days=jour, minutes=10))
        client = Client(HTTP_USER_AGENT=FIREFOX_ANDROID, REMOTE_ADDR="41.230.10.5")
        client.post(reverse("connexion"), {"username": "amira", "password": MOT_DE_PASSE})
        entree = JournalConnexion.objects.first()
        self.assertTrue(entree.suspect)
        self.assertTrue(any("inhabituelle" in message.subject for message in mail.outbox))

    def test_compte_desactive(self):
        creer_utilisateur(is_active=False)
        reponse = self.client.post(reverse("connexion"), {"username": "amira", "password": MOT_DE_PASSE})
        self.assertEqual(reponse.status_code, 200)

    def test_deconnexion(self):
        self.connecter(creer_utilisateur())
        self.client.post(reverse("deconnexion"))
        self.assertEqual(self.client.get(reverse("index")).status_code, 302)


class BlocageForceBruteTests(BaseTest):
    def setUp(self):
        super().setUp()
        self.user = creer_utilisateur()

    def rater(self, fois, client=None):
        client = client or self.client
        for _ in range(fois):
            reponse = client.post(reverse("connexion"), {"username": "amira", "password": "faux"})
        return reponse

    def test_blocage_apres_5_echecs(self):
        self.rater(MAX_ECHECS_COMPTE - 1)
        self.user.refresh_from_db()
        self.assertFalse(self.user.est_bloque)
        reponse = self.rater(1)
        self.user.refresh_from_db()
        self.assertTrue(self.user.est_bloque)
        self.assertContains(reponse, "compte bloqué")
        self.assertTrue(any("bloqué" in message.subject for message in mail.outbox))

    def test_bon_mot_de_passe_refuse_pendant_le_blocage(self):
        self.rater(MAX_ECHECS_COMPTE)
        reponse = self.client.post(reverse("connexion"), {"username": "amira", "password": MOT_DE_PASSE})
        self.assertContains(reponse, "temporairement bloqué")
        self.assertEqual(self.client.get(reverse("index")).status_code, 302)

    def test_une_reussite_remet_le_compteur_a_zero(self):
        self.rater(MAX_ECHECS_COMPTE - 1)
        self.client.post(reverse("connexion"), {"username": "amira", "password": MOT_DE_PASSE})
        self.client.post(reverse("deconnexion"))
        self.rater(MAX_ECHECS_COMPTE - 1)
        self.user.refresh_from_db()
        self.assertFalse(self.user.est_bloque)

    def test_deblocage_par_admin(self):
        self.rater(MAX_ECHECS_COMPTE)
        admin = self.connecter(creer_utilisateur("adm", role=Utilisateur.Role.ADMIN), Client())
        admin.post(reverse("debloquer-utilisateur", args=[self.user.pk]))
        self.user.refresh_from_db()
        self.assertFalse(self.user.est_bloque)

    def test_deblocage_par_reinitialisation_du_mot_de_passe(self):
        self.rater(MAX_ECHECS_COMPTE)
        Client().post(reverse("mdp-oublie"), {"email": self.user.email})
        client = Client()
        reponse = client.get(lien_dans_mail("/reinitialiser/"), follow=True)
        client.post(reponse.redirect_chain[-1][0], {"new_password1": "Nouveau2026!xyz", "new_password2": "Nouveau2026!xyz"})
        self.user.refresh_from_db()
        self.assertFalse(self.user.est_bloque)
        self.assertTrue(self.user.check_password("Nouveau2026!xyz"))

    def test_blocage_par_appareil(self):
        pirate = Client(REMOTE_ADDR="10.9.9.9")
        for numero in range(20):
            pirate.post(reverse("connexion"), {"username": f"inconnu{numero}", "password": "x"})
        reponse = pirate.post(reverse("connexion"), {"username": "amira", "password": MOT_DE_PASSE})
        self.assertContains(reponse, "depuis cet appareil")
        reponse = Client(REMOTE_ADDR="10.9.9.8").post(reverse("connexion"), {"username": "amira", "password": MOT_DE_PASSE})
        self.assertEqual(reponse.status_code, 302)


class DeuxFacteursVuesTests(BaseTest):
    def test_activation_puis_connexion_en_deux_etapes(self):
        user = creer_utilisateur()
        self.connecter(user)
        reponse = self.client.get(reverse("activer-2fa"))
        self.assertContains(reponse, "data:image/png;base64")
        secret = self.client.session["2fa_secret_temp"]
        self.client.post(reverse("activer-2fa"), {"code": pyotp.TOTP(secret).now()})
        user.refresh_from_db()
        self.assertTrue(user.double_auth_active)
        self.client.post(reverse("deconnexion"))

        reponse = self.client.post(reverse("connexion"), {"username": "amira", "password": MOT_DE_PASSE})
        self.assertRedirects(reponse, reverse("connexion-2fa"))
        self.assertEqual(self.client.get(reverse("index")).status_code, 302)  # pas encore connecté
        self.client.post(reverse("connexion-2fa"), {"mode": "application", "code": pyotp.TOTP(secret).now()})
        self.assertEqual(self.client.get(reverse("index")).status_code, 200)
        self.assertTrue(JournalConnexion.objects.filter(succes=True, double_auth=True).exists())

    def test_cinq_codes_faux(self):
        user = creer_utilisateur(double_auth_active=True, secret_totp=deux_facteurs.generer_secret())
        self.client.post(reverse("connexion"), {"username": user.username, "password": MOT_DE_PASSE})
        for _ in range(5):
            reponse = self.client.post(reverse("connexion-2fa"), {"mode": "application", "code": "000000"})
        self.assertRedirects(reponse, reverse("connexion"))


class ProfilTests(BaseTest):
    def setUp(self):
        super().setUp()
        self.user = creer_utilisateur()
        self.connecter(self.user)

    def test_modifier_ses_informations(self):
        reponse = self.client.post(reverse("mon-profil"), {
            "enregistrer_profil": "1", "first_name": "Amira", "last_name": "Salah", "email": "amira@exemple.com", "niveau": "L2",
        })
        self.assertRedirects(reponse, reverse("mon-profil"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "Amira")

    def test_changer_son_mot_de_passe_sans_etre_deconnecte(self):
        self.client.post(reverse("mon-profil"), {
            "changer_mdp": "1", "old_password": MOT_DE_PASSE,
            "new_password1": "Nouveau2026!xyz", "new_password2": "Nouveau2026!xyz",
        })
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Nouveau2026!xyz"))
        self.assertEqual(self.client.get(reverse("mon-profil")).status_code, 200)

    def test_supprimer_son_compte(self):
        self.client.post(reverse("supprimer-mon-compte"), {"mot_de_passe": "faux"})
        self.assertTrue(Utilisateur.objects.filter(pk=self.user.pk).exists())
        self.client.post(reverse("supprimer-mon-compte"), {"mot_de_passe": MOT_DE_PASSE})
        self.assertFalse(Utilisateur.objects.filter(pk=self.user.pk).exists())

    def test_mes_connexions(self):
        self.assertEqual(self.client.get(reverse("mes-connexions")).status_code, 200)


class AdministrationTests(BaseTest):
    def setUp(self):
        super().setUp()
        self.admin = creer_utilisateur("adm", role=Utilisateur.Role.ADMIN)
        self.etudiant = creer_utilisateur("etu")

    def test_crud_utilisateurs(self):
        self.connecter(self.admin)
        # Create
        self.client.post(reverse("ajouter-utilisateur"), {
            "username": "prof1", "first_name": "Sami", "last_name": "T", "email": "prof1@exemple.com",
            "role": "ENSEIGNANT", "is_active": "on", "password1": MOT_DE_PASSE, "password2": MOT_DE_PASSE,
        })
        prof = Utilisateur.objects.get(username="prof1")
        # Read
        self.assertContains(self.client.get(reverse("liste-utilisateurs"), {"q": "sami"}), "Sami")
        # Update
        self.client.post(reverse("modifier-utilisateur", args=[prof.pk]), {
            "username": "prof1", "first_name": "Sami", "last_name": "T", "email": "prof1@exemple.com", "role": "ETUDIANT",
        })
        prof.refresh_from_db()
        self.assertEqual(prof.role, Utilisateur.Role.ETUDIANT)
        self.assertFalse(prof.is_active)  # case « actif » non cochée
        # Delete
        self.client.post(reverse("supprimer-utilisateur", args=[prof.pk]))
        self.assertFalse(Utilisateur.objects.filter(pk=prof.pk).exists())

    def test_admin_ne_peut_pas_se_supprimer_ni_se_desactiver(self):
        self.connecter(self.admin)
        self.client.post(reverse("supprimer-utilisateur", args=[self.admin.pk]))
        self.client.post(reverse("activer-utilisateur", args=[self.admin.pk]))
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_acces_refuse_par_url_directe(self):
        """Un étudiant ne peut pas contourner le menu en tapant l'adresse : erreur 403."""
        self.connecter(self.etudiant)
        pages = [
            ("get", reverse("liste-utilisateurs")), ("get", reverse("ajouter-utilisateur")),
            ("get", reverse("roles-permissions")), ("get", reverse("journal-connexions")),
            ("get", reverse("visages-doublons")), ("get", reverse("importer-utilisateurs")),
            ("post", reverse("supprimer-utilisateur", args=[self.admin.pk])),
            ("post", reverse("activer-utilisateur", args=[self.admin.pk])),
            ("post", reverse("debloquer-utilisateur", args=[self.admin.pk])),
        ]
        for methode, url in pages:
            with self.subTest(url=url):
                self.assertEqual(getattr(self.client, methode)(url).status_code, 403)
        self.assertTrue(Utilisateur.objects.filter(pk=self.admin.pk, is_active=True).exists())

    def test_page_403_personnalisee(self):
        self.connecter(self.etudiant)
        self.assertContains(self.client.get(reverse("liste-utilisateurs")), "Accès refusé", status_code=403)

    def test_tableaux_de_bord_selon_le_role(self):
        enseignant = creer_utilisateur("prof", role=Utilisateur.Role.ENSEIGNANT)
        for user, gabarit in [(self.admin, "dashboard_admin.html"), (enseignant, "dashboard_enseignant.html"),
                              (self.etudiant, "dashboard_etudiant.html")]:
            with self.subTest(role=user.role):
                self.assertTemplateUsed(self.connecter(user, Client()).get(reverse("index")), gabarit)

    def test_pages_admin_accessibles(self):
        self.connecter(self.admin)
        for nom in ["liste-utilisateurs", "roles-permissions", "journal-connexions", "visages-doublons",
                    "importer-utilisateurs", "modele-import"]:
            with self.subTest(page=nom):
                self.assertEqual(self.client.get(reverse(nom)).status_code, 200)

    def test_import_par_la_page_admin(self):
        self.connecter(self.admin)
        fichier = SimpleUploadedFile("classe.csv", "prenom;nom;email\nAmira;Salah;amira.s@ex.com\n".encode())
        reponse = self.client.post(reverse("importer-utilisateurs"),
                                   {"fichier": fichier, "role_par_defaut": "ETUDIANT", "envoyer_emails": "on"})
        self.assertContains(reponse, "1 compte")
        self.assertIn("Bienvenue", mail.outbox[-1].subject)


# =====================================================================
# Reconnaissance faciale (vues) : le modèle d'IA est simulé
# =====================================================================

def encodage(personne, variation=0.0):
    """Faux encodage de 128 nombres, différent pour chaque personne."""
    hasard = random.Random(personne)
    base = [hasard.uniform(-1, 1) for _ in range(128)]
    bruit = random.Random(f"{personne}{variation}")
    return [v + bruit.uniform(-variation, variation) for v in base]


def faux_analyseur(image):
    """Les « images » des tests sont des textes comme 'amira|0.0' (personne | rotation de la tête)."""
    from ai_services import VisageErreur

    if image == "vide":
        raise VisageErreur("Aucun visage détecté.")
    personne, lacet = image.split("|")
    return {"encodage": encodage(personne, 0.1), "lacet": float(lacet)}


@mock.patch("apps.accounts.views_visage.analyser_visage", side_effect=faux_analyseur)
class ReconnaissanceFacialeTests(BaseTest):
    def setUp(self):
        super().setUp()
        self.user = creer_utilisateur()

    def envoyer(self, url, donnees, client=None):
        return (client or self.client).post(url, json.dumps(donnees), content_type="application/json")

    def enregistrer(self, user, personne):
        ProfilFacial.objects.create(utilisateur=user, encodages=[encodage(personne)], consentement_le=timezone.now())

    def test_enregistrement(self, _):
        self.connecter(self.user)
        url = reverse("enregistrer-visage")
        donnees = {"images": ["amira|0"] * 3, "consentement": True, "mot_de_passe": MOT_DE_PASSE}
        self.assertEqual(self.envoyer(url, {**donnees, "consentement": False}).status_code, 400)
        self.assertEqual(self.envoyer(url, {**donnees, "mot_de_passe": "faux"}).status_code, 400)
        self.assertEqual(self.envoyer(url, {**donnees, "images": ["amira|0", "vide", "amira|0"]}).status_code, 400)
        self.assertEqual(self.envoyer(url, {**donnees, "images": ["amira|0", "sami|0", "amira|0"]}).status_code, 400)
        self.assertEqual(self.envoyer(url, donnees).status_code, 200)
        self.assertEqual(len(ProfilFacial.objects.get(utilisateur=self.user).encodages), 3)

    def test_un_visage_pour_un_seul_compte(self, _):
        self.enregistrer(creer_utilisateur("autre"), "amira")
        self.connecter(self.user)
        reponse = self.envoyer(reverse("enregistrer-visage"),
                               {"images": ["amira|0"] * 3, "consentement": True, "mot_de_passe": MOT_DE_PASSE})
        self.assertIn("déjà associé", reponse.json()["message"])

    def test_connexion_avec_nom_d_utilisateur(self, _):
        self.enregistrer(self.user, "amira")
        reponse = self.envoyer(reverse("connexion-visage"),
                               {"username": "amira", "image_face": "amira|0", "image_tournee": "amira|0.5"})
        self.assertTrue(reponse.json()["ok"])
        self.assertEqual(JournalConnexion.objects.first().methode, JournalConnexion.Methode.VISAGE)

    def test_identification_sans_nom_d_utilisateur(self, _):
        self.enregistrer(self.user, "amira")
        self.enregistrer(creer_utilisateur("sami"), "sami")
        self.envoyer(reverse("connexion-visage"), {"username": "", "image_face": "sami|0", "image_tournee": "sami|0.5"})
        self.assertEqual(int(self.client.session["_auth_user_id"]), Utilisateur.objects.get(username="sami").pk)

    def test_photo_immobile_refusee(self, _):
        """Détection de vivacité : sans mouvement de tête (photo, écran), la connexion est refusée."""
        self.enregistrer(self.user, "amira")
        reponse = self.envoyer(reverse("connexion-visage"),
                               {"username": "", "image_face": "amira|0", "image_tournee": "amira|0.05"})
        self.assertIn("Mouvement de tête", reponse.json()["message"])

    def test_visage_inconnu_et_blocage(self, _):
        self.enregistrer(self.user, "amira")
        for _ in range(5):
            reponse = self.envoyer(reverse("connexion-visage"),
                                   {"username": "amira", "image_face": "pirate|0", "image_tournee": "pirate|0.5"})
        self.assertEqual(reponse.status_code, 429)
        self.assertEqual(JournalConnexion.objects.filter(succes=False, methode="VISAGE").count(), 5)

    def test_2fa_demandee_apres_le_visage(self, _):
        self.enregistrer(self.user, "amira")
        self.user.double_auth_active = True
        self.user.secret_totp = deux_facteurs.generer_secret()
        self.user.save()
        reponse = self.envoyer(reverse("connexion-visage"),
                               {"username": "", "image_face": "amira|0", "image_tournee": "amira|0.5"})
        self.assertEqual(reponse.json()["redirection"], reverse("connexion-2fa"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_compte_bloque(self, _):
        self.enregistrer(self.user, "amira")
        self.user.bloque_jusqua = timezone.now() + timedelta(minutes=5)
        self.user.save()
        reponse = self.envoyer(reverse("connexion-visage"),
                               {"username": "", "image_face": "amira|0", "image_tournee": "amira|0.5"})
        self.assertEqual(reponse.status_code, 403)

    def test_page_doublons_et_suppression(self, _):
        self.enregistrer(self.user, "amira")
        self.enregistrer(creer_utilisateur("clone"), "amira")
        admin = self.connecter(creer_utilisateur("adm", role=Utilisateur.Role.ADMIN), Client())
        self.assertContains(admin.get(reverse("visages-doublons")), "1 doublon")
        self.connecter(self.user)
        self.client.post(reverse("supprimer-visage"))