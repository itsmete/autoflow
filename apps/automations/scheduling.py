"""
Everything that talks to django_celery_beat lives here.

This used to be inlined in signals.py, which is a bad place to notice a bug: a
signal handler is only reachable by saving a model, so all three defects below
survived a 285-test suite. Pulled out here they are plain functions and can be
tested directly.

The three:

  * the cron fields were assigned to CrontabSchedule in the wrong order,
  * a 'one_time' schedule produced no PeriodicTask at all, so it never fired,
  * the repeat count of an 'n' schedule was validated and then never read.
"""
from django.db.models import F
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django_celery_beat.models import ClockedSchedule, CrontabSchedule, PeriodicTask

from .models import AutomationTrigger, TriggerType

import json
import logging


logger = logging.getLogger(__name__)

TASK_PATH = 'apps.automations.tasks.run_automation'

# A standard 5-field cron expression, in the order croniter validates it:
#
#     minute  hour  day-of-month  month  day-of-week
#
# CrontabSchedule names its columns differently and the original code assigned
# them positionally in ITS order (minute, hour, day_of_week, day_of_month,
# month_of_year). That silently rewrote '0 9 * * 1' - Mondays at 09:00 - into
# "every day in January at 09:00". Nothing raised; the automation simply ran at
# the wrong time.
CRON_FIELDS = ('minute','hour','day_of_month','month_of_year','day_of_week')


def task_name(automation_id):
        return f"automation_{automation_id}"


def parse_cron(cron_expression):
        """
        Split a 5-field cron expression into CrontabSchedule kwargs.

        Returns None for anything that is not five fields. The serializer already
        runs croniter over this, but a trigger saved outside the API bypasses that
        and a malformed expression must not take the save down with an IndexError.
        """
        parts = cron_expression.split()

        if len(parts) != len(CRON_FIELDS):
                logger.error("Ignoring malformed cron expression %r",cron_expression)
                return None

        return dict(zip(CRON_FIELDS,parts))


def _crontab_for(cron_expression):
        fields = parse_cron(cron_expression)
        if not fields:
                return None

        schedule,_ = CrontabSchedule.objects.get_or_create(**fields)
        return schedule


def _clocked_for(start_time):
        """
        Build the beat schedule for a 'one_time' trigger.

        one_time carries a start_time and no cron expression, and the old code
        returned early whenever cron was missing. So one_time triggers were accepted
        by the serializer, written to the database, and then never fired - there was
        no beat entry for them to fire from. ClockedSchedule is beat's answer to
        "run once, at this instant".
        """
        when = start_time

        if isinstance(when,str):
                when = parse_datetime(when)

        if not when:
                logger.error("Ignoring unparseable one_time start_time %r",start_time)
                return None

        if timezone.is_naive(when):
                when = timezone.make_aware(when)

        if when < timezone.now():
                # beat fires a past clocked time on its next tick. Worth saying out
                # loud, because "it ran immediately" looks like a bug otherwise.
                logger.warning("one_time start_time %s is in the past, it will fire on the next beat tick",when)

        schedule,_ = ClockedSchedule.objects.get_or_create(clocked_time = when)
        return schedule


def sync_periodic_task(trigger):
        """
        Create or update the beat entry for a scheduled trigger.

        PeriodicTask holds four mutually exclusive schedule kinds, so the ones we
        are not using have to be cleared explicitly - otherwise switching a trigger
        from one_time to forever leaves both crontab and clocked set.

        Saving a scheduled trigger also RESTARTS its repeat budget - see the reset at
        the bottom. Two reasons, and the second is the real one:

          * it matches what the API already does, since
            AutomationWriteSerializer.update() deletes every trigger and recreates it,
          * and the alternative is not "keep the count", it is "keep whatever count
            the in-memory instance happened to hold". A stale instance writes its own
            stale run_count back over the counter, so preserving it would depend on
            whether the caller refetched. Deterministic beats clever here.
        """
        config = trigger.config or {}
        schedule_type = config.get('schedule_type')

        crontab = None
        clocked = None
        one_off = False

        if schedule_type == 'one_time':
                clocked = _clocked_for(config.get('start_time'))
                one_off = True

                if not clocked:
                        return None
        else:
                cron_expression = config.get('cron_expression')
                if not cron_expression:
                        return None

                crontab = _crontab_for(cron_expression)
                if not crontab:
                        return None

        automation = trigger.automation

        task,_ = PeriodicTask.objects.update_or_create(
                name = task_name(automation.id),
                defaults = {
                        'task' : TASK_PATH,
                        'crontab' : crontab,
                        'clocked' : clocked,
                        'interval' : None,
                        'solar' : None,
                        'one_off' : one_off,
                        # UUIDs are not JSON serializable and CELERY_TASK_SERIALIZER is
                        # 'json'. The task looks the automation up by pk, so a string is
                        # enough.
                        'args' : json.dumps([str(automation.id),trigger.initial_data]),
                        # Tells the task the run came from beat rather than from a person
                        # or a webhook. Only beat-driven runs spend the 'n' budget.
                        'kwargs' : json.dumps({'source' : 'scheduled'}),
                        'enabled' : automation.is_active,
                },
        )

        # Redefining the schedule restarts the budget. Written through the queryset
        # so it does not re-enter post_save, and mirrored onto the instance so the
        # caller does not keep reading a stale number.
        AutomationTrigger.objects.filter(pk = trigger.pk).update(run_count = 0)
        trigger.run_count = 0

        return task


def delete_periodic_task(automation_id):
        PeriodicTask.objects.filter(name = task_name(automation_id)).delete()


def set_periodic_task_enabled(automation_id,enabled):
        PeriodicTask.objects.filter(name = task_name(automation_id)).update(enabled = enabled)


def record_scheduled_run(automation_id):
        """
        Count one beat-driven firing and stop a counted ('n') schedule once its
        budget is spent.

        config['count'] was validated by ScheduledTriggerSerializer and then read by
        nothing at all, so 'n' behaved exactly like 'forever'.

        The counter lives on the trigger rather than on PeriodicTask.total_run_count.
        beat owns that field and flushes it on its own cadence (sync_every), so a
        worker reading it immediately after the dispatch can see a stale value.
        Counting here - inside the task that actually ran - is the only place the
        number is certainly right.
        """
        trigger = AutomationTrigger.objects.filter(
                automation_id = automation_id,
                trigger_type = TriggerType.SCHEDULED,
        ).first()

        if not trigger:
                return

        config = trigger.config or {}

        # 'forever' has no budget and 'one_time' is already handled by beat's one_off
        # flag, so neither of them needs a counter.
        if config.get('schedule_type') != 'n':
                return

        # F() rather than read-modify-write: two overlapping runs would otherwise
        # both read the same value and one increment would be lost.
        AutomationTrigger.objects.filter(pk = trigger.pk).update(run_count = F('run_count') + 1)
        trigger.refresh_from_db(fields = ['run_count'])

        count = config.get('count')

        if count and trigger.run_count >= count:
                set_periodic_task_enabled(automation_id,False)
                logger.info(
                        "Automation %s has used its %s scheduled runs, disabling the beat entry",
                        automation_id,
                        count,
                )
