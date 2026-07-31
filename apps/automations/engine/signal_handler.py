from django.forms.models import model_to_dict
from rest_framework.utils.encoders import JSONEncoder
from ..tasks import run_automation
import json


def create_signal_handler(automation_id,event):

        def handler(sender,instance,**kwargs):
                # We have to define created in kwargs , post_save takes 'created' explictly however, 'deleted' not ,
                # not to write a different function for delete, we have to give it in kwargs.
                #

                created = kwargs.get('created',None)
                if event == 'created' and not created:
                        return

                if event == 'updated' and created:
                        return


                # model_to_dict returns live python objects - UUIDs, dates, Decimals -
                # and CELERY_TASK_SERIALIZER is 'json', so handing them straight to
                # .delay() raises "Object of type UUID is not JSON serializable" and
                # the automation never runs. Round-tripping through DRF's encoder
                # produces the same payload the API would have sent.
                data = json.loads(json.dumps(model_to_dict(instance),cls=JSONEncoder))
                run_automation.delay(str(automation_id),data)

        return handler
