"""Services IA partagés entre les modules EduVox (une fonction par service)."""

from .gemini import appeler_gemini
from .pdf import extraire_texte_pdf
from .resume import resumer_cours
from .traduction import traduire_texte
from .illustration import generer_image
from .transcription import duree_audio, lire_wav, transcrire, transcrire_question, transcrire_wav
from .visage import VisageErreur, analyser_visage, similarite


__all__ = ["appeler_gemini", "duree_audio", "generer_image", "lire_wav", "transcrire_question", "transcrire_wav", "extraire_texte_pdf", "resumer_cours", "traduire_texte", "transcrire",  "VisageErreur", "analyser_visage", "similarite"]
