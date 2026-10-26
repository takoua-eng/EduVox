"""Tâches Celery du module assistant : transcription Whisper, illustrations, indexation des cours, fiches.

Ces traitements durent plusieurs secondes : ils ne bloquent pas la requête HTTP, la page interroge
leur état. Les deux tâches sont idempotentes (rien n'est refait si l'objet n'est plus « en cours »).
"""

import io
import logging
import time

from celery import shared_task
from django.core.files.base import ContentFile
from django.db import transaction

from ai_services import generer_image, lire_wav, transcrire_question
from ai_services.illustration import modele_image
from ai_services.transcription import MODELE_PAR_DEFAUT as WHISPER_PAR_DEFAUT

from .models import FicheRevision, Illustration, JournalIA, MessageVocal
from .reglages import reglage
from .services import (
    ErreurAssistant,
    generer_diagramme,
    generer_fiche,
    hugging_face_configure,
    journaliser,
    modele_gemini,
    preparer_prompt_image,
    traduire_erreur,
)

logger = logging.getLogger(__name__)


def lancer_apres_validation(tache, objet_id, en_cas_d_echec):
    """Envoie la tâche une fois la transaction validée ; si Celery/Redis est injoignable, `en_cas_d_echec()`."""
    def envoyer():
        try:
            tache.delay(objet_id)
        except Exception:
            logger.exception("Tâche %s (%s) non envoyée", tache.name, objet_id)
            en_cas_d_echec()
    transaction.on_commit(envoyer)


def _modele_whisper():
    import os
    return os.environ.get("WHISPER_MODEL") or WHISPER_PAR_DEFAUT


@shared_task(soft_time_limit=300, time_limit=330)
def transcrire_message(message_id):
    message = MessageVocal.objects.select_related("session__utilisateur").filter(
        pk=message_id, statut=MessageVocal.Statut.EN_ATTENTE, mode=MessageVocal.Mode.VOCAL).first()
    if message is None:
        return
    utilisateur = message.session.utilisateur
    debut = time.monotonic()
    try:
        _, duree_audio = lire_wav(message.audio.path)
        if duree_audio > reglage("ASSISTANT_AUDIO_MAX_S"):
            raise ErreurAssistant(f"Enregistrement trop long ({reglage('ASSISTANT_AUDIO_MAX_S')} s au maximum).",
                                  "validation")
        if duree_audio < 0.5:
            raise ErreurAssistant("Enregistrement trop court : maintenez le micro et parlez distinctement.",
                                  "validation")
        # Langue imposée par la session, sinon détectée par Whisper parmi fr / en / ar
        texte, langue, _ = transcrire_question(message.audio.path, langue=message.session.langue or None)
    except ErreurAssistant as erreur:
        _echec_transcription(message, erreur, debut)
        return
    except ValueError as erreur:  # format audio ou silence
        _echec_transcription(message, ErreurAssistant(str(erreur), "audio"), debut)
        return
    except Exception:
        logger.exception("Échec inattendu de la transcription du message %s", message_id)
        _echec_transcription(message, ErreurAssistant("La transcription a échoué. Réessayez ou écrivez votre question.",
                                                      "service"), debut)
        return
    duree_ms = int((time.monotonic() - debut) * 1000)
    MessageVocal.objects.filter(pk=message_id).update(
        texte=texte[:reglage("ASSISTANT_QUESTION_MAX_CARACTERES")], statut=MessageVocal.Statut.PRET,
        duree_audio_s=round(duree_audio, 2), duree_ms=duree_ms, modele=_modele_whisper(), message_erreur="",
        langue=langue or "",
    )
    journaliser(JournalIA.Type.TRANSCRIPTION, utilisateur, modele=_modele_whisper(), duree_ms=duree_ms)


def _echec_transcription(message, erreur, debut):
    duree_ms = int((time.monotonic() - debut) * 1000)
    MessageVocal.objects.filter(pk=message.pk).update(statut=MessageVocal.Statut.ERREUR,
                                                     message_erreur=erreur.message[:255], duree_ms=duree_ms)
    journaliser(JournalIA.Type.TRANSCRIPTION, message.session.utilisateur, erreur, _modele_whisper(), duree_ms)


