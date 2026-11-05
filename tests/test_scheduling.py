"""
Scheduling: cron field order, one_time firing, and the 'n' repeat budget.

All three were broken and none of them was visible. The suite had exactly one
scheduling assertion and it used "0 1 * * *" - an expression whose last three fields
are all '*', which is precisely the shape that cannot tell a correct field mapping
from a scrambled one. Every expression here uses distinct, non-wildcard values.
"""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from django_celery_beat.models import ClockedSchedule, CrontabSchedule, PeriodicTask

from apps.automations.models import AutomationTrigger
from apps.automations.scheduling import (
    parse_cron,
    record_scheduled_run,
    task_name,
)
from apps.automations.tasks import run_automation
from tests import factories


class CronParsingTests(TestCase):

    def test_fields_land_in_the_columns_standard_cron_assigns_them_to(self):
        # "at 09:05, on the 13th, in June, on Mondays" - every field distinct so a
        # transposition cannot hide.
        self.assertEqual(
            parse_cron("5 9 13 6 1"),
            {
                "minute": "5",
                "hour": "9",
                "day_of_month": "13",
                "month_of_year": "6",
                "day_of_week": "1",
            },
        )

    def test_an_expression_without_five_fields_is_rejected_rather_than_indexed(self):
        self.assertIsNone(parse_cron("0 9 *"))
        self.assertIsNone(parse_cron(""))


class ScheduledTriggerBeatEntryTests(TestCase):

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.automation = factories.make_automation(self.tenant)

    def _scheduled(self, config):
        return factories.make_trigger(
            self.automation, trigger_type="scheduled", config=config
        )

    def test_a_weekly_cron_produces_a_weekly_schedule_not_a_yearly_one(self):
        # The regression test for the field-order bug. '0 9 * * 1' is Mondays at
        # 09:00; the old mapping stored it as day_of_week='*', month_of_year='1',
        # i.e. "every day in January at 09:00". Nothing raised - it just ran at the
        # wrong time, forever.
        self._scheduled({"schedule_type": "forever", "cron_expression": "0 9 * * 1"})

        schedule = CrontabSchedule.objects.get(minute="0", hour="9")
        self.assertEqual(schedule.day_of_week, "1")
        self.assertEqual(schedule.month_of_year, "*")
        self.assertEqual(schedule.day_of_month, "*")

    def test_a_day_of_month_cron_is_not_read_as_a_day_of_week(self):
        self._scheduled({"schedule_type": "forever", "cron_expression": "30 6 15 * *"})

        schedule = CrontabSchedule.objects.get(minute="30", hour="6")
        self.assertEqual(schedule.day_of_month, "15")
        self.assertEqual(schedule.day_of_week, "*")

    def test_a_cron_schedule_creates_an_enabled_periodic_task(self):
        self._scheduled({"schedule_type": "forever", "cron_expression": "0 9 * * 1"})

        task = PeriodicTask.objects.get(name=task_name(self.automation.id))
        self.assertTrue(task.enabled)
        self.assertIsNotNone(task.crontab)
        self.assertIsNone(task.clocked)

    def test_beat_is_told_the_run_came_from_the_schedule(self):
        # record_scheduled_run() only spends the repeat budget when the task says the
        # run came from beat, so the kwargs have to carry that.
        self._scheduled({"schedule_type": "forever", "cron_expression": "0 9 * * 1"})

        task = PeriodicTask.objects.get(name=task_name(self.automation.id))
        self.assertIn("scheduled", task.kwargs)

    def test_a_malformed_cron_expression_does_not_take_the_save_down(self):
        # A trigger written outside the API skips croniter validation entirely.
        self._scheduled({"schedule_type": "forever", "cron_expression": "0 9 *"})

        self.assertFalse(
            PeriodicTask.objects.filter(name=task_name(self.automation.id)).exists()
        )


class OneTimeScheduleTests(TestCase):

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.automation = factories.make_automation(self.tenant)
        self.when = timezone.now() + timedelta(days=1)

    def _one_time(self, start_time):
        return factories.make_trigger(
            self.automation,
            trigger_type="scheduled",
            config={"schedule_type": "one_time", "start_time": start_time},
        )

    def test_a_one_time_schedule_produces_a_beat_entry_at_all(self):
        # It produced none. The old handler returned early whenever there was no
        # cron expression, and one_time carries none by definition - so the trigger
        # was accepted, stored, and then never fired.
        self._one_time(self.when.isoformat())

        task = PeriodicTask.objects.get(name=task_name(self.automation.id))
        self.assertTrue(task.one_off)
        self.assertIsNotNone(task.clocked)
        self.assertIsNone(task.crontab)

    def test_the_clocked_time_matches_the_configured_start_time(self):
        self._one_time(self.when.isoformat())

        schedule = ClockedSchedule.objects.get()
        self.assertEqual(schedule.clocked_time, self.when)

    def test_a_naive_start_time_is_made_aware(self):
        naive = (timezone.now() + timedelta(days=2)).replace(tzinfo=None)
        self._one_time(naive.isoformat())

        schedule = ClockedSchedule.objects.get()
        self.assertIsNotNone(timezone.is_aware(schedule.clocked_time))

    def test_an_unparseable_start_time_is_ignored_rather_than_raising(self):
        self._one_time("not a datetime")

        self.assertFalse(
            PeriodicTask.objects.filter(name=task_name(self.automation.id)).exists()
        )

    def test_switching_from_one_time_to_cron_clears_the_clocked_schedule(self):
        # PeriodicTask holds four mutually exclusive schedule kinds; leaving the old
        # one set is how a trigger ends up with two.
        trigger = self._one_time(self.when.isoformat())

        trigger.config = {"schedule_type": "forever", "cron_expression": "0 9 * * 1"}
        trigger.save()

        task = PeriodicTask.objects.get(name=task_name(self.automation.id))
        self.assertIsNone(task.clocked)
        self.assertIsNotNone(task.crontab)
        self.assertFalse(task.one_off)


