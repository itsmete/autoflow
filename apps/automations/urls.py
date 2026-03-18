from django.urls import path
from .views import WebhookAutomationView , ManualTriggeredAutomationView,\
AutomationListCreateView,AutomationDetailView
urlpatterns = [
        path("webhook/",WebhookAutomationView.as_view()),
        path("<uuid:pk>/run/",ManualTriggeredAutomationView.as_view()),

        path('',AutomationListCreateView.as_view()),
        path('<uuid:pk>/',AutomationDetailView.as_view())


]