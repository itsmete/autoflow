from django.urls import path
from .views import WebhookAutomationView , ManualTriggeredAutomationView,\
AutomationListCreateView,AutomationDetailView,TemplateDetailView,\
TemplateListCreateView
urlpatterns = [
        path("webhook/",WebhookAutomationView.as_view()),
        path("<uuid:pk>/run/",ManualTriggeredAutomationView.as_view()),

        path('',AutomationListCreateView.as_view()),
        path('<uuid:pk>/',AutomationDetailView.as_view()),

        path('templates/',TemplateListCreateView.as_view()),
        path('templates/<uuid:pk>/',TemplateDetailView.as_view()),


]