class RepeatBudgetTests(TestCase):

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.automation = factories.make_automation(self.tenant)

    def _counted(self, count):
        return factories.make_trigger(
            self.automation,
            trigger_type="scheduled",
            config={
                "schedule_type": "n",
                "count": count,
                "cron_expression": "0 9 * * 1",
            },
        )

    def _task(self):
        return PeriodicTask.objects.get(name=task_name(self.automation.id))

    def test_a_counted_schedule_survives_until_its_budget_is_spent(self):
        self._counted(3)

        record_scheduled_run(self.automation.id)
        record_scheduled_run(self.automation.id)

        self.assertTrue(self._task().enabled)

    def test_a_counted_schedule_disables_itself_on_the_last_run(self):
        # config['count'] was validated by the serializer and then read by nothing,
        # so 'n' behaved exactly like 'forever'.
        self._counted(3)

        for _ in range(3):
            record_scheduled_run(self.automation.id)

        self.assertFalse(self._task().enabled)
        self.assertEqual(AutomationTrigger.objects.get().run_count, 3)

    def test_a_forever_schedule_is_never_disabled(self):
        factories.make_trigger(
            self.automation,
            trigger_type="scheduled",
            config={"schedule_type": "forever", "cron_expression": "0 9 * * 1"},
        )

        for _ in range(5):
            record_scheduled_run(self.automation.id)

        self.assertTrue(self._task().enabled)
        self.assertEqual(AutomationTrigger.objects.get().run_count, 0)

    def test_redefining_the_schedule_restarts_the_budget(self):
        # Deliberate semantics, not an accident: saving a scheduled trigger resets
        # the counter and re-enables the entry. The alternative would have been to
        # keep whatever run_count the in-memory instance held, which depends on
        # whether the caller refetched - a coin flip is worse than either answer.
        trigger = self._counted(2)

        record_scheduled_run(self.automation.id)
        record_scheduled_run(self.automation.id)
        self.assertFalse(self._task().enabled)

        trigger.config = {
            "schedule_type": "n",
            "count": 5,
            "cron_expression": "0 9 * * 1",
        }
        trigger.save()

        self.assertTrue(self._task().enabled)
        self.assertEqual(AutomationTrigger.objects.get().run_count, 0)

    def test_a_stale_instance_cannot_silently_rewind_the_counter(self):
        # The bug this class of reset exists to kill: `trigger` below still holds
        # run_count=0 in memory while the database says 2.
        trigger = self._counted(5)

        record_scheduled_run(self.automation.id)
        record_scheduled_run(self.automation.id)
        self.assertEqual(AutomationTrigger.objects.get().run_count, 2)

        # Not a schedule edit at all - but it must still land on a defined value.
        trigger.initial_data = {"x": 1}
        trigger.save()

        self.assertEqual(AutomationTrigger.objects.get().run_count, 0)


class RunSourceTests(TestCase):

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.automation = factories.make_automation(self.tenant)
        factories.make_trigger(
            self.automation,
            trigger_type="scheduled",
            config={
                "schedule_type": "n",
                "count": 2,
                "cron_expression": "0 9 * * 1",
            },
        )
        # A run that SUCCEEDS: a failing one is now retried by the queue, and the
        # budget is deliberately spent on the final return, not on the first
        # attempt. This class is about the source flag, so keep the run final.
        step = factories.make_step(self.automation, key="hook", action_type="webhook")
        factories.make_edge(self.automation, None, step)

    def test_a_manual_run_does_not_spend_the_budget(self):
        with patch("apps.automations.engine.actions.send_webhook.requests.request"):
            run_automation(str(self.automation.id), {})

        self.assertEqual(AutomationTrigger.objects.get().run_count, 0)

    def test_a_beat_driven_run_spends_the_budget(self):
        with patch("apps.automations.engine.actions.send_webhook.requests.request"):
            run_automation(str(self.automation.id), {}, source="scheduled")

        self.assertEqual(AutomationTrigger.objects.get().run_count, 1)


class BeatEntryLifecycleTests(TestCase):

    def setUp(self):
        self.tenant = factories.make_tenant()
        self.automation = factories.make_automation(self.tenant)
        self.trigger = factories.make_trigger(
            self.automation,
            trigger_type="scheduled",
            config={"schedule_type": "forever", "cron_expression": "0 9 * * 1"},
        )

    def test_deactivating_the_automation_disables_the_entry(self):
        self.automation.is_active = False
        self.automation.save()

        self.assertFalse(
            PeriodicTask.objects.get(name=task_name(self.automation.id)).enabled
        )

    def test_deleting_the_trigger_removes_the_entry(self):
        self.trigger.delete()

        self.assertFalse(
            PeriodicTask.objects.filter(name=task_name(self.automation.id)).exists()
        )

    def test_deleting_the_automation_removes_the_entry(self):
        automation_id = self.automation.id
        self.automation.delete()

        self.assertFalse(
            PeriodicTask.objects.filter(name=task_name(automation_id)).exists()
        )
