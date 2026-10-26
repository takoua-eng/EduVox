"""Formulaires EduSprint.

- Listes blanches explicites de champs (`fields`) : statut, propriétaire, équipe, auteur, état, dates de
  soumission… ne sont jamais modifiables par un formulaire (protection contre l'attribution de masse).
- Accessibilité : chaque champ en erreur reçoit aria-invalid et aria-describedby (aide + erreurs).
"""

from django import forms
from django.core.validators import URLValidator
from django.utils import timezone

from apps.accounts.models import Utilisateur

from .models import (
    TAILLE_EQUIPE_MAX,
    CommentaireTache,
    Defi,
    Equipe,
    EvenementScenario,
    Jalon,
    Livrable,
    ReponseEvenement,
    Tache,
    VersionLivrable,
)
from .stockage import FORMATS, valider_fichier

FORMAT_DATETIME = "%Y-%m-%dT%H:%M"


class DateHeureInput(forms.DateTimeInput):
    input_type = "datetime-local"

    def __init__(self, **kwargs):
        super().__init__(format=FORMAT_DATETIME, **kwargs)


class DateInput(forms.DateInput):
    input_type = "date"

    def __init__(self, **kwargs):
        super().__init__(format="%Y-%m-%d", **kwargs)


class FormulaireAccessible:
    """Classes Bootstrap + attributs ARIA reliant chaque champ à son aide et à ses erreurs."""

    def _styliser(self):
        for nom, champ in self.fields.items():
            widget = champ.widget
            if isinstance(widget, (forms.CheckboxInput, forms.RadioSelect, forms.CheckboxSelectMultiple)):
                widget.attrs.setdefault("class", "form-check-input")
            elif isinstance(widget, forms.Select):
                widget.attrs.setdefault("class", "form-select")
            else:
                widget.attrs.setdefault("class", "form-control")
            if champ.help_text:
                widget.attrs["aria-describedby"] = f"id_{self.add_prefix(nom)}_helptext"

    def full_clean(self):
        super().full_clean()
        for nom in self.errors:
            if nom in self.fields:
                widget = self.fields[nom].widget
                widget.attrs["aria-invalid"] = "true"
                ids = [f"id_{self.add_prefix(nom)}_erreurs"]
                if self.fields[nom].help_text:
                    ids.append(f"id_{self.add_prefix(nom)}_helptext")
                widget.attrs["aria-describedby"] = " ".join(ids)
                classe = widget.attrs.get("class", "")
                if "is-invalid" not in classe:
                    widget.attrs["class"] = f"{classe} is-invalid".strip()


