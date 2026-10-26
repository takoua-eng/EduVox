"""Module 3 : quiz, questions, choix, tentatives et réponses des étudiants.

Règles d'évaluation :
- QCM : une seule bonne réponse (2 à 6 choix). Vrai/Faux : deux choix « Vrai » et « Faux ».
  Une réponse juste rapporte tous les points de la question, une réponse fausse ou absente 0.
- Question ouverte : corrigée par Gemini (note entre 0 et le barème), révisable par l'enseignant.
- Un quiz qui a déjà des tentatives n'est plus modifiable : on crée une nouvelle version
  (copie en brouillon) pour ne pas fausser les scores déjà obtenus.
"""

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone

from apps.accounts.models import Enseignant
from apps.courses.models import Cours

POINTS_MIN = Decimal("0.25")
POINTS_MAX = Decimal("20")
CHOIX_MIN_QCM = 2
CHOIX_MAX_QCM = 6
QUESTIONS_MAX = 30
TENTATIVES_MAX = 10
ENONCE_MAX = 1000
REPONSE_OUVERTE_MAX = 3000
INDICES_MAX = 3                      # indices IA par tentative
COEF_INDICE = Decimal("0.75")        # une question aidée par un indice rapporte 75 % de ses points
SEUIL_SORTIES = 3                    # sorties de page à partir desquelles la tentative est « à vérifier »
SECONDES_MIN_PAR_QUESTION = 5        # en dessous, les réponses sont signalées comme très rapides
SEUIL_CERTIFICAT = 70                # score minimal (%) pour obtenir le certificat de réussite
INTERVALLES_LEITNER = {1: 1, 2: 2, 3: 4, 4: 8, 5: 16}   # boîte → jours avant la prochaine révision


class TraitementIA:
    """État d'un traitement IA long stocké dans un champ JSON : {statut, resultat, message, date}."""

    AUCUN, EN_COURS, TERMINE, ERREUR = "aucun", "en_cours", "termine", "erreur"

    @staticmethod
    def etat(valeur):
        return (valeur or {}).get("statut", TraitementIA.AUCUN)

    @staticmethod
    def en_cours():
        return {"statut": TraitementIA.EN_COURS, "date": timezone.now().isoformat()}

    @staticmethod
    def termine(resultat):
        return {"statut": TraitementIA.TERMINE, "resultat": resultat, "date": timezone.now().isoformat()}

    @staticmethod
    def erreur(message):
        return {"statut": TraitementIA.ERREUR, "message": message, "date": timezone.now().isoformat()}


