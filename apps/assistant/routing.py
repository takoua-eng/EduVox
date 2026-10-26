from django.urls import path

from .consumers import AssistantConsumer

websocket_urlpatterns = [
    path("ws/assistant/sessions/<int:pk>/", AssistantConsumer.as_asgi()),
]
