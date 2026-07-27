from .email import EmailProvider
from .instagram import InstagramProvider
from .whatsapp import WhatsAppProvider
from .sms import SMSProvider

PROVIDERS = {
        'email' : EmailProvider,       
        'instagram' : InstagramProvider,       
        'sms' : SMSProvider,       
        'whatsapp' : WhatsAppProvider,       
}


def get_provider_class(provider_name):
        return PROVIDERS.get(provider_name)