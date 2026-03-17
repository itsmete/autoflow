from django.urls import path
from .views import WebhookAutomationView , ManualTriggeredAutomationView
urlpatterns = [
        path("webhook/",WebhookAutomationView.as_view()),
        path("<uuid:pk>/run",ManualTriggeredAutomationView.as_view())

]