class Quiz(models.Model):
    class Statut(models.TextChoices):
        BROUILLON = "brouillon", "Brouillon"
        PUBLIE = "publie", "Publié"
        ARCHIVE = "archive", "Archivé"

    class Origine(models.TextChoices):
        MANUEL = "manuel", "Création manuelle"
        IA = "ia", "Généré par l'IA"

    class Difficulte(models.TextChoices):
        FACILE = "facile", "Facile"
        MOYEN = "moyen", "Moyen"
        DIFFICILE = "difficile", "Difficile"

    class Generation(models.TextChoices):
        AUCUNE = "aucune", "Aucune"
        EN_COURS = "en_cours", "Génération en cours"
        TERMINEE = "terminee", "Génération terminée"
        ERREUR = "erreur", "Échec de la génération"

    titre = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    # Le cours peut être supprimé par son module : on garde le quiz (et son historique) avec le titre du cours
    cours = models.ForeignKey(Cours, on_delete=models.SET_NULL, null=True, blank=True, related_name="quiz")
    titre_cours = models.CharField(max_length=200, blank=True)
    enseignant = models.ForeignKey(Enseignant, on_delete=models.CASCADE, related_name="quiz")
    difficulte = models.CharField(max_length=10, choices=Difficulte.choices, default=Difficulte.MOYEN)
    tentatives_max = models.PositiveSmallIntegerField(
        "nombre de tentatives autorisées", default=1,
        validators=[MinValueValidator(1), MaxValueValidator(TENTATIVES_MAX)],
    )
    afficher_corrige = models.BooleanField(
        "montrer le corrigé après la soumission", default=True,
        help_text="Bonnes réponses et explications visibles par l'étudiant une fois sa tentative soumise.",
    )

    statut = models.CharField(max_length=10, choices=Statut.choices, default=Statut.BROUILLON)
    origine = models.CharField(max_length=10, choices=Origine.choices, default=Origine.MANUEL)
    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)
    date_publication = models.DateTimeField(null=True, blank=True)

    # Génération IA (tâche Celery)
    statut_generation = models.CharField(max_length=10, choices=Generation.choices, default=Generation.AUCUNE)
    message_generation = models.TextField(blank=True)
    parametres_generation = models.JSONField(default=dict, blank=True)

    # Versions : copie créée quand le quiz d'origine a déjà des tentatives
    version = models.PositiveSmallIntegerField(default=1)
    version_precedente = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="versions_suivantes"
    )

    # IA pour l'enseignant : audit qualité avant publication, analyse anonymisée de la classe
    audit_ia = models.JSONField(default=dict, blank=True)
    analyse_ia = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-date_creation"]
        verbose_name = "quiz"
        verbose_name_plural = "quiz"
        constraints = [
            models.CheckConstraint(
                condition=Q(tentatives_max__gte=1, tentatives_max__lte=TENTATIVES_MAX),
                name="quiz_tentatives_max_entre_1_et_10",
            ),
        ]

    def __str__(self):
        return self.titre

    def save(self, *args, **kwargs):
        if self.cours_id and not self.titre_cours:
            self.titre_cours = self.cours.titre
        super().save(*args, **kwargs)

    @property
    def a_des_tentatives(self):
        return self.tentatives.exists()

    @property
    def generation_en_cours(self):
        return self.statut_generation == self.Generation.EN_COURS

    @property
    def est_modifiable(self):
        """Questions et réglages modifiables : brouillon, sans génération en cours et sans aucune tentative."""
        return self.statut == self.Statut.BROUILLON and not self.generation_en_cours and not self.a_des_tentatives

    @property
    def est_supprimable(self):
        """Seul un brouillon jamais tenté peut être supprimé ; sinon on l'archive."""
        return self.statut == self.Statut.BROUILLON and not self.generation_en_cours and not self.a_des_tentatives

    def points_total(self):
        return self.questions.aggregate(total=Sum("points"))["total"] or Decimal("0")

    def erreurs_publication(self):
        """Liste des problèmes qui empêchent la publication (vide = publiable)."""
        erreurs = []
        if self.generation_en_cours:
            erreurs.append("La génération par l'IA n'est pas terminée.")
        if self.cours_id is None:
            erreurs.append("Le quiz doit être rattaché à un cours.")
        questions = list(self.questions.prefetch_related("choix"))
        if not questions:
            erreurs.append("Le quiz ne contient aucune question.")
        for question in questions:
            for erreur in question.erreurs_coherence():
                erreurs.append(f"Question {question.ordre} : {erreur}")
        return erreurs

    def prochain_ordre(self):
        dernier = self.questions.aggregate(m=models.Max("ordre"))["m"]
        return (dernier or 0) + 1


