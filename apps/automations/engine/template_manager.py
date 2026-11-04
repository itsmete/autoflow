"""
Templates: declare what a template needs, resolve it against the run, substitute it.

Rendering itself lives in expressions.py, which is pure Python and never executes
tenant-authored text. This module used to import the Template model for a type hint
and gettext for two error messages; both are gone, because those two imports were the
only thing standing between it and the engine purity rule
(tests/test_engine_purity.py).

`Template.fields` is now optional. A declared field still gets validation, a default
and an explicit source; an undeclared placeholder is simply resolved from the
execution context, which is what makes `{{customer.address.city}}` and
`{{steps.1.output.id}}` work without a schema entry (ROADMAP section 4.5).
"""
from collections.abc import Mapping

from .exceptions import TemplateRenderError
from .expressions import render_string
from .paths import MISSING, resolve_path


class TemplateManager():


        def _lookup(self,source_data,field_name):
                """
                The value of `field_name` in `source_data`, dotted paths included.

                Declared fields were top-level dict keys only, so a field pointing at
                `customer.name` never resolved. The nested walk comes first and the
                literal key is the fallback, because a field legitimately named
                "a.b" must still mean the key "a.b", not a path.
                """
                if not isinstance(source_data,Mapping):
                        return None

                value = resolve_path(source_data,field_name)

                if value is MISSING:
                        value = source_data.get(field_name)

                return None if value is MISSING else value


        def fetch(self,template,config,payload) -> dict:
                resolved = {}

                sources = {
                        'config' : config,
                        'payload' : payload,
                }

                for field_name,field_def in (template.fields or {}).items():

                        source = field_def.get('source')

                        if source == 'ai':
                                continue # TO DO

                        source_data = sources.get(source)

                        if source_data is None:
                                raise TemplateRenderError(
                                        "Invalid source type: %s" % source
                                )

                        value = self._lookup(source_data,field_name)

                        if value is None and field_def.get('required'):
                                default = field_def.get('default')

                                if default is not None:
                                        value = default
                                else:
                                        raise TemplateRenderError(
                                                "Required field %s not found in %s"
                                                % (field_name,source)
                                        )

                        if value is not None:
                                resolved[field_name] = value

                return resolved


        def fill(self,template,resolved_fields = None,context = None):
                """
                Substitute the fetched fields into the body.

                Values that were fetched override the context: a declared field is a
                promise about where the value comes from, and the declaration wins.
                """
                merged = {}

                if context:
                        merged.update(context)

                if resolved_fields:
                        merged.update(resolved_fields)

                return render_string(template.raw_text,merged)


        def render(self,template,config,payload):
                fetched = self.fetch(template,config,payload)

                # Everything the run knows, weakest first: the payload and config stay
                # in scope so undeclared placeholders resolve straight through, and the
                # declared-and-fetched values sit on top.
                merged = {}
                merged.update(payload or {})
                merged.update(config or {})
                merged.update(fetched)

                return self.fill(template,fetched,context = merged)
