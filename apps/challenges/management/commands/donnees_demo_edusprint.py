"""Données de démonstration EduSprint (comptes demo_*, 4 défis « [DÉMO] » en cours).

    python manage.py donnees_demo_edusprint --confirmer

- Tout est créé en UNE transaction : en cas d'erreur, rien n'est enregistré.
- Refuse de s'exécuter si des comptes demo_* ou des défis [DÉMO] existent déjà (pas de doublon).
- Les équipes, adhésions, imprévus, réponses et livrables passent par les services métier (mêmes règles que
  l'interface) ; seules les dates sont ensuite décalées pour placer les défis « en cours ».
- Aucun appel Gemini : l'imprévu « origine IA » est un brouillon saisi directement pour la démonstration.
"""

import secrets
from datetime import timedelta

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Enseignant, Etudiant, Utilisateur
from apps.challenges.models import CommentaireTache, Defi, EvenementScenario, Jalon, Tache, VersionLivrable
from apps.challenges.services import defis as regles_defis
from apps.challenges.services import equipes as regles_equipes
from apps.challenges.services import evenements as regles_evenements
from apps.challenges.services import livrables as regles_livrables

PREFIXE = "[DÉMO] "
ETUDIANTS = [("Amine", "Ben Salah"), ("Sarra", "Trabelsi"), ("Youssef", "Gharbi"), ("Lina", "Mansour"),
             ("Karim", "Jaziri"), ("Nour", "Haddad")]

DEFIS = [
    {"titre": "Réduire la consommation d'eau du campus", "budget": 2000, "niveau": "intermediaire",
     "description": "Mission de conseil pour l'université : proposer un plan d'économie d'eau réaliste.",
     "contexte": "L'université constate une hausse de 15 % de sa facture d'eau. La direction veut un plan d'action "
                 "chiffré, réalisable en six mois, sans travaux lourds.",
     "objectifs": "Diagnostiquer les usages de l'eau\nProposer trois actions chiffrées\nEstimer les économies attendues",
     "competences": "Gestion de projet\nAnalyse de données\nCommunication écrite",
     "livrables": "Proposition\nMaquette de l'affiche de sensibilisation\nBudget prévisionnel",
     "contraintes": "Solution réalisable en 6 mois\nPas de travaux de plomberie lourds",
     "jalons": [("Diagnostic des usages", "Relevé des consommations\nIdentification des fuites"),
                ("Plan d'action", "Trois actions chiffrées\nPlanning de mise en œuvre"),
                ("Remise finale", "Proposition complète\nBudget prévisionnel détaillé")]},
    {"titre": "Application de covoiturage étudiant", "budget": None, "niveau": "debutant",
     "description": "Concevoir une application qui met en relation les étudiants qui partagent leurs trajets.",
     "contexte": "L'association des étudiants constate que 60 % des étudiants viennent seuls en voiture. Elle "
                 "souhaite une application simple pour organiser le covoiturage.",
     "objectifs": "Identifier les besoins des étudiants\nConcevoir le parcours de réservation\nPrésenter une maquette",
     "competences": "Conception UX\nTravail en équipe",
     "livrables": "Étude des besoins\nMaquette des écrans\nParcours utilisateur",
     "contraintes": "Accessible sur mobile\nAucune donnée de localisation conservée",
     "jalons": [("Étude des besoins", "Questionnaire et synthèse des réponses"),
                ("Maquette", "Écrans principaux\nParcours de réservation d'un trajet")]},
    {"titre": "Tableau de bord énergétique pour une PME", "budget": 5000, "niveau": "avance",
     "description": "Aider une PME industrielle à suivre et réduire sa consommation électrique.",
     "contexte": "Une PME de 80 salariés veut suivre sa consommation électrique par atelier et repérer les dérives.",
     "objectifs": "Définir les indicateurs de suivi\nConcevoir le tableau de bord\nProposer des alertes",
     "competences": "Analyse de données\nVisualisation\nGestion de projet",
     "livrables": "Cahier des indicateurs\nMaquette du tableau de bord\nPlan de déploiement",
     "contraintes": "Données anonymisées uniquement\nBudget matériel limité",
     "jalons": [("Indicateurs", "Liste des indicateurs justifiés"),
                ("Maquette", "Maquette interactive\nRègles d'alerte"),
                ("Déploiement", "Plan de déploiement chiffré")]},
    {"titre": "Campagne anti-gaspillage au restaurant universitaire", "budget": 800, "niveau": "debutant",
     "description": "Réduire le gaspillage alimentaire au restaurant universitaire grâce à une campagne ciblée.",
     "contexte": "Le restaurant universitaire jette environ 40 kg de nourriture par jour. Le CROUS veut une "
                 "campagne de sensibilisation mesurable.",
     "objectifs": "Mesurer le gaspillage actuel\nConcevoir la campagne\nDéfinir les indicateurs de réussite",
     "competences": "Communication\nGestion de projet",
     "livrables": "Diagnostic chiffré\nSupports de campagne\nPlan de mesure",
     "contraintes": "Budget de 800 DT\nCampagne de deux semaines maximum",
     "jalons": [("Diagnostic", "Pesée des déchets sur 3 jours"),
                ("Campagne", "Affiches et messages\nCalendrier de la campagne")]},
]