class Question(models.Model):
    class Type(models.TextChoices):
        QCM = "qcm", "QCM (une seule bonne réponse)"
        VRAI_FAUX = "vrai_faux", "Vrai / Faux"
        OUVERTE = "ouverte", "Question ouverte"

    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="questions")
    ordre = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    type_question = models.CharField("type", max_length=10, choices=Type.choices, default=Type.QCM)
    enonce = models.TextField("énoncé", max_length=ENONCE_MAX)
    points = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("1"),
        validators=[MinValueValidator(POINTS_MIN), MaxValueValidator(POINTS_MAX)],
    )
    explication = models.TextField(blank=True, help_text="Montrée à l'étudiant avec le corrigé.")
    reponse_attendue = models.TextField(
        "réponse attendue / barème", blank=True,
        help_text="Questions ouvertes : éléments attendus et critères de notation utilisés pour la correction.",
    )

    class Meta:
        ordering = ["ordre", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["quiz", "ordre"], name="question_ordre_unique_par_quiz"),
            models.CheckConstraint(condition=Q(points__gte=POINTS_MIN, points__lte=POINTS_MAX),
                                   name="question_points_entre_bornes"),
        ]

    def __str__(self):
        return f"Q{self.ordre} – {self.enonce[:60]}"

    @property
    def a_des_choix(self):
        return self.type_question in (self.Type.QCM, self.Type.VRAI_FAUX)

    def clean(self):
        if self.type_question == self.Type.OUVERTE and not (self.reponse_attendue or "").strip():
            raise ValidationError({"reponse_attendue": "Indiquez la réponse attendue ou le barème : ils servent à la correction."})

    def erreurs_coherence(self, choix=None):
        """Vérifie les choix selon le type (utilisé à la publication et après la génération IA)."""
        choix = list(self.choix.all()) if choix is None else choix
        nb_corrects = sum(1 for c in choix if c.est_correct)
        if self.type_question == self.Type.OUVERTE:
            erreurs = [] if (self.reponse_attendue or "").strip() else ["réponse attendue manquante."]
            if choix:
                erreurs.append("une question ouverte ne doit pas avoir de choix.")
            return erreurs
        if self.type_question == self.Type.VRAI_FAUX:
            textes = sorted(c.texte.strip().lower() for c in choix)
            if textes != ["faux", "vrai"] or nb_corrects != 1:
                return ["une question Vrai/Faux doit avoir les choix « Vrai » et « Faux », dont un seul correct."]
            return []
        erreurs = []
        if not CHOIX_MIN_QCM <= len(choix) <= CHOIX_MAX_QCM:
            erreurs.append(f"un QCM doit avoir entre {CHOIX_MIN_QCM} et {CHOIX_MAX_QCM} choix.")
        if nb_corrects != 1:
            erreurs.append("un QCM doit avoir exactement une bonne réponse.")
        if len({c.texte.strip().lower() for c in choix}) != len(choix):
            erreurs.append("deux choix sont identiques.")
        return erreurs


class Choix(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="choix")
    texte = models.CharField(max_length=300)
    est_correct = models.BooleanField("bonne réponse", default=False)
    ordre = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["ordre", "pk"]
        verbose_name_plural = "choix"

    def __str__(self):
        return self.texte


class TentativeQuiz(models.Model):
    class Statut(models.TextChoices):
        EN_COURS = "en_cours", "En cours"
        SOUMISE = "soumise", "Soumise – correction en attente"
        CORRIGEE = "corrigee", "Corrigée"

    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="tentatives")
    etudiant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tentatives_quiz")
    numero = models.PositiveSmallIntegerField(default=1)
    statut = models.CharField(max_length=10, choices=Statut.choices, default=Statut.EN_COURS)
    date_debut = models.DateTimeField(auto_now_add=True)
    date_soumission = models.DateTimeField(null=True, blank=True)
    date_correction = models.DateTimeField(null=True, blank=True)
    # Calculés côté serveur uniquement (jamais reçus du navigateur)
    score = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    score_max = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    # Indices IA demandés pendant la tentative : {"<id question>": "texte de l'indice"}
    indices = models.JSONField(default=dict, blank=True)
    # Intégrité : graine du mélange des choix, signaux d'attention ({"sorties": n, "collages": n})
    graine = models.PositiveIntegerField(default=0)
    integrite = models.JSONField(default=dict, blank=True)
    # Bilan pédagogique personnalisé généré après la correction
    bilan_ia = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-date_debut"]
        verbose_name = "tentative de quiz"
        verbose_name_plural = "tentatives de quiz"
        constraints = [
            models.UniqueConstraint(fields=["quiz", "etudiant", "numero"], name="tentative_numero_unique"),
            # Une seule tentative ouverte à la fois par étudiant et par quiz
            models.UniqueConstraint(fields=["quiz", "etudiant"], condition=Q(statut="en_cours"),
                                    name="une_tentative_en_cours_par_quiz"),
        ]

    def __str__(self):
        return f"{self.etudiant} – {self.quiz} (tentative {self.numero})"

    @property
    def est_soumise(self):
        return self.statut != self.Statut.EN_COURS

    def nb_en_attente(self):
        return self.reponses.filter(points_obtenus__isnull=True).count()

    def recalculer_score(self):
        """Score = somme des points déjà attribués. Définitif quand plus aucune réponse n'attend de correction."""
        agregat = self.reponses.aggregate(total=Sum("points_obtenus"))
        self.score = agregat["total"] or Decimal("0")
        if self.est_soumise:
            if self.nb_en_attente() == 0:
                self.statut = self.Statut.CORRIGEE
                self.date_correction = self.date_correction or timezone.now()
            else:
                self.statut = self.Statut.SOUMISE
                self.date_correction = None
        self.save(update_fields=["score", "statut", "date_correction"])

    @property
    def pourcentage(self):
        if self.score is None or not self.score_max:
            return None
        return round(float(self.score) * 100 / float(self.score_max))

    @property
    def indices_restants(self):
        return max(0, INDICES_MAX - len(self.indices or {}))

    @property
    def duree(self):
        """Durée de la tentative (début → soumission), ou None."""
        return self.date_soumission - self.date_debut if self.date_soumission else None

    @property
    def signaux_integrite(self):
        """Signaux à vérifier par l'enseignant (jamais une accusation : un contexte pour juger)."""
        donnees = self.integrite or {}
        signaux = []
        if donnees.get("sorties", 0) >= SEUIL_SORTIES:
            signaux.append(f"{donnees['sorties']} sorties de la page pendant le quiz")
        if donnees.get("collages", 0):
            signaux.append(f"{donnees['collages']} texte(s) collé(s) dans une réponse rédigée")
        nb_questions = self.reponses.count() if self.est_soumise else 0
        if self.duree and nb_questions and self.duree.total_seconds() < nb_questions * SECONDES_MIN_PAR_QUESTION:
            signaux.append(f"réponses très rapides ({self.duree_texte} pour {nb_questions} questions)")
        return signaux

    @property
    def duree_texte(self):
        if not self.duree:
            return ""
        secondes = int(self.duree.total_seconds())
        if secondes >= 3600:
            return f"{secondes // 3600} h {secondes % 3600 // 60:02d}"
        return f"{secondes // 60} min {secondes % 60:02d} s" if secondes >= 60 else f"{secondes} s"


