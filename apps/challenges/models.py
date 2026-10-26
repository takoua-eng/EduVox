"""Modèles EduSprint : défis, jalons, équipes, tâches, livrables versionnés, imprévus et traitements IA.

Principes :
- les règles qui doivent tenir même en cas d'accès concurrent sont garanties par la base
  (contraintes CHECK et index UNIQUE partiels, supportés par PostgreSQL et SQLite) ;
- les verrous métier (version soumise immuable, événement publié figé, brief d'un défi commencé
  non modifiable, transitions de statut) sont vérifiés dans `save()` : ni un formulaire, ni l'admin
  Django ne peuvent les contourner ;
- les liens vers les utilisateurs sont en SET_NULL (ou CASCADE pour la participation personnelle) :
  la suppression d'un compte par le module accounts ne doit jamais échouer ni effacer le travail d'une équipe.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxLengthValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone

from .stockage import chemin_livrable, stockage_prive

UTILISATEUR = settings.AUTH_USER_MODEL
TAILLE_EQUIPE_MAX = 12


def lignes(texte):
    """Découpe un champ « un élément par ligne » en liste (puces et lignes vides retirées)."""
    resultat = []
    for ligne in (texte or "").splitlines():
        ligne = ligne.strip().lstrip("-•*").strip()
        if ligne:
            resultat.append(ligne)
    return resultat


def valeurs_en_base(instance, champs):
    """Valeurs actuellement enregistrées de `champs` (None si l'objet n'existe pas encore)."""
    if not instance.pk:
        return None
    return type(instance).objects.filter(pk=instance.pk).values(*champs).first()


# ---------------------------------------------------------------------------
# Défi et jalons
# ---------------------------------------------------------------------------

class Defi(models.Model):
    class Statut(models.TextChoices):
        BROUILLON = "brouillon", "Brouillon"
        INSCRIPTIONS = "inscriptions", "Inscriptions ouvertes"
        EN_COURS = "en_cours", "En cours"
        TERMINE = "termine", "Terminé"
        ARCHIVE = "archive", "Archivé"

    class Niveau(models.TextChoices):
        DEBUTANT = "debutant", "Débutant (L1-L2)"
        INTERMEDIAIRE = "intermediaire", "Intermédiaire (L3)"
        AVANCE = "avance", "Avancé (Master)"

    class Origine(models.TextChoices):
        MANUELLE = "manuelle", "Saisie manuelle"
        IA = "ia", "Brief proposé par l'IA"

    # Transitions autorisées : toute autre est refusée dans save()
    TRANSITIONS = {
        Statut.BROUILLON: {Statut.INSCRIPTIONS, Statut.ARCHIVE},
        Statut.INSCRIPTIONS: {Statut.BROUILLON, Statut.EN_COURS, Statut.ARCHIVE},
        Statut.EN_COURS: {Statut.TERMINE},
        Statut.TERMINE: {Statut.ARCHIVE},
        Statut.ARCHIVE: set(),
    }
    # Exigences figées dès le démarrage : un changement passe par un événement tracé
    CHAMPS_BRIEF = ("titre", "description", "contexte", "objectifs", "competences", "livrables_attendus",
                    "contraintes", "budget", "niveau", "inscriptions_debut", "inscriptions_fin", "date_debut",
                    "date_fin", "taille_min", "taille_max")

    titre = models.CharField(max_length=200)
    description = models.TextField("accroche", max_length=1000, validators=[MaxLengthValidator(1000)],
                                   help_text="Résumé de la mission en quelques phrases.")
    contexte = models.TextField(max_length=5000, validators=[MaxLengthValidator(5000)],
                                help_text="Situation du client virtuel et problème à résoudre.")
    objectifs = models.TextField(max_length=3000, validators=[MaxLengthValidator(3000)],
                                 help_text="Un objectif par ligne.")
    competences = models.TextField("compétences visées", max_length=2000, validators=[MaxLengthValidator(2000)],
                                   help_text="Une compétence par ligne.")
    livrables_attendus = models.TextField(max_length=3000, validators=[MaxLengthValidator(3000)],
                                          help_text="Un livrable par ligne.")
    contraintes = models.TextField(max_length=3000, blank=True, validators=[MaxLengthValidator(3000)],
                                   help_text="Une contrainte par ligne (facultatif).")
    budget = models.DecimalField("budget (DT)", max_digits=10, decimal_places=2, null=True, blank=True,
                                 validators=[MinValueValidator(0), MaxValueValidator(10_000_000)])
    niveau = models.CharField(max_length=20, choices=Niveau.choices, default=Niveau.INTERMEDIAIRE)

    enseignant = models.ForeignKey(UTILISATEUR, on_delete=models.SET_NULL, null=True, related_name="defis_crees")
    co_enseignants = models.ManyToManyField(
        UTILISATEUR, blank=True, related_name="defis_partages",
        help_text="Enseignants autorisés explicitement par le propriétaire à gérer ce défi.",
    )

    inscriptions_debut = models.DateTimeField("ouverture des inscriptions")
    inscriptions_fin = models.DateTimeField("clôture des inscriptions")
    date_debut = models.DateTimeField("début du défi")
    date_fin = models.DateTimeField("fin du défi")
    taille_min = models.PositiveSmallIntegerField("taille minimale d'équipe", default=2,
                                                  validators=[MinValueValidator(1), MaxValueValidator(TAILLE_EQUIPE_MAX)])
    taille_max = models.PositiveSmallIntegerField("taille maximale d'équipe", default=4,
                                                  validators=[MinValueValidator(1), MaxValueValidator(TAILLE_EQUIPE_MAX)])

    statut = models.CharField(max_length=20, choices=Statut.choices, default=Statut.BROUILLON)
    origine = models.CharField(max_length=10, choices=Origine.choices, default=Origine.MANUELLE)
    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)
    date_ouverture = models.DateTimeField(null=True, blank=True)
    date_demarrage = models.DateTimeField(null=True, blank=True)
    date_cloture = models.DateTimeField(null=True, blank=True)
    date_archivage = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "défi"
        ordering = ["-date_creation"]
        constraints = [
            models.CheckConstraint(condition=Q(taille_min__gte=1) & Q(taille_max__lte=TAILLE_EQUIPE_MAX)
                                   & Q(taille_min__lte=models.F("taille_max")), name="edusprint_defi_tailles",
                                   violation_error_message="Tailles d'équipe incohérentes (1 ≤ min ≤ max ≤ 12)."),
            models.CheckConstraint(condition=Q(budget__isnull=True) | Q(budget__gte=0), name="edusprint_defi_budget",
                                   violation_error_message="Le budget ne peut pas être négatif."),
            models.CheckConstraint(
                condition=Q(inscriptions_debut__lt=models.F("inscriptions_fin"))
                & Q(inscriptions_fin__lte=models.F("date_debut")) & Q(date_debut__lt=models.F("date_fin")),
                name="edusprint_defi_dates",
                violation_error_message="Dates incohérentes : ouverture < clôture des inscriptions ≤ début < fin.",
            ),
        ]
        indexes = [
            models.Index(fields=["statut", "date_debut"], name="edusprint_defi_statut_idx"),
            models.Index(fields=["enseignant", "statut"], name="edusprint_defi_ens_idx"),
        ]

    def __str__(self):
        return self.titre

    # ---- états dérivés ----
    @property
    def brief_modifiable(self):
        return self.statut in (self.Statut.BROUILLON, self.Statut.INSCRIPTIONS)

    @property
    def inscriptions_ouvertes(self):
        maintenant = timezone.now()
        return (self.statut == self.Statut.INSCRIPTIONS
                and self.inscriptions_debut <= maintenant <= self.inscriptions_fin)

    @property
    def en_cours(self):
        return self.statut == self.Statut.EN_COURS

    @property
    def lecture_seule(self):
        """Clôturé ou archivé : historique consultable, plus aucune contribution d'équipe."""
        return self.statut in (self.Statut.TERMINE, self.Statut.ARCHIVE)

    @property
    def equipes_modifiables(self):
        """Les équipes s'organisent (tâches, commentaires) pendant les inscriptions et le défi."""
        return self.statut in (self.Statut.INSCRIPTIONS, self.Statut.EN_COURS)

    @property
    def visible_etudiants(self):
        return self.statut != self.Statut.BROUILLON

    def objectifs_liste(self):
        return lignes(self.objectifs)

    def competences_liste(self):
        return lignes(self.competences)

    def livrables_liste(self):
        return lignes(self.livrables_attendus)

    def contraintes_liste(self):
        return lignes(self.contraintes)

    def peut_passer_a(self, statut):
        return statut in self.TRANSITIONS.get(self.statut, set())

    def a_des_contributions(self):
        return self.equipes.exists() or self.evenements.exists()

    # ---- validations ----
    def clean(self):
        erreurs = {}
        if self.taille_min and self.taille_max and self.taille_min > self.taille_max:
            erreurs["taille_max"] = "La taille maximale doit être supérieure ou égale à la taille minimale."
        if self.inscriptions_debut and self.inscriptions_fin and self.inscriptions_debut >= self.inscriptions_fin:
            erreurs["inscriptions_fin"] = "La clôture des inscriptions doit suivre leur ouverture."
        if self.inscriptions_fin and self.date_debut and self.inscriptions_fin > self.date_debut:
            erreurs["date_debut"] = "Le défi doit commencer après la clôture des inscriptions."
        if self.date_debut and self.date_fin and self.date_debut >= self.date_fin:
            erreurs["date_fin"] = "La fin du défi doit suivre son début."
        if self.date_debut and self.date_fin and self.pk:
            hors = [j.titre for j in self.jalons.all() if not (self.date_debut <= j.echeance <= self.date_fin)]
            if hors:
                erreurs.setdefault("date_fin", f"Échéance(s) de jalon hors de la période du défi : {', '.join(hors)}.")
        if self.pk and self.taille_max:
            pleine = (Equipe.objects.filter(defi=self)
                      .annotate(n=models.Count("participations", filter=Q(participations__etat=ParticipationEquipe.Etat.ACCEPTEE)))
                      .filter(n__gt=self.taille_max).first())
            if pleine:
                erreurs["taille_max"] = f"L'équipe « {pleine.nom} » compte déjà plus de {self.taille_max} membres."
        if erreurs:
            raise ValidationError(erreurs)

    def save(self, *args, **kwargs):
        actuel = valeurs_en_base(self, ("statut",) + self.CHAMPS_BRIEF)
        if actuel:
            ancien = actuel["statut"]
            if ancien != self.statut and self.statut not in self.TRANSITIONS.get(ancien, set()):
                raise ValidationError(f"Transition interdite : {ancien} → {self.statut}.")
            if ancien not in (self.Statut.BROUILLON, self.Statut.INSCRIPTIONS) and not getattr(self, "_via_evenement", False):
                modifies = [c for c in self.CHAMPS_BRIEF if actuel[c] != getattr(self, c)]
                if modifies:
                    raise ValidationError(
                        "Le défi a commencé : ses exigences ne peuvent plus être modifiées directement "
                        "(champs : %s). Publiez un imprévu tracé." % ", ".join(modifies))
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.statut != self.Statut.BROUILLON or self.a_des_contributions():
            raise ValidationError("Seul un défi brouillon sans contribution peut être supprimé ; archivez-le.")
        return super().delete(*args, **kwargs)