@shared_task(soft_time_limit=300, time_limit=330)
def generer_illustration(illustration_id):
    illustration = Illustration.objects.select_related("session__utilisateur", "session__cours", "message").filter(
        pk=illustration_id, statut=Illustration.Statut.EN_COURS).first()
    if illustration is None:
        return
    utilisateur = illustration.session.utilisateur
    diagramme = illustration.type_illustration == Illustration.Type.DIAGRAMME
    type_appel = JournalIA.Type.DIAGRAMME if diagramme else JournalIA.Type.ILLUSTRATION
    modele = modele_gemini() if diagramme else modele_image()
    debut = time.monotonic()
    try:
        if diagramme:
            code, alternatif, _ = generer_diagramme(illustration)
            illustration.code_mermaid = code
        else:
            if not hugging_face_configure():  # vérifié avant d'appeler Gemini : pas d'appel inutile
                raise ErreurAssistant("La génération d'images n'est pas configurée (jeton Hugging Face absent).",
                                      "configuration")
            prompt, alternatif = preparer_prompt_image(illustration)
            illustration.prompt_ia = prompt
            try:
                image = generer_image(prompt)
            except RuntimeError as erreur:  # HF_TOKEN absent
                raise ErreurAssistant("La génération d'images n'est pas configurée (jeton Hugging Face absent).",
                                      "configuration") from erreur
            except Exception as erreur:
                raise traduire_erreur(erreur) from erreur
            tampon = io.BytesIO()
            image.convert("RGB").save(tampon, format="WEBP", quality=85)
            illustration.image.save(f"illustration-{illustration.pk}.webp", ContentFile(tampon.getvalue()), save=False)
    except ErreurAssistant as erreur:
        _echec_illustration(illustration, erreur, debut, type_appel, modele, utilisateur)
        return
    except Exception:
        logger.exception("Échec inattendu de l'illustration %s", illustration_id)
        _echec_illustration(illustration, ErreurAssistant("La génération a échoué. Réessayez plus tard."),
                            debut, type_appel, modele, utilisateur)
        return
    illustration.texte_alternatif = alternatif or illustration.demande[:300]
    illustration.statut = Illustration.Statut.TERMINEE
    illustration.modele = modele
    illustration.message_erreur = ""
    illustration.duree_ms = int((time.monotonic() - debut) * 1000)
    illustration.save()
    journaliser(type_appel, utilisateur, modele=modele, duree_ms=illustration.duree_ms)


def _echec_illustration(illustration, erreur, debut, type_appel, modele, utilisateur):
    duree_ms = int((time.monotonic() - debut) * 1000)
    Illustration.objects.filter(pk=illustration.pk).update(statut=Illustration.Statut.ERREUR,
                                                           message_erreur=erreur.message[:255], duree_ms=duree_ms,
                                                           modele=modele)
    journaliser(type_appel, utilisateur, erreur, modele, duree_ms)


@shared_task(soft_time_limit=120, time_limit=140)
def indexer_cours(cours_id):
    """Prépare l'index des passages d'un cours dès qu'une session y est rattachée (1re question plus rapide)."""
    from apps.courses.models import Cours

    from . import rag

    cours = Cours.objects.filter(pk=cours_id).select_related("transcription", "resume").first()
    if cours is None:
        return
    try:
        rag.indexer(cours)
    except Exception:
        logger.warning("Indexation du cours %s impossible (elle sera retentée à la 1re question)", cours_id,
                       exc_info=True)


@shared_task(soft_time_limit=180, time_limit=200)
def generer_fiche_revision(fiche_id):
    fiche = FicheRevision.objects.select_related("session__utilisateur", "session__cours").filter(
        pk=fiche_id, statut=FicheRevision.Statut.EN_COURS).first()
    if fiche is None:
        return
    utilisateur = fiche.session.utilisateur
    debut = time.monotonic()
    try:
        contenu, langue, _ = generer_fiche(fiche.session)
    except ErreurAssistant as erreur:
        _echec_fiche(fiche, erreur, debut, utilisateur)
        return
    except Exception:
        logger.exception("Échec inattendu de la fiche %s", fiche_id)
        _echec_fiche(fiche, ErreurAssistant("La génération de la fiche a échoué. Réessayez plus tard."), debut,
                     utilisateur)
        return
    duree_ms = int((time.monotonic() - debut) * 1000)
    FicheRevision.objects.filter(pk=fiche_id).update(
        statut=FicheRevision.Statut.TERMINEE, contenu=contenu, langue=langue, message_erreur="",
        modele=modele_gemini(), duree_ms=duree_ms,
    )
    journaliser(JournalIA.Type.FICHE, utilisateur, modele=modele_gemini(), duree_ms=duree_ms)


def _echec_fiche(fiche, erreur, debut, utilisateur):
    duree_ms = int((time.monotonic() - debut) * 1000)
    FicheRevision.objects.filter(pk=fiche.pk).update(statut=FicheRevision.Statut.ERREUR,
                                                     message_erreur=erreur.message[:255], duree_ms=duree_ms)
    journaliser(JournalIA.Type.FICHE, utilisateur, erreur, modele_gemini(), duree_ms)
