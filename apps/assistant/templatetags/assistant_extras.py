import re

from django import template
from django.utils.safestring import mark_safe

from ..rag import citations
from ..services import rendre_markdown

register = template.Library()

_GROUPE_CITATION = re.compile(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]")


@register.filter
def markdown(texte):
    """Rendu Markdown sûr (HTML brut désactivé) des réponses de l'assistant."""
    return mark_safe(rendre_markdown(texte))


@register.filter
def reponse_html(message):
    """Réponse en Markdown sûr, où les citations [1] [2][3] deviennent des liens vers les passages du cours."""
    html = rendre_markdown(message.texte)
    valides = {s["n"] for s in message.sources or []}
    if not valides:
        return mark_safe(html)

    def lien(correspondance):
        numeros = [int(n) for n in re.findall(r"\d+", correspondance.group(1))]
        if not all(n in valides for n in numeros):
            return correspondance.group(0)
        liens = "".join(f'<a class="ast-cite" href="#src-{message.pk}-{n}" aria-label="Source {n}">{n}</a>'
                        for n in numeros)
        return f'<sup class="ast-cites">{liens}</sup>'

    return mark_safe(_GROUPE_CITATION.sub(lien, html))


@register.filter
def sources_citees(message):
    """Passages du cours réellement cités dans la réponse, dans l'ordre des numéros."""
    cites = set(citations(message.texte, message.sources or []))
    return [s for s in message.sources or [] if s["n"] in cites]
