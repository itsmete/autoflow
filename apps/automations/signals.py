from django.db.models.signals import post_save,post_delete
from django.dispatch import receiver

from django_celery_beat.models import CrontabSchedule, PeriodicTask

from .models import AutomationTrigger,TriggerType,Automation

import json

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








