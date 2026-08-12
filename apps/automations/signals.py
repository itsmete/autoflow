from django.db.models.signals import post_save,post_delete
from django.dispatch import receiver


from .engine.signal_handler import create_signal_handler
from .engine.signal_registry import get_signal_for_event
from .scheduling import delete_periodic_task,set_periodic_task_enabled,sync_periodic_task

from .models import AutomationTrigger,TriggerType,Automation
from django.apps import apps

import logging


logger = logging.getLogger(__name__)

def register_existing_signal_triggers(data : AutomationTrigger = None):

        if not data :
                data = AutomationTrigger.objects.filter(trigger_type=TriggerType.SIGNAL, automation__is_active=True)

        for obj in data:
                model_name = obj.config.get('model_name')
                event = obj.config.get('event_type')

                # obj is the TRIGGER; the orchestrator looks the row up as
                # Automation.objects.get(id=...), so passing obj.id sent it a trigger
                # pk and every signal-driven run died on Automation.DoesNotExist -
                # logged as "Automation not found" and otherwise invisible.
                handler = create_signal_handler(obj.automation_id, event=str(event))
                try:
                        sender_model = apps.get_model(model_name)
                except LookupError as e:
                        logger.error(f"Lookup error due to Invalid Model name/path. model_name = {model_name}")
                        continue


                signal = get_signal_for_event(event)
                if signal:
                        signal.connect(handler,sender=sender_model,dispatch_uid=f"automation_{obj.automation.id}_{model_name}_{event}")



@receiver(post_save,sender=AutomationTrigger)
def create_or_update_periodic_task_after_trigger_object_changed(sender,instance,created,**kwargs):

        # The beat bookkeeping itself lives in scheduling.py. It used to be inlined
        # here, where it was only reachable by saving a model - which is how a
        # scrambled cron field order and a one_time schedule that never fired both
        # stayed invisible.
        if instance.trigger_type == TriggerType.SCHEDULED:
                sync_periodic_task(instance)


@receiver(post_save,sender = Automation)
def change__cron_task_activity_after_automation_changed(sender,instance,created,**kwargs):

        set_periodic_task_enabled(instance.id,instance.is_active)


@receiver(post_delete,sender=Automation)
def deactivate_cron_task_after_automation_delete(sender,instance,**kwargs):
        delete_periodic_task(instance.id)


@receiver(post_delete,sender = AutomationTrigger)
def deactivate_cron_task_after_trigger_delete(sender,instance,**kwargs):
        delete_periodic_task(instance.automation_id)




@receiver(post_save,sender = AutomationTrigger)
def connect_signal_triggered_automations(sender,instance,**kwargs):
        if instance.trigger_type == TriggerType.SIGNAL :
                register_existing_signal_triggers(data = [instance])
