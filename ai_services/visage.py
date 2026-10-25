"""Reconnaissance faciale EduVox (OpenCV : détection YuNet + reconnaissance SFace).

Aucune photo n'est conservée : on ne garde que l'« encodage » du visage,
une liste de 128 nombres qui le décrit.
"""

import base64
import urllib.request
from functools import lru_cache
from pathlib import Path

DOSSIER_MODELES = Path(__file__).resolve().parent / "modeles"
_BASE = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models"
MODELES = {
    "detection": ("face_detection_yunet_2023mar.onnx", f"{_BASE}/face_detection_yunet/face_detection_yunet_2023mar.onnx"),
    "reconnaissance": ("face_recognition_sface_2021dec.onnx", f"{_BASE}/face_recognition_sface/face_recognition_sface_2021dec.onnx"),
}

SEUIL_PAR_DEFAUT = 0.363      # seuil de similarité recommandé par OpenCV pour SFace
TAILLE_MIN_VISAGE = 80        # largeur minimale du visage, en pixels
NETTETE_MIN = 25              # en dessous : image trop floue
LUMINOSITE_MIN = 45           # en dessous : image trop sombre


class VisageErreur(Exception):
    """Problème sur l'image (pas de visage, flou, plusieurs personnes…). Le message est affichable."""


def telecharger_modeles():
    """Télécharge les deux modèles OpenCV s'ils ne sont pas encore présents (~39 Mo)."""
    DOSSIER_MODELES.mkdir(exist_ok=True)
    chemins = {}
    for cle, (nom, url) in MODELES.items():
        chemin = DOSSIER_MODELES / nom
        if not chemin.exists() or chemin.stat().st_size < 10_000:
            urllib.request.urlretrieve(url, chemin)
        chemins[cle] = chemin
    return chemins


@lru_cache(maxsize=1)
def _modeles():
    import cv2

    chemins = telecharger_modeles()
    detecteur = cv2.FaceDetectorYN.create(str(chemins["detection"]), "", (320, 320), 0.8, 0.3, 5000)
    reconnaisseur = cv2.FaceRecognizerSF.create(str(chemins["reconnaissance"]), "")
    return detecteur, reconnaisseur


def decoder_image(donnees):
    """Accepte une image en octets ou en « data URL » (webcam) et renvoie une image OpenCV."""
    import cv2
    import numpy as np

    try:
        if isinstance(donnees, str):
            donnees = base64.b64decode(donnees.split(",", 1)[-1])
        image = cv2.imdecode(np.frombuffer(donnees, np.uint8), cv2.IMREAD_COLOR) if donnees else None
    except (ValueError, cv2.error):
        image = None
    if image is None:
        raise VisageErreur("Aucune image reçue de la caméra. Vérifiez que la caméra est allumée et autorisée.")
    hauteur, largeur = image.shape[:2]
    if largeur > 800:
        image = cv2.resize(image, (800, int(hauteur * 800 / largeur)))
    return image


def _lacet(visage):
    """Rotation gauche/droite de la tête : 0 = de face, signe = côté.

    Points de YuNet : œil droit (4,5), œil gauche (6,7), nez (8,9).
    """
    oeil_d, oeil_g, nez = visage[4], visage[6], visage[8]
    milieu = (oeil_d + oeil_g) / 2
    ecart = max(abs(oeil_g - oeil_d), 1.0)
    return float((nez - milieu) / ecart)


def analyser_visage(donnees):
    """Vérifie la qualité de l'image et renvoie {"encodage": [...], "lacet": float}."""
    import cv2

    image = decoder_image(donnees)
    detecteur, reconnaisseur = _modeles()
    hauteur, largeur = image.shape[:2]
    detecteur.setInputSize((largeur, hauteur))
    _, visages = detecteur.detect(image)

    if visages is None or len(visages) == 0:
        raise VisageErreur("Aucun visage détecté. Placez-vous face à la caméra, bien éclairé(e).")
    if len(visages) > 1:
        raise VisageErreur("Plusieurs visages détectés. Une seule personne doit être devant la caméra.")

    visage = visages[0]
    x, y, l, h = (int(v) for v in visage[:4])
    if l < TAILLE_MIN_VISAGE:
        raise VisageErreur("Visage trop petit. Rapprochez-vous de la caméra.")

    zone = cv2.cvtColor(image[max(y, 0):y + h, max(x, 0):x + l], cv2.COLOR_BGR2GRAY)
    if zone.size == 0:
        raise VisageErreur("Visage partiellement hors du cadre.")
    if zone.mean() < LUMINOSITE_MIN:
        raise VisageErreur("Image trop sombre. Allumez la lumière ou rapprochez-vous d'une fenêtre.")
    if cv2.Laplacian(zone, cv2.CV_64F).var() < NETTETE_MIN:
        raise VisageErreur("Image floue. Restez immobile pendant la capture.")

    encodage = reconnaisseur.feature(reconnaisseur.alignCrop(image, visage))
    return {"encodage": encodage.flatten().astype(float).tolist(), "lacet": _lacet(visage)}


def similarite(encodage_a, encodage_b):
    """Similarité cosinus entre deux encodages (1 = identique, ~0 = personnes différentes)."""
    import numpy as np

    a, b = np.asarray(encodage_a, dtype=np.float32), np.asarray(encodage_b, dtype=np.float32)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))