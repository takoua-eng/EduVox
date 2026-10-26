"""Stockage privé des livrables : aucun fichier n'est accessible par une URL publique.

Les fichiers sont rangés hors de MEDIA_ROOT sous un nom aléatoire (UUID) ; le nom d'origine, nettoyé,
n'est conservé qu'en base pour l'affichage. Le seul accès passe par la vue de téléchargement, qui vérifie
les droits sur la version demandée.
"""

import os
import uuid

from django.core.exceptions import ValidationError
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible
from django.utils.text import get_valid_filename

from .services.reglages import dossier_stockage_prive, reglage

# Extension → signature attendue au début du fichier (None : texte UTF-8 vérifié)
FORMATS = {
    "pdf": [b"%PDF-"],
    "txt": None,
    "md": None,
    "docx": [b"PK\x03\x04"],
    "pptx": [b"PK\x03\x04"],
    "xlsx": [b"PK\x03\x04"],
    "odt": [b"PK\x03\x04"],
    "png": [b"\x89PNG\r\n\x1a\n"],
    "jpg": [b"\xff\xd8\xff"],
    "jpeg": [b"\xff\xd8\xff"],
}
# Formats dont le texte peut être analysé par l'IA (les autres sont seulement déposés)
FORMATS_ANALYSABLES = {"pdf", "txt", "md"}


@deconstructible
class StockagePrive(FileSystemStorage):
    """FileSystemStorage sans base_url : `url()` est refusé, le fichier n'a pas d'adresse publique."""

    def __init__(self):
        super().__init__(location=None, base_url=None)

    @property
    def base_location(self):
        return str(dossier_stockage_prive())

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    def url(self, name):
        raise ValueError("Les livrables EduSprint ne sont accessibles que par la vue de téléchargement protégée.")


stockage_prive = StockagePrive()


def chemin_livrable(instance, nom_original):
    extension = extension_de(nom_original)
    equipe = instance.livrable.equipe
    return f"livrables/defi_{equipe.defi_id}/equipe_{equipe.pk}/{uuid.uuid4().hex}.{extension}"


def extension_de(nom):
    return os.path.splitext(nom or "")[1].lower().lstrip(".")


def nom_affichable(nom):
    """Nom d'origine nettoyé (sans chemin ni caractère dangereux), utilisé pour l'affichage et le téléchargement."""
    base = os.path.basename((nom or "").replace("\\", "/"))
    return (get_valid_filename(base) or "fichier")[:200]


def valider_fichier(fichier):
    """Vérifie l'extension, la taille et la signature réelle du fichier envoyé."""
    extension = extension_de(fichier.name)
    if extension not in FORMATS:
        raise ValidationError(
            "Format non accepté. Formats autorisés : %(formats)s.",
            params={"formats": ", ".join(sorted(FORMATS))}, code="format",
        )
    maximum = reglage("EDUSPRINT_FICHIER_MAX_MO")
    if fichier.size > maximum * 1024 * 1024:
        raise ValidationError("Le fichier dépasse %(max)s Mo.", params={"max": maximum}, code="taille")
    if fichier.size == 0:
        raise ValidationError("Le fichier est vide.", code="vide")

    position = fichier.tell() if hasattr(fichier, "tell") else 0
    fichier.seek(0)
    debut = fichier.read(2048)
    fichier.seek(position)
    signatures = FORMATS[extension]
    if signatures is None:
        try:
            debut.decode("utf-8")
        except UnicodeDecodeError as erreur:
            # Un caractère multi-octets peut être coupé en fin de bloc : on tolère seulement ce cas
            if erreur.start < len(debut) - 4:
                raise ValidationError("Le fichier texte doit être encodé en UTF-8.", code="encodage")
        if b"\x00" in debut:
            raise ValidationError("Ce fichier n'est pas un fichier texte.", code="binaire")
    elif not any(debut.startswith(signature) for signature in signatures):
        raise ValidationError("Le contenu du fichier ne correspond pas à son extension.", code="signature")
