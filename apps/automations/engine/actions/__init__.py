from .send_email import SendEmail
from .send_webhook import SendWebhook
from .send_insta_dm import SendInstagramDM
from .send_wp_message import SendWhatsappMessage
from .send_sms import SendSMS

ACTION_CLASSES = {
        'email' : SendEmail,
        'webhook' : SendWebhook,
        'instagram' : SendInstagramDM,
        'whatsapp' : SendWhatsappMessage,
        'sms' : SendSMS

}

def get_action_class(action_class):
        return ACTION_CLASSES.get(action_class)
