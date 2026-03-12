# This is called polymorphic serializer, 
# Each action needs different fields, and serializer too
# and we assign a serializer for each of them.
from .email import EmailActionSerializer
from .webhook import WebhookActionSerializer

ACTION_SERIALIZERS =   {
        'email' : EmailActionSerializer,
        # 'whatsapp' : WhatsappActionSerializer,
        'webhook' : WebhookActionSerializer,
        # 'sms'  : SMSActionSerializer
}

def get_action_serializer(action_type):
        return ACTION_SERIALIZERS.get(action_type)
