from django import template
from django.utils.safestring import mark_safe

from ..services import rendre_markdown

register = template.Library()


@register.filter
def markdown(texte):
    """Rendu Markdown sûr (HTML brut désactivé) des réponses de l'assistant."""
    return mark_safe(rendre_markdown(texte))