class Jalon(models.Model):
    defi = models.ForeignKey(Defi, on_delete=models.CASCADE, related_name="jalons")
    titre = models.CharField(max_length=200)
    description = models.TextField(max_length=2000, blank=True, validators=[MaxLengthValidator(2000)])
    ordre = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(50)])
    echeance = models.DateTimeField("échéance")
    exigences = models.TextField("exigences attendues", max_length=3000, blank=True,
                                 validators=[MaxLengthValidator(3000)], help_text="Une exigence par ligne.")

    class Meta:
        ordering = ["defi", "ordre", "echeance"]
        constraints = [models.UniqueConstraint(fields=["defi", "ordre"], name="edusprint_jalon_ordre_unique")]

    def __str__(self):
        return f"{self.ordre}. {self.titre}"

    def exigences_liste(self):
        return lignes(self.exigences)

    @property
    def depasse(self):
        return self.echeance < timezone.now()

    def clean(self):
        if self.defi_id and self.echeance:
            defi = self.defi
            if not (defi.date_debut <= self.echeance <= defi.date_fin):
                raise ValidationError({"echeance": "L'échéance doit être comprise entre le début et la fin du défi."})

    def save(self, *args, **kwargs):
        defi = Defi.objects.filter(pk=self.defi_id).values("statut").first()
        if defi and defi["statut"] not in (Defi.Statut.BROUILLON, Defi.Statut.INSCRIPTIONS) \
                and not getattr(self, "_via_evenement", False):
            raise ValidationError("Le défi a commencé : ses jalons ne se modifient que par un imprévu publié.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if not self.defi.brief_modifiable:
            raise ValidationError("Le défi a commencé : ses jalons ne peuvent plus être supprimés.")
        if self.taches.exists() or self.livrables.exists():
            raise ValidationError("Ce jalon est utilisé par des tâches ou des livrables.")
        return super().delete(*args, **kwargs)


# ---------------------------------------------------------------------------
# Équipes et adhésions
# ---------------------------------------------------------------------------

class Equipe(models.Model):
    class Statut(models.TextChoices):
        ACTIVE = "active", "Active"
        ECARTEE = "ecartee", "Écartée (taille minimale non atteinte au démarrage)"
        DISSOUTE = "dissoute", "Dissoute"

    defi = models.ForeignKey(Defi, on_delete=models.PROTECT, related_name="equipes")
    nom = models.CharField(max_length=80)
    description = models.TextField(max_length=500, blank=True, validators=[MaxLengthValidator(500)])
    coordinateur = models.ForeignKey(UTILISATEUR, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name="equipes_coordonnees")
    statut = models.CharField(max_length=10, choices=Statut.choices, default=Statut.ACTIVE)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["defi", "nom"]
        constraints = [models.UniqueConstraint(Lower("nom"), "defi", name="edusprint_equipe_nom_unique",
                                               violation_error_message="Une équipe porte déjà ce nom dans ce défi.")]

    def __str__(self):
        return self.nom

    def membres(self):
        return (ParticipationEquipe.objects.filter(equipe=self, etat=ParticipationEquipe.Etat.ACCEPTEE)
                .select_related("etudiant").order_by("date_reponse", "pk"))

    def nb_membres(self):
        return self.participations.filter(etat=ParticipationEquipe.Etat.ACCEPTEE).count()

    def est_membre(self, user):
        return self.participations.filter(etudiant_id=user.pk, etat=ParticipationEquipe.Etat.ACCEPTEE).exists()

    def est_coordinateur(self, user):
        return self.coordinateur_id is not None and self.coordinateur_id == user.pk

    @property
    def active(self):
        return self.statut == self.Statut.ACTIVE

    def clean(self):
        if self.pk and self.coordinateur_id and not self.est_membre(self.coordinateur):
            raise ValidationError({"coordinateur": "Le coordinateur doit être un membre accepté de l'équipe."})

    def save(self, *args, **kwargs):
        if self.pk and self.coordinateur_id and not self.participations.filter(
                etudiant_id=self.coordinateur_id, etat=ParticipationEquipe.Etat.ACCEPTEE).exists():
            raise ValidationError("Le coordinateur doit être un membre accepté de l'équipe.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Une équipe n'est jamais supprimée : elle est dissoute ou écartée (historique conservé).")


class ParticipationEquipe(models.Model):
    class Etat(models.TextChoices):
        EN_ATTENTE = "en_attente", "En attente"
        ACCEPTEE = "acceptee", "Acceptée"
        REFUSEE = "refusee", "Refusée"
        RETIREE = "retiree", "Retirée"

    equipe = models.ForeignKey(Equipe, on_delete=models.CASCADE, related_name="participations")
    # Copie de equipe.defi : nécessaire à la contrainte « une seule équipe acceptée par défi »
    defi = models.ForeignKey(Defi, on_delete=models.PROTECT, related_name="participations", editable=False)
    etudiant = models.ForeignKey(UTILISATEUR, on_delete=models.CASCADE, related_name="participations_defis")
    role = models.CharField("rôle dans l'équipe", max_length=80, blank=True,
                            help_text="Ex. : responsable budget, maquettiste…")
    message = models.TextField("message de motivation", max_length=500, blank=True, validators=[MaxLengthValidator(500)])
    etat = models.CharField(max_length=12, choices=Etat.choices, default=Etat.EN_ATTENTE)
    motif = models.CharField(max_length=200, blank=True)
    traite_par = models.ForeignKey(UTILISATEUR, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    date_demande = models.DateTimeField(auto_now_add=True)
    date_reponse = models.DateTimeField(null=True, blank=True)
    date_retrait = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "participation à une équipe"
        ordering = ["-date_demande"]
        constraints = [
            # Un étudiant n'appartient qu'à une seule équipe acceptée par défi (même en cas d'acceptations simultanées)
            models.UniqueConstraint(fields=["defi", "etudiant"], condition=Q(etat="acceptee"),
                                    name="edusprint_une_equipe_par_defi"),
            # Pas de demande dupliquée vers la même équipe
            models.UniqueConstraint(fields=["equipe", "etudiant"], condition=Q(etat__in=["en_attente", "acceptee"]),
                                    name="edusprint_demande_unique"),
        ]
        indexes = [models.Index(fields=["equipe", "etat"], name="edusprint_particip_etat_idx")]

    def __str__(self):
        return f"{self.etudiant} → {self.equipe} ({self.get_etat_display()})"

    def clean(self):
        from apps.accounts.models import Utilisateur

        if self.etudiant_id and self.etudiant.role != Utilisateur.Role.ETUDIANT:
            raise ValidationError({"etudiant": "Seul un étudiant peut rejoindre une équipe."})

    def save(self, *args, **kwargs):
        self.defi_id = Equipe.objects.values_list("defi_id", flat=True).get(pk=self.equipe_id)
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# Tâches et commentaires
# ---------------------------------------------------------------------------

class Tache(models.Model):
    class Statut(models.TextChoices):
        A_FAIRE = "a_faire", "À faire"
        EN_COURS = "en_cours", "En cours"
        A_VERIFIER = "a_verifier", "À vérifier"
        TERMINE = "termine", "Terminé"

    class Priorite(models.TextChoices):
        BASSE = "basse", "Basse"
        NORMALE = "normale", "Normale"
        HAUTE = "haute", "Haute"

    ORDRE_STATUTS = [Statut.A_FAIRE, Statut.EN_COURS, Statut.A_VERIFIER, Statut.TERMINE]

    equipe = models.ForeignKey(Equipe, on_delete=models.PROTECT, related_name="taches")
    jalon = models.ForeignKey(Jalon, on_delete=models.PROTECT, null=True, blank=True, related_name="taches")
    titre = models.CharField(max_length=200)
    description = models.TextField(max_length=3000, blank=True, validators=[MaxLengthValidator(3000)])
    responsable = models.ForeignKey(UTILISATEUR, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="taches_defis")
    priorite = models.CharField("priorité", max_length=10, choices=Priorite.choices, default=Priorite.NORMALE)
    echeance = models.DateField("échéance", null=True, blank=True)
    statut = models.CharField(max_length=12, choices=Statut.choices, default=Statut.A_FAIRE)
    cree_par = models.ForeignKey(UTILISATEUR, on_delete=models.SET_NULL, null=True, related_name="+")
    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)
    date_terminee = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "tâche"
        ordering = ["echeance", "-date_creation"]
        indexes = [models.Index(fields=["equipe", "statut"], name="edusprint_tache_statut_idx")]

    def __str__(self):
        return self.titre

    @property
    def en_retard(self):
        return bool(self.echeance and self.statut != self.Statut.TERMINE and self.echeance < timezone.localdate())

    def statut_voisin(self, decalage):
        index = self.ORDRE_STATUTS.index(self.statut) + decalage
        return self.ORDRE_STATUTS[index] if 0 <= index < len(self.ORDRE_STATUTS) else None

    def clean(self):
        erreurs = {}
        if self.equipe_id:
            if self.jalon_id and self.jalon.defi_id != self.equipe.defi_id:
                erreurs["jalon"] = "Ce jalon n'appartient pas au défi de l'équipe."
            if self.responsable_id and not self.equipe.est_membre(self.responsable):
                erreurs["responsable"] = "Le responsable doit être un membre accepté de l'équipe."
            if self.echeance and self.echeance > timezone.localtime(self.equipe.defi.date_fin).date():
                erreurs["echeance"] = "L'échéance dépasse la fin du défi."
        if erreurs:
            raise ValidationError(erreurs)

    def save(self, *args, **kwargs):
        if self.statut == self.Statut.TERMINE and not self.date_terminee:
            self.date_terminee = timezone.now()
        elif self.statut != self.Statut.TERMINE:
            self.date_terminee = None
        super().save(*args, **kwargs)


class CommentaireTache(models.Model):
    tache = models.ForeignKey(Tache, on_delete=models.CASCADE, related_name="commentaires")
    auteur = models.ForeignKey(UTILISATEUR, on_delete=models.SET_NULL, null=True, related_name="+")
    contenu = models.TextField(max_length=2000, validators=[MaxLengthValidator(2000)])
    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "commentaire de tâche"
        ordering = ["date_creation"]

    def __str__(self):
        return f"Commentaire de {self.auteur} sur « {self.tache} »"


# ---------------------------------------------------------------------------
# Livrables versionnés
# ---------------------------------------------------------------------------

class Livrable(models.Model):
    equipe = models.ForeignKey(Equipe, on_delete=models.PROTECT, related_name="livrables")
    jalon = models.ForeignKey(Jalon, on_delete=models.PROTECT, null=True, blank=True, related_name="livrables")
    titre = models.CharField(max_length=200)
    description = models.TextField(max_length=2000, blank=True, validators=[MaxLengthValidator(2000)])
    cree_par = models.ForeignKey(UTILISATEUR, on_delete=models.SET_NULL, null=True, related_name="+")
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["jalon__ordre", "date_creation"]
        constraints = [models.UniqueConstraint(Lower("titre"), "equipe", name="edusprint_livrable_titre_unique",
                                               violation_error_message="Un livrable porte déjà ce titre dans votre équipe.")]

    def __str__(self):
        return self.titre

    def derniere_version(self):
        return self.versions.order_by("-numero").first()

    def echeance(self):
        return self.jalon.echeance if self.jalon_id else self.equipe.defi.date_fin

    def clean(self):
        if self.equipe_id and self.jalon_id and self.jalon.defi_id != self.equipe.defi_id:
            raise ValidationError({"jalon": "Ce jalon n'appartient pas au défi de l'équipe."})


class VersionLivrable(models.Model):
    class Etat(models.TextChoices):
        BROUILLON = "brouillon", "Brouillon"
        SOUMIS = "soumis", "Soumis"
        A_COMPLETER = "a_completer", "À compléter"
        ACCEPTE = "accepte", "Accepté"

    TRANSITIONS = {
        Etat.BROUILLON: {Etat.SOUMIS},
        Etat.SOUMIS: {Etat.A_COMPLETER, Etat.ACCEPTE},
        Etat.A_COMPLETER: set(),
        Etat.ACCEPTE: set(),
    }
    CHAMPS_CONTENU = ("livrable_id", "numero", "fichier", "nom_fichier", "lien", "texte", "note_equipe")

    livrable = models.ForeignKey(Livrable, on_delete=models.CASCADE, related_name="versions")
    numero = models.PositiveSmallIntegerField("version", default=1)
    fichier = models.FileField(storage=stockage_prive, upload_to=chemin_livrable, blank=True, max_length=255)
    nom_fichier = models.CharField(max_length=200, blank=True)
    taille_fichier = models.PositiveIntegerField(null=True, blank=True)
    lien = models.URLField("lien externe", max_length=500, blank=True,
                           help_text="Conservé tel quel : il n'est jamais ouvert ni analysé par le serveur.")
    texte = models.TextField("contenu textuel", max_length=60000, blank=True, validators=[MaxLengthValidator(60000)],
                             help_text="Texte du dossier saisi directement (analysable par l'IA).")
    note_equipe = models.TextField("message à l'enseignant", max_length=2000, blank=True,
                                   validators=[MaxLengthValidator(2000)])
    auteur = models.ForeignKey(UTILISATEUR, on_delete=models.SET_NULL, null=True, related_name="+")
    date_preparation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)
    etat = models.CharField(max_length=12, choices=Etat.choices, default=Etat.BROUILLON)
    soumis_par = models.ForeignKey(UTILISATEUR, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    date_soumission = models.DateTimeField(null=True, blank=True)
    en_retard = models.BooleanField("soumission tardive", default=False)
    retour_enseignant = models.TextField(max_length=5000, blank=True, validators=[MaxLengthValidator(5000)])
    retour_par = models.ForeignKey(UTILISATEUR, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    date_retour = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "version de livrable"
        ordering = ["livrable", "-numero"]
        constraints = [
            models.UniqueConstraint(fields=["livrable", "numero"], name="edusprint_version_numero_unique"),
            models.UniqueConstraint(fields=["livrable"], condition=Q(etat="brouillon"),
                                    name="edusprint_un_brouillon_par_livrable"),
            models.UniqueConstraint(fields=["livrable"], condition=Q(etat="soumis"),
                                    name="edusprint_une_soumission_en_attente"),
            models.CheckConstraint(condition=~(Q(fichier="") & Q(lien="") & Q(texte="")),
                                   name="edusprint_version_contenu",
                                   violation_error_message="Ajoutez au moins un fichier, un lien ou un contenu textuel."),
        ]

    def __str__(self):
        return f"{self.livrable} – v{self.numero}"

    @property
    def est_brouillon(self):
        return self.etat == self.Etat.BROUILLON

    @property
    def extension(self):
        from .stockage import extension_de
        return extension_de(self.nom_fichier)

    def clean(self):
        if not (self.fichier or self.lien or (self.texte or "").strip()):
            raise ValidationError("Ajoutez au moins un fichier, un lien ou un contenu textuel.")

    def save(self, *args, **kwargs):
        actuel = valeurs_en_base(self, ("etat",) + self.CHAMPS_CONTENU)
        if actuel:
            ancien = actuel["etat"]
            if ancien != self.etat and self.etat not in self.TRANSITIONS[ancien]:
                raise ValidationError(f"Transition de version interdite : {ancien} → {self.etat}.")
            if ancien != self.Etat.BROUILLON:
                courant = {c: getattr(self, c) for c in self.CHAMPS_CONTENU}
                courant["fichier"] = self.fichier.name or ""
                if any(actuel[c] != courant[c] for c in self.CHAMPS_CONTENU):
                    raise ValidationError("Une version soumise ne peut pas être modifiée : déposez une nouvelle version.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.etat != self.Etat.BROUILLON:
            raise ValidationError("Une version soumise fait partie de l'historique : elle ne peut pas être supprimée.")
        fichier = self.fichier.name
        resultat = super().delete(*args, **kwargs)
        if fichier:
            stockage_prive.delete(fichier)
        return resultat


# ---------------------------------------------------------------------------
# Imprévus (client virtuel) et réponses d'adaptation
# ---------------------------------------------------------------------------

class EvenementScenario(models.Model):
    class Type(models.TextChoices):
        BUDGET = "budget", "Réduction du budget"
        EXIGENCE = "exigence", "Nouvelle exigence"
        OUTIL = "outil", "Indisponibilité d'un outil"
        PUBLIC = "public", "Changement de public"
        DELAI = "delai", "Modification de délai"
        AUTRE = "autre", "Autre contrainte"

    class Origine(models.TextChoices):
        MANUELLE = "manuelle", "Manuelle"
        IA = "ia", "Proposée par l'IA"

    class Etat(models.TextChoices):
        BROUILLON = "brouillon", "Brouillon"
        VALIDE = "valide", "Validé"
        PUBLIE = "publie", "Publié"

    CHAMPS_CONTENU = ("titre", "description", "type_contrainte", "justification", "impacts_attendus",
                      "elements_attendus", "echeance_reponse", "jalon_concerne_id", "nouvelle_echeance", "nouveau_budget")

    defi = models.ForeignKey(Defi, on_delete=models.PROTECT, related_name="evenements")
    titre = models.CharField(max_length=200)
    description = models.TextField(max_length=3000, validators=[MaxLengthValidator(3000)])
    type_contrainte = models.CharField("type de contrainte", max_length=10, choices=Type.choices)
    origine = models.CharField(max_length=10, choices=Origine.choices, default=Origine.MANUELLE)
    etat = models.CharField(max_length=10, choices=Etat.choices, default=Etat.BROUILLON)
    justification = models.TextField("justification pédagogique", max_length=2000, blank=True,
                                     validators=[MaxLengthValidator(2000)])
    impacts_attendus = models.TextField(max_length=2000, blank=True, validators=[MaxLengthValidator(2000)],
                                        help_text="Un impact par ligne.")
    elements_attendus = models.TextField("éléments attendus dans la réponse", max_length=2000, blank=True,
                                         validators=[MaxLengthValidator(2000)], help_text="Un élément par ligne.")
    echeance_reponse = models.DateTimeField("réponse attendue avant le", null=True, blank=True)
    # Changement réel facultatif, saisi par l'enseignant (jamais par l'IA) et appliqué à la publication
    jalon_concerne = models.ForeignKey(Jalon, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    nouvelle_echeance = models.DateTimeField("nouvelle échéance du jalon", null=True, blank=True)
    nouveau_budget = models.DecimalField("nouveau budget (DT)", max_digits=10, decimal_places=2, null=True, blank=True,
                                         validators=[MinValueValidator(0)])
    modifications_appliquees = models.JSONField(default=dict, blank=True, editable=False)

    cree_par = models.ForeignKey(UTILISATEUR, on_delete=models.SET_NULL, null=True, related_name="+")
    valide_par = models.ForeignKey(UTILISATEUR, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)
    date_validation = models.DateTimeField(null=True, blank=True)
    date_publication = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "événement de scénario (imprévu)"
        verbose_name_plural = "événements de scénario (imprévus)"
        ordering = ["-date_publication", "-date_creation"]
        indexes = [models.Index(fields=["defi", "etat"], name="edusprint_evenement_etat_idx")]

    def __str__(self):
        return self.titre

    def impacts_liste(self):
        return lignes(self.impacts_attendus)

    def elements_liste(self):
        return lignes(self.elements_attendus)

    @property
    def publie(self):
        return self.etat == self.Etat.PUBLIE

    def clean(self):
        erreurs = {}
        defi = self.defi if self.defi_id else None
        if self.jalon_concerne_id and defi and self.jalon_concerne.defi_id != defi.pk:
            erreurs["jalon_concerne"] = "Ce jalon n'appartient pas à ce défi."
        if self.nouvelle_echeance:
            if self.type_contrainte != self.Type.DELAI:
                erreurs["nouvelle_echeance"] = "Une nouvelle échéance n'est possible que pour une modification de délai."
            elif not self.jalon_concerne_id:
                erreurs["jalon_concerne"] = "Indiquez le jalon dont l'échéance change."
            elif defi and not (defi.date_debut <= self.nouvelle_echeance <= defi.date_fin):
                erreurs["nouvelle_echeance"] = "La nouvelle échéance doit rester dans la période du défi."
        elif self.jalon_concerne_id and self.type_contrainte == self.Type.DELAI:
            erreurs["nouvelle_echeance"] = "Indiquez la nouvelle échéance du jalon."
        if self.nouveau_budget is not None and self.type_contrainte != self.Type.BUDGET:
            erreurs["nouveau_budget"] = "Un nouveau budget n'est possible que pour un imprévu de type budget."
        if self.echeance_reponse and defi and self.echeance_reponse > defi.date_fin:
            erreurs["echeance_reponse"] = "La réponse doit être attendue avant la fin du défi."
        if erreurs:
            raise ValidationError(erreurs)

    def save(self, *args, **kwargs):
        actuel = valeurs_en_base(self, ("etat",) + self.CHAMPS_CONTENU)
        if actuel:
            contenu_modifie = any(actuel[c] != getattr(self, c) for c in self.CHAMPS_CONTENU)
            if actuel["etat"] == self.Etat.PUBLIE:
                if contenu_modifie or self.etat != self.Etat.PUBLIE:
                    raise ValidationError("Un imprévu publié est conservé tel quel dans l'historique.")
            elif contenu_modifie and actuel["etat"] == self.Etat.VALIDE:
                # Toute modification après validation impose une nouvelle validation
                self.etat = self.Etat.BROUILLON
                self.valide_par = None
                self.date_validation = None
            if self.etat == self.Etat.PUBLIE and actuel["etat"] != self.Etat.VALIDE:
                raise ValidationError("Un imprévu doit être validé avant d'être publié.")
        elif self.etat != self.Etat.BROUILLON:
            raise ValidationError("Un imprévu est créé en brouillon.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.etat == self.Etat.PUBLIE:
            raise ValidationError("Un imprévu publié ne peut pas être supprimé.")
        return super().delete(*args, **kwargs)


class ReponseEvenement(models.Model):
    class Etat(models.TextChoices):
        BROUILLON = "brouillon", "Brouillon"
        SOUMISE = "soumise", "Soumise"

    CHAMPS_CONTENU = ("analyse_impact", "decisions", "adaptation_planning")

    evenement = models.ForeignKey(EvenementScenario, on_delete=models.PROTECT, related_name="reponses")
    equipe = models.ForeignKey(Equipe, on_delete=models.PROTECT, related_name="reponses_evenements")
    analyse_impact = models.TextField("analyse de l'impact", max_length=4000, validators=[MaxLengthValidator(4000)])
    decisions = models.TextField("décisions prises", max_length=4000, validators=[MaxLengthValidator(4000)])
    adaptation_planning = models.TextField("adaptation du planning", max_length=4000,
                                           validators=[MaxLengthValidator(4000)])
    etat = models.CharField(max_length=10, choices=Etat.choices, default=Etat.BROUILLON)
    auteur = models.ForeignKey(UTILISATEUR, on_delete=models.SET_NULL, null=True, related_name="+")
    modifie_par = models.ForeignKey(UTILISATEUR, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    soumis_par = models.ForeignKey(UTILISATEUR, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)
    date_soumission = models.DateTimeField(null=True, blank=True)
    en_retard = models.BooleanField(default=False)

    class Meta:
        verbose_name = "réponse d'adaptation"
        verbose_name_plural = "réponses d'adaptation"
        constraints = [models.UniqueConstraint(fields=["evenement", "equipe"], name="edusprint_une_reponse_par_equipe")]

    def __str__(self):
        return f"Réponse de {self.equipe} à « {self.evenement} »"

    def clean(self):
        if self.evenement_id and self.equipe_id:
            if self.evenement.defi_id != self.equipe.defi_id:
                raise ValidationError("Cette équipe ne participe pas au défi de cet imprévu.")
            if self.evenement.etat != EvenementScenario.Etat.PUBLIE:
                raise ValidationError("On ne répond qu'à un imprévu publié.")

    def save(self, *args, **kwargs):
        evenement = EvenementScenario.objects.filter(pk=self.evenement_id).values("defi_id", "etat").first()
        equipe = Equipe.objects.filter(pk=self.equipe_id).values("defi_id").first()
        if not evenement or not equipe or evenement["defi_id"] != equipe["defi_id"]:
            raise ValidationError("Cette équipe ne participe pas au défi de cet imprévu.")
        if evenement["etat"] != EvenementScenario.Etat.PUBLIE:
            raise ValidationError("On ne répond qu'à un imprévu publié.")
        actuel = valeurs_en_base(self, ("etat",) + self.CHAMPS_CONTENU)
        if actuel and actuel["etat"] == self.Etat.SOUMISE:
            if self.etat != self.Etat.SOUMISE or any(actuel[c] != getattr(self, c) for c in self.CHAMPS_CONTENU):
                raise ValidationError("Une réponse soumise ne peut plus être modifiée.")
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# Traitements IA (état consultable, idempotence, mesures)
# ---------------------------------------------------------------------------

DEMANDES_ACTIVES = ("en_attente", "en_cours")


class DemandeIA(models.Model):
    """Une demande de traitement Gemini. Ne contient ni prompt complet, ni clé, ni donnée personnelle."""

    class Type(models.TextChoices):
        BRIEF = "brief", "Génération de brief"
        EVENEMENT = "evenement", "Proposition d'imprévu"
        VERIFICATION = "verification", "Vérification de livrable"

    class Statut(models.TextChoices):
        EN_ATTENTE = "en_attente", "En attente"
        EN_COURS = "en_cours", "En cours"
        TERMINE = "termine", "Terminé"
        ECHOUE = "echoue", "Échoué"

    ACTIFS = DEMANDES_ACTIVES

    type_demande = models.CharField(max_length=15, choices=Type.choices)
    statut = models.CharField(max_length=12, choices=Statut.choices, default=Statut.EN_ATTENTE)
    demandeur = models.ForeignKey(UTILISATEUR, on_delete=models.SET_NULL, null=True, related_name="demandes_edusprint")
    defi = models.ForeignKey(Defi, on_delete=models.CASCADE, null=True, blank=True, related_name="demandes_ia")
    version = models.ForeignKey(VersionLivrable, on_delete=models.CASCADE, null=True, blank=True,
                                related_name="analyses")
    parametres = models.JSONField(default=dict, blank=True)
    resultat = models.JSONField(default=dict, blank=True)
    message = models.CharField(max_length=300, blank=True)
    empreinte = models.CharField("empreinte du texte analysé", max_length=64, blank=True)
    defi_cree = models.ForeignKey(Defi, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    evenement_cree = models.ForeignKey(EvenementScenario, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name="+")
    modele = models.CharField(max_length=80, blank=True)
    duree_ms = models.PositiveIntegerField(null=True, blank=True)
    essais = models.PositiveSmallIntegerField(default=0)
    code_erreur = models.CharField(max_length=30, blank=True)
    date_creation = models.DateTimeField(auto_now_add=True, db_index=True)
    date_debut = models.DateTimeField(null=True, blank=True)
    date_fin = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "demande IA"
        verbose_name_plural = "demandes IA"
        ordering = ["-date_creation"]
        constraints = [
            # Double clic / double soumission : un seul traitement actif par objet
            models.UniqueConstraint(fields=["version"], condition=Q(type_demande="verification", statut__in=DEMANDES_ACTIVES),
                                    name="edusprint_une_verification_active"),
            models.UniqueConstraint(fields=["defi"], condition=Q(type_demande="evenement", statut__in=DEMANDES_ACTIVES),
                                    name="edusprint_une_proposition_active"),
            models.UniqueConstraint(fields=["demandeur"], condition=Q(type_demande="brief", statut__in=DEMANDES_ACTIVES),
                                    name="edusprint_un_brief_actif"),
        ]
        indexes = [models.Index(fields=["demandeur", "date_creation"], name="edusprint_demande_quota_idx")]

    def __str__(self):
        return f"{self.get_type_demande_display()} – {self.get_statut_display()}"

    @property
    def active(self):
        return self.statut in self.ACTIFS
