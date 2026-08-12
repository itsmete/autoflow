from celery import shared_task
from .engine.orchestrator import AutomationOrchestrator
from .scheduling import record_scheduled_run

@shared_task
def run_automation(automation_id ,data ,source = 'manual'):

        result = AutomationOrchestrator().run(automation_id,data)

        # Only beat passes source='scheduled' (see scheduling.sync_periodic_task); a
        # manual or webhook run must not spend a counted schedule's budget.
        if source == 'scheduled':
                record_scheduled_run(automation_id)

        return result
