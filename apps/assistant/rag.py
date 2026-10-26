"""Réponses appuyées sur le cours (RAG : Retrieval-Augmented Generation).

1. Indexation : le texte du cours (transcription complète, sinon résumé) est découpé en passages d'environ
   900 caractères qui se chevauchent un peu ; chaque passage est transformé en vecteur par le modèle
   d'embedding de Gemini et enregistré (PassageCours). L'index est refait si le texte du cours change.
2. Recherche : la question (avec la précédente, pour les relances du type « et pourquoi ? ») est vectorisée,
   puis comparée aux passages par similarité cosinus ; les 4 plus proches sont donnés à Gemini, numérotés,
   avec la consigne de les citer [1], [2]… L'étudiant voit sous la réponse les extraits cités.
"""

import hashlib
import logging
import os
import re
import time

from django.db import IntegrityError, transaction

from .models import JournalIA, PassageCours

logger = logging.getLogger(__name__)

TAILLE_PASSAGE = 900
CHEVAUCHEMENT = 150
NB_PASSAGES = 4
DIMENSION = 768
LOT = 100  # textes par appel d'embedding


def modele_embedding():
    return os.environ.get("ASSISTANT_EMBEDDING_MODEL") or "gemini-embedding-001"


def texte_du_cours(cours):
    """Texte le plus complet disponible : la transcription (PDF / audio), sinon le résumé."""
    for relation in ("transcription", "resume"):
        objet = getattr(cours, relation, None)  # RelatedObjectDoesNotExist hérite d'AttributeError
        if objet is not None and (objet.texte or "").strip():
            return objet.texte.strip()
    return ""


def decouper(texte, taille=TAILLE_PASSAGE, chevauchement=CHEVAUCHEMENT):
    """Découpe en passages d'au plus `taille` caractères, sans couper de phrase.

    Chaque passage reprend la ou les dernières phrases du précédent (au plus `chevauchement` caractères)
    pour qu'une idée à cheval sur deux passages reste retrouvable.
    """
    phrases = []
    for paragraphe in re.split(r"\n\s*\n", texte):
        paragraphe = re.sub(r"\s+", " ", paragraphe).strip()
        for phrase in re.split(r"(?<=[.!?؟])\s+", paragraphe):
            while len(phrase) > taille:  # phrase interminable : coupe sur un espace
                coupe = phrase.rfind(" ", taille // 2, taille)
                coupe = coupe if coupe > 0 else taille
                phrases.append(phrase[:coupe].strip())
                phrase = phrase[coupe:].strip()
            if phrase:
                phrases.append(phrase)

    longueur = lambda morceaux: sum(len(m) + 1 for m in morceaux)  # noqa: E731
    passages, courant = [], []
    for phrase in phrases:
        if courant and longueur(courant) + len(phrase) > taille:
            passages.append(" ".join(courant))
            reprise = []
            for precedente in reversed(courant):
                if longueur(reprise) + len(precedente) > chevauchement:
                    break
                reprise.insert(0, precedente)
            courant = reprise
        courant.append(phrase)
    if courant:
        passages.append(" ".join(courant))
    return [p for p in passages if len(p) > 20]


def empreinte(texte):
    return hashlib.sha256(f"{modele_embedding()}|{DIMENSION}|{TAILLE_PASSAGE}|{texte}".encode()).hexdigest()[:32]


def vectoriser(textes, tache, titre=None):
    """Vecteurs (listes de floats) des `textes` via Gemini. `tache` : RETRIEVAL_DOCUMENT ou RETRIEVAL_QUERY."""
    from google.genai import types

    from .services import _client

    client = _client()
    vecteurs = []
    for i in range(0, len(textes), LOT):
        config = types.EmbedContentConfig(task_type=tache, output_dimensionality=DIMENSION,
                                          **({"title": titre} if titre and tache == "RETRIEVAL_DOCUMENT" else {}))
        resultat = client.models.embed_content(model=modele_embedding(), contents=textes[i:i + LOT], config=config)
        vecteurs.extend([round(v, 6) for v in e.values] for e in resultat.embeddings)
    return vecteurs


def indexer(cours, utilisateur=None):
    """Passages indexés du cours (créés ou recréés si le texte a changé). Liste vide si le cours n'a pas de texte."""
    texte = texte_du_cours(cours)
    if not texte:
        return []
    cle = empreinte(texte)
    passages = list(PassageCours.objects.filter(cours=cours))
    if passages and all(p.empreinte == cle for p in passages):
        return passages

    morceaux = decouper(texte)
    debut = time.monotonic()
    try:
        vecteurs = vectoriser(morceaux, "RETRIEVAL_DOCUMENT", titre=cours.titre)
    except Exception as erreur:
        from .services import journaliser, traduire_erreur
        journaliser(JournalIA.Type.INDEXATION, utilisateur, traduire_erreur(erreur), modele_embedding(),
                    int((time.monotonic() - debut) * 1000))
        raise
    from .services import journaliser
    journaliser(JournalIA.Type.INDEXATION, utilisateur, modele=modele_embedding(),
                duree_ms=int((time.monotonic() - debut) * 1000))
    try:
        with transaction.atomic():
            PassageCours.objects.filter(cours=cours).delete()
            return PassageCours.objects.bulk_create(
                PassageCours(cours=cours, ordre=i, texte=morceau, vecteur=vecteur, empreinte=cle)
                for i, (morceau, vecteur) in enumerate(zip(morceaux, vecteurs), start=1)
            )
    except IntegrityError:  # indexation simultanée par une autre requête : on garde la sienne
        return list(PassageCours.objects.filter(cours=cours))


def rechercher(cours, requete, utilisateur=None, nb=NB_PASSAGES):
    """Les `nb` passages du cours les plus proches de `requete` : liste de (passage, score cosinus)."""
    import numpy as np

    passages = indexer(cours, utilisateur)
    if not passages:
        return []
    debut = time.monotonic()
    try:
        (vecteur,) = vectoriser([requete], "RETRIEVAL_QUERY")
    except Exception as erreur:
        from .services import journaliser, traduire_erreur
        journaliser(JournalIA.Type.RECHERCHE, utilisateur, traduire_erreur(erreur), modele_embedding(),
                    int((time.monotonic() - debut) * 1000))
        raise
    from .services import journaliser
    journaliser(JournalIA.Type.RECHERCHE, utilisateur, modele=modele_embedding(),
                duree_ms=int((time.monotonic() - debut) * 1000))

    matrice = np.array([p.vecteur for p in passages], dtype=np.float32)
    q = np.array(vecteur, dtype=np.float32)
    scores = matrice @ q / (np.linalg.norm(matrice, axis=1) * np.linalg.norm(q) + 1e-9)
    meilleurs = np.argsort(-scores)[:nb]
    return [(passages[i], float(scores[i])) for i in sorted(meilleurs, key=lambda i: passages[i].ordre)]


def citations(texte, sources):
    """Numéros de sources réellement cités dans la réponse ([1], [2][3], [1, 3]…)."""
    valides = {s["n"] for s in sources}
    cites = set()
    for groupe in re.findall(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]", texte or ""):
        cites.update(int(n) for n in re.findall(r"\d+", groupe))
    return sorted(cites & valides)
