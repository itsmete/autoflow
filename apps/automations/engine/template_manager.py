from ..models import Template
from .exceptions import TemplateRenderError
from django.utils.translation import gettext_lazy as _
import re

class TemplateManager():


        def fetch(self,template : Template,config,payload) -> dict:
                resolved = {}

                sources = {
                        'config' : config,
                        'payload' : payload,
                }
                
                for field_name, field_def in template.fields.items():
                        
                        source = field_def.get('source')
                       
                        if source == 'ai':
                                continue # TO DO
                        
                        source_data= sources.get(source)
                        
                        if source_data is None:
                                raise TemplateRenderError(
                                        _("Invalid source type: %(source)s") % {"source": source}
                        )


                        value = source_data.get(field_name)
                        
                        
                        if value is None and field_def.get('required'):
                                default = field_def.get('default')
                                if default is not None:
                                        value = default
                                elif field_def.get('required'):
                                        raise TemplateRenderError(
                                                _("Required field %(field)s not found in %(source)s")
                                                % {'field' : field_name , "source" : source}
                                        )


                        if value is not None:
                                resolved['field_name'] = value
                return resolved



        def fill(self, template: Template , resolved_fields):
                result = template.raw_text
                
                def replace_block(match):
                        field_name = match.group(1)
                        content = match.group(2)

                        if field_name in resolved_fields:
                                return content           

                        return ''
                
                result = re.sub(
                        r'\{%if (\w+)%\}(.*?)\{%endif%\}',
                        replace_block,
                        result,
                        flags= re.DOTALL
                )     

                for key,value in resolved_fields.items():
                        result = result.replace('{{' + key + '}}', str(value))

                return result

        def render(self, template: Template , config , payload):
                resolved = self.fetch(template,config,payload)
                return self.fill(template, resolved)