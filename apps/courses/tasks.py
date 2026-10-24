import logging

from celery import shared_task

from ai_services import duree_audio, extraire_texte_pdf, resumer_cours, transcrire

from .models import Cours, Resume, Transcription, TraductionResume

logger = logging.getLogger(__name__)


def _echec(cours, etape, erreur):
    logger.exception("Échec du traitement du cours %s", cours.pk)
    cours.mettre_a_jour(statut=Cours.Statut.ERREUR, message_erreur=f"Échec pendant {etape} : {erreur}")


def _enregistrer_texte(cours, texte):
    """Sauvegarde le texte du cours et son nombre de mots (temps de lecture estimé)."""
    transcription, _ = Transcription.objects.update_or_create(cours=cours, defaults={"texte": texte})
    cours.mettre_a_jour(nb_mots=len(texte.split()))
    return transcription


@shared_task
def transcrire_cours(cours_id):
    """Transcrit l'audio du cours avec Whisper et enregistre la Transcription."""
    cours = Cours.objects.get(pk=cours_id)
    cours.mettre_a_jour(statut=Cours.Statut.EN_COURS, progression=0, message_erreur="")

    try:
        if cours.type_fichier != Cours.TypeFichier.AUDIO:
            raise ValueError("seuls les cours audio peuvent être transcrits.")
        texte = transcrire(cours.fichier.path)
        _enregistrer_texte(cours, texte)
        if not cours.duree_audio:
            cours.mettre_a_jour(duree_audio=duree_audio(cours.fichier.path))
        cours.mettre_a_jour(statut=Cours.Statut.TERMINE, progression=100)
    except Exception as erreur:
        _echec(cours, "la transcription audio", erreur)


@shared_task
def generer_resume_cours(cours_id, type_resume=Resume.Type.DETAILLE, consigne=""):
    """Génère le résumé du cours à partir de sa transcription (ou du texte du PDF)."""
    cours = Cours.objects.get(pk=cours_id)
    cours.mettre_a_jour(statut=Cours.Statut.EN_COURS, progression=0, message_erreur="")
    etape = "l'extraction du texte"

    try:
        transcription = Transcription.objects.filter(cours=cours).first()
        if transcription is None:
            if cours.type_fichier == Cours.TypeFichier.AUDIO:
                raise ValueError("l'audio doit d'abord être transcrit.")
            transcription = _enregistrer_texte(cours, extraire_texte_pdf(cours.fichier.path))
        cours.mettre_a_jour(progression=33)

        etape = "la génération du résumé"
        texte_resume = resumer_cours(transcription.texte, type_resume=type_resume, consigne=consigne)
        cours.mettre_a_jour(progression=66)
        resume, _ = Resume.objects.update_or_create(
            cours=cours, defaults={"texte": texte_resume, "type_resume": type_resume, "consigne": consigne}
        )
        # Les traductions de l'ancien résumé ne sont plus valables
        TraductionResume.objects.filter(resume=resume).delete()

        cours.mettre_a_jour(statut=Cours.Statut.TERMINE, progression=100)
    except Exception as erreur:
        _echec(cours, etape, erreur)