TACHES = [
    ("Relever les données de départ", "termine", "haute", -2),
    ("Rédiger la synthèse du diagnostic", "a_verifier", "normale", 1),
    ("Préparer la maquette", "en_cours", "haute", 2),
    ("Chiffrer les actions", "a_faire", "normale", 3),
    ("Relire le dossier final", "a_faire", "basse", None),
    ("Contacter le client pour validation", "en_cours", "normale", -1),  # en retard
]


class Command(BaseCommand):
    help = "Crée des comptes demo_* et 4 défis [DÉMO] en cours (base configurée). Exige --confirmer."

    def add_arguments(self, parser):
        parser.add_argument("--confirmer", action="store_true", help="Confirme la création dans la base configurée")

    def handle(self, *args, **options):
        if not options["confirmer"]:
            raise CommandError("Ajoutez --confirmer : des comptes et des données seront créés dans la base configurée.")
        if Utilisateur.objects.filter(username__startswith="demo_").exists() or \
                Defi.objects.filter(titre__startswith=PREFIXE).exists():
            raise CommandError("Des données de démonstration existent déjà : rien n'a été créé.")
        mot_de_passe = "Demo-" + secrets.token_urlsafe(9)
        with transaction.atomic():
            prof, etudiants = self.comptes(mot_de_passe)
            for index, modele in enumerate(DEFIS):
                self.defi(index, modele, prof, etudiants)
        self.stdout.write(self.style.SUCCESS("Données de démonstration créées."))
        self.stdout.write(f"Enseignant : demo_prof · Étudiants : demo_etu1 … demo_etu6 · Mot de passe : {mot_de_passe}")

    # ---------- Comptes ----------
    def comptes(self, mot_de_passe):
        prof = Utilisateur.objects.create_user(
            username="demo_prof", email="demo_prof@demo.eduvox.test", password=mot_de_passe, first_name="Hela",
            last_name="Démo", role=Utilisateur.Role.ENSEIGNANT, email_verifie=True)
        Enseignant.objects.create(utilisateur=prof, specialite="Gestion de projet")
        etudiants = []
        for i, (prenom, nom) in enumerate(ETUDIANTS, start=1):
            user = Utilisateur.objects.create_user(
                username=f"demo_etu{i}", email=f"demo_etu{i}@demo.eduvox.test", password=mot_de_passe,
                first_name=prenom, last_name=nom, role=Utilisateur.Role.ETUDIANT, email_verifie=True)
            Etudiant.objects.create(utilisateur=user, niveau="L3")
            etudiants.append(user)
        return prof, etudiants

    # ---------- Un défi complet ----------
    def defi(self, index, modele, prof, etudiants):
        maintenant = timezone.now()
        defi = Defi.objects.create(
            titre=PREFIXE + modele["titre"], description=modele["description"], contexte=modele["contexte"],
            objectifs=modele["objectifs"], competences=modele["competences"], livrables_attendus=modele["livrables"],
            contraintes=modele["contraintes"], budget=modele["budget"], niveau=modele["niveau"], enseignant=prof,
            inscriptions_debut=maintenant - timedelta(hours=1), inscriptions_fin=maintenant + timedelta(days=1),
            date_debut=maintenant + timedelta(days=1), date_fin=maintenant + timedelta(days=8), taille_min=2, taille_max=3)
        nb_jalons = len(modele["jalons"])
        for ordre, (titre, exigences) in enumerate(modele["jalons"], start=1):
            Jalon.objects.create(defi=defi, ordre=ordre, titre=titre, exigences=exigences,
                                 echeance=defi.date_debut + timedelta(days=round(7 * ordre / nb_jalons)))
        regles_defis.ouvrir_inscriptions(defi)
        defi.refresh_from_db()

        # Équipes : rotation des étudiants d'un défi à l'autre (une seule équipe par étudiant et par défi)
        ordre = etudiants[index:] + etudiants[:index]
        a = regles_equipes.creer_equipe(defi, ordre[0], "Équipe Aqua" if index == 0 else f"Équipe Alpha {index + 1}")
        b = regles_equipes.creer_equipe(defi, ordre[3], "Équipe Delta" if index == 0 else f"Équipe Bêta {index + 1}")
        for membre in (ordre[1], ordre[2]):
            regles_equipes.accepter_demande(regles_equipes.demander_adhesion(a, membre), ordre[0])
        regles_equipes.accepter_demande(regles_equipes.demander_adhesion(b, ordre[4]), ordre[3])
        if index == 0:
            regles_equipes.creer_equipe(defi, ordre[5], "Équipe Solo")  # écartée au démarrage (taille min = 2)
        else:
            refusee = regles_equipes.demander_adhesion(b, ordre[5])
            regles_equipes.refuser_demande(refusee, ordre[3], "Équipe déjà organisée.")
        regles_defis.demarrer(defi, prof)

        # Décalage des dates : défi commencé il y a 3 jours, premier jalon déjà passé
        debut = maintenant - timedelta(days=3)
        fin = maintenant + timedelta(days=4 + index)
        Defi.objects.filter(pk=defi.pk).update(inscriptions_debut=debut - timedelta(days=4),
                                               inscriptions_fin=debut, date_debut=debut, date_fin=fin)
        echeances = [maintenant - timedelta(days=1)] + [
            maintenant + timedelta(days=1 + i * (3 + index) // max(1, nb_jalons - 1)) for i in range(1, nb_jalons)]
        echeances[-1] = fin
        for jalon, echeance in zip(defi.jalons.order_by("ordre"), echeances):
            Jalon.objects.filter(pk=jalon.pk).update(echeance=echeance)
        defi.refresh_from_db()
        jalons = list(defi.jalons.order_by("ordre"))

        for equipe, membres in ((a, ordre[0:3]), (b, [ordre[3], ordre[4]])):
            equipe.refresh_from_db()
            self.taches(equipe, membres, jalons)
        self.imprevus(defi, prof, a, b, ordre)
        self.livrables(defi, prof, a, b, ordre, jalons)

    def taches(self, equipe, membres, jalons):
        aujourd_hui = timezone.localdate()
        for i, (titre, statut, priorite, jours) in enumerate(TACHES[:6 if len(membres) == 3 else 4]):
            tache = Tache(equipe=equipe, titre=titre, statut=statut, priorite=priorite, cree_par=membres[0],
                          responsable=membres[i % len(membres)] if i != 4 else None,
                          jalon=jalons[min(i // 2, len(jalons) - 1)],
                          echeance=aujourd_hui + timedelta(days=jours) if jours is not None else None,
                          description="Tâche de démonstration EduSprint.")
            tache.full_clean()
            tache.save()
            if i < 2:
                CommentaireTache.objects.create(tache=tache, auteur=membres[-1], contenu="J'ai relu, c'est bon pour moi.")
                CommentaireTache.objects.create(tache=tache, auteur=membres[0], contenu="Merci, je passe à la suite.")

    def imprevus(self, defi, prof, a, b, ordre):
        type_publie = "budget" if defi.budget is not None else "public"
        publie = EvenementScenario.objects.create(
            defi=defi, titre="Le client revoit ses priorités" if type_publie == "public" else "Le budget est réduit de 25 %",
            type_contrainte=type_publie, cree_par=prof,
            description=("Le client annonce une coupe budgétaire : le budget disponible baisse de 25 %."
                         if type_publie == "budget" else "Le public visé inclut désormais le personnel administratif."),
            justification="Apprendre à arbitrer et à reprioriser sous contrainte.",
            impacts_attendus="Revoir le périmètre\nAdapter le planning",
            elements_attendus="Actions conservées ou abandonnées\nNouveau planning",
            echeance_reponse=timezone.now() + timedelta(days=2),
            nouveau_budget=(defi.budget * 3 / 4) if type_publie == "budget" else None)
        regles_evenements.valider(publie, prof)
        publie = regles_evenements.publier(publie, prof)
        reponse = regles_evenements.enregistrer_reponse(
            publie, a, ordre[1], "L'imprévu réduit notre marge de manœuvre sur deux actions.",
            "Nous conservons les deux actions les plus rentables et reportons la troisième.",
            "La maquette est avancée d'un jour pour libérer du temps sur le chiffrage.")
        regles_evenements.soumettre_reponse(reponse, ordre[0])
        regles_evenements.enregistrer_reponse(publie, b, ordre[4], "Analyse en cours.", "À décider en réunion.",
                                              "Planning à revoir.")  # brouillon privé de l'équipe B
        valide = EvenementScenario.objects.create(
            defi=defi, titre="Un outil de maquettage devient indisponible", type_contrainte="outil", cree_par=prof,
            description="La licence de l'outil de maquettage expire : trouvez une alternative.",
            elements_attendus="Outil de remplacement\nImpact sur le planning")
        regles_evenements.valider(valide, prof)
        EvenementScenario.objects.create(
            defi=defi, titre="Nouvelle exigence d'accessibilité", type_contrainte="exigence", origine="ia",
            cree_par=prof, description="Le client exige que tous les supports respectent les règles d'accessibilité.",
            justification="Proposition de démonstration (aucun appel IA réel).",
            impacts_attendus="Revoir les supports", elements_attendus="Liste des adaptations")

    def livrables(self, defi, prof, a, b, ordre, jalons):
        Etat = VersionLivrable.Etat
        # Équipe A : v1 « à compléter » + v2 brouillon ; livrable du premier jalon soumis en retard puis accepté
        v1 = regles_livrables.creer_livrable(
            a, ordre[1], "Proposition", "Proposition principale", None,
            texte="Diagnostic : relevé des consommations réalisé. Deux actions proposées, chiffrage partiel.",
            fichier=ContentFile("Proposition de l'équipe (version 1).\n".encode("utf-8"), name="proposition_v1.txt"))
        regles_livrables.soumettre(v1, ordre[0])
        regles_livrables.evaluer(v1, prof, Etat.A_COMPLETER, "Il manque le chiffrage de la deuxième action.")
        regles_livrables.nouvelle_version(v1.livrable, ordre[2], texte="Version 2 : les deux actions sont chiffrées.")
        retard = regles_livrables.creer_livrable(a, ordre[0], "Synthèse du diagnostic", "", jalons[0],
                                                 texte="Synthèse remise après l'échéance du jalon.")
        regles_livrables.soumettre(retard, ordre[0])
        regles_livrables.evaluer(retard, prof, Etat.ACCEPTE, "Bon travail, attention aux délais.")
        # Équipe B : version soumise en attente de relecture + brouillon
        attente = regles_livrables.creer_livrable(b, ordre[4], "Maquette", "", jalons[min(1, len(jalons) - 1)],
                                                  texte="Maquette des écrans principaux.", lien="https://exemple.test/maquette")
        regles_livrables.soumettre(attente, ordre[3])
        regles_livrables.creer_livrable(b, ordre[4], "Budget prévisionnel", "", None, texte="Brouillon du budget.")
