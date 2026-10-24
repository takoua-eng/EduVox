import pdfplumber


def extraire_texte_pdf(chemin):
    """Renvoie le texte de toutes les pages du PDF situé à `chemin`."""
    with pdfplumber.open(chemin) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages]
    texte = "\n\n".join(pages).strip()
    if not texte:
        raise ValueError("Le PDF ne contient aucun texte lisible (document scanné ?).")
    return texte
