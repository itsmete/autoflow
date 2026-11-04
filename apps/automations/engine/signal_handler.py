import importlib

from collections.abc import Mapping


def _runtime():
        # Lazy: apps.automations.runtime imports Django, DRF and Celery, and the engine
        # must stay importable without them (tests/test_engine_purity.py).
        return importlib.import_module('apps.automations.runtime')


class _RunAutomationProxy:

        """
        Stands in for `apps.automations.tasks.run_automation`.

        The engine may not import celery, but the dispatch itself is a Celery concern.
        Keeping a module-level `run_automation` with a `.delay` method means both
        consumers work unchanged: the real one goes to the runtime adapter, and
        `@patch("apps.automations.engine.signal_handler.run_automation.delay")`
        still patches exactly the object the handler calls.
        """

        def delay(self,automation_id,data):
                return _runtime().dispatch_automation(automation_id,data)


run_automation = _RunAutomationProxy()


def _matches(config,key,value):
        """
        Optional comparison constants on a `field_changed` trigger.

        Absent or null means "any value". The string form is compared as well as the
        raw one because the same config may say `"to_value": "42"` while the column is
        a number - and refusing that would be surprising, not safe.
        """
        if key not in config or config.get(key) is None:
                return True

        expected = config.get(key)
        return value == expected or str(value) == str(expected)


def create_signal_handler(automation_id,event,config = None):

        config = config or {}

        def handler(sender,instance,**kwargs):
                # We have to define created in kwargs , post_save takes 'created' explictly however, 'deleted' not ,
                # not to write a different function for delete, we have to give it in kwargs.
                #

                created = kwargs.get('created',None)

                if event == 'created' and not created:
                        return

                if event == 'updated' and created:
                        return

                if event == 'field_changed':
                        # A new row has no previous value, so a change cannot be
                        # reported for it - `created` already has an event of its own.
                        if created:
                                return

                        previous = getattr(instance,'_autoflow_prev',None)

                        if previous is None:
                                previous = kwargs.get('previous_data')

                        # No snapshot means the old row was never read (a save outside
                        # the wired path, or a sender whose pre_save is not connected).
                        # Guessing "everything changed" from that would fire the
                        # automation on every unrelated save.
                        if not isinstance(previous,Mapping):
                                return

                        field = config.get('field')

                        if not field:
                                return

                        data = _runtime().serialize_model_instance(instance)
                        old_value = previous.get(field)
                        new_value = data.get(field)

                        if old_value == new_value:
                                return

                        if not _matches(config,'from_value',old_value):
                                return

                        if not _matches(config,'to_value',new_value):
                                return

                        # The comparison is the whole event: hand the change over in the
                        # shape an expression reads it, `{{_changes.status.from}}`.
                        data['_changes'] = {field : {'from' : old_value,'to' : new_value}}

                        run_automation.delay(str(automation_id),data)
                        return

                # model_to_dict returns live python objects - UUIDs, dates, Decimals -
                # and CELERY_TASK_SERIALIZER is 'json', so handing them straight to
                # .delay() raises "Object of type UUID is not JSON serializable" and
                # the automation never runs. The runtime serializer round-trips
                # through DRF's encoder, producing the payload the API would have sent.
                data = _runtime().serialize_model_instance(instance)
                run_automation.delay(str(automation_id),data)

        return handler
