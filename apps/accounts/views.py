from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.views import LoginView
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views import View
from django.urls import reverse
from . import deux_facteurs
from web_project import TemplateLayout
from web_project.template_helpers.theme import TemplateHelper
from django.contrib.auth.views import LogoutView

from .decorators import RoleRequiredMixin
from .models import Utilisateur
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.views.generic import CreateView, ListView, TemplateView, UpdateView
from .forms import AjoutUtilisateurForm, ConnexionForm , InscriptionForm, ModifierUtilisateurForm, ProfilForm
from django.contrib.auth import views as auth_views
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.contrib.auth.models import Group, Permission
from .roles import PERMISSIONS
from .emails import envoyer_email_verification
from .tokens import jeton_verification_email



def layout_sans_menu(view, context):
    context = TemplateLayout.init(view, context)
    context.update({"layout_path": TemplateHelper.set_layout("layout_blank.html", context)})
    return context


# ---------- Connexion / inscription ----------

class ConnexionView(LoginView):
    template_name = "accounts/connexion.html"
    authentication_form = ConnexionForm

    def get_context_data(self, **kwargs):
        return layout_sans_menu(self, super().get_context_data(**kwargs))

    def form_valid(self, form):
        user = form.get_user()
        if user.double_auth_active:
            self.request.session["2fa_user_id"] = user.pk
            self.request.session["2fa_next"] = self.get_success_url()
            self.request.session["2fa_essais"] = 0
            return redirect("connexion-2fa")
        reponse = super().form_valid(form)
        if self.request.POST.get("se_souvenir"):
            self.request.session.set_expiry(14 * 24 * 3600)  # 2 semaines
        else:
            self.request.session.set_expiry(0)               # fin de session à la fermeture du navigateur
        return reponse
    
    
    
@method_decorator(login_not_required, name="dispatch")
class InscriptionView(CreateView):
    form_class = InscriptionForm
    template_name = "accounts/inscription.html"
    

    def get_context_data(self, **kwargs):
        return layout_sans_menu(self, super().get_context_data(**kwargs))

    def form_valid(self, form):
        user = form.save()
        envoyer_email_verification(self.request, user)
        return redirect("verification-envoyee")


# ---------- Espace administrateur ----------

class AdminRequiredMixin(PermissionRequiredMixin):
    """Accès réservé aux utilisateurs ayant la permission de gérer les utilisateurs."""
    permission_required = "accounts.gerer_utilisateurs"


class ListeUtilisateursView(AdminRequiredMixin, ListView):
    model = Utilisateur
    template_name = "accounts/liste_utilisateurs.html"
    context_object_name = "utilisateurs"
    paginate_by = 10

    def get_queryset(self):
        qs = Utilisateur.objects.order_by("-date_inscription")
        recherche = self.request.GET.get("q", "").strip()
        role = self.request.GET.get("role", "")
        statut = self.request.GET.get("statut", "")
        if recherche:
            qs = qs.filter(
                Q(username__icontains=recherche)
                | Q(first_name__icontains=recherche)
                | Q(last_name__icontains=recherche)
                | Q(email__icontains=recherche)
            )
        if role:
            qs = qs.filter(role=role)
        if statut == "actif":
            qs = qs.filter(is_active=True)
        elif statut == "inactif":
            qs = qs.filter(is_active=False)
        return qs

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        tous = Utilisateur.objects.all()
        context.update({
            "stat_total": tous.count(),
            "stat_etudiants": tous.filter(role=Utilisateur.Role.ETUDIANT).count(),
            "stat_enseignants": tous.filter(role=Utilisateur.Role.ENSEIGNANT).count(),
            "stat_inactifs": tous.filter(is_active=False).count(),
            "roles": Utilisateur.Role.choices,
            "q": self.request.GET.get("q", ""),
            "role_choisi": self.request.GET.get("role", ""),
            "statut_choisi": self.request.GET.get("statut", ""),
        })
        return context


class AjouterUtilisateurView(AdminRequiredMixin, CreateView):
    form_class = AjoutUtilisateurForm
    template_name = "accounts/form_utilisateur.html"
    success_url = reverse_lazy("liste-utilisateurs")

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        context["titre"] = "Ajouter un utilisateur"
        return context

    def form_valid(self, form):
        messages.success(self.request, "Utilisateur créé avec succès.")
        return super().form_valid(form)


