from .scheduled import ScheduledTriggerSerializer
from .webhook import WebhookTriggerSerializer
from .manual import ManualTriggerSerializer
from .signal import SignalTriggerSerializer
from .automation_completed import AutomationCompletedTriggerSerializer

TRIGGER_SERIALIZERS =  {
        'scheduled' : ScheduledTriggerSerializer, 
        'manual' : ManualTriggerSerializer,
        'webhook' : WebhookTriggerSerializer,
        'signal' : SignalTriggerSerializer,
        'automation_completed' : AutomationCompletedTriggerSerializer

}

def get_trigger_serializer_class(trigger_type):
        return TRIGGER_SERIALIZERS.get(trigger_type)
