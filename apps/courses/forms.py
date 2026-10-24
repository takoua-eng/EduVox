from django import forms
from django.conf import settings
from django.db.models import Q

from .models import (
    EXTENSIONS_AUDIO,
    EXTENSIONS_PDF,
    Avis,
    Cours,
    Matiere,
    NotePersonnelle,
    Resume,
    Signalement,
    TraductionResume,
)
from .permissions import cours_publics

EXTENSIONS_ACCEPTEES = EXTENSIONS_PDF + EXTENSIONS_AUDIO


class CoursForm(forms.ModelForm):
    class Meta:
        model = Cours
        fields = [
            "titre", "description", "fichier",
            "matiere", "niveau", "mots_cles", "prerequis",
            "publication", "date_publication",
        ]
        labels = {
            "fichier": "Fichier du cours (PDF ou audio)",
            "matiere": "Matière",
            "mots_cles": "Mots-clés",
            "date_publication": "Date de publication",
        }
        widgets = {
            "date_publication": forms.DateTimeInput(
                attrs={"type": "datetime-local", "class": "form-control"}, format="%Y-%m-%dT%H:%M"
            ),
            "prerequis": forms.SelectMultiple(attrs={"class": "form-select", "size": 6}),
        }

    def __init__(self, *args, enseignant=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Prérequis possibles : cours visibles des étudiants + ses propres cours (sauf celui-ci)
        prerequis = Cours.objects.filter(Q(pk__in=cours_publics()) | Q(enseignant=enseignant))
        if self.instance.pk:
            prerequis = prerequis.exclude(pk=self.instance.pk)
        self.fields["prerequis"].queryset = prerequis.order_by("titre")
        self.fields["prerequis"].required = False

    def clean_fichier(self):
        fichier = self.cleaned_data["fichier"]
        if "fichier" not in self.changed_data:
            return fichier

        if Cours.type_depuis_nom(fichier.name) is None:
            raise forms.ValidationError(
                "Format non accepté. Formats autorisés : " + ", ".join(EXTENSIONS_ACCEPTEES)
            )
        taille_max = settings.COURS_TAILLE_MAX_MO * 1024 * 1024
        if fichier.size > taille_max:
            raise forms.ValidationError(
                f"Le fichier dépasse la taille maximale de {settings.COURS_TAILLE_MAX_MO} Mo."
            )
        return fichier

    def clean_mots_cles(self):
        # Normalise : « a , b,,c » → « a, b, c »
        mots = [mot.strip() for mot in self.cleaned_data["mots_cles"].split(",") if mot.strip()]
        return ", ".join(mots)

    def clean(self):
        donnees = super().clean()
        if donnees.get("publication") == Cours.Publication.PROGRAMME and not donnees.get("date_publication"):
            self.add_error("date_publication", "Indiquez la date à laquelle le cours sera publié.")
        return donnees

    def save(self, commit=True):
        cours = super().save(commit=False)
        if "fichier" in self.changed_data:
            # Nouveau fichier : on repart de zéro pour le traitement IA
            cours.type_fichier = Cours.type_depuis_nom(cours.fichier.name)
            cours.statut = Cours.Statut.EN_ATTENTE
            cours.progression = 0
            cours.message_erreur = ""
            cours.duree_audio = None
            cours.nb_mots = None
        if commit:
            cours.save()
            self.save_m2m()
        return cours


class GenerationResumeForm(forms.Form):
    type_resume = forms.ChoiceField(choices=Resume.Type.choices, initial=Resume.Type.DETAILLE, label="Type de résumé")
    consigne = forms.CharField(
        max_length=500, required=False, label="Consigne (facultatif)",
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Ex. : insiste sur les formules"}),
    )


class TraductionForm(forms.Form):
    langue = forms.ChoiceField(choices=TraductionResume.Langue.choices)


class ReponseAvisForm(forms.Form):
    reponse = forms.CharField(max_length=2000, widget=forms.Textarea(attrs={"rows": 2}), label="Votre réponse")


# ---------- Étudiant ----------

class AvisForm(forms.ModelForm):
    class Meta:
        model = Avis
        fields = ["note", "commentaire"]
        widgets = {"commentaire": forms.Textarea(attrs={"rows": 3, "maxlength": 2000})}

    note = forms.TypedChoiceField(choices=[(i, i) for i in range(1, 6)], coerce=int, label="Note")


class NotePersonnelleForm(forms.ModelForm):
    class Meta:
        model = NotePersonnelle
        fields = ["texte"]


class SignalementForm(forms.ModelForm):
    class Meta:
        model = Signalement
        fields = ["motif", "message"]
        widgets = {"message": forms.Textarea(attrs={"rows": 3, "maxlength": 2000})}


# ---------- Administrateur ----------

class MatiereForm(forms.ModelForm):
    class Meta:
        model = Matiere
        fields = ["nom"]
