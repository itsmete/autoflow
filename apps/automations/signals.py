from django.db.models.signals import post_save,post_delete
from django.dispatch import receiver


from .engine.signal_handler import create_signal_handler
from .engine.signal_registry import get_signal_for_event
from django_celery_beat.models import CrontabSchedule, PeriodicTask

from .models import AutomationTrigger,TriggerType,Automation
from django.apps import apps

import json
import logging


logger = logging.getLogger(__name__)

def register_existing_signal_triggers(data : AutomationTrigger = None):

        if not data :
                data = AutomationTrigger.objects.filter(trigger_type=TriggerType.SIGNAL, automation__is_active=True)

        for obj in data:
                model_name = obj.config.get('model_name')
                event = obj.config.get('event_type')

                handler = create_signal_handler(obj.id, event=str(event))
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
        
        if instance.trigger_type == TriggerType.SCHEDULED:
                cron_exp  = instance.config['cron_expression'] 
                crons = cron_exp.split() #minute , hour, day_of_week , day_of_month , month_of_year

                schedule, _  = CrontabSchedule.objects.get_or_create(
                        minute = crons[0],
                        hour = crons[1],
                        day_of_week = crons[2],
                        day_of_month = crons[3],
                        month_of_year = crons[4] 

                )


                PeriodicTask.objects.update_or_create(
                        name = f"automation_{instance.automation.id}",
                        defaults={
                                'task' : "apps.automations.tasks.run_automation",
                                'crontab' : schedule,
                                'args' : json.dumps([instance.automation.id,instance.initial_data]),
                                'enabled' : instance.automation.is_active 
                        },
                        
                        
                )

@receiver(post_save,sender = Automation)
def change__cron_task_activity_after_automation_changed(sender,instance,created,**kwargs):
        
        PeriodicTask.objects.filter(
                        name = f"automation_{instance.id}"
        ).update(enabled = instance.is_active)
        

@receiver(post_delete,sender=Automation)
def deactivate_cron_task_after_automation_delete(sender,instance,**kwargs):
        PeriodicTask.objects.filter(
                name = f"automation_{instance.id}"
        ).delete()


@receiver(post_delete,sender = AutomationTrigger)
def deactivate_cron_task_after_trigger_delete(sender,instance,**kwargs):
        
        PeriodicTask.objects.filter(
                name = f"automation_{instance.automation.id}"
        ).delete()





@receiver(post_save,sender = AutomationTrigger)
def connect_signal_triggered_automations(sender,instance,**kwargs):             
        if instance.trigger_type == TriggerType.SIGNAL : 
                register_existing_signal_triggers(data = [instance])
