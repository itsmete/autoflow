from abc import abstractmethod
from collections.abc import Mapping

from ..exceptions import ExecutionError
from ..expressions import resolve_value
from ..template_manager import TemplateManager


class BaseAction:
        provider_class = None
        requires_channel = False

        # The namespaces of the execution context (ROADMAP section 4.5). They are
        # copied into `prepared` so an expression can reach them, but they are about
        # the RUN and not about the message: recording them in a step's output would
        # copy the whole run into every step, and the next step's output on top.
        CONTEXT_NAMESPACES = (
                'trigger','steps','vars','automation','tenant','branch',
        )


        def __init__(self ,action_model):
                self.action_model = action_model
                self.template_manager = TemplateManager()

        def prepare(self,payload) -> dict:
                config = getattr(self.action_model,'config',None) or {}

                # Expressions inside the config - `{{trigger.customer.email}}`, a
                # `{%if %}...{%endif %}` block - are resolved against whatever the
                # caller passed, and native types are kept, so a step can send the
                # number 5 to a provider instead of the string "5". Without this a
                # step could only ever send the literal text of its own config, which
                # is what made passing data between steps impossible (section 4.5).
                if isinstance(payload,Mapping):
                        result = resolve_value(dict(config),payload)
                else:
                        result = dict(config)

                if isinstance(payload,Mapping):
                        for key in payload:
                                if key not in result:
                                        result[key] = payload[key]

                        # Also flattened. The graph hands over the whole execution
                        # context, while providers speak flat payloads and read
                        # `to` / `subject` / `body` / `message` off the top level
                        # (see channels/providers). One copy of the trigger is what
                        # makes an action work identically whether it was reached
                        # from the legacy executor or from a graph step.
                        trigger = payload.get('trigger')
                        if isinstance(trigger,Mapping):
                                for key in trigger:
                                        if key not in result:
                                                result[key] = trigger[key]


                if self.action_model.template:
                        rendered = self.template_manager.render(
                                self.action_model.template,
                                result,
                                payload
                        )
                        result['message'] = rendered

                        # Email providers read `body`, WhatsApp and SMS read
                        # `message`. A rendered template is both, and a template that
                        # declared its own `body` keeps it.
                        if result.get('body') is None:
                                result['body'] = rendered

                return result
        

        
        def execute(self, prepared_data) -> bool:
                if self.requires_channel:
                        channel = self.action_model.channel
                        if not channel:
                                raise ExecutionError(
                                "Channel is required for this action"
                                )
                        creds = channel.credentials
                        try:
                                # provider.send(credentials, payload) - one dict, not
                                # keyword arguments. Splatting prepared_data raised
                                # TypeError on every channel-backed action.
                                self.provider_class().send(creds, prepared_data)
                        except Exception:
                                raise ExecutionError(
                                "An error occurred while executing action %s"
                                % str(self.action_model.id)
                                )
                else:
                        # Honour what the subclass reports instead of always claiming
                        # success; the executor's retry logic reads this return value.
                        return bool(self._execute_without_channel(prepared_data))

                return True
        
        @abstractmethod
        def _execute_without_channel(prepared_data):
                pass

        def run(self,payload):
                prepared = self.prepare(payload)
                return self.execute(prepared)


        def run_with_output(self,payload) -> dict:
                """
                Run once and report what happened, for `context['steps'][key]`.

                A bool cannot answer "what did step 1 actually send", and step output
                is what makes `{{steps.1.output.message}}` in the next step possible
                (ROADMAP section 4.5). `prepared` is the resolved payload the provider
                saw, minus the run namespaces, so it is both the audit record and a
                stable thing to point an expression at.
                """
                prepared = self.prepare(payload)

                ok = self.execute(prepared)

                return {
                        'sent' : bool(ok),
                        'action_type' : getattr(self.action_model,'action_type',''),
                        'message' : prepared.get('message'),
                        'prepared' : {
                                key : value
                                for key,value in prepared.items()
                                if key not in self.CONTEXT_NAMESPACES
                        },
                }
