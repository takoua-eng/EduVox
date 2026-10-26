"""Règles d'accès EduSprint (vérifiées côté serveur, objet par objet).

- Enseignant : gère ses défis et ceux dont le propriétaire l'a explicitement nommé co-enseignant.
  Il voit les espaces d'équipe de ses défis mais jamais les BROUILLONS des équipes (versions de livrable,
  réponses d'adaptation non soumises) : ils restent privés jusqu'à la soumission.
- Étudiant : voit les défis publiés ; n'accède qu'à l'espace des équipes dont il est membre accepté.
- Coordinateur : en plus, gère les adhésions, la coordination et les soumissions officielles de l'équipe.
- Administrateur (rôle ADMIN ou superutilisateur) : consultation de tous les défis et modération
  (archivage), sans création ni modification du contenu pédagogique.

Tout objet hors périmètre renvoie 404, pour ne pas révéler son existence (pas d'énumération par URL).
"""

from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404

from apps.accounts.models import Utilisateur

from .models import Defi, Equipe, ParticipationEquipe, ReponseEvenement, VersionLivrable


def est_admin(user):
    return user.is_authenticated and (user.is_superuser or user.role == Utilisateur.Role.ADMIN)


def est_enseignant(user):
    return user.is_authenticated and not est_admin(user) and user.role == Utilisateur.Role.ENSEIGNANT


def est_etudiant(user):
    return user.is_authenticated and not est_admin(user) and user.role == Utilisateur.Role.ETUDIANT


def role(user):
    if est_admin(user):
        return "admin"
    if est_enseignant(user):
        return "enseignant"
    return "etudiant" if est_etudiant(user) else ""


# ---------- Défis ----------

def defis_geres(user):
    if not est_enseignant(user):
        return Defi.objects.none()
    return Defi.objects.filter(Q(enseignant=user) | Q(co_enseignants=user)).distinct()


def peut_gerer_defi(user, defi):
    return est_enseignant(user) and (defi.enseignant_id == user.pk or defi.co_enseignants.filter(pk=user.pk).exists())


def est_proprietaire(user, defi):
    return est_enseignant(user) and defi.enseignant_id == user.pk


def defis_visibles(user):
    if est_admin(user):
        return Defi.objects.all()
    publics = ~Q(statut__in=[Defi.Statut.BROUILLON, Defi.Statut.ARCHIVE])
    if est_enseignant(user):
        return Defi.objects.filter(publics | Q(enseignant=user) | Q(co_enseignants=user)).distinct()
    if est_etudiant(user):
        # Un défi archivé reste consultable par ceux qui y ont participé (historique)
        participe = Q(participations__etudiant=user, participations__etat=ParticipationEquipe.Etat.ACCEPTEE)
        return Defi.objects.filter(publics | (Q(statut=Defi.Statut.ARCHIVE) & participe)).distinct()
    return Defi.objects.none()


def get_defi_visible(user, pk):
    return get_object_or_404(defis_visibles(user).select_related("enseignant"), pk=pk)


def get_defi_gere(user, pk):
    return get_object_or_404(defis_geres(user).select_related("enseignant"), pk=pk)


# ---------- Équipes ----------

def participation_acceptee(user, defi):
    if not est_etudiant(user):
        return None
    return (ParticipationEquipe.objects.select_related("equipe")
            .filter(defi=defi, etudiant=user, etat=ParticipationEquipe.Etat.ACCEPTEE).first())


def acces_equipe(user, equipe):
    """'membre', 'enseignant', 'admin' ou None. Une demande en attente ne donne aucun accès."""
    if est_etudiant(user) and equipe.est_membre(user):
        return "membre"
    if peut_gerer_defi(user, equipe.defi):
        return "enseignant"
    if est_admin(user):
        return "admin"
    return None


def get_equipe_accessible(user, pk):
    equipe = get_object_or_404(Equipe.objects.select_related("defi", "coordinateur"), pk=pk)
    acces = acces_equipe(user, equipe)
    if acces is None:
        raise Http404("Équipe introuvable.")
    return equipe, acces


def peut_contribuer(user, equipe):
    """Tâches et commentaires : membre d'une équipe active, défi en inscriptions ou en cours."""
    return est_etudiant(user) and equipe.active and equipe.defi.equipes_modifiables and equipe.est_membre(user)


def peut_deposer(user, equipe):
    """Livrables et réponses d'adaptation : pendant le défi uniquement."""
    return est_etudiant(user) and equipe.active and equipe.defi.en_cours and equipe.est_membre(user)


# ---------- Tâches ----------

def peut_modifier_tache(user, tache):
    """Créateur, responsable ou coordinateur : modifier le contenu et faire avancer la tâche."""
    equipe = tache.equipe
    return peut_contribuer(user, equipe) and user.pk in (tache.cree_par_id, tache.responsable_id, equipe.coordinateur_id)


def peut_supprimer_tache(user, tache):
    """Seul le créateur supprime sa tâche, et seulement si personne d'autre n'y a contribué (commentaire)."""
    if not peut_contribuer(user, tache.equipe) or tache.cree_par_id != user.pk:
        return False
    return not tache.commentaires.exclude(auteur=user).exists()


def responsables_autorises(user, equipe):
    """Le coordinateur affecte à tout membre ; un membre s'affecte lui-même (ou personne)."""
    membres = Utilisateur.objects.filter(participations_defis__equipe=equipe,
                                         participations_defis__etat=ParticipationEquipe.Etat.ACCEPTEE)
    if equipe.est_coordinateur(user):
        return membres
    return membres.filter(pk=user.pk)


# ---------- Livrables et réponses ----------

def versions_visibles(user, acces, livrable):
    versions = livrable.versions.all()
    if acces != "membre":
        versions = versions.exclude(etat=VersionLivrable.Etat.BROUILLON)
    return versions


def get_version_accessible(user, pk):
    version = get_object_or_404(
        VersionLivrable.objects.select_related("livrable__equipe__defi", "livrable__jalon", "auteur"), pk=pk)
    equipe = version.livrable.equipe
    acces = acces_equipe(user, equipe)
    if acces is None or (acces != "membre" and version.est_brouillon):
        raise Http404("Version introuvable.")
    return version, acces


def peut_modifier_version(user, version):
    return version.est_brouillon and version.auteur_id == user.pk and peut_deposer(user, version.livrable.equipe)


def peut_supprimer_version(user, version):
    return peut_modifier_version(user, version)


def peut_soumettre(user, equipe):
    return peut_deposer(user, equipe) and equipe.est_coordinateur(user)


def reponse_visible(acces, reponse):
    return reponse is not None and (acces == "membre" or reponse.etat == ReponseEvenement.Etat.SOUMISE)
