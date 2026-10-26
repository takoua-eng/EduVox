"""Formulaires du module assistant.

Les règles de validation sont les mêmes côté serveur (ici) et côté navigateur (attributs HTML
required / minlength / maxlength / pattern + messages de _validation.html) : le serveur reste la référence.
"""

import re

from django import forms

from .models import Illustration, SessionAssistant
from .permissions import cours_utilisables
from .reglages import reglage

TITRE_MIN, TITRE_MAX = 3, 150
DEMANDE_MIN, DEMANDE_MAX = 3, 500
QUESTION_MIN = 2
_LETTRE = re.compile(r"[^\W\d_]")  # au moins une lettre (accents compris)


def nettoyer_espaces(texte):
    """Supprime les espaces en trop (début, fin, répétitions) sans toucher aux retours à la ligne."""
    lignes = [re.sub(r"[ \t ]+", " ", ligne).strip() for ligne in (texte or "").splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lignes)).strip()


def verifier_lettre(valeur, quoi):
    if not _LETTRE.search(valeur):
        raise forms.ValidationError(f"{quoi} doit contenir au moins une lettre.", code="sans_lettre")


class SessionForm(forms.ModelForm):
    class Meta:
        model = SessionAssistant
        fields = ["titre", "cours", "mode_reponse"]
        labels = {"titre": "Titre de la session", "cours": "Cours (facultatif)", "mode_reponse": "Réponse de l'assistant"}
        help_texts = {"cours": "L'assistant s'appuiera sur le résumé de ce cours pour répondre."}
        widgets = {
            "titre": forms.TextInput(attrs={"placeholder": "Ex. : Révisions sur les matrices", "autocomplete": "off",
                                            "minlength": TITRE_MIN, "maxlength": TITRE_MAX}),
            "mode_reponse": forms.RadioSelect,
        }
        error_messages = {"titre": {"required": "Donnez un titre à la session.",
                                    "max_length": f"Le titre ne doit pas dépasser {TITRE_MAX} caractères."}}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["cours"].queryset = cours_utilisables(user).order_by("titre")
        self.fields["cours"].empty_label = "Aucun cours (question générale)"
        self.fields["cours"].error_messages["invalid_choice"] = "Ce cours n'est pas disponible."
        self.fields["mode_reponse"].error_messages["invalid_choice"] = "Choisissez « Écrit » ou « Parole »."
        self.fields["mode_reponse"].required = False  # absent : écrit (valeur par défaut)
        self.fields["titre"].widget.attrs.update({"class": "form-control", "required": True,
                                                  "aria-describedby": "titre-aide id_titre_erreur"})
        self.fields["cours"].widget.attrs["class"] = "form-select"

    def clean_mode_reponse(self):
        return self.cleaned_data.get("mode_reponse") or SessionAssistant.ModeReponse.TEXTE

    def clean_titre(self):
        titre = nettoyer_espaces(self.cleaned_data["titre"]).replace("\n", " ")
        if len(titre) < TITRE_MIN:
            raise forms.ValidationError(f"Le titre doit contenir au moins {TITRE_MIN} caractères.", code="trop_court")
        verifier_lettre(titre, "Le titre")
        # Comparaison en Python : insensible à la casse ET aux accents majuscules (« RÉVISIONS » = « Révisions »),
        # quelle que soit la base (iexact de SQLite ne gère que l'ASCII)
        autres = SessionAssistant.objects.filter(utilisateur=self.user).exclude(pk=self.instance.pk)
        if self.user is not None and titre.casefold() in {t.casefold() for t in autres.values_list("titre", flat=True)}:
            raise forms.ValidationError("Vous avez déjà une session portant ce titre.", code="doublon")
        return titre


class QuestionForm(forms.Form):
    texte = forms.CharField(label="Votre question", widget=forms.Textarea(attrs={"rows": 2}),
                            error_messages={"required": "Écrivez une question."})

    def clean_texte(self):
        return valider_question(self.cleaned_data["texte"])


def valider_question(texte):
    """Règles d'une question (formulaire HTTP et WebSocket). Renvoie le texte nettoyé ou lève ValidationError."""
    texte = nettoyer_espaces(texte)
    maximum = reglage("ASSISTANT_QUESTION_MAX_CARACTERES")
    if not texte:
        raise forms.ValidationError("Écrivez ou dictez une question.", code="vide")
    if len(texte) < QUESTION_MIN:
        raise forms.ValidationError("La question est trop courte.", code="trop_court")
    if len(texte) > maximum:
        raise forms.ValidationError(f"La question est trop longue ({len(texte)} caractères, {maximum} au maximum).",
                                    code="trop_long")
    verifier_lettre(texte, "La question")
    return texte


class IllustrationForm(forms.Form):
    type_illustration = forms.ChoiceField(label="Type", choices=Illustration.Type.choices,
                                          initial=Illustration.Type.DIAGRAMME,
                                          error_messages={"invalid_choice": "Type d'illustration inconnu."})
    demande = forms.CharField(label="Que voulez-vous visualiser ?", max_length=DEMANDE_MAX,
                              error_messages={"required": "Décrivez ce que vous voulez visualiser.",
                                              "max_length": f"{DEMANDE_MAX} caractères au maximum."})
    message = forms.IntegerField(required=False, min_value=1, widget=forms.HiddenInput)

    def clean_demande(self):
        demande = nettoyer_espaces(self.cleaned_data["demande"]).replace("\n", " ")
        if len(demande) < DEMANDE_MIN:
            raise forms.ValidationError(f"La description doit contenir au moins {DEMANDE_MIN} caractères.",
                                        code="trop_court")
        verifier_lettre(demande, "La description")
        return demande

    def premiere_erreur(self):
        for erreurs in self.errors.values():
            return erreurs[0]
        return "Demande invalide."


class ModeReponseForm(forms.Form):
    mode_reponse = forms.ChoiceField(choices=SessionAssistant.ModeReponse.choices)
