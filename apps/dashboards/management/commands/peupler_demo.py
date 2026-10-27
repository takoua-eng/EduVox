"""Remplit la base avec des données de démonstration cohérentes (utilisateurs, cours, quiz, activité).

Toutes les données créées sont reconnaissables (identifiant « vitrine_… », e-mail « @vitrine.eduvox.test ») et
se suppriment avec --supprimer. Hors SQLite (base partagée), --oui est obligatoire.
"""

import io
import os
import random
import secrets
import textwrap
import unicodedata
from datetime import timedelta
from decimal import Decimal

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db import DEFAULT_DB_ALIAS, connections, transaction
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone
from django.utils.text import slugify

from apps.accounts.models import Enseignant, Etudiant, JournalConnexion, Utilisateur
from apps.assistant.models import MessageVocal, SessionAssistant
from apps.courses.models import Avis, Consultation, Cours, Favori, Matiere, Resume, Transcription
from apps.quizzes.models import Choix, Question, Quiz, ReponseEtudiant, TentativeQuiz

from ...donnees_demo import COMMENTAIRES_AVIS, COURS, ECHANGES_ASSISTANT, ENSEIGNANTS, NOMS, PRENOMS

PREFIXE = "vitrine_"   # distinct des comptes demo_* du module EduSprint (donnees_demo_edusprint)
DOMAINE = "vitrine.eduvox.test"   # domaine réservé (.test) : aucun e-mail ne peut être réellement délivré
JOURS = 30                     # l'activité est répartie sur les 30 derniers jours
POINTS_QUESTION = Decimal("2")
APPAREILS = ["Chrome sur Windows", "Firefox sur Windows", "Chrome sur Android", "Safari sur iPhone/iPad", "Edge sur Windows"]


def utilisateurs_demo():
    return Utilisateur.objects.filter(username__startswith=PREFIXE, email__endswith=f"@{DOMAINE}")


def identifiant(prenom, nom):
    texte = unicodedata.normalize("NFKD", f"{prenom}.{nom}").encode("ascii", "ignore").decode().lower()
    return PREFIXE + texte.replace(" ", "")