class ModifierUtilisateurView(AdminRequiredMixin, UpdateView):
    model = Utilisateur
    form_class = ModifierUtilisateurForm
    template_name = "accounts/form_utilisateur.html"
    success_url = reverse_lazy("liste-utilisateurs")

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        context["titre"] = f"Modifier {self.object.username}"
        return context

    def form_valid(self, form):
        if form.instance == self.request.user and not form.cleaned_data["is_active"]:
            form.add_error("is_active", "Vous ne pouvez pas désactiver votre propre compte.")
            return self.form_invalid(form)
        messages.success(self.request, "Utilisateur modifié avec succès.")
        return super().form_valid(form)


class ActiverUtilisateurView(AdminRequiredMixin, View):
    def post(self, request, pk):
        user = get_object_or_404(Utilisateur, pk=pk)
        if user == request.user:
            messages.error(request, "Vous ne pouvez pas désactiver votre propre compte.")
        else:
            user.is_active = not user.is_active
            user.save(update_fields=["is_active"])
            etat = "activé" if user.is_active else "désactivé"
            messages.success(request, f"Le compte de {user.username} a été {etat}.")
        return redirect("liste-utilisateurs")


class SupprimerUtilisateurView(AdminRequiredMixin, View):
    def post(self, request, pk):
        user = get_object_or_404(Utilisateur, pk=pk)
        if user == request.user:
            messages.error(request, "Vous ne pouvez pas supprimer votre propre compte.")
        else:
            nom = user.username
            user.delete()
            messages.success(request, f"Le compte de {nom} a été supprimé.")
        return redirect("liste-utilisateurs")
    
    
# ---------- Mon profil (tous les utilisateurs connectés) ----------

class MonProfilView(TemplateView):
    template_name = "accounts/mon_profil.html"

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        context.setdefault("form_profil", ProfilForm(instance=self.request.user))
        context.setdefault("form_mdp", PasswordChangeForm(user=self.request.user))
        context["codes_restants"] = self.request.user.codes_recuperation.filter(utilise=False).count()
        return context

    def post(self, request, *args, **kwargs):
        # Formulaire 1 : informations personnelles
        if "enregistrer_profil" in request.POST:
            utilisateur = Utilisateur.objects.get(pk=request.user.pk)
            form = ProfilForm(request.POST, request.FILES, instance=utilisateur)
            if form.is_valid():
                form.save()
                messages.success(request, "Votre profil a été mis à jour.")
                return redirect("mon-profil")
            return self.render_to_response(self.get_context_data(form_profil=form))

        # Formulaire 2 : mot de passe
        form = PasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)  # reste connecté après le changement
            messages.success(request, "Votre mot de passe a été modifié.")
            return redirect("mon-profil")
        return self.render_to_response(self.get_context_data(form_mdp=form))
    
# ---------- Mot de passe oublié ----------

class SansMenuMixin:
    """Affiche la page sans le menu (comme la connexion)."""

    def get_context_data(self, **kwargs):
        return layout_sans_menu(self, super().get_context_data(**kwargs))


class MotDePasseOublieView(SansMenuMixin, auth_views.PasswordResetView):
    template_name = "accounts/mdp_oublie.html"
    email_template_name = "accounts/emails/mdp_reinitialisation.txt"
    subject_template_name = "accounts/emails/mdp_reinitialisation_sujet.txt"
    success_url = reverse_lazy("mdp-envoye")


class MotDePasseEnvoyeView(SansMenuMixin, auth_views.PasswordResetDoneView):
    template_name = "accounts/mdp_envoye.html"


class ReinitialiserMotDePasseView(SansMenuMixin, auth_views.PasswordResetConfirmView):
    template_name = "accounts/mdp_reinitialiser.html"
    success_url = reverse_lazy("mdp-termine")

    def form_valid(self, form):
        from .securite import debloquer
        reponse = super().form_valid(form)
        debloquer(form.user)  # nouveau mot de passe : le compte est débloqué
        return reponse


class DebloquerUtilisateurView(AdminRequiredMixin, View):
    def post(self, request, pk):
        from .securite import debloquer
        user = get_object_or_404(Utilisateur, pk=pk)
        debloquer(user)
        messages.success(request, f"Le compte de {user.username} a été débloqué.")
        return redirect("liste-utilisateurs")


