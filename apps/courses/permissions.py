"""Règles d'accès du module cours.

- Enseignant : crée, modifie et supprime uniquement SES cours, et les voit quel que soit leur statut.
- Étudiant : consulte les cours publiés (ou dont la publication programmée est passée),
  non masqués et non archivés.
- Administrateur (rôle ADMIN ou superutilisateur) : consulte tous les cours, y compris masqués
  et archivés ; il modère (masquer, archiver) mais ne crée, ne modifie ni ne supprime aucun cours.

Les permissions Django de roles.py ne suffisent pas ici : l'administrateur y possède
aussi « publier_cours », alors qu'il ne doit ni créer ni modifier de cours.
"""

from django.contrib.auth.mixins import UserPassesTestMixin
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404

from apps.accounts.models import Enseignant, Utilisateur

from .models import Cours


def est_admin(user):
    return user.is_superuser or user.role == Utilisateur.Role.ADMIN


def est_enseignant(user):
    return not est_admin(user) and user.role == Utilisateur.Role.ENSEIGNANT


def est_etudiant(user):
    return not est_admin(user) and user.role == Utilisateur.Role.ETUDIANT


def est_proprietaire(user, cours):
    return est_enseignant(user) and cours.enseignant.utilisateur_id == user.pk


def cours_publics():
    """Cours visibles des étudiants : publiés, non masqués, non archivés."""
    return Cours.objects.filter(Cours.filtre_publie(), masque=False, archive=False)


def est_public(cours):
    return cours.est_publie and not cours.masque and not cours.archive


def peut_consulter(user, cours):
    return est_admin(user) or est_proprietaire(user, cours) or (est_etudiant(user) and est_public(cours))


def cours_visibles(user):
    """Cours que l'utilisateur peut voir (liste, recherche, recommandations...)."""
    if est_admin(user):
        return Cours.objects.all()
    if est_etudiant(user):
        return cours_publics()
    if est_enseignant(user):
        return Cours.objects.filter(enseignant__utilisateur=user)
    return Cours.objects.none()


class EnseignantRequisMixin(UserPassesTestMixin):
    """Accès réservé aux enseignants (création de cours)."""

    def test_func(self):
        return est_enseignant(self.request.user)

    def get_enseignant(self):
        try:
            return self.request.user.enseignant
        except Enseignant.DoesNotExist:
            raise PermissionDenied("Votre profil enseignant est introuvable. Contactez un administrateur.")


class EtudiantRequisMixin(UserPassesTestMixin):
    """Accès réservé aux étudiants (favoris, avis, notes personnelles, signalements...)."""

    def test_func(self):
        return est_etudiant(self.request.user)


class AdminRequisMixin(UserPassesTestMixin):
    """Accès réservé à l'administrateur (rôle ADMIN ou superutilisateur) : modération, statistiques globales."""

    def test_func(self):
        return est_admin(self.request.user)


class ProprietaireCoursMixin(EnseignantRequisMixin):
    """Modification / suppression : l'enseignant n'accède qu'à ses propres cours."""

    def get_queryset(self):
        # Le cours d'un autre enseignant n'est pas trouvé : 404
        return Cours.objects.filter(enseignant__utilisateur=self.request.user)


class ConsultationCoursMixin:
    """Consultation : étudiant, enseignant propriétaire ou administrateur."""

    def get_cours(self):
        cours = get_object_or_404(
            Cours.objects.select_related("enseignant__utilisateur", "matiere"), pk=self.kwargs["pk"]
        )
        if est_etudiant(self.request.user) and not est_public(cours):
            # Brouillon, masqué ou archivé : l'étudiant ne doit même pas savoir qu'il existe
            raise Http404("Cours introuvable.")
        if not peut_consulter(self.request.user, cours):
            raise PermissionDenied
        return cours