class ReponseEtudiant(models.Model):
    class Correction(models.TextChoices):
        AUTOMATIQUE = "automatique", "Corrigée automatiquement"
        EN_ATTENTE = "en_attente", "Correction IA en attente"
        EN_COURS = "en_cours", "Correction IA en cours"
        IA = "ia", "Corrigée par l'IA"
        ERREUR = "erreur", "Correction IA indisponible – en attente"
        ENSEIGNANT = "enseignant", "Corrigée par l'enseignant"

    tentative = models.ForeignKey(TentativeQuiz, on_delete=models.CASCADE, related_name="reponses")
    # RESTRICT : impossible de supprimer une question ou un choix déjà utilisé dans une réponse
    question = models.ForeignKey(Question, on_delete=models.RESTRICT, related_name="reponses")
    choix = models.ForeignKey(Choix, on_delete=models.RESTRICT, null=True, blank=True, related_name="reponses")
    texte = models.TextField(blank=True, max_length=REPONSE_OUVERTE_MAX)

    points_obtenus = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    est_correcte = models.BooleanField(null=True, blank=True)
    statut_correction = models.CharField(max_length=12, choices=Correction.choices, default=Correction.AUTOMATIQUE)
    explication_correction = models.TextField(blank=True)
    message_erreur = models.CharField(max_length=255, blank=True)

    # Trace de la correction IA (sans secret ni donnée personnelle) : sert aussi aux métriques
    points_ia = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    modele_ia = models.CharField(max_length=60, blank=True)
    duree_ia_ms = models.PositiveIntegerField(null=True, blank=True)
    nb_essais_ia = models.PositiveSmallIntegerField(default=0)
    date_correction = models.DateTimeField(null=True, blank=True)
    corrige_par = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="reponses_corrigees"
    )
    indice_utilise = models.BooleanField(default=False)
    # « Pourquoi ma réponse est fausse ? » : explication IA mise en cache (un seul appel par réponse)
    explication_ia = models.TextField(blank=True)

    class Meta:
        ordering = ["question__ordre"]
        verbose_name = "réponse d'étudiant"
        verbose_name_plural = "réponses d'étudiants"
        constraints = [
            models.UniqueConstraint(fields=["tentative", "question"], name="une_reponse_par_question"),
            models.CheckConstraint(condition=Q(points_obtenus__isnull=True) | Q(points_obtenus__gte=0),
                                   name="reponse_points_positifs"),
        ]

    def __str__(self):
        return f"Réponse à {self.question} ({self.tentative})"

    def clean(self):
        if self.choix_id and self.choix.question_id != self.question_id:
            raise ValidationError("Ce choix n'appartient pas à la question.")
        if self.points_obtenus is not None and not 0 <= self.points_obtenus <= self.question.points:
            raise ValidationError({"points_obtenus": f"La note doit être comprise entre 0 et {self.question.points}."})

    @property
    def en_attente(self):
        return self.points_obtenus is None