class MotDePasseTermineView(SansMenuMixin, auth_views.PasswordResetCompleteView):
    template_name = "accounts/mdp_termine.html"
    

class SupprimerMonCompteView(View):
    def post(self, request):
        user = request.user
        if not user.check_password(request.POST.get("mot_de_passe", "")):
            messages.error(request, "Mot de passe incorrect : votre compte n'a pas été supprimé.")
            return redirect("mon-profil")
        if user.is_superuser:
            messages.error(request, "Un super-administrateur ne peut pas supprimer son propre compte.")
            return redirect("mon-profil")
        logout(request)
        user.delete()
        messages.success(request, "Votre compte a été supprimé définitivement.")
        return redirect("connexion")
    
    # ---------- Vérification de l'e-mail ----------

@method_decorator(login_not_required, name="dispatch")
class VerificationEnvoyeeView(SansMenuMixin, TemplateView):
    template_name = "accounts/verification_envoyee.html"


@method_decorator(login_not_required, name="dispatch")
class VerifierEmailView(View):
    def get(self, request, uidb64, token):
        try:
            user = Utilisateur.objects.get(pk=force_str(urlsafe_base64_decode(uidb64)))
        except (TypeError, ValueError, OverflowError, Utilisateur.DoesNotExist):
            user = None

        if user and jeton_verification_email.check_token(user, token):
            user.email_verifie = True
            user.save(update_fields=["email_verifie"])
            messages.success(request, "Adresse e-mail confirmée ! Vous pouvez maintenant vous connecter.")
        else:
            messages.error(request, "Ce lien de confirmation est invalide ou a expiré.")
        return redirect("connexion")


@method_decorator(login_not_required, name="dispatch")
class RenvoyerVerificationView(SansMenuMixin, TemplateView):
    template_name = "accounts/renvoyer_verification.html"

    def post(self, request):
        email = request.POST.get("email", "").strip()
        user = Utilisateur.objects.filter(email__iexact=email, email_verifie=False).first()
        if user:
            envoyer_email_verification(request, user)
        # Même réponse dans tous les cas, pour ne pas révéler quels e-mails existent
        return redirect("verification-envoyee")
    
# ---------- Double authentification (2FA) ----------

class Activer2FAView(TemplateView):
    template_name = "accounts/2fa_activer.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.double_auth_active:
            messages.info(request, "La double authentification est déjà activée.")
            return redirect("mon-profil")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        # Le secret reste en session tant que l'utilisateur n'a pas confirmé avec un code
        secret = self.request.session.get("2fa_secret_temp")
        if not secret:
            secret = deux_facteurs.generer_secret()
            self.request.session["2fa_secret_temp"] = secret
        context["secret"] = secret
        context["qr_code"] = deux_facteurs.qr_code_base64(
            deux_facteurs.uri_totp(self.request.user, secret)
        )
        return context

    def post(self, request):
        secret = request.session.get("2fa_secret_temp")
        if secret and deux_facteurs.verifier_code_totp(secret, request.POST.get("code", "")):
            user = request.user
            user.secret_totp = secret
            user.double_auth_active = True
            user.save(update_fields=["secret_totp", "double_auth_active"])
            del request.session["2fa_secret_temp"]
            request.session["2fa_codes_a_afficher"] = deux_facteurs.generer_codes_recuperation(user)
            messages.success(request, "La double authentification est activée !")
            return redirect("codes-recuperation")
        return self.render_to_response(
            self.get_context_data(erreur="Code incorrect. Vérifiez que l'heure de votre téléphone est automatique, puis réessayez.")
        )


class CodesRecuperationView(TemplateView):
    template_name = "accounts/2fa_codes.html"

    def get(self, request, *args, **kwargs):
        codes = request.session.pop("2fa_codes_a_afficher", None)
        if not codes:
            return redirect("mon-profil")  # les codes ne s'affichent qu'une seule fois
        return self.render_to_response(self.get_context_data(codes=codes))

    def get_context_data(self, **kwargs):
        return TemplateLayout.init(self, super().get_context_data(**kwargs))


