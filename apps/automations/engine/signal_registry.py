from django.db.models.signals import post_save,post_delete

# event map for  automationTrigger Model TYPE : EVENT 

SIGNAL_EVENT_MAP = {
        'created' : post_save,
        'updated' : post_save,
        'deleted' : post_delete
}

def get_signal_for_event(singal_event):
        return SIGNAL_EVENT_MAP.get(singal_event)