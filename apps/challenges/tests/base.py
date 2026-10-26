"""Outils de test EduSprint : comptes, défi prêt à l'emploi, équipe, faux client Gemini (aucun appel réel)."""

import json
import os
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Enseignant, Etudiant, Utilisateur
from apps.challenges.models import Defi, Equipe, Jalon, ParticipationEquipe
from apps.challenges.services import equipes as regles_equipes

AVEC_CLE = mock.patch.dict(os.environ, {"GEMINI_API_KEY": "cle-de-test"})


def creer_utilisateur(username, role, **extra):
    user = Utilisateur.objects.create_user(username=username, email=f"{username}@eduvox.test",
                                           password="motdepasse-test", role=role, **extra)
    if role == Utilisateur.Role.ENSEIGNANT:
        Enseignant.objects.create(utilisateur=user)
    elif role == Utilisateur.Role.ETUDIANT:
        Etudiant.objects.create(utilisateur=user)
    return user


def faux_client(reponses):
    """Faux client Gemini : chaque appel consomme une réponse (dict → JSON, str telle quelle, Exception levée)."""
    appels = []

    def generate_content(**kwargs):
        appels.append(kwargs)
        valeur = reponses.pop(0) if isinstance(reponses, list) else reponses
        if isinstance(valeur, Exception):
            raise valeur
        return SimpleNamespace(text=valeur if isinstance(valeur, str) else json.dumps(valeur))

    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    client.appels = appels
    return client


@contextmanager
def gemini_simule(reponses):
    client = faux_client(reponses)
    with AVEC_CLE, mock.patch("apps.challenges.services.gemini._client", return_value=client), \
            mock.patch("apps.challenges.services.gemini.time.sleep"):
        yield client


class BaseEduSprint(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.prof = creer_utilisateur("prof", Utilisateur.Role.ENSEIGNANT, first_name="Sami", last_name="Prof")
        cls.autre_prof = creer_utilisateur("autre_prof", Utilisateur.Role.ENSEIGNANT)
        cls.admin = creer_utilisateur("admin", Utilisateur.Role.ADMIN, is_staff=True)
        cls.alice = creer_utilisateur("alice", Utilisateur.Role.ETUDIANT, first_name="Alice", last_name="B")
        cls.bob = creer_utilisateur("bob", Utilisateur.Role.ETUDIANT, first_name="Bob", last_name="C")
        cls.carla = creer_utilisateur("carla", Utilisateur.Role.ETUDIANT)
        cls.dina = creer_utilisateur("dina", Utilisateur.Role.ETUDIANT)

    def creer_defi(self, statut=Defi.Statut.BROUILLON, enseignant=None, **extra):
        """Défi aux dates cohérentes : inscriptions ouvertes maintenant, début dans 2 jours, fin dans 7 jours."""
        maintenant = timezone.now()
        valeurs = dict(
            titre="Réduire la consommation d'eau du campus", description="Mission de conseil pour l'université.",
            contexte="L'université veut réduire de 20 % sa consommation d'eau en un an.",
            objectifs="Diagnostiquer les usages\nProposer trois actions chiffrées",
            competences="Gestion de projet\nAnalyse de données", livrables_attendus="Proposition\nMaquette\nBudget prévisionnel",
            contraintes="Solution réalisable en 6 mois", budget=2000, enseignant=enseignant or self.prof,
            inscriptions_debut=maintenant - timedelta(hours=1), inscriptions_fin=maintenant + timedelta(days=2),
            date_debut=maintenant + timedelta(days=2), date_fin=maintenant + timedelta(days=7),
            taille_min=2, taille_max=3,
        )
        valeurs.update(extra)
        defi = Defi.objects.create(**valeurs)
        Jalon.objects.create(defi=defi, ordre=1, titre="Diagnostic", echeance=valeurs["date_debut"] + timedelta(days=2),
                             exigences="Relevé des consommations\nIdentification des fuites")
        Jalon.objects.create(defi=defi, ordre=2, titre="Remise finale", echeance=valeurs["date_fin"],
                             exigences="Proposition chiffrée")
        if statut != Defi.Statut.BROUILLON:
            Defi.objects.filter(pk=defi.pk).update(statut=statut)  # mise en place directe de l'état de départ
            defi.refresh_from_db()
        return defi

    def creer_equipe(self, defi, coordinateur, *membres, nom="Les Hydros"):
        """Équipe acceptée (créée pendant les inscriptions, quel que soit l'état final du défi)."""
        statut = defi.statut
        if statut != Defi.Statut.INSCRIPTIONS:
            Defi.objects.filter(pk=defi.pk).update(statut=Defi.Statut.INSCRIPTIONS)
            defi.refresh_from_db()
        equipe = regles_equipes.creer_equipe(defi, coordinateur, nom)
        for membre in membres:
            demande = regles_equipes.demander_adhesion(equipe, membre)
            regles_equipes.accepter_demande(demande, coordinateur)
        if statut != Defi.Statut.INSCRIPTIONS:
            Defi.objects.filter(pk=defi.pk).update(statut=statut)
            defi.refresh_from_db()
        equipe.refresh_from_db()
        return equipe

    def defi_en_cours(self, membres=None, autres=(), **extra):
        """Défi démarré (dates décalées dans le passé) avec l'équipe Alice (coordinatrice) + `membres` (Bob).

        `autres` : équipes supplémentaires [(coordinateur, (membres…), nom)], formées pendant les inscriptions.
        """
        defi = self.creer_defi(Defi.Statut.INSCRIPTIONS, **extra)
        equipe = self.creer_equipe(defi, self.alice, *(membres if membres is not None else (self.bob,)))
        for coordinateur, membres_autres, nom in autres:
            self.creer_equipe(defi, coordinateur, *membres_autres, nom=nom)
        maintenant = timezone.now()
        Defi.objects.filter(pk=defi.pk).update(
            statut=Defi.Statut.EN_COURS, inscriptions_debut=maintenant - timedelta(days=3),
            inscriptions_fin=maintenant - timedelta(days=1), date_debut=maintenant - timedelta(days=1),
            date_fin=maintenant + timedelta(days=5))
        Jalon.objects.filter(defi=defi, ordre=1).update(echeance=maintenant + timedelta(days=1))
        Jalon.objects.filter(defi=defi, ordre=2).update(echeance=maintenant + timedelta(days=5))
        defi.refresh_from_db()
        equipe.refresh_from_db()
        return defi, equipe

    def connecter(self, user):
        self.client.force_login(user)


def participation(etudiant, equipe):
    return ParticipationEquipe.objects.get(etudiant=etudiant, equipe=equipe)


__all__ = ["AVEC_CLE", "BaseEduSprint", "Equipe", "creer_utilisateur", "faux_client", "gemini_simule", "participation"]
