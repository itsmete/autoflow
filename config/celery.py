import os 
from celery import Celery



os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings.development')


app = Celery('autoflow')

#  All configurations starting with 'CELERY_*' is loaded
app.config_from_object('django.conf:settings',namespace='CELERY')

app.autodiscover_tasks()





