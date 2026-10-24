from .gemini import appeler_gemini

CONSIGNES_PAR_TYPE = {
    "court": "Rédige un résumé court : un seul paragraphe de 5 phrases au maximum.",
    "detaille": (
        "Rédige un résumé détaillé et structuré :\n"
        "- commence par une phrase qui présente le sujet du cours ;\n"
        "- liste ensuite les notions essentielles sous forme de points ;\n"
        "- termine par les points clés à retenir."
    ),
    "points_cles": "Liste uniquement les points clés du cours, sous forme de puces courtes (10 au maximum).",
    "fiche": (
        "Rédige une fiche de révision : définitions importantes, formules ou règles à connaître, "
        "exemples types, puis 3 questions d'auto-évaluation avec leurs réponses."
    ),
}

PROMPT_RESUME = """Tu es un assistant pédagogique. Tu rédiges en français, pour des étudiants, à partir du cours ci-dessous.
{consigne_type}
N'invente aucune information absente du cours.{consigne_enseignant}

Cours :
\"\"\"
{texte}
\"\"\""""


def resumer_cours(texte, type_resume="detaille", consigne=""):
    """Construit le prompt de résumé (type + consigne libre de l'enseignant) puis appelle Gemini."""
    consigne_enseignant = f"\nConsigne supplémentaire de l'enseignant : {consigne.strip()}" if consigne.strip() else ""
    prompt = PROMPT_RESUME.format(
        consigne_type=CONSIGNES_PAR_TYPE.get(type_resume, CONSIGNES_PAR_TYPE["detaille"]),
        consigne_enseignant=consigne_enseignant,
        texte=texte,
    )
    return appeler_gemini(prompt)
