"""Partie IA du module Utilisateurs : enregistrement du visage et connexion par reconnaissance faciale."""

import json
import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.generic import TemplateView

from ai_services import VisageErreur, analyser_visage, similarite
from web_project import TemplateLayout

from .models import ProfilFacial, Utilisateur
from .securite import enregistrer_connexion
from .views import SansMenuMixin

NB_CAPTURES = 3          # photos prises à l'enregistrement
ECART_LACET_MIN = 0.25   # rotation minimale de la tête exigée pour prouver que c'est une vraie personne
MAX_ESSAIS = 5           # essais de connexion par visage avant blocage temporaire
DUREE_BLOCAGE = 300      # secondes
MARGE_IDENTIFICATION = 0.05  # écart minimal entre les deux visages les plus proches (évite les confusions)


def seuil():
    return getattr(settings, "FACE_SEUIL", 0.363)


def identifier(encodage, exclure=None):
    """Compare un visage à tous les visages enregistrés (identification « 1 parmi N »).

    Renvoie (profil le plus proche, son score, score du 2e plus proche).
    """
    profils = ProfilFacial.objects.filter(utilisateur__is_active=True).select_related("utilisateur")
    if exclure is not None:
        profils = profils.exclude(utilisateur=exclure)
    resultats = sorted(((p.score(encodage), p) for p in profils), key=lambda r: r[0], reverse=True)
    if not resultats:
        return None, 0.0, 0.0
    deuxieme = resultats[1][0] if len(resultats) > 1 else 0.0
    return resultats[0][1], resultats[0][0], deuxieme


def _lire_json(request):
    try:
        return json.loads(request.body or b"{}")
    except ValueError:
        return {}


def erreur(message, statut=400):
    return JsonResponse({"ok": False, "message": message}, status=statut)


# ---------- Enregistrement du visage (dans Mon profil) ----------

class EnregistrerVisageView(TemplateView):
    template_name = "accounts/visage_enregistrer.html"

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        context["nb_captures"] = NB_CAPTURES
        context["deja_enregistre"] = ProfilFacial.objects.filter(utilisateur=self.request.user).exists()
        return context

    def post(self, request):
        donnees = _lire_json(request)
        if not donnees.get("consentement"):
            return erreur("Vous devez accepter l'utilisation de votre visage pour continuer.")
        if not request.user.check_password(donnees.get("mot_de_passe", "")):
            return erreur("Mot de passe incorrect.")

        images = donnees.get("images") or []
        if len(images) != NB_CAPTURES:
            return erreur(f"{NB_CAPTURES} photos sont nécessaires.")

        encodages = []
        for numero, image in enumerate(images, start=1):
            try:
                encodages.append(analyser_visage(image)["encodage"])
            except VisageErreur as e:
                return erreur(f"Photo {numero} : {e}")

        # Les captures doivent montrer la même personne
        for autre in encodages[1:]:
            if similarite(encodages[0], autre) < seuil():
                return erreur("Les photos ne semblent pas montrer la même personne. Recommencez.")

        # Anti-fraude : un même visage ne peut pas être associé à deux comptes
        autre, score_autre, _ = identifier(encodages[0], exclure=request.user)
        if autre and score_autre >= seuil():
            return erreur("Ce visage est déjà associé à un autre compte. Contactez un administrateur.")

        ProfilFacial.objects.update_or_create(
            utilisateur=request.user,
            defaults={"encodages": encodages, "consentement_le": timezone.now()},
        )
        messages.success(request, "Votre visage est enregistré : vous pouvez maintenant vous connecter avec la caméra.")
        return JsonResponse({"ok": True, "redirection": reverse("mon-profil")})


class SupprimerVisageView(View):
    def post(self, request):
        ProfilFacial.objects.filter(utilisateur=request.user).delete()
        messages.success(request, "Vos données faciales ont été supprimées.")
        return redirect("mon-profil")


# ---------- Connexion par reconnaissance faciale ----------