def pdf_du_cours(titre, texte):
    """Petit PDF contenant le titre et le texte du cours (lisible par l'extraction de texte du module Cours)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    tampon = io.BytesIO()
    page = canvas.Canvas(tampon, pagesize=A4)
    page.setTitle(titre)
    page.setFont("Helvetica-Bold", 16)
    page.drawString(60, 780, titre)
    page.setFont("Helvetica", 11)
    y = 750
    for ligne in textwrap.wrap(texte, 90):
        page.drawString(60, y, ligne)
        y -= 16
    page.save()
    return tampon.getvalue()


class Command(BaseCommand):
    help = "Remplit la base avec des données de démonstration (supprimables avec --supprimer)."

    def add_arguments(self, parser):
        parser.add_argument("--etudiants", type=int, default=20, help="Nombre d'étudiants (20 au maximum).")
        parser.add_argument("--mot-de-passe", help="Mot de passe des comptes de démo (sinon : DEMO_MOT_DE_PASSE, "
                                                   "sinon un mot de passe aléatoire affiché une fois).")
        parser.add_argument("--seed", type=int, default=2026, help="Graine : mêmes données à chaque exécution.")
        parser.add_argument("--reinitialiser", action="store_true", help="Supprime les données de démo puis les recrée.")
        parser.add_argument("--supprimer", action="store_true", help="Supprime uniquement les données de démo.")
        parser.add_argument("--oui", action="store_true", help="Confirme l'écriture dans une base autre que SQLite.")

    # ---------- Garde-fous ----------

    def verifier_base(self, options):
        connexion = connections[DEFAULT_DB_ALIAS]
        nom = connexion.settings_dict.get("NAME")
        self.stdout.write(f"Base cible : {connexion.vendor} ({nom})")
        if connexion.vendor != "sqlite" and not options["oui"]:
            raise CommandError("Cette base n'est pas une base SQLite locale (base partagée ?). "
                               "Relancez avec --oui pour confirmer l'écriture.")
        executor = MigrationExecutor(connexion)
        en_attente = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if en_attente:
            noms = ", ".join(f"{m.app_label}.{m.name}" for m, _ in en_attente[:5])
            raise CommandError(f"Migrations non appliquées ({noms}…) : lancez d'abord « python manage.py migrate ».")

    def handle(self, *args, **options):
        self.verifier_base(options)
        if options["supprimer"] or options["reinitialiser"]:
            self.supprimer()
            if options["supprimer"]:
                return
        elif utilisateurs_demo().exists():
            raise CommandError("Des données de démo existent déjà : utilisez --reinitialiser ou --supprimer.")

        mot_de_passe = options["mot_de_passe"] or os.environ.get("DEMO_MOT_DE_PASSE")
        affiche = not mot_de_passe  # une variable vide compte comme absente
        mot_de_passe = mot_de_passe or secrets.token_urlsafe(9)
        self.hasard = random.Random(options["seed"])
        self.maintenant = timezone.now()
        self.fichiers = []
        try:
            with transaction.atomic():
                bilan = self.creer(mot_de_passe, min(options["etudiants"], len(PRENOMS)))
        except Exception:
            for nom in self.fichiers:  # la base est annulée : on retire aussi les PDF déjà écrits
                default_storage.delete(nom)
            raise

        self.stdout.write(self.style.SUCCESS("Données de démonstration créées :"))
        for libelle, nombre in bilan.items():
            self.stdout.write(f"  {libelle} : {nombre}")
        self.stdout.write(f"Comptes : {PREFIXE}<prénom>.<nom> (ex. {self.exemples[0]}, {self.exemples[1]})")
        if affiche:
            self.stdout.write(self.style.WARNING(f"Mot de passe de tous les comptes de démo (affiché une seule fois) : "
                                                 f"{mot_de_passe}"))

    def supprimer(self):
        utilisateurs = utilisateurs_demo()
        cours = Cours.objects.filter(enseignant__utilisateur__in=utilisateurs)
        fichiers = [c.fichier.name for c in cours if c.fichier]
        with transaction.atomic():
            nb_utilisateurs = utilisateurs.count()
            nb_cours = cours.count()
            utilisateurs.delete()  # en cascade : profils, cours, quiz, tentatives, consultations, journal…
        for nom in fichiers:
            default_storage.delete(nom)
        self.stdout.write(f"Données de démo supprimées : {nb_utilisateurs} comptes, {nb_cours} cours.")

    # ---------- Création ----------

    def date_passee(self, jours_min=0, jours_max=JOURS):
        """Date aléatoire entre jours_max et jours_min jours avant maintenant."""
        secondes = self.hasard.uniform(jours_min * 86400, jours_max * 86400)
        return self.maintenant - timedelta(seconds=secondes)

    def apres(self, date, jours_max=JOURS):
        """Date aléatoire entre `date` et maintenant (jamais dans le futur)."""
        ecart = max((self.maintenant - date).total_seconds(), 60)
        return date + timedelta(seconds=self.hasard.uniform(0, min(ecart, jours_max * 86400)))

    def utilisateur(self, prenom, nom, role, mot_de_passe):
        user = Utilisateur.objects.create_user(
            username=identifiant(prenom, nom), email=f"{identifiant(prenom, nom)[len(PREFIXE):]}@{DOMAINE}",
            password=mot_de_passe, first_name=prenom, last_name=nom, role=role, email_verifie=True)
        Utilisateur.objects.filter(pk=user.pk).update(date_inscription=self.date_passee(JOURS + 5, JOURS + 60))
        return user

    def creer(self, mot_de_passe, nb_etudiants):
        h = self.hasard
        profs, cours_par_matiere, tous_les_cours, quiz_publies = [], {}, [], []

        for prenom, nom, matiere_nom in ENSEIGNANTS:
            prof = self.utilisateur(prenom, nom, Utilisateur.Role.ENSEIGNANT, mot_de_passe)
            enseignant = Enseignant.objects.create(utilisateur=prof, specialite=matiere_nom)
            profs.append(prof)
            matiere, _ = Matiere.objects.get_or_create(nom=matiere_nom)
            for rang, donnees in enumerate(COURS[matiere_nom]):
                cours = self.creer_cours(enseignant, matiere, donnees, programme=(matiere_nom == "Anglais" and rang == 2))
                tous_les_cours.append(cours)
                if cours.publication == Cours.Publication.PUBLIE:
                    cours_par_matiere.setdefault(matiere_nom, []).append(cours)
                    quiz_publies.append(self.creer_quiz(cours, enseignant, donnees["questions"]))

        etudiants = []
        noms = h.sample(NOMS, nb_etudiants)
        for prenom, nom in zip(PRENOMS[:nb_etudiants], noms):
            etudiant = self.utilisateur(prenom, nom, Utilisateur.Role.ETUDIANT, mot_de_passe)
            Etudiant.objects.create(utilisateur=etudiant, niveau=h.choice(["L1", "L2", "L3"]))
            etudiants.append(etudiant)
        self.exemples = [etudiants[0].username, profs[0].username]

        publies = [c for liste in cours_par_matiere.values() for c in liste]
        bilan = {"enseignants": len(profs), "étudiants": len(etudiants), "cours": len(tous_les_cours),
                 "quiz publiés": len(quiz_publies), "tentatives de quiz": 0, "consultations": 0, "avis": 0,
                 "favoris": 0, "conversations avec l'assistant": 0, "connexions journalisées": 0}
        for rang, etudiant in enumerate(etudiants):
            aptitude = h.uniform(0.45, 0.95)  # probabilité de bien répondre : des profils variés
            consultes = h.sample(publies, h.randint(3, 8))
            for cours in consultes:
                for _ in range(h.randint(1, 3)):
                    self.dater(Consultation.objects.create(utilisateur=etudiant, cours=cours), "date",
                               self.apres(cours.date_creation))
                    bilan["consultations"] += 1
            for cours in h.sample(consultes, h.randint(0, min(2, len(consultes)))):
                Favori.objects.create(utilisateur=etudiant, cours=cours)
                bilan["favoris"] += 1
            for cours in h.sample(consultes, h.randint(0, min(3, len(consultes)))):
                note = h.choices([5, 4, 3, 2], weights=[45, 35, 15, 5])[0]
                avis = Avis.objects.create(utilisateur=etudiant, cours=cours, note=note,
                                           commentaire=h.choice(COMMENTAIRES_AVIS[note]))
                date = self.apres(cours.date_creation)
                Avis.objects.filter(pk=avis.pk).update(date_creation=date, date_modification=date)
                bilan["avis"] += 1
            for quiz in h.sample(quiz_publies, h.randint(2, min(6, len(quiz_publies)))):
                self.creer_tentative(quiz, etudiant, aptitude)
                bilan["tentatives de quiz"] += 1
            if rang % 2 == 0:
                cours = h.choice(consultes)
                self.creer_conversation(etudiant, cours)
                bilan["conversations avec l'assistant"] += 1

        for rang, user in enumerate(profs + etudiants):
            bilan["connexions journalisées"] += self.creer_connexions(user, suspecte=(rang % 7 == 3))
        return bilan

    def dater(self, objet, champ, date):
        """Les champs auto_now_add ne se règlent qu'après la création."""
        type(objet).objects.filter(pk=objet.pk).update(**{champ: date})
        setattr(objet, champ, date)
        return objet

    def creer_cours(self, enseignant, matiere, donnees, programme=False):
        nom_fichier = default_storage.save(f"cours/{PREFIXE}{slugify(donnees['titre'])}.pdf",
                                           ContentFile(pdf_du_cours(donnees["titre"], donnees["texte"])))
        self.fichiers.append(nom_fichier)
        cours = Cours.objects.create(
            titre=donnees["titre"], description=donnees["description"], fichier=nom_fichier,
            type_fichier=Cours.TypeFichier.PDF, enseignant=enseignant, matiere=matiere, niveau=donnees["niveau"],
            mots_cles=donnees["mots_cles"], statut=Cours.Statut.TERMINE, progression=100,
            nb_mots=len(donnees["texte"].split()),
            publication=Cours.Publication.PROGRAMME if programme else Cours.Publication.PUBLIE,
            date_publication=self.maintenant + timedelta(days=7) if programme else None,
        )
        self.dater(cours, "date_creation", self.date_passee(JOURS + 1, JOURS + 25))
        Transcription.objects.create(cours=cours, texte=donnees["texte"])
        Resume.objects.create(cours=cours, texte=donnees["resume"], type_resume=Resume.Type.DETAILLE)
        return cours

    def creer_quiz(self, cours, enseignant, questions):
        quiz = Quiz.objects.create(titre=f"Quiz – {cours.titre}", cours=cours, enseignant=enseignant,
                                   statut=Quiz.Statut.PUBLIE, origine=self.hasard.choice([Quiz.Origine.IA, Quiz.Origine.MANUEL]))
        creation = self.apres(cours.date_creation, jours_max=3)
        Quiz.objects.filter(pk=quiz.pk).update(date_creation=creation, date_publication=creation)
        quiz.date_creation = creation
        for ordre, (enonce, choix, bonne) in enumerate(questions, start=1):
            vrai_faux = choix == ["Vrai", "Faux"]
            question = Question.objects.create(
                quiz=quiz, ordre=ordre, enonce=enonce, points=POINTS_QUESTION,
                type_question=Question.Type.VRAI_FAUX if vrai_faux else Question.Type.QCM)
            for rang, texte in enumerate(choix):
                Choix.objects.create(question=question, texte=texte, est_correct=(rang == bonne), ordre=rang)
        return quiz

    def creer_tentative(self, quiz, etudiant, aptitude):
        """Tentative soumise avec de vraies réponses : le score est recalculé par le module Quiz lui-même."""
        tentative = TentativeQuiz.objects.create(quiz=quiz, etudiant=etudiant)
        debut = self.apres(quiz.date_creation)
        for question in quiz.questions.prefetch_related("choix"):
            choix = list(question.choix.all())
            bon = next(c for c in choix if c.est_correct)
            reponse = bon if self.hasard.random() < aptitude else self.hasard.choice([c for c in choix if c != bon])
            ReponseEtudiant.objects.create(
                tentative=tentative, question=question, choix=reponse, est_correcte=reponse.est_correct,
                points_obtenus=question.points if reponse.est_correct else Decimal("0"),
                statut_correction=ReponseEtudiant.Correction.AUTOMATIQUE, date_correction=debut)
        soumission = debut + timedelta(minutes=self.hasard.randint(3, 15))
        tentative.score_max = POINTS_QUESTION * quiz.questions.count()
        tentative.statut = TentativeQuiz.Statut.SOUMISE
        tentative.date_soumission = soumission
        tentative.save(update_fields=["score_max", "statut", "date_soumission"])
        tentative.recalculer_score()
        TentativeQuiz.objects.filter(pk=tentative.pk).update(date_debut=debut, date_correction=soumission)

    def creer_conversation(self, etudiant, cours):
        matiere = cours.matiere.nom
        question, reponse = ECHANGES_ASSISTANT[matiere][0]
        session = SessionAssistant.objects.create(utilisateur=etudiant, titre=f"Révision : {cours.titre}", cours=cours)
        date = self.apres(cours.date_creation)
        for decalage, (role, texte) in enumerate([(MessageVocal.Role.UTILISATEUR, question),
                                                  (MessageVocal.Role.ASSISTANT, reponse)]):
            message = MessageVocal.objects.create(session=session, role=role, texte=texte, langue="fr",
                                                  duree_ms=self.hasard.randint(900, 2500) if decalage else None)
            self.dater(message, "date", date + timedelta(seconds=5 * decalage))
        SessionAssistant.objects.filter(pk=session.pk).update(date_ouverture=date, date_maj=date + timedelta(seconds=5))

    def creer_connexions(self, user, suspecte=False):
        """Connexions habituelles (mêmes heures, appareil et réseau), quelques échecs, parfois une connexion suspecte."""
        h = self.hasard
        heures = sorted(h.sample(range(8, 22), 3))
        appareil = h.choice(APPAREILS)
        reseau = f"41.{h.randint(224, 231)}.{h.randint(0, 255)}"
        entrees = []
        for _ in range(h.randint(8, 18)):
            jour = h.randint(0, JOURS - 1)
            date = (timezone.localtime(self.maintenant) - timedelta(days=jour)).replace(
                hour=h.choice(heures), minute=h.randint(0, 59), second=0, microsecond=0)
            if date > self.maintenant:
                date -= timedelta(days=1)
            succes = h.random() > 0.1
            entrees.append((date, JournalConnexion(
                utilisateur=user, nom_saisi=user.username, ip=f"{reseau}.{h.randint(2, 250)}", appareil=appareil,
                methode=JournalConnexion.Methode.MOT_DE_PASSE, succes=succes,
                motif="" if succes else "Mot de passe incorrect", score_risque=0 if succes else None,
                temps_ia_ms=round(h.uniform(0.4, 3.0), 1) if succes else None)))
        if suspecte:
            date = (timezone.localtime(self.maintenant) - timedelta(days=h.randint(1, 10))).replace(hour=3, minute=12)
            entrees.append((date, JournalConnexion(
                utilisateur=user, nom_saisi=user.username, ip="185.220.101.7", appareil="Firefox sur Linux",
                succes=True, score_risque=100, suspect=True, temps_ia_ms=round(h.uniform(380, 520), 1),
                raisons_risque=["Heure inhabituelle (03 h)", "Nouvel appareil (Firefox sur Linux)",
                                "Nouvelle adresse réseau"])))
        for date, entree in entrees:
            entree.save()
            self.dater(entree, "date", date)
        return len(entrees)
