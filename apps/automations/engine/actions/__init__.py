from .send_email import SendEmail
from .send_webhook import SendWebhook

ACTION_CLASSES = {
        'email' : SendEmail,
        'webhook' : SendWebhook
}

def get_action_class(action_class):
        return ACTION_CLASSES.get(action_class)
