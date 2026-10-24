from .gemini import appeler_gemini

LANGUES = {"fr": "français", "en": "anglais", "ar": "arabe"}

PROMPT_TRADUCTION = """Traduis le texte ci-dessous en {langue}.
Garde exactement la même structure (titres, listes, paragraphes). Ne traduis pas les formules.
Réponds uniquement avec la traduction, sans commentaire.

Texte :
\"\"\"
{texte}
\"\"\""""


def traduire_texte(texte, langue):
    """Traduit `texte` dans la langue donnée par son code ('fr', 'en' ou 'ar') avec Gemini."""
    if langue not in LANGUES:
        raise ValueError(f"Langue non prise en charge : {langue}")
    return appeler_gemini(PROMPT_TRADUCTION.format(langue=LANGUES[langue], texte=texte))
