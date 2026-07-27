from django.forms.models import model_to_dict
from ..tasks import run_automation

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
                
                
                data = model_to_dict(instance)
                run_automation.delay(automation_id,data)

        return handler