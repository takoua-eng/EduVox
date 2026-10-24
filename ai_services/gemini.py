import os

MODELE_PAR_DEFAUT = "gemini-3.8-flash"


def appeler_gemini(prompt):
    """Envoie `prompt` à Gemini et renvoie la réponse texte. Clé lue dans GEMINI_API_KEY."""
    from google import genai

    cle = os.environ.get("GEMINI_API_KEY")
    if not cle:
        raise RuntimeError("La variable d'environnement GEMINI_API_KEY n'est pas définie.")

    client = genai.Client(api_key=cle)
    reponse = client.models.generate_content(
        model=os.environ.get("GEMINI_MODEL") or MODELE_PAR_DEFAUT,
        contents=prompt,
    )
    if not reponse.text:
        raise RuntimeError("Gemini a renvoyé une réponse vide.")
    return reponse.text.strip()
