from django.views.generic import TemplateView
from web_project import TemplateLayout
from web_project.template_helpers.theme import TemplateHelper


class SystemView(TemplateView):
    template_name = "pages/system/not-found.html"
    status = ""

    def dispatch(self, request, *args, **kwargs):
        # Page d'erreur : elle doit répondre quelle que soit la méthode (GET, POST...)
        return self.get(request, *args, **kwargs)

    def render_to_response(self, context, **response_kwargs):
        # Renvoie le vrai code HTTP (404, 400, 500) et pas 200
        if self.status:
            response_kwargs.setdefault("status", self.status)
        return super().render_to_response(context, **response_kwargs)

    def get_context_data(self, **kwargs):
        # A function to init the global layout. It is defined in web_project/__init__.py file
        context = TemplateLayout.init(self, super().get_context_data(**kwargs))

        # Define the layout for this module
        # _templates/layout/system.html
        context.update(
            {
                "layout_path": TemplateHelper.set_layout("system.html", context),
                "status": self.status,
            }
        )

        return context
