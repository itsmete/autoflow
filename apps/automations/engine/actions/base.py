from ..template_manager import TemplateManager
from ..exceptions import ExecutionError
from abc import abstractmethod
from django.utils.translation import gettext_lazy as _ 

class BaseAction:
        provider_class = None
        requires_channel = False


        def __init__(self ,action_model):
                self.action_model = action_model
                self.template_manager = TemplateManager()

        def prepare(self,payload) -> str:
                result = {**self.action_model.config}
                if payload:
                        for key in payload:
                                if key not in result:
                                        result[key] = payload[key]


                if self.action_model.template:
                        rendered = self.template_manager.render(
                                self.action_model.template,
                                self.action_model.config,
                                payload
                        )
                        result['message'] = rendered
                
                return result
        

        
        def execute(self, prepared_data) -> bool:
                if self.requires_channel:
                        channel = self.action_model.channel
                        if not channel:
                                raise ExecutionError(
                                _("Channel is required for this action")
                                )
                        creds = channel.credentials
                        try:
                                self.provider_class().send(creds, **prepared_data)
                        except Exception:
                                raise ExecutionError(
                                _("An error occurred while executing action %(id)s")
                                % {"id": str(self.action_model.id)}
                                )
                else:
                        self._execute_without_channel(prepared_data)

                return True
        
        @abstractmethod
        def _execute_without_channel(prepared_data):
                pass

        def run(self,payload):
                prepared = self.prepare(payload)
                return self.execute(prepared)
