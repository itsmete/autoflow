from django.db.models.signals import post_save,post_delete,pre_save
from django.dispatch import receiver

from collections.abc import Mapping


from .engine.signal_handler import create_signal_handler
from .engine.signal_registry import get_signal_for_event
from .runtime import resolve_model,serialize_model_instance
from .scheduling import delete_periodic_task,set_periodic_task_enabled,sync_periodic_task

from .models import AutomationTrigger,TriggerType,Automation

import logging


logger = logging.getLogger(__name__)


def capture_field_changed_snapshot(sender,instance,**kwargs):
        """
        Remember what the row looked like before this save.

        A post_save receiver cannot answer "did `status` change?" - the old values are
        already gone. So `field_changed` watches a pre_save on the ONE model it was
        asked about (connected below, not project-wide) and parks a serialized copy on
        the instance; the post_save handler compares against it.

        `_state.adding`, not `instance.pk`: BaseModel declares id with
        default=uuid.uuid4, so the pk is populated long before a row exists.
        """
        if instance._state.adding or not instance.pk:
                return

        try:
                old = sender.objects.filter(pk = instance.pk).first()
        except Exception:
                # Unmigrated database, or a table that stopped existing. A trigger
                # registration must never take the save down with it.
                logger.debug(
                        "automations: no previous row for %s, skipping snapshot",
                        sender.__name__,
                )
                return

        if old is None:
                return

        instance._autoflow_prev = serialize_model_instance(old)


def register_existing_signal_triggers(data : AutomationTrigger = None):

        if not data :
                data = AutomationTrigger.objects.filter(trigger_type=TriggerType.SIGNAL, automation__is_active=True)

        for obj in data:
                model_name = obj.config.get('model_name')
                event = obj.config.get('event_type')

                try:
                        sender_model = resolve_model(model_name)
                except LookupError as e:
                        logger.error(f"Lookup error due to Invalid Model name/path. model_name = {model_name}")
                        continue

                if event == 'field_changed':
                        # Connected per watched model, with dispatch_uid, rather than as
                        # a senderless global receiver: the global version would run a
                        # SELECT on every save of every model in the project, which is
                        # the whole database paying for one trigger.
                        pre_save.connect(
                                capture_field_changed_snapshot,
                                sender = sender_model,
                                dispatch_uid = f"automation_presave_{model_name}",
                        )

                # obj is the TRIGGER; the orchestrator looks the row up as
                # Automation.objects.get(id=...), so passing obj.id sent it a trigger
                # pk and every signal-driven run died on Automation.DoesNotExist -
                # logged as "Automation not found" and otherwise invisible.
                #
                # config is passed on because `field_changed` needs the watched field
                # and its optional comparison values; the handler holds no model.
                handler = create_signal_handler(
                        obj.automation_id,
                        event = str(event),
                        config = obj.config if isinstance(obj.config,Mapping) else {},
                )

                signal = get_signal_for_event(event)
                if signal:
                        # weak=False, and it is not optional: a receiver connected
                        # weakly to nothing but Django's registry is collected the
                        # moment this loop drops the local `handler`, so the trigger
                        # was stored, reported as connected, and silently never fired.
                        signal.connect(
                                handler,
                                sender = sender_model,
                                weak = False,
                                dispatch_uid = f"automation_{obj.automation.id}_{model_name}_{event}",
                        )



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
