"""Balises EduSprint : badges de statut (icône + TEXTE, jamais la couleur seule) et nom affichable."""

from django import template
from django.utils.html import format_html

register = template.Library()

# valeur → (variante de couleur, icône Remix)
STYLES = {
    # défi
    "brouillon": ("neutre", "ri-draft-line"), "inscriptions": ("info", "ri-user-add-line"),
    "en_cours": ("actif", "ri-play-circle-line"), "termine": ("fin", "ri-flag-2-line"),
    "archive": ("neutre", "ri-archive-line"),
    # participation / équipe
    "en_attente": ("attente", "ri-time-line"), "acceptee": ("succes", "ri-check-line"),
    "refusee": ("erreur", "ri-close-line"), "retiree": ("neutre", "ri-arrow-go-back-line"),
    "active": ("succes", "ri-team-line"), "ecartee": ("attente", "ri-user-unfollow-line"),
    "dissoute": ("neutre", "ri-forbid-line"),
    # tâches
    "a_faire": ("neutre", "ri-checkbox-blank-circle-line"), "a_verifier": ("attente", "ri-eye-line"),
    # livrables / réponses / imprévus
    "soumis": ("info", "ri-send-plane-line"), "soumise": ("succes", "ri-send-plane-line"),
    "a_completer": ("attente", "ri-error-warning-line"), "accepte": ("succes", "ri-checkbox-circle-line"),
    "valide": ("info", "ri-shield-check-line"), "publie": ("succes", "ri-megaphone-line"),
    # traitements IA
    "echoue": ("erreur", "ri-error-warning-line"),
    # couverture des exigences
    "couverte": ("succes", "ri-checkbox-circle-line"), "partielle": ("attente", "ri-contrast-line"),
    "non_couverte": ("erreur", "ri-close-circle-line"), "non_verifiable": ("neutre", "ri-question-line"),
    # priorités
    "haute": ("erreur", "ri-arrow-up-double-line"), "normale": ("neutre", "ri-subtract-line"),
    "basse": ("info", "ri-arrow-down-line"),
}


@register.simple_tag
def badge(valeur, libelle, extra=""):
    variante, icone = STYLES.get(valeur, ("neutre", "ri-information-line"))
    return format_html('<span class="es-badge es-badge--{} {}"><i class="icon-base ri {}" aria-hidden="true"></i>{}</span>',
                       variante, extra, icone, libelle)


@register.filter
def nom_utilisateur(user):
    if user is None:
        return "Compte supprimé"
    return user.get_full_name() or user.username


@register.filter
def initiales(user):
    if user is None:
        return "?"
    nom = user.get_full_name() or user.username
    morceaux = [m for m in nom.split() if m]
    return "".join(m[0] for m in morceaux[:2]).upper() or "?"


@register.filter
def pourcentage(valeur, total):
    try:
        return round(100 * int(valeur) / int(total)) if int(total) else 0
    except (TypeError, ValueError):
        return 0
