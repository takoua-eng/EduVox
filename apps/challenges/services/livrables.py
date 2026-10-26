"""Livrables versionnés : une version soumise n'est jamais écrasée ; une nouvelle version garde l'historique.

Cycle d'une version : brouillon → soumis → (à compléter | accepté).
- Un seul brouillon et une seule soumission en attente par livrable (index UNIQUE partiels).
- Nouvelle version possible seulement quand la dernière est « à compléter » (ou lors du premier dépôt).
- Soumission officielle par le coordinateur, pendant le défi ; retard calculé par rapport au jalon
  (ou à la fin du défi).
- Exception métier explicite : après la clôture, l'enseignant peut encore rendre son retour sur une
  version déjà soumise (aucune nouvelle contribution d'équipe n'est possible).
"""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from ..models import Defi, Livrable, VersionLivrable
from ..permissions import peut_deposer, peut_gerer_defi, peut_soumettre
from ..stockage import nom_affichable

Etat = VersionLivrable.Etat


def _remplir(version, fichier=None, lien="", texte="", note_equipe=""):
    if fichier:
        version.fichier = fichier
        version.nom_fichier = nom_affichable(fichier.name)
        version.taille_fichier = fichier.size
    version.lien = lien or ""
    version.texte = texte or ""
    version.note_equipe = note_equipe or ""


def creer_livrable(equipe, user, titre, description="", jalon=None, **contenu):
    if not peut_deposer(user, equipe):
        raise ValidationError("Vous ne pouvez pas déposer de livrable pour cette équipe.")
    try:
        with transaction.atomic():
            livrable = Livrable(equipe=equipe, jalon=jalon, titre=titre.strip(), description=description, cree_par=user)
            livrable.full_clean()
            livrable.save()
            version = VersionLivrable(livrable=livrable, numero=1, auteur=user)
            _remplir(version, **contenu)
            version.full_clean()
            version.save()
    except IntegrityError:
        raise ValidationError("Un livrable porte déjà ce titre dans votre équipe.")
    return version


def nouvelle_version(livrable, user, **contenu):
    if not peut_deposer(user, livrable.equipe):
        raise ValidationError("Vous ne pouvez pas déposer de version pour cette équipe.")
    try:
        with transaction.atomic():
            livrable = Livrable.objects.select_for_update().get(pk=livrable.pk)
            derniere = livrable.derniere_version()
            if derniere and derniere.etat != Etat.A_COMPLETER:
                raise ValidationError({
                    Etat.BROUILLON: "Un brouillon existe déjà : complétez-le plutôt que d'en créer un autre.",
                    Etat.SOUMIS: "La version soumise attend le retour de l'enseignant.",
                    Etat.ACCEPTE: "Ce livrable a été accepté : aucune nouvelle version n'est nécessaire.",
                }[derniere.etat])
            numero = (livrable.versions.aggregate(m=Max("numero"))["m"] or 0) + 1
            version = VersionLivrable(livrable=livrable, numero=numero, auteur=user)
            _remplir(version, **contenu)
            version.full_clean()
            version.save()
    except IntegrityError:
        raise ValidationError("Une autre version vient d'être créée : rechargez la page.")
    return version


def modifier_brouillon(version, user, fichier=None, lien="", texte="", note_equipe="", retirer_fichier=False):
    with transaction.atomic():
        version = VersionLivrable.objects.select_for_update(of=("self",)).select_related("livrable__equipe__defi").get(pk=version.pk)
        if version.etat != Etat.BROUILLON or version.auteur_id != user.pk or not peut_deposer(user, version.livrable.equipe):
            raise ValidationError("Seul l'auteur peut modifier ce brouillon, pendant le défi.")
        ancien = version.fichier.name if (fichier or retirer_fichier) else None
        if retirer_fichier and not fichier:
            version.fichier = ""
            version.nom_fichier = ""
            version.taille_fichier = None
        _remplir(version, fichier=fichier, lien=lien, texte=texte, note_equipe=note_equipe)
        version.full_clean()
        version.save()
        if ancien:
            transaction.on_commit(lambda: version.fichier.storage.delete(ancien))
    return version


def soumettre(version, user):
    """Double clic : la ligne est verrouillée et l'état revérifié ; la seconde requête est refusée sans effet."""
    try:
        with transaction.atomic():
            version = VersionLivrable.objects.select_for_update(of=("self",)).select_related(
                "livrable__equipe__defi", "livrable__jalon").get(pk=version.pk)
            equipe = version.livrable.equipe
            if version.etat != Etat.BROUILLON:
                raise ValidationError("Cette version a déjà été soumise.")
            if not peut_soumettre(user, equipe):
                raise ValidationError("Seul le coordinateur soumet officiellement, pendant le défi.")
            maintenant = timezone.now()
            version.etat = Etat.SOUMIS
            version.date_soumission = maintenant
            version.soumis_par = user
            version.en_retard = maintenant > version.livrable.echeance()
            version.save(update_fields=["etat", "date_soumission", "soumis_par", "en_retard", "date_modification"])
    except IntegrityError:
        raise ValidationError("Une version de ce livrable attend déjà le retour de l'enseignant.")
    return version


def evaluer(version, enseignant, decision, retour):
    with transaction.atomic():
        version = VersionLivrable.objects.select_for_update(of=("self",)).select_related("livrable__equipe__defi").get(pk=version.pk)
        defi = version.livrable.equipe.defi
        if not peut_gerer_defi(enseignant, defi):
            raise ValidationError("Vous ne gérez pas ce défi.")
        if defi.statut not in (Defi.Statut.EN_COURS, Defi.Statut.TERMINE):
            raise ValidationError("Le défi est archivé : les retours sont figés.")
        if version.etat != Etat.SOUMIS:
            raise ValidationError("Cette version a déjà reçu un retour.")
        if decision not in (Etat.A_COMPLETER, Etat.ACCEPTE):
            raise ValidationError("Décision inconnue.")
        if decision == Etat.A_COMPLETER and not (retour or "").strip():
            raise ValidationError("Expliquez ce qui doit être complété.")
        version.etat = decision
        version.retour_enseignant = (retour or "").strip()
        version.retour_par = enseignant
        version.date_retour = timezone.now()
        version.save(update_fields=["etat", "retour_enseignant", "retour_par", "date_retour", "date_modification"])
    return version


def supprimer_brouillon(version, user):
    with transaction.atomic():
        version = VersionLivrable.objects.select_for_update(of=("self",)).select_related("livrable__equipe__defi").get(pk=version.pk)
        if version.etat != Etat.BROUILLON or version.auteur_id != user.pk or not peut_deposer(user, version.livrable.equipe):
            raise ValidationError("Seul l'auteur peut supprimer son brouillon.")
        livrable = version.livrable
        version.delete()
        if not livrable.versions.exists():
            livrable.delete()
            return None
    return livrable
