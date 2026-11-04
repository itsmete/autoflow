import importlib

# event map for  automationTrigger Model TYPE : EVENT
#
# The VALUES are Django signal names, not signal objects: importing
# django.db.models.signals here would make the engine unusable outside a configured
# Django project (tests/test_engine_purity.py). The names are resolved to the real
# signals by apps/automations/runtime.py, which is allowed to know Django.
#
# `field_changed` rides on post_save like `updated` does; the difference is in the
# handler, which compares a pre_save snapshot of the row against the new values.
SIGNAL_EVENT_MAP = {
        'created' : 'post_save',
        'updated' : 'post_save',
        'field_changed' : 'post_save',
        'deleted' : 'post_delete',
}

def get_signal_for_event(singal_event):
        signal_name = SIGNAL_EVENT_MAP.get(singal_event)

        if signal_name is None:
                return None

        return importlib.import_module('apps.automations.runtime').get_django_signal(signal_name)