@method_decorator(login_not_required, name="dispatch")
class ConnexionVisageView(SansMenuMixin, TemplateView):
    template_name = "accounts/connexion_visage.html"

    def _bloque(self):
        fin = self.request.session.get("visage_blocage_jusqua", 0)
        return time.time() < fin

    def _echec(self, message, nom="", utilisateur=None):
        enregistrer_connexion(self.request, utilisateur=utilisateur, nom_saisi=nom, succes=False,
                              methode="VISAGE", motif=message)
        essais = self.request.session.get("visage_essais", 0) + 1
        self.request.session["visage_essais"] = essais
        if essais >= MAX_ESSAIS:
            self.request.session["visage_essais"] = 0
            self.request.session["visage_blocage_jusqua"] = time.time() + DUREE_BLOCAGE
            return erreur("Trop de tentatives. Réessayez dans 5 minutes ou connectez-vous avec votre mot de passe.", 429)
        return erreur(f"{message} ({MAX_ESSAIS - essais} essai(s) restant(s))", 401)

    def post(self, request):
        if self._bloque():
            return erreur("Trop de tentatives. Réessayez dans quelques minutes ou utilisez votre mot de passe.", 429)

        donnees = _lire_json(request)
        nom = (donnees.get("username") or "").strip()
        image_face, image_tournee = donnees.get("image_face"), donnees.get("image_tournee")
        if not image_face or not image_tournee:
            return erreur("Laissez la caméra prendre les deux photos.")

        try:
            face = analyser_visage(image_face)
            tournee = analyser_visage(image_tournee)
        except VisageErreur as e:
            return erreur(str(e))

        # Détection de vivacité : une photo imprimée ou un écran ne peut pas tourner la tête
        if abs(tournee["lacet"] - face["lacet"]) < ECART_LACET_MIN:
            return self._echec("Mouvement de tête non détecté : tournez davantage la tête lors de la 2e photo.", nom)
        if similarite(face["encodage"], tournee["encodage"]) < seuil():
            return self._echec("Les deux photos ne montrent pas la même personne.", nom)

        if nom:
            # Vérification « 1 pour 1 » : ce visage est-il celui de ce compte ?
            user = Utilisateur.objects.filter(username__iexact=nom, is_active=True).select_related("profil_facial").first()
            profil = getattr(user, "profil_facial", None) if user else None
            if not profil or profil.score(face["encodage"]) < seuil():
                # Message volontairement identique dans les deux cas : on ne révèle pas quels comptes existent
                return self._echec("Visage non reconnu pour ce compte.", nom, user)
        else:
            # Identification « 1 parmi N » : l'IA retrouve seule le compte
            profil, score, deuxieme = identifier(face["encodage"])
            if not profil or score < seuil():
                return self._echec("Visage non reconnu. Enregistrez d'abord votre visage depuis votre profil.")
            if score - deuxieme < MARGE_IDENTIFICATION:
                return self._echec("Plusieurs comptes ressemblent à ce visage : saisissez votre nom d'utilisateur.")
            user = profil.utilisateur

        if not user.email_verifie and not user.is_superuser:
            return erreur("Vous devez d'abord confirmer votre adresse e-mail.", 403)

        profil.derniere_utilisation = timezone.now()
        profil.save(update_fields=["derniere_utilisation"])
        request.session["visage_essais"] = 0

        # Si la 2FA est activée, le code du téléphone reste obligatoire
        if user.double_auth_active:
            request.session["2fa_user_id"] = user.pk
            request.session["2fa_next"] = reverse("index")
            request.session["2fa_essais"] = 0
            request.session["2fa_methode"] = "VISAGE"
            return JsonResponse({"ok": True, "redirection": reverse("connexion-2fa")})

        request.methode_connexion = "VISAGE"  # lu par le journal des connexions
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        return JsonResponse({"ok": True, "redirection": reverse("index")})


# ---------- Contrôle des visages en double (administrateur) ----------

class VisagesDoublonsView(PermissionRequiredMixin, TemplateView):
    permission_required = "accounts.gerer_utilisateurs"
    template_name = "accounts/visages_doublons.html"

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        profils = list(ProfilFacial.objects.select_related("utilisateur"))
        doublons = []
        for i, a in enumerate(profils):
            for b in profils[i + 1:]:
                score = max(similarite(x, y) for x in a.encodages for y in b.encodages)
                if score >= seuil():
                    doublons.append({"a": a.utilisateur, "b": b.utilisateur, "score": round(score * 100)})
        context.update({
            "doublons": sorted(doublons, key=lambda d: d["score"], reverse=True),
            "nb_profils": len(profils),
        })
        return context