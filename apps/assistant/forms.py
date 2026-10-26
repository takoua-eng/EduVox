from django import forms

from .models import Illustration, SessionAssistant
from .permissions import cours_utilisables
from .reglages import reglage


class SessionForm(forms.ModelForm):
    class Meta:
        model = SessionAssistant
        fields = ["titre", "cours"]
        labels = {"titre": "Titre de la session", "cours": "Cours (facultatif)"}
        help_texts = {"cours": "L'assistant s'appuiera sur le résumé de ce cours pour répondre."}
        widgets = {"titre": forms.TextInput(attrs={"placeholder": "Ex. : Révisions sur les matrices",
                                                   "autocomplete": "off"})}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cours"].queryset = cours_utilisables(user).order_by("titre")
        self.fields["cours"].empty_label = "Aucun cours (question générale)"
        for champ in self.fields.values():
            champ.widget.attrs.setdefault("class", "form-select" if isinstance(champ.widget, forms.Select)
                                          else "form-control")

    def clean_titre(self):
        titre = self.cleaned_data["titre"].strip()
        if len(titre) < 3:
            raise forms.ValidationError("Le titre doit contenir au moins 3 caractères.")
        return titre


class QuestionForm(forms.Form):
    texte = forms.CharField(label="Votre question", widget=forms.Textarea(attrs={"rows": 2}))

    def clean_texte(self):
        texte = self.cleaned_data["texte"].strip()
        maximum = reglage("ASSISTANT_QUESTION_MAX_CARACTERES")
        if len(texte) > maximum:
            raise forms.ValidationError(f"{maximum} caractères au maximum.")
        return texte


class IllustrationForm(forms.Form):
    type_illustration = forms.ChoiceField(label="Type", choices=Illustration.Type.choices,
                                          initial=Illustration.Type.DIAGRAMME)
    demande = forms.CharField(label="Que voulez-vous visualiser ?", min_length=3, max_length=500)
    message = forms.IntegerField(required=False, widget=forms.HiddenInput)
