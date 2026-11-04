# This is called polymorphic serializer, 
# Each action needs different fields, and serializer too
# and we assign a serializer for each of them.
from .email import EmailActionSerializer
from .webhook import WebhookActionSerializer
from .whatsapp import WhatsappActionSerializer
from .sms import SMSActionSerializer
from .instagram import InstagramDMActionSerializer
from .http_request import HttpRequestActionSerializer

ACTION_SERIALIZERS =   {
        'email' : EmailActionSerializer,
        'whatsapp' : WhatsappActionSerializer,
        'webhook' : WebhookActionSerializer,
        'sms'  : SMSActionSerializer,
        'instagram' : InstagramDMActionSerializer,
        'http_request' : HttpRequestActionSerializer
}

def get_action_serializer(action_type):
        return ACTION_SERIALIZERS.get(action_type)