class Formulaire(FormulaireAccessible, forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._styliser()


class FormulaireModele(FormulaireAccessible, forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._styliser()


LIGNES = {"rows": 4}


# ---------- Défis et jalons ----------

class DefiForm(FormulaireModele):
    class Meta:
        model = Defi
        fields = ["titre", "description", "contexte", "objectifs", "competences", "livrables_attendus", "contraintes",
                  "budget", "niveau", "inscriptions_debut", "inscriptions_fin", "date_debut", "date_fin",
                  "taille_min", "taille_max", "co_enseignants"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "contexte": forms.Textarea(attrs={"rows": 5}),
            "objectifs": forms.Textarea(attrs=LIGNES), "competences": forms.Textarea(attrs=LIGNES),
            "livrables_attendus": forms.Textarea(attrs=LIGNES), "contraintes": forms.Textarea(attrs=LIGNES),
            "inscriptions_debut": DateHeureInput(), "inscriptions_fin": DateHeureInput(),
            "date_debut": DateHeureInput(), "date_fin": DateHeureInput(),
            "co_enseignants": forms.CheckboxSelectMultiple(),
            "budget": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "taille_min": forms.NumberInput(attrs={"min": 1, "max": TAILLE_EQUIPE_MAX}),
            "taille_max": forms.NumberInput(attrs={"min": 1, "max": TAILLE_EQUIPE_MAX}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        proprietaire = self.instance.pk is None or self.instance.enseignant_id == getattr(user, "pk", None)
        if proprietaire:
            self.fields["co_enseignants"].queryset = Utilisateur.objects.filter(
                role=Utilisateur.Role.ENSEIGNANT, is_active=True).exclude(pk=getattr(user, "pk", None)).order_by(
                "last_name", "username")
            self.fields["co_enseignants"].label = "Co-enseignants autorisés"
        else:
            # Seul le propriétaire accorde la permission de gérer son défi
            del self.fields["co_enseignants"]


class JalonForm(FormulaireModele):
    class Meta:
        model = Jalon
        fields = ["titre", "ordre", "echeance", "description", "exigences"]
        widgets = {"echeance": DateHeureInput(), "description": forms.Textarea(attrs={"rows": 3}),
                   "exigences": forms.Textarea(attrs=LIGNES)}

    def clean_ordre(self):
        # « defi » n'est pas un champ du formulaire : Django ne vérifierait pas l'unicité (defi, ordre)
        ordre = self.cleaned_data["ordre"]
        if Jalon.objects.filter(defi_id=self.instance.defi_id, ordre=ordre).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Un autre jalon porte déjà ce numéro d'ordre.")
        return ordre


class GenerationBriefForm(Formulaire):
    theme = forms.CharField(label="Thème de la mission", min_length=3, max_length=200,
                            help_text="Ex. : réduire la consommation d'eau d'une université.")
    niveau = forms.ChoiceField(choices=Defi.Niveau.choices, initial=Defi.Niveau.INTERMEDIAIRE)
    duree_jours = forms.IntegerField(label="Durée (jours)", min_value=1, max_value=60, initial=5)
    taille_min = forms.IntegerField(label="Taille minimale d'équipe", min_value=1, max_value=TAILLE_EQUIPE_MAX, initial=2)
    taille_max = forms.IntegerField(label="Taille maximale d'équipe", min_value=1, max_value=TAILLE_EQUIPE_MAX, initial=4)
    contraintes = forms.CharField(label="Contraintes souhaitées (facultatif)", required=False, max_length=1000,
                                  widget=forms.Textarea(attrs={"rows": 3}),
                                  help_text="Ex. : budget de 2 000 DT, livrables : proposition, maquette, budget prévisionnel.")
    date_debut = forms.DateTimeField(label="Début prévu du défi", widget=DateHeureInput(),
                                     help_text="Sert à convertir les délais relatifs des jalons en dates réelles.")

    def clean_date_debut(self):
        valeur = self.cleaned_data["date_debut"]
        if valeur <= timezone.now():
            raise forms.ValidationError("Choisissez une date future.")
        return valeur

    def clean(self):
        donnees = super().clean()
        if donnees.get("taille_min") and donnees.get("taille_max") and donnees["taille_min"] > donnees["taille_max"]:
            self.add_error("taille_max", "La taille maximale doit être supérieure ou égale à la taille minimale.")
        return donnees


class ApercuJalonForm(Formulaire):
    titre = forms.CharField(max_length=200)
    echeance = forms.DateTimeField(label="Échéance", widget=DateHeureInput())
    description = forms.CharField(max_length=1000, required=False, widget=forms.Textarea(attrs={"rows": 2}))
    exigences = forms.CharField(label="Exigences (une par ligne)", max_length=3000, widget=forms.Textarea(attrs={"rows": 3}))


ApercuJalonFormSet = forms.formset_factory(ApercuJalonForm, extra=0, min_num=1, max_num=6, validate_min=True,
                                           validate_max=True)


# ---------- Équipes ----------

class EquipeForm(FormulaireModele):
    class Meta:
        model = Equipe
        fields = ["nom", "description"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}
        labels = {"nom": "Nom de l'équipe"}


class AdhesionForm(Formulaire):
    message = forms.CharField(label="Message au coordinateur (facultatif)", required=False, max_length=500,
                              widget=forms.Textarea(attrs={"rows": 2}))


class RefusForm(Formulaire):
    motif = forms.CharField(label="Motif (facultatif)", required=False, max_length=200)


class TransfertForm(Formulaire):
    nouveau = forms.ModelChoiceField(label="Nouveau coordinateur", queryset=Utilisateur.objects.none())

    def __init__(self, *args, equipe=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["nouveau"].queryset = Utilisateur.objects.filter(
            participations_defis__equipe=equipe, participations_defis__etat="acceptee").exclude(pk=user.pk)
        self.fields["nouveau"].label_from_instance = lambda u: u.get_full_name() or u.username


# ---------- Tâches ----------

class TacheForm(FormulaireModele):
    class Meta:
        model = Tache
        fields = ["titre", "description", "jalon", "responsable", "priorite", "echeance"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3}), "echeance": DateInput()}

    def __init__(self, *args, equipe=None, responsables=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.equipe = equipe
        self.fields["jalon"].queryset = equipe.defi.jalons.all()
        self.fields["jalon"].required = False
        choix = responsables
        if self.instance.responsable_id:
            choix = choix | Utilisateur.objects.filter(pk=self.instance.responsable_id)
        self.fields["responsable"].queryset = choix.distinct()
        self.fields["responsable"].label_from_instance = lambda u: u.get_full_name() or u.username
        self.fields["responsable"].empty_label = "Non affectée"


class CommentaireForm(FormulaireModele):
    class Meta:
        model = CommentaireTache
        fields = ["contenu"]
        widgets = {"contenu": forms.Textarea(attrs={"rows": 3})}
        labels = {"contenu": "Votre commentaire"}


# ---------- Livrables ----------

class ContenuVersionMixin:
    """Fichier (formats et taille limités, signature vérifiée), lien http(s) conservé tel quel, texte saisi."""

    def _ajouter_champs_contenu(self):
        self.fields["fichier"] = forms.FileField(
            required=False, label="Fichier",
            help_text=f"Formats : {', '.join(sorted(FORMATS))}. Seuls PDF (avec texte), TXT et MD sont analysables par l'IA.",
            widget=forms.ClearableFileInput(attrs={"accept": ",".join(f".{e}" for e in sorted(FORMATS))}))
        self.fields["lien"] = forms.URLField(required=False, max_length=500, label="Lien externe (facultatif)",
                                             validators=[URLValidator(schemes=["http", "https"])],
                                             help_text="Le lien est conservé tel quel : il n'est jamais ouvert par le serveur.")
        self.fields["texte"] = forms.CharField(required=False, max_length=60000, label="Contenu textuel (facultatif)",
                                               widget=forms.Textarea(attrs={"rows": 8}),
                                               help_text="Collez ici le texte de votre dossier pour permettre sa vérification par l'IA.")
        self.fields["note_equipe"] = forms.CharField(required=False, max_length=2000, label="Message à l'enseignant",
                                                     widget=forms.Textarea(attrs={"rows": 2}))

    def clean_fichier(self):
        fichier = self.cleaned_data.get("fichier")
        if fichier:
            valider_fichier(fichier)
        return fichier

    def _verifier_contenu(self, donnees, existant=False):
        if not (donnees.get("fichier") or existant or donnees.get("lien") or (donnees.get("texte") or "").strip()):
            raise forms.ValidationError("Ajoutez au moins un fichier, un lien ou un contenu textuel.")


class LivrableForm(ContenuVersionMixin, Formulaire):
    titre = forms.CharField(max_length=200)
    description = forms.CharField(max_length=2000, required=False, widget=forms.Textarea(attrs={"rows": 2}))
    jalon = forms.ModelChoiceField(queryset=Jalon.objects.none(), required=False, empty_label="Livrable final (sans jalon)")

    def __init__(self, *args, equipe=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.equipe = equipe
        self.fields["jalon"].queryset = equipe.defi.jalons.all()
        self._ajouter_champs_contenu()
        self._styliser()

    def clean_titre(self):
        titre = self.cleaned_data["titre"].strip()
        if Livrable.objects.filter(equipe=self.equipe, titre__iexact=titre).exists():
            raise forms.ValidationError("Un livrable porte déjà ce titre dans votre équipe.")
        return titre

    def clean(self):
        donnees = super().clean()
        self._verifier_contenu(donnees)
        return donnees


class VersionForm(ContenuVersionMixin, Formulaire):
    retirer_fichier = forms.BooleanField(required=False, label="Retirer le fichier actuel")

    def __init__(self, *args, version=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.version = version
        self._ajouter_champs_contenu()
        if not (version and version.fichier):
            del self.fields["retirer_fichier"]
        self._styliser()

    def clean(self):
        donnees = super().clean()
        existant = bool(self.version and self.version.fichier and not donnees.get("retirer_fichier"))
        self._verifier_contenu(donnees, existant=existant)
        return donnees


class EvaluationForm(Formulaire):
    decision = forms.ChoiceField(label="Décision", widget=forms.RadioSelect, choices=[
        (VersionLivrable.Etat.A_COMPLETER, "Demander un complément"),
        (VersionLivrable.Etat.ACCEPTE, "Accepter le livrable"),
    ])
    retour = forms.CharField(label="Retour à l'équipe", required=False, max_length=5000,
                             widget=forms.Textarea(attrs={"rows": 4}),
                             help_text="Obligatoire pour une demande de complément.")

    def clean(self):
        donnees = super().clean()
        if donnees.get("decision") == VersionLivrable.Etat.A_COMPLETER and not (donnees.get("retour") or "").strip():
            self.add_error("retour", "Expliquez ce qui doit être complété.")
        return donnees


# ---------- Imprévus ----------

class EvenementForm(FormulaireModele):
    class Meta:
        model = EvenementScenario
        fields = ["titre", "type_contrainte", "description", "justification", "impacts_attendus", "elements_attendus",
                  "echeance_reponse", "jalon_concerne", "nouvelle_echeance", "nouveau_budget"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}), "justification": forms.Textarea(attrs={"rows": 3}),
            "impacts_attendus": forms.Textarea(attrs={"rows": 3}), "elements_attendus": forms.Textarea(attrs={"rows": 3}),
            "echeance_reponse": DateHeureInput(), "nouvelle_echeance": DateHeureInput(),
            "nouveau_budget": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
        }
        help_texts = {
            "jalon_concerne": "Modification de délai uniquement : jalon dont l'échéance change à la publication.",
            "nouveau_budget": "Réduction de budget uniquement : appliqué au défi à la publication (tracé).",
        }

    def __init__(self, *args, defi=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.defi = defi
        self.fields["jalon_concerne"].queryset = defi.jalons.all()
        self.fields["jalon_concerne"].empty_label = "Aucun"


class PropositionEvenementForm(Formulaire):
    consigne = forms.CharField(label="Orientation souhaitée (facultatif)", required=False, max_length=500,
                               help_text="Ex. : un imprévu qui touche le public cible.")


class ReponseForm(FormulaireModele):
    class Meta:
        model = ReponseEvenement
        fields = ["analyse_impact", "decisions", "adaptation_planning"]
        widgets = {c: forms.Textarea(attrs={"rows": 4}) for c in ["analyse_impact", "decisions", "adaptation_planning"]}