class AppelIA(models.Model):
    """Journal technique des appels Gemini : limite de débit et métriques (durée, taux d'échec, validité JSON).

    Ne contient ni prompt, ni réponse d'étudiant, ni clé : seulement le résultat de l'appel.
    """

    class Type(models.TextChoices):
        GENERATION = "generation", "Génération de quiz"
        CORRECTION = "correction", "Correction de réponse ouverte"
        INDICE = "indice", "Indice pendant le quiz"
        IMPORT = "import", "Import de questions"
        EXPLICATION = "explication", "Explication d'une erreur"
        BILAN = "bilan", "Bilan personnalisé"
        AUDIT = "audit", "Audit qualité du quiz"
        ANALYSE = "analyse", "Analyse de la classe"

    type_appel = models.CharField(max_length=12, choices=Type.choices)
    utilisateur = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="appels_ia_quiz")
    quiz = models.ForeignKey(Quiz, on_delete=models.SET_NULL, null=True, blank=True, related_name="appels_ia")
    date = models.DateTimeField(auto_now_add=True, db_index=True)
    succes = models.BooleanField(default=False)
    code = models.CharField(max_length=20, default="ok", help_text="ok, delai, quota, reseau, json_invalide...")
    modele = models.CharField(max_length=60, blank=True)
    duree_ms = models.PositiveIntegerField(null=True, blank=True)
    essais = models.PositiveSmallIntegerField(default=1)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-date"]
        verbose_name = "appel IA"
        verbose_name_plural = "appels IA (quiz)"

    def __str__(self):
        return f"{self.get_type_appel_display()} – {'succès' if self.succes else self.code} – {self.date:%d/%m/%Y %H:%M}"


class CarteRevision(models.Model):
    """Fiche du carnet de révision espacée (méthode Leitner) : une question ratée par l'étudiant.

    Boîte 1 → révision le lendemain ; chaque bonne réponse fait monter d'une boîte (intervalle doublé),
    une erreur renvoie en boîte 1. En boîte 5 réussie, la notion est considérée comme maîtrisée.
    """

    etudiant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cartes_revision")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="cartes_revision")
    boite = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(5)])
    prochaine_revision = models.DateField(default=timezone.localdate, db_index=True)
    maitrisee = models.BooleanField(default=False)
    nb_revisions = models.PositiveIntegerField(default=0)
    nb_reussites = models.PositiveIntegerField(default=0)
    derniere_revision = models.DateTimeField(null=True, blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["prochaine_revision", "boite", "pk"]
        verbose_name = "fiche de révision"
        verbose_name_plural = "fiches de révision"
        constraints = [
            models.UniqueConstraint(fields=["etudiant", "question"], name="une_fiche_par_question"),
            models.CheckConstraint(condition=Q(boite__gte=1, boite__lte=5), name="fiche_boite_entre_1_et_5"),
        ]

    def __str__(self):
        return f"{self.etudiant} – {self.question} (boîte {self.boite})"

    def enregistrer_reponse(self, juste):
        """Applique la règle de Leitner et planifie la prochaine révision."""
        self.nb_revisions += 1
        self.derniere_revision = timezone.now()
        if juste:
            self.nb_reussites += 1
            if self.boite == 5:
                self.maitrisee = True
            self.boite = min(5, self.boite + 1)
        else:
            self.boite, self.maitrisee = 1, False
        self.prochaine_revision = timezone.localdate() + timedelta(days=INTERVALLES_LEITNER[self.boite])
        self.save()