class RegenererCodesView(View):
    def post(self, request):
        if not request.user.double_auth_active:
            return redirect("mon-profil")
        if not request.user.check_password(request.POST.get("mot_de_passe", "")):
            messages.error(request, "Mot de passe incorrect : les codes n'ont pas été régénérés.")
            return redirect("mon-profil")
        request.session["2fa_codes_a_afficher"] = deux_facteurs.generer_codes_recuperation(request.user)
        messages.success(request, "Nouveaux codes générés. Les anciens ne fonctionnent plus.")
        return redirect("codes-recuperation")


class Desactiver2FAView(View):
    def post(self, request):
        user = request.user
        if not user.check_password(request.POST.get("mot_de_passe", "")):
            messages.error(request, "Mot de passe incorrect : la double authentification reste activée.")
            return redirect("mon-profil")
        user.double_auth_active = False
        user.secret_totp = ""
        user.save(update_fields=["double_auth_active", "secret_totp"])
        user.codes_recuperation.all().delete()
        messages.success(request, "La double authentification est désactivée.")
        return redirect("mon-profil")


@method_decorator(login_not_required, name="dispatch")
class Connexion2FAView(SansMenuMixin, TemplateView):
    template_name = "accounts/connexion_2fa.html"
    MAX_ESSAIS = 5

    def dispatch(self, request, *args, **kwargs):
        if not request.session.get("2fa_user_id"):
            return redirect("connexion")
        return super().dispatch(request, *args, **kwargs)

    def _oublier_session(self):
        for cle in ("2fa_user_id", "2fa_next", "2fa_essais"):
            self.request.session.pop(cle, None)

    def post(self, request):
        user = Utilisateur.objects.filter(pk=request.session["2fa_user_id"], is_active=True).first()
        mode = request.POST.get("mode", "application")
        code = request.POST.get("code", "")

        valide = False
        if user:
            if mode == "recuperation":
                valide = deux_facteurs.utiliser_code_recuperation(user, code)
            else:
                valide = deux_facteurs.verifier_code_totp(user.secret_totp, code)

        if valide:
            suivant = request.session.get("2fa_next") or reverse("index")
            self._oublier_session()
            login(request, user)
            if mode == "recuperation":
                restants = user.codes_recuperation.filter(utilise=False).count()
                messages.warning(request, f"Code de récupération utilisé. Il vous en reste {restants}.")
            return redirect(suivant)

        essais = request.session.get("2fa_essais", 0) + 1
        request.session["2fa_essais"] = essais
        if not user or essais >= self.MAX_ESSAIS:
            self._oublier_session()
            messages.error(request, "Trop de tentatives incorrectes. Veuillez vous reconnecter.")
            return redirect("connexion")

        return self.render_to_response(self.get_context_data(
            erreur=f"Code incorrect. Il vous reste {self.MAX_ESSAIS - essais} essai(s).",
            mode=mode,
        ))
        
class RolesPermissionsView(AdminRequiredMixin, TemplateView):
    template_name = "accounts/roles_permissions.html"

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        groupes = list(Group.objects.prefetch_related("permissions").order_by("name"))
        codes = [code for code, _ in PERMISSIONS]
        permissions = Permission.objects.filter(
            content_type__app_label="accounts", codename__in=codes
        ).order_by("name")

        matrice = []
        for permission in permissions:
            matrice.append({
                "permission": permission,
                "acces": [permission in groupe.permissions.all() for groupe in groupes],
            })

        context.update({
            "groupes": [
                {"nom": g.name, "membres": g.user_set.count(), "nb_permissions": g.permissions.count()}
                for g in groupes
            ],
            "matrice": matrice,
        })
        return context
    
    
# ---------- Page d'erreur 403 (accès refusé) ----------

def erreur_403(request, exception=None):
    context = layout_sans_menu(None, {})
    return render(request, "accounts/erreur_403.html", context, status=403)

@method_decorator(login_not_required, name="dispatch")
class DeconnexionView(LogoutView):
    """Déconnexion en POST uniquement (protection CSRF).
    Un accès direct par l'adresse (GET) ne déconnecte pas : on redirige proprement au lieu d'une erreur 405."""

    http_method_names = ["get", "post", "options"]

    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            messages.info(request, "Pour vous déconnecter, utilisez le bouton « Déconnexion » du menu de votre profil.")
            return redirect("index")
        return redirect("connexion")