import math
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import Enseignant

EXTENSIONS_PDF = [".pdf"]
EXTENSIONS_AUDIO = [".mp3", ".wav", ".m4a", ".ogg", ".flac", ".webm"]

MOTS_PAR_MINUTE_LECTURE = 200
MOIS_RENTREE = 9  # L'année universitaire commence en septembre


def annee_universitaire(date):
    """Année de début de l'année universitaire d'une date : 15/10/2025 → 2025, 15/03/2026 → 2025."""
    date = timezone.localtime(date) if timezone.is_aware(date) else date
    return date.year if date.month >= MOIS_RENTREE else date.year - 1


def bornes_annee_universitaire(annee):
    """Du 1er septembre `annee` (inclus) au 1er septembre `annee + 1` (exclu), en heure locale."""
    debut = timezone.make_aware(datetime(annee, MOIS_RENTREE, 1))
    fin = timezone.make_aware(datetime(annee + 1, MOIS_RENTREE, 1))
    return debut, fin


class Matiere(models.Model):
    """Matière d'un cours (gérée par l'administrateur)."""

    nom = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["nom"]
        verbose_name = "matière"

    def __str__(self):
        return self.nom


class Cours(models.Model):
    class TypeFichier(models.TextChoices):
        PDF = "pdf", "PDF"
        AUDIO = "audio", "Audio"

    class Statut(models.TextChoices):
        EN_ATTENTE = "en_attente", "En attente"
        EN_COURS = "en_cours", "En cours de traitement"
        TERMINE = "termine", "Terminé"
        ERREUR = "erreur", "Erreur"

    class Niveau(models.TextChoices):
        DEBUTANT = "debutant", "Débutant"
        INTERMEDIAIRE = "intermediaire", "Intermédiaire"
        AVANCE = "avance", "Avancé"

    class Publication(models.TextChoices):
        BROUILLON = "brouillon", "Brouillon"
        PUBLIE = "publie", "Publié"
        PROGRAMME = "programme", "Publication programmée"

    titre = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    fichier = models.FileField(upload_to="cours/")
    type_fichier = models.CharField(max_length=10, choices=TypeFichier.choices)
    enseignant = models.ForeignKey(Enseignant, on_delete=models.CASCADE, related_name="cours")
    date_creation = models.DateTimeField(auto_now_add=True)
    statut = models.CharField(max_length=20, choices=Statut.choices, default=Statut.EN_ATTENTE)
    progression = models.PositiveSmallIntegerField(
        default=0, validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    message_erreur = models.TextField(blank=True)

    # Organisation (filtres, recommandations, prérequis)
    matiere = models.ForeignKey(Matiere, on_delete=models.SET_NULL, null=True, blank=True, related_name="cours")
    niveau = models.CharField(max_length=20, choices=Niveau.choices, blank=True)
    mots_cles = models.CharField(max_length=255, blank=True, help_text="Séparés par des virgules")
    prerequis = models.ManyToManyField(
        "self", symmetrical=False, blank=True, related_name="requis_pour", verbose_name="prérequis"
    )

    # Publication et modération
    publication = models.CharField(max_length=20, choices=Publication.choices, default=Publication.BROUILLON)
    date_publication = models.DateTimeField(null=True, blank=True)
    masque = models.BooleanField(default=False)
    archive = models.BooleanField(default=False)
    date_archivage = models.DateTimeField(null=True, blank=True)

    # Temps estimé de lecture / d'écoute
    duree_audio = models.PositiveIntegerField(null=True, blank=True, help_text="En secondes")
    nb_mots = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-date_creation"]
        verbose_name = "cours"
        verbose_name_plural = "cours"

    def __str__(self):
        return self.titre

    @staticmethod
    def filtre_publie():
        """Condition « publié » : publié, ou programmé avec une date déjà passée."""
        return Q(publication=Cours.Publication.PUBLIE) | Q(
            publication=Cours.Publication.PROGRAMME, date_publication__lte=timezone.now()
        )

    @property
    def est_publie(self):
        if self.publication == self.Publication.PUBLIE:
            return True
        return (
            self.publication == self.Publication.PROGRAMME
            and self.date_publication is not None
            and self.date_publication <= timezone.now()
        )

    @property
    def liste_mots_cles(self):
        return [mot.strip() for mot in self.mots_cles.split(",") if mot.strip()]

    @property
    def minutes_estimees(self):
        """Durée d'écoute (audio) ou de lecture (texte), arrondie à la minute supérieure."""
        if self.type_fichier == self.TypeFichier.AUDIO and self.duree_audio:
            return max(1, math.ceil(self.duree_audio / 60))
        if self.nb_mots:
            return max(1, math.ceil(self.nb_mots / MOTS_PAR_MINUTE_LECTURE))
        return None

    @staticmethod
    def type_depuis_nom(nom_fichier):
        """Renvoie 'pdf' ou 'audio' selon l'extension, ou None si elle n'est pas acceptée."""
        extension = Path(nom_fichier).suffix.lower()
        if extension in EXTENSIONS_PDF:
            return Cours.TypeFichier.PDF
        if extension in EXTENSIONS_AUDIO:
            return Cours.TypeFichier.AUDIO
        return None

    def mettre_a_jour(self, **champs):
        """Met à jour quelques champs en base sans écraser les autres (utilisé par la tâche Celery)."""
        for nom, valeur in champs.items():
            setattr(self, nom, valeur)
        self.save(update_fields=list(champs))


class Transcription(models.Model):
    cours = models.OneToOneField(Cours, on_delete=models.CASCADE, related_name="transcription")
    texte = models.TextField()
    date = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Transcription de « {self.cours} »"


class Resume(models.Model):
    class Type(models.TextChoices):
        COURT = "court", "Court"
        DETAILLE = "detaille", "Détaillé"
        POINTS_CLES = "points_cles", "Points clés"
        FICHE = "fiche", "Fiche de révision"

    cours = models.OneToOneField(Cours, on_delete=models.CASCADE, related_name="resume")
    texte = models.TextField()
    date_generation = models.DateTimeField(auto_now=True)
    type_resume = models.CharField(max_length=20, choices=Type.choices, default=Type.DETAILLE)
    consigne = models.TextField(blank=True)

    class Meta:
        verbose_name = "résumé"

    def __str__(self):
        return f"Résumé de « {self.cours} »"


class TraductionResume(models.Model):
    class Langue(models.TextChoices):
        ANGLAIS = "en", "Anglais"
        ARABE = "ar", "Arabe"

    resume = models.ForeignKey(Resume, on_delete=models.CASCADE, related_name="traductions")
    langue = models.CharField(max_length=2, choices=Langue.choices)
    texte = models.TextField()
    date = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "traduction de résumé"
        verbose_name_plural = "traductions de résumé"
        constraints = [models.UniqueConstraint(fields=["resume", "langue"], name="traduction_unique_par_langue")]

    def __str__(self):
        return f"{self.resume} ({self.get_langue_display()})"


# ---------- Activité des étudiants ----------

class Consultation(models.Model):
    """Une ligne par consultation d'un cours par un étudiant (statistiques et historique)."""

    utilisateur = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="consultations_cours")
    cours = models.ForeignKey(Cours, on_delete=models.CASCADE, related_name="consultations")
    date = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date"]
        indexes = [models.Index(fields=["utilisateur", "-date"])]

    def __str__(self):
        return f"{self.utilisateur} → {self.cours}"


