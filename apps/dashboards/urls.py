from django.urls import path
from .views import DashboardsView

urlpatterns = [
    path("", DashboardsView.as_view(), name="index"),
]