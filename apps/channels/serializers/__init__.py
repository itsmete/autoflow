from .email import EmailProviderSerializer
from .instagram import InstagramProviderSerializer
from .sms import SMSProviderSerializer
from .whatsapp import WhatsAppProviderSerializer

PROVIDER_SERIALIZERS = {
        'email' : EmailProviderSerializer,       
        'instagram' : InstagramProviderSerializer,       
        'sms' : SMSProviderSerializer,       
        'whatsapp' : WhatsAppProviderSerializer,       
}


def get_serializer_class(serializer_name):
        return PROVIDER_SERIALIZERS.get(serializer_name)