class Telechargement(models.Model):
    class Format(models.TextChoices):
        TXT = "txt", "Texte"
        PDF = "pdf", "PDF"

    utilisateur = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="telechargements_cours")
    cours = models.ForeignKey(Cours, on_delete=models.CASCADE, related_name="telechargements")
    format = models.CharField(max_length=3, choices=Format.choices)
    date = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "téléchargement"
        ordering = ["-date"]

    def __str__(self):
        return f"{self.utilisateur} → {self.cours} ({self.format})"


class Favori(models.Model):
    utilisateur = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="favoris_cours")
    cours = models.ForeignKey(Cours, on_delete=models.CASCADE, related_name="favoris")
    date_ajout = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_ajout"]
        constraints = [models.UniqueConstraint(fields=["utilisateur", "cours"], name="favori_unique")]

    def __str__(self):
        return f"{self.utilisateur} ♥ {self.cours}"


class Avis(models.Model):
    """Un seul avis par étudiant et par cours, modifiable ; l'enseignant peut y répondre."""

    utilisateur = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="avis_cours")
    cours = models.ForeignKey(Cours, on_delete=models.CASCADE, related_name="avis")
    note = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    commentaire = models.TextField(blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)
    reponse = models.TextField(blank=True)
    date_reponse = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name_plural = "avis"
        ordering = ["-date_modification"]
        constraints = [
            models.UniqueConstraint(fields=["utilisateur", "cours"], name="avis_unique_par_etudiant"),
            models.CheckConstraint(condition=Q(note__gte=1, note__lte=5), name="avis_note_entre_1_et_5"),
        ]

    def __str__(self):
        return f"{self.note}/5 de {self.utilisateur} sur {self.cours}"


