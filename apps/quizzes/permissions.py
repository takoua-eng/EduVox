"""Règles d'accès du module quiz (réutilise les rôles et la visibilité des cours du module 2).

- Enseignant : gère uniquement SES quiz, rattachés à SES cours, et voit les tentatives de ses quiz.
- Étudiant : voit les quiz publiés dont le cours est public (publié, non masqué, non archivé),
  et uniquement ses propres tentatives.
- Administrateur : consultation de tous les quiz et résultats, sans création ni modification
  (même logique que le module cours).
Tout objet hors périmètre renvoie 404, pour ne pas révéler son existence.
"""

from django.contrib.auth.mixins import UserPassesTestMixin
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404

from apps.accounts.models import Enseignant
from apps.courses.permissions import cours_publics, cours_visibles, est_admin, est_enseignant, est_etudiant

from .models import Question, Quiz, TentativeQuiz

__all__ = ["est_admin", "est_enseignant", "est_etudiant", "peut_gerer", "quiz_visibles", "verifier_tentative"]


def quiz_publics():
    return Quiz.objects.filter(statut=Quiz.Statut.PUBLIE, cours__in=cours_publics())


def quiz_visibles(user):
    if est_admin(user):
        return Quiz.objects.all()
    if est_enseignant(user):
        return Quiz.objects.filter(enseignant__utilisateur=user)
    if est_etudiant(user):
        return quiz_publics()
    return Quiz.objects.none()


def cours_utilisables(user):
    """Cours auxquels un enseignant peut rattacher un quiz : les siens."""
    return cours_visibles(user) if est_enseignant(user) else cours_visibles(user).none()


def peut_gerer(user, quiz):
    return est_enseignant(user) and quiz.enseignant.utilisateur_id == user.pk


def get_quiz_visible(user, pk):
    return get_object_or_404(quiz_visibles(user).select_related("cours", "enseignant__utilisateur"), pk=pk)


class EnseignantQuizMixin(UserPassesTestMixin):
    """Vues de gestion : réservées aux enseignants, sur leurs propres quiz."""

    def test_func(self):
        return est_enseignant(self.request.user)

    def get_enseignant(self):
        try:
            return self.request.user.enseignant
        except Enseignant.DoesNotExist:
            raise PermissionDenied("Votre profil enseignant est introuvable. Contactez un administrateur.")

    def get_quiz(self, pk=None):
        return get_object_or_404(
            Quiz.objects.filter(enseignant__utilisateur=self.request.user).select_related("cours"),
            pk=pk or self.kwargs["pk"],
        )

    def get_question(self, pk=None):
        return get_object_or_404(
            Question.objects.filter(quiz__enseignant__utilisateur=self.request.user).select_related("quiz"),
            pk=pk or self.kwargs["pk"],
        )


class EtudiantQuizMixin(UserPassesTestMixin):
    def test_func(self):
        return est_etudiant(self.request.user)


def acces_tentative(user, tentative):
    """L'étudiant voit ses tentatives ; l'enseignant celles de ses quiz ; l'admin toutes."""
    if tentative.etudiant_id == user.pk:
        return True
    return est_admin(user) or peut_gerer(user, tentative.quiz)


def verifier_tentative(user, pk):
    tentative = get_object_or_404(
        TentativeQuiz.objects.select_related("quiz__cours", "quiz__enseignant__utilisateur", "etudiant"), pk=pk
    )
    if not acces_tentative(user, tentative):
        raise Http404("Tentative introuvable.")
    return tentative
