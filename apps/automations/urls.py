from django.urls import path
from .views import WebhookAutomationView , ManualTriggeredAutomationView,\
AutomationListCreateView,AutomationDetailView,TemplateDetailView,\
TemplateListCreateView,AutomationRunListView,AutomationRunDetailView,\
AutomationStepRunListView,AutomationRunReplayView,TestAutomationView
urlpatterns = [
        path("webhook/",WebhookAutomationView.as_view()),
        path("<uuid:pk>/run/",ManualTriggeredAutomationView.as_view()),

        # Run history, dry-run and replay (ROADMAP section 5.2). Declared before the
        # <uuid:pk>/ routes below only for readability - both spellings are distinct.
        path("<uuid:pk>/runs/",AutomationRunListView.as_view()),
        path("<uuid:pk>/test/",TestAutomationView.as_view()),
        path("runs/<uuid:run_id>/",AutomationRunDetailView.as_view()),
        path("runs/<uuid:run_id>/steps/",AutomationStepRunListView.as_view()),
        path("runs/<uuid:run_id>/replay/",AutomationRunReplayView.as_view()),

        path('',AutomationListCreateView.as_view()),
        path('<uuid:pk>/',AutomationDetailView.as_view()),

        path('templates/',TemplateListCreateView.as_view()),
        path('templates/<uuid:pk>/',TemplateDetailView.as_view()),


]
