from .scheduled import ScheduledTriggerSerializer
from .webhook import WebhookTriggerSerializer
from .manual import ManualTriggerSerializer
from .signal import SignalTriggerSerializer

TRIGGER_SERIALIZERS =  {
        'scheduled' : ScheduledTriggerSerializer, 
        'manual' : ManualTriggerSerializer,
        'webhook' : WebhookTriggerSerializer,
        'signal' : SignalTriggerSerializer

}

def get_trigger_serializer_class(trigger_type):
        return TRIGGER_SERIALIZERS.get(trigger_type)        