class NotePersonnelle(models.Model):
    """Notes privées d'un étudiant sur un cours."""

    utilisateur = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notes_cours")
    cours = models.ForeignKey(Cours, on_delete=models.CASCADE, related_name="notes_personnelles")
    texte = models.TextField(blank=True)
    date_modification = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "note personnelle"
        verbose_name_plural = "notes personnelles"
        constraints = [models.UniqueConstraint(fields=["utilisateur", "cours"], name="note_personnelle_unique")]

    def __str__(self):
        return f"Notes de {self.utilisateur} sur {self.cours}"


# ---------- Modération ----------

class Signalement(models.Model):
    class Motif(models.TextChoices):
        INAPPROPRIE = "inapproprie", "Contenu inapproprié"
        ERRONE = "errone", "Contenu erroné"
        DROITS = "droits", "Droits d'auteur"
        TECHNIQUE = "technique", "Problème technique (fichier, résumé…)"
        AUTRE = "autre", "Autre"

    class Statut(models.TextChoices):
        NOUVEAU = "nouveau", "Nouveau"
        TRAITE = "traite", "Traité"
        REJETE = "rejete", "Rejeté"

    auteur = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="signalements_cours")
    cours = models.ForeignKey(Cours, on_delete=models.CASCADE, related_name="signalements")
    motif = models.CharField(max_length=20, choices=Motif.choices)
    message = models.TextField(blank=True)
    statut = models.CharField(max_length=10, choices=Statut.choices, default=Statut.NOUVEAU)
    date = models.DateTimeField(auto_now_add=True)
    traite_par = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="signalements_traites"
    )
    date_traitement = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date"]

    def __str__(self):
        return f"Signalement de « {self.cours} » ({self.get_motif_display()})"


class JournalActivite(models.Model):
    class Action(models.TextChoices):
        CREATION = "creation", "Création"
        MODIFICATION = "modification", "Modification"
        SUPPRESSION = "suppression", "Suppression"
        MASQUAGE = "masquage", "Masquage"
        REAFFICHAGE = "reaffichage", "Réaffichage"
        ARCHIVAGE = "archivage", "Archivage"
        DESARCHIVAGE = "desarchivage", "Désarchivage"

    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="activites_cours"
    )
    action = models.CharField(max_length=20, choices=Action.choices)
    # Le cours peut avoir été supprimé : on garde son titre
    cours = models.ForeignKey(Cours, on_delete=models.SET_NULL, null=True, blank=True, related_name="journal")
    titre_cours = models.CharField(max_length=200)
    date = models.DateTimeField(auto_now_add=True)
    details = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "activité"
        verbose_name_plural = "journal d'activité"
        ordering = ["-date"]

    def __str__(self):
        return f"{self.get_action_display()} de « {self.titre_cours} » par {self.utilisateur}"

    @classmethod
    def enregistrer(cls, utilisateur, action, cours, details=""):
        return cls.objects.create(
            utilisateur=utilisateur, action=action, cours=cours, titre_cours=cours.titre, details=details
        )
