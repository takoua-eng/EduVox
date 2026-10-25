"""Import d'utilisateurs en masse (administrateur)."""

from django.contrib import messages
from django.http import HttpResponse
from django.views import View
from django.views.generic import TemplateView

from web_project import TemplateLayout

from .importation import COLONNES, ImportErreur, envoyer_invitation, importer, lire_fichier
from .models import Utilisateur
from .views import AdminRequiredMixin


class ImportUtilisateursView(AdminRequiredMixin, TemplateView):
    template_name = "accounts/import_utilisateurs.html"

    def get_context_data(self, **kwargs):
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))
        context["colonnes"] = COLONNES
        return context

    def post(self, request):
        fichier = request.FILES.get("fichier")
        if not fichier:
            messages.error(request, "Choisissez un fichier.")
            return self.render_to_response(self.get_context_data())

        role = request.POST.get("role_par_defaut", Utilisateur.Role.ETUDIANT)
        if role not in (Utilisateur.Role.ETUDIANT, Utilisateur.Role.ENSEIGNANT):
            role = Utilisateur.Role.ETUDIANT

        try:
            lignes = lire_fichier(fichier)
        except ImportErreur as e:
            messages.error(request, str(e))
            return self.render_to_response(self.get_context_data())

        crees, erreurs = importer(lignes, role_par_defaut=role)
        if request.POST.get("envoyer_emails"):
            for user in crees:
                envoyer_invitation(request, user)

        if crees:
            messages.success(request, f"{len(crees)} compte(s) créé(s).")
        if erreurs:
            messages.warning(request, f"{len(erreurs)} ligne(s) ignorée(s) : voir le détail ci-dessous.")
        return self.render_to_response(self.get_context_data(crees=crees, erreurs=erreurs, envoye=bool(request.POST.get("envoyer_emails"))))


class ModeleImportView(AdminRequiredMixin, View):
    """Fichier CSV d'exemple, prêt à ouvrir dans Excel."""

    def get(self, request):
        lignes = [
            ";".join(COLONNES),
            "Amira;Salah;amira.salah@exemple.com;Etudiant;;L2 Informatique",
            "Youssef;Ben Ali;youssef.benali@exemple.com;Etudiant;ybenali;L2 Informatique",
            "Sami;Trabelsi;sami.trabelsi@exemple.com;Enseignant;;Intelligence artificielle",
        ]
        reponse = HttpResponse("\ufeff" + "\r\n".join(lignes), content_type="text/csv; charset=utf-8")
        reponse["Content-Disposition"] = 'attachment; filename="modele_import_eduvox.csv"'
        return reponse