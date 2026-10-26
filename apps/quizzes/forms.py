from decimal import Decimal

from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory

from .models import CHOIX_MAX_QCM, CHOIX_MIN_QCM, Choix, Question, Quiz
from .permissions import cours_utilisables
from .services.generation import SOURCES, TYPES, sources_disponibles

NB_QUESTIONS_IA_MAX = 15


class AccessibleMixin:
    """Classes Bootstrap sur les champs, et champ relié à son aide et à ses erreurs (lecteurs d'écran)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for nom, champ in self.fields.items():
            widget = champ.widget
            if isinstance(widget, (forms.CheckboxInput, forms.RadioSelect, forms.CheckboxSelectMultiple)):
                classe = "form-check-input"
            elif isinstance(widget, forms.Select):
                classe = "form-select"
            else:
                classe = "form-control"
            widget.attrs["class"] = f"{widget.attrs.get('class', '')} {classe}".strip()

    def full_clean(self):
        super().full_clean()
        for nom in self.errors:
            if nom not in self.fields:
                continue
            champ = self[nom]
            references = [f"{champ.auto_id}_erreurs"]
            if self.fields[nom].help_text:
                references.insert(0, f"{champ.auto_id}_helptext")
            attrs = self.fields[nom].widget.attrs
            attrs.update({"aria-describedby": " ".join(references), "aria-invalid": "true"})
            attrs["class"] = f"{attrs.get('class', '')} is-invalid"


class QuizForm(AccessibleMixin, forms.ModelForm):
    class Meta:
        model = Quiz
        fields = ["titre", "description", "cours", "difficulte", "tentatives_max", "afficher_corrige"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cours"].queryset = cours_utilisables(user)
        self.fields["cours"].required = True
        self.fields["cours"].empty_label = "— Choisir un de vos cours —"


class GenerationQuizForm(AccessibleMixin, forms.Form):
    cours = forms.ModelChoiceField(queryset=None, empty_label="— Choisir un de vos cours —")
    source = forms.ChoiceField(choices=SOURCES, label="Texte utilisé", initial=SOURCES[0][0])
    titre = forms.CharField(max_length=200, required=False, label="Titre du quiz",
                            help_text="Facultatif : « Quiz – titre du cours » par défaut.")
    nb_questions = forms.IntegerField(min_value=1, max_value=NB_QUESTIONS_IA_MAX, initial=5,
                                      label="Nombre de questions")
    types = forms.MultipleChoiceField(
        choices=[(t, Question.Type(t).label) for t in TYPES], initial=list(TYPES),
        widget=forms.CheckboxSelectMultiple, label="Types de questions",
    )
    difficulte = forms.ChoiceField(choices=Quiz.Difficulte.choices, initial=Quiz.Difficulte.MOYEN, label="Difficulté")

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cours"].queryset = cours_utilisables(user)

    def clean(self):
        donnees = super().clean()
        cours, source = donnees.get("cours"), donnees.get("source")
        if cours and source and source not in sources_disponibles(cours):
            libelle = dict(SOURCES)[source].lower()
            self.add_error("source", f"Ce texte n'est pas disponible pour ce cours ({libelle} absent). "
                                     "Générez d'abord le résumé ou la transcription depuis la page du cours.")
        return donnees


class QuestionForm(AccessibleMixin, forms.ModelForm):
    bonne_reponse_vf = forms.ChoiceField(
        choices=[("vrai", "Vrai"), ("faux", "Faux")], required=False, widget=forms.RadioSelect,
        label="Bonne réponse",
    )

    class Meta:
        model = Question
        fields = ["type_question", "enonce", "points", "explication", "reponse_attendue"]
        widgets = {
            "enonce": forms.Textarea(attrs={"rows": 3}),
            "explication": forms.Textarea(attrs={"rows": 2}),
            "reponse_attendue": forms.Textarea(attrs={"rows": 3}),
            "points": forms.NumberInput(attrs={"step": "0.25"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = self.instance
        if instance.pk and instance.type_question == Question.Type.VRAI_FAUX:
            correct = instance.choix.filter(est_correct=True).first()
            if correct:
                self.initial["bonne_reponse_vf"] = correct.texte.lower()

    def clean(self):
        donnees = super().clean()
        if donnees.get("type_question") == Question.Type.VRAI_FAUX and not donnees.get("bonne_reponse_vf"):
            self.add_error("bonne_reponse_vf", "Indiquez si l'affirmation est vraie ou fausse.")
        return donnees


class ChoixForm(AccessibleMixin, forms.ModelForm):
    class Meta:
        model = Choix
        fields = ["texte", "est_correct"]


class BaseChoixFormSet(BaseInlineFormSet):
    """Choix d'un QCM : entre 2 et 6, une seule bonne réponse, pas de doublon."""

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        gardes = [f for f in self.forms if f.cleaned_data and not f.cleaned_data.get("DELETE")
                  and (f.cleaned_data.get("texte") or "").strip()]
        if not CHOIX_MIN_QCM <= len(gardes) <= CHOIX_MAX_QCM:
            raise forms.ValidationError(f"Un QCM doit avoir entre {CHOIX_MIN_QCM} et {CHOIX_MAX_QCM} choix remplis.")
        if sum(1 for f in gardes if f.cleaned_data.get("est_correct")) != 1:
            raise forms.ValidationError("Cochez exactement une bonne réponse.")
        textes = [f.cleaned_data["texte"].strip().lower() for f in gardes]
        if len(set(textes)) != len(textes):
            raise forms.ValidationError("Deux choix sont identiques.")


def choix_formset(nb_existants=0):
    return inlineformset_factory(
        Question, Choix, form=ChoixForm, formset=BaseChoixFormSet, fields=["texte", "est_correct"],
        extra=max(CHOIX_MIN_QCM, 4 - nb_existants) if nb_existants < CHOIX_MAX_QCM else 0,
        max_num=CHOIX_MAX_QCM, validate_max=True, can_delete=True,
    )


class RevisionForm(AccessibleMixin, forms.Form):
    """Révision par l'enseignant de la note d'une réponse ouverte."""

    points = forms.DecimalField(min_value=Decimal("0"), max_digits=5, decimal_places=2, label="Note")
    commentaire = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False, max_length=2000,
                                  label="Commentaire pour l'étudiant")

    def __init__(self, *args, maximum, **kwargs):
        super().__init__(*args, **kwargs)
        self.maximum = maximum
        self.fields["points"].widget.attrs.update({"max": maximum, "step": "0.25"})

    def clean_points(self):
        points = self.cleaned_data["points"]
        if points > self.maximum:
            raise forms.ValidationError(f"La note ne peut pas dépasser {self.maximum}.")
        return points
