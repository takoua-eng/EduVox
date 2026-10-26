from django.conf import settings
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone


class SessionAssistant(models.Model):
    """Conversation d'un utilisateur avec l'assistant, éventuellement rattachée à un cours (contexte)."""

    utilisateur = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sessions_assistant")
    titre = models.CharField(max_length=150)
    cours = models.ForeignKey("courses.Cours", on_delete=models.SET_NULL, null=True, blank=True,
                              related_name="sessions_assistant", help_text="Le résumé du cours sert de contexte à l'IA")
    active = models.BooleanField(default=True)
    date_ouverture = models.DateTimeField(auto_now_add=True)
    date_fermeture = models.DateTimeField(null=True, blank=True)
    date_maj = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date_maj", "-pk"]
        verbose_name = "session d'assistant"
        verbose_name_plural = "sessions d'assistant"

    def __str__(self):
        return f"{self.titre} ({self.utilisateur.username})"

    def fermer(self):
        self.active = False
        self.date_fermeture = timezone.now()
        self.save(update_fields=["active", "date_fermeture", "date_maj"])

    def rouvrir(self):
        self.active = True
        self.date_fermeture = None
        self.save(update_fields=["active", "date_fermeture", "date_maj"])


class MessageVocal(models.Model):
    """Un message de la conversation : question (écrite ou orale) ou réponse de l'assistant."""

    class Role(models.TextChoices):
        UTILISATEUR = "UTILISATEUR", "Vous"
        ASSISTANT = "ASSISTANT", "Assistant"

    class Mode(models.TextChoices):
        TEXTE = "TEXTE", "Écrit"
        VOCAL = "VOCAL", "Oral"

    class Statut(models.TextChoices):
        EN_ATTENTE = "EN_ATTENTE", "En cours"
        PRET = "PRET", "Prêt"
        ERREUR = "ERREUR", "Erreur"

    class Evaluation(models.IntegerChoices):
        UTILE = 1, "Utile"
        PAS_UTILE = -1, "Pas utile"

    session = models.ForeignKey(SessionAssistant, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=12, choices=Role.choices)
    mode = models.CharField(max_length=5, choices=Mode.choices, default=Mode.TEXTE)
    texte = models.TextField(blank=True)
    audio = models.FileField(upload_to="assistant/audio/%Y/%m/", null=True, blank=True)
    duree_audio_s = models.FloatField(null=True, blank=True)
    statut = models.CharField(max_length=10, choices=Statut.choices, default=Statut.PRET)
    message_erreur = models.CharField(max_length=255, blank=True)
    evaluation = models.SmallIntegerField(choices=Evaluation.choices, null=True, blank=True)
    modele = models.CharField(max_length=80, blank=True)
    duree_ms = models.PositiveIntegerField(null=True, blank=True, help_text="Durée totale du traitement IA")
    premier_morceau_ms = models.PositiveIntegerField(null=True, blank=True,
                                                     help_text="Délai avant le premier morceau de réponse")
    date = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "pk"]
        verbose_name = "message"

    def __str__(self):
        return f"{self.get_role_display()} : {self.texte[:60]}"

    @property
    def est_question(self):
        return self.role == self.Role.UTILISATEUR


class Illustration(models.Model):
    """Image (Hugging Face) ou diagramme (code Mermaid écrit par Gemini) généré à la demande."""

    class Type(models.TextChoices):
        IMAGE = "IMAGE", "Illustration"
        DIAGRAMME = "DIAGRAMME", "Diagramme"

    class Statut(models.TextChoices):
        EN_COURS = "EN_COURS", "Génération en cours"
        TERMINEE = "TERMINEE", "Terminée"
        ERREUR = "ERREUR", "Erreur"

    session = models.ForeignKey(SessionAssistant, on_delete=models.CASCADE, related_name="illustrations")
    message = models.ForeignKey(MessageVocal, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name="illustrations")
    type_illustration = models.CharField(max_length=10, choices=Type.choices, default=Type.IMAGE)
    demande = models.CharField(max_length=500)
    prompt_ia = models.TextField(blank=True)
    texte_alternatif = models.CharField(max_length=300, blank=True)
    image = models.ImageField(upload_to="assistant/illustrations/%Y/%m/", null=True, blank=True)
    code_mermaid = models.TextField(blank=True)
    statut = models.CharField(max_length=10, choices=Statut.choices, default=Statut.EN_COURS)
    message_erreur = models.CharField(max_length=255, blank=True)
    modele = models.CharField(max_length=80, blank=True)
    duree_ms = models.PositiveIntegerField(null=True, blank=True)
    date = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-pk"]
        verbose_name = "illustration"

    def __str__(self):
        return f"{self.get_type_illustration_display()} : {self.demande[:60]}"


class JournalIA(models.Model):
    """Journal technique de chaque appel IA : sert aux métriques (taux de succès, latence)."""

    class Type(models.TextChoices):
        TRANSCRIPTION = "TRANSCRIPTION", "Transcription (Whisper)"
        REPONSE = "REPONSE", "Réponse (Gemini)"
        ILLUSTRATION = "ILLUSTRATION", "Illustration (Hugging Face)"
        DIAGRAMME = "DIAGRAMME", "Diagramme (Gemini)"

    utilisateur = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="+")
    type_appel = models.CharField(max_length=15, choices=Type.choices)
    succes = models.BooleanField()
    code = models.CharField(max_length=30, default="ok")
    modele = models.CharField(max_length=80, blank=True)
    duree_ms = models.PositiveIntegerField(null=True, blank=True)
    premier_morceau_ms = models.PositiveIntegerField(null=True, blank=True)
    date = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-date"]
        verbose_name = "appel IA"
        verbose_name_plural = "journal des appels IA"

    def __str__(self):
        return f"{self.get_type_appel_display()} – {'ok' if self.succes else self.code}"


# Les fichiers (audio, images) sont supprimés du disque avec leur ligne en base
@receiver(post_delete, sender=MessageVocal)
def _supprimer_audio(sender, instance, **kwargs):
    if instance.audio:
        instance.audio.delete(save=False)


@receiver(post_delete, sender=Illustration)
def _supprimer_image(sender, instance, **kwargs):
    if instance.image:
        instance.image.delete(save=False)
