from celery import shared_task
from .engine.orchestrator import AutomationOrchestrator

@shared_task
def run_automation(automation_id ,data):
        
        result = AutomationOrchestrator().run(automation_id,data)
        return result