"""

        AutomationStepWriteSerializer
        AutomationStepReadSerializer
        AutomationEdgeWriteSerializer
        AutomationEdgeReadSerializer

        validate_step_config()  -> polymorphic, schema-driven step validation
        validate_step_graph()   -> the graph itself: keys, edges, cycles, reachability

The graph is the reason Faz 1.B exists (ROADMAP §5.1). A step is one carrier for
every kind of node the flow can hold, so its `config` is only meaningful together
with `step_type` - exactly the situation the trigger/action serializers already
solved by validating against a per-type schema instead of declaring fields.

Two things are deliberately NOT here:

- the engine. `apps/automations/engine/` must stay importable without Django
  (tests/test_engine_purity.py), so nothing in this module may leak into it.
- AutomationConditionSerializer, at module level. automations.py imports this
  module for the step serializers, so importing back from it would close the
  cycle; the condition serializer is imported inside the validator instead.

"""

import re

from django.utils import dateparse
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from apps.channels.serializers.channel import ChannelReadSerializer

from ..models import ActionTypes,AutomationEdge,AutomationStep,StepTypes
from .actions import get_action_serializer


# Actions that cannot do anything without a Channel to read credentials from. Same
# set AutomationActionWriteSerializer enforces; kept in one place so the legacy and
# the graph path cannot disagree about which action types need a channel.
CHANNEL_REQUIRED_ACTIONS = {'email', 'sms', 'whatsapp', 'instagram'}

# "30s", "15m", "24h", "7d". A bare integer is seconds; a bare string is not a
# duration, and the engine must never guess what "30" meant.
WAIT_DURATION_PATTERN = re.compile(r'^\d+[smhd]$')

# "{{trigger.due_at}}" - resolved at runtime against the execution context.
EXPRESSION_PATTERN = re.compile(r'^\{\{.+\}\}$')


def _validate_conditions(conditions,label):
        """
        A list of condition dicts, validated with the same serializer the automation
        level conditions go through.

        Order matters (logical_operator groups by reading order), so the list is the
        only thing that carries it here - there is no `order` column for conditions
        embedded in a step config.
        """
        # Local import: see the module docstring.
        from .automations import AutomationConditionSerializer

        if not isinstance(conditions,list) or not conditions:
                raise serializers.ValidationError(
                        _("%(label)s için en az bir koşul gereklidir") % {'label' : label}
                )

        for item in conditions:
                if not isinstance(item,dict):
                        raise serializers.ValidationError(
                                _("%(label)s koşulları sözlük olmalıdır") % {'label' : label}
                        )

                # raise_exception, because is_valid() returning False here would let
                # an unvalidated condition through - the same mistake that once made
                # every trigger config validate nothing at all (AGENTS §7).
                AutomationConditionSerializer(data = item).is_valid(raise_exception = True)

        return conditions


def _validate_action_config(config,action_type,channel,template):
        if not action_type:
                raise serializers.ValidationError(
                        _("Aksiyon adımı için action_type zorunludur")
                )

        if action_type not in ActionTypes.values:
                raise serializers.ValidationError(
                        _("Geçersiz aksiyon tipi: %(action_type)s") % {'action_type' : action_type}
                )

        serializer_cls = get_action_serializer(action_type)

        if not serializer_cls:
                raise serializers.ValidationError(_("Invalid action type"))

        if action_type in CHANNEL_REQUIRED_ACTIONS and not channel:
                raise serializers.ValidationError(
                        _("Channel is required for this action type")
                )

        if channel is not None and channel.channel_type != action_type:
                raise serializers.ValidationError(
                        _("Channel type doesn't match with action type")
                )

        if template is not None and template.channel_type != action_type:
                raise serializers.ValidationError(
                        _("Template channel type doesn't match with action type")
                )

        # validate_config, not serializer_cls(data=config).is_valid(): the latter
        # binds no fields on BaseConfigSerializer, so nothing is validated and the
        # config comes back empty (AGENTS §7).
        return serializer_cls().validate_config(config)


def _validate_filter_config(config):
        if 'conditions' not in config:
                raise serializers.ValidationError(
                        _("Filtre adımı 'conditions' alanını taşımalıdır")
                )

        _validate_conditions(config.get('conditions'),_("Filtre"))


def _validate_branch_config(config):
        branches = config.get('branches')

        if not isinstance(branches,list) or not branches:
                raise serializers.ValidationError(
                        _("Dallanma adımı en az bir dal taşımalıdır")
                )

        seen = set()

        for branch in branches:
                if not isinstance(branch,dict):
                        raise serializers.ValidationError(
                                _("Her dal bir sözlük olmalıdır")
                        )

                key = branch.get('key')

                if not isinstance(key,str) or not key.strip():
                        raise serializers.ValidationError(
                                _("Her dalın boş olmayan bir 'key' değeri olmalıdır")
                        )

                # Edges point at a branch by this key, so a duplicate would make the
                # outgoing edge ambiguous rather than merely untidy.
                if key in seen:
                        raise serializers.ValidationError(
                                _("Dal anahtarları benzersiz olmalıdır: %(key)s") % {'key' : key}
                        )

                seen.add(key)

                if 'conditions' not in branch:
                        raise serializers.ValidationError(
                                _("'%(key)s' dalı 'conditions' alanını taşımalıdır") % {'key' : key}
                        )

                _validate_conditions(branch.get('conditions'),_("'%(key)s' dalı") % {'key' : key})

        default = config.get('default')

        if default is not None and not isinstance(default,str):
                raise serializers.ValidationError(
                        _("Dallanma adımının 'default' değeri metin olmalıdır")
                )


def _validate_wait_duration(duration):
        if isinstance(duration,bool):
                # bool is an int subclass; True would otherwise pass as 1 second.
                raise serializers.ValidationError(
                        _("Bekleme süresi pozitif bir tam sayı ya da '30s'/'15m'/'24h'/'7d' biçiminde olmalıdır")
                )

        if isinstance(duration,int):
                if duration <= 0:
                        raise serializers.ValidationError(
                                _("Bekleme süresi pozitif olmalıdır")
                        )
                return

        if isinstance(duration,str) and WAIT_DURATION_PATTERN.match(duration.strip()):
                return

        raise serializers.ValidationError(
                _("Bekleme süresi pozitif bir tam sayı ya da '30s'/'15m'/'24h'/'7d' biçiminde olmalıdır")
        )


def _validate_wait_until(until):
        if not isinstance(until,str) or not until.strip():
                raise serializers.ValidationError(
                        _("'until' boş olmayan bir tarih ya da {{...}} ifadesi olmalıdır")
                )

        until = until.strip()

        if EXPRESSION_PATTERN.match(until):
                return

        if dateparse.parse_datetime(until) is not None:
                return

        if dateparse.parse_date(until) is not None:
                return

        raise serializers.ValidationError(
                _("'until' ISO-8601 bir tarih ya da {{...}} ifadesi olmalıdır")
        )


def _validate_wait_config(config):
        # Either an offset or an absolute moment, never both silently: if 'duration'
        # is present but broken, saying so is better than falling through to 'until'.
        if 'duration' in config:
                _validate_wait_duration(config.get('duration'))
                return

        if 'until' in config:
                _validate_wait_until(config.get('until'))
                return

        raise serializers.ValidationError(
                _("Bekleme adımı 'duration' ya da 'until' alanlarından birini taşımalıdır")
        )


def _validate_set_variable_config(config):
        variables = config.get('variables')

        if not isinstance(variables,dict) or not variables:
                raise serializers.ValidationError(
                        _("Değişken atama adımı boş olmayan bir 'variables' sözlüğü taşımalıdır")
                )

        for name in variables:
                if not isinstance(name,str) or not name.strip():
                        raise serializers.ValidationError(
                                _("Değişken adı boş olmayan bir metin olmalıdır")
                        )


def validate_step_config(step_type,config,action_type = '',channel = None,template = None):
        """
        Validate one step's `config` against its `step_type`.

        Returns the (possibly normalized) config: the action branch fills in the
        defaults the action schema declares, so what gets stored is what the provider
        will later read. Everything else is returned untouched.

        Raises serializers.ValidationError - never returns False. A validator that
        rejects by returning False is silently ignored by every caller that only
        catches exceptions (AGENTS §7).
        """
        if not isinstance(config,dict):
                raise serializers.ValidationError(
                        _("Adım ayarları bir sözlük olmalıdır")
                )

        if step_type == StepTypes.ACTION:
                return _validate_action_config(config,action_type,channel,template)

        if step_type == StepTypes.FILTER:
                _validate_filter_config(config)
                return config

        if step_type == StepTypes.BRANCH:
                _validate_branch_config(config)
                return config

        if step_type == StepTypes.WAIT:
                _validate_wait_config(config)
                return config

        if step_type == StepTypes.SET_VARIABLE:
                _validate_set_variable_config(config)
                return config

        # loop, run_automation, human_approval and the AI/MCP steps have no schema
        # yet; their executors arrive in Faz 1.D. Refusing them now would only mean
        # adding them back one migration at a time.
        return config


def _declared_branch_keys(step):
        """
        Which branch_keys an outgoing edge of `step` may carry.

        'default' is always legal: it is the fall-through of a branch that matched
        nothing, and it does not have to be declared to be reachable.
        """
        keys = {'default'}

        config = step.get('config') or {}

        default = config.get('default')
        if isinstance(default,str) and default.strip():
                keys.add(default)

        for branch in config.get('branches') or []:
                if isinstance(branch,dict) and isinstance(branch.get('key'),str):
                        keys.add(branch['key'])

        return keys


def _has_cycle(adjacency):
        """
        Iterative DFS in three colours (white/grey/black).

        Iterative rather than recursive because the graph is user input: a deep chain
        would hit the interpreter's recursion limit and surface as a 500 instead of a
        400. Grey-to-grey is the back edge, i.e. the cycle.
        """
        WHITE,GREY,BLACK = 0,1,2
        colour = {node : WHITE for node in adjacency}

        for start in adjacency:
                if colour[start] != WHITE:
                        continue

                colour[start] = GREY
                stack = [(start,iter(adjacency[start]))]

                while stack:
                        node,children = stack[-1]
                        descended = False

                        for child in children:
                                if colour[child] == GREY:
                                        return (node,child)

                                if colour[child] == WHITE:
                                        colour[child] = GREY
                                        stack.append((child,iter(adjacency[child])))
                                        descended = True
                                        break

                        if not descended:
                                colour[node] = BLACK
                                stack.pop()

        return None


def validate_step_graph(steps_data,edges_data):
        """
        Validate a submitted step graph and return its normalized edges.

        Everything here is a property of the WHOLE graph, which is why it cannot live
        on the step serializer: a step is valid on its own and still part of a flow
        that can never run (an orphan, a cycle, two steps with one name).

        When `edges` is omitted for a non-branch flow it is derived: one entry edge
        into the first step, then a chain. That is the same shape
        sync_steps_from_actions() builds, so `steps` alone behaves exactly like the
        legacy `actions` list.
        """
        keys = []
        seen = set()

        for step in steps_data:
                key = step.get('key')

                if key in seen:
                        raise serializers.ValidationError({
                                'steps' : _("Adım anahtarları benzersiz olmalıdır: %(key)s") % {'key' : key}
                        })

                seen.add(key)
                keys.append(key)

        if not keys:
                if edges_data:
                        raise serializers.ValidationError({
                                'edges' : _("Adım tanımlanmadan bağlantı tanımlanamaz")
                        })
                return []

        if edges_data is None:
                if any(step.get('step_type') == StepTypes.BRANCH for step in steps_data):
                        raise serializers.ValidationError({
                                'edges' : _("Dallanma adımı içeren bir akış için bağlantılar açıkça verilmelidir")
                        })

                edges_data = [
                        {
                                'from_step' : keys[index - 1] if index else None,
                                'to_step' : key,
                                'branch_key' : '',
                                'order' : index,
                        }
                        for index,key in enumerate(keys)
                ]

        step_map = {step.get('key') : step for step in steps_data}

        normalized = []

        for index,edge in enumerate(edges_data):
                from_key = edge.get('from_step') or None
                to_key = edge.get('to_step')
                branch_key = edge.get('branch_key') or ''

                if from_key is not None and from_key not in step_map:
                        raise serializers.ValidationError({
                                'edges' : _("Bilinmeyen kaynak adım: %(key)s") % {'key' : from_key}
                        })

                if to_key not in step_map:
                        raise serializers.ValidationError({
                                'edges' : _("Bilinmeyen hedef adım: %(key)s") % {'key' : to_key}
                        })

                if from_key is not None and from_key == to_key:
                        raise serializers.ValidationError({
                                'edges' : _("Bir adım kendisine bağlanamaz: %(key)s") % {'key' : from_key}
                        })

                if branch_key and from_key is not None:
                        source = step_map[from_key]

                        if source.get('step_type') == StepTypes.BRANCH:
                                if branch_key not in _declared_branch_keys(source):
                                        raise serializers.ValidationError({
                                                'edges' : _("'%(key)s' dalı '%(branch_key)s' adımında tanımlı değil") % {
                                                        'key' : from_key,
                                                        'branch_key' : branch_key,
                                                }
                                        })

                normalized.append({
                        'from_step' : from_key,
                        'to_step' : to_key,
                        'branch_key' : branch_key,
                        'order' : edge.get('order',index),
                })

        entry_keys = [edge['to_step'] for edge in normalized if edge['from_step'] is None]

        if not entry_keys:
                # Without one, the flow is a closed loop of steps that nothing starts.
                raise serializers.ValidationError({
                        'edges' : _("Akışın bir giriş bağlantısı olmalıdır (from_step = null)")
                })

        adjacency = {key : [] for key in keys}
        for edge in normalized:
                if edge['from_step'] is not None:
                        adjacency[edge['from_step']].append(edge['to_step'])

        cycle = _has_cycle(adjacency)

        if cycle is not None:
                raise serializers.ValidationError({
                        'edges' : _("Akışta döngü var: %(node)s -> %(child)s") % {
                                'node' : cycle[0],
                                'child' : cycle[1],
                        }
                })

        reachable = set()
        queue = list(entry_keys)

        while queue:
                node = queue.pop()
                if node in reachable:
                        continue
                reachable.add(node)
                queue.extend(adjacency[node])

        orphans = [key for key in keys if key not in reachable]

        if orphans:
                raise serializers.ValidationError({
                        'edges' : _("Akışta erişilemeyen adım(lar) var: %(keys)s") % {'keys' : ', '.join(orphans)}
                })

        return normalized


class AutomationStepWriteSerializer(serializers.ModelSerializer):

        key = serializers.CharField(max_length = 64)

        class Meta:
                model = AutomationStep
                fields = ['key','step_type','action_type','config','channel','template','on_failure','position']

        def validate_key(self,value):
                value = value.strip()

                if not value:
                        raise serializers.ValidationError(_("Adım anahtarı boş olamaz"))

                return value

        def validate(self,attrs):
                step_type = attrs.get('step_type',StepTypes.ACTION)
                config = attrs.get('config')

                if config is None:
                        config = {}

                attrs['config'] = validate_step_config(
                        step_type,
                        config,
                        action_type = attrs.get('action_type',''),
                        channel = attrs.get('channel'),
                        template = attrs.get('template'),
                )

                return attrs


class AutomationStepReadSerializer(serializers.ModelSerializer):

        channel = ChannelReadSerializer(read_only = True)

        class Meta:
                model = AutomationStep
                fields = ['id','key','step_type','action_type','config','channel','template','on_failure','position']
                # A list, never '__all__': DRF requires a list/tuple here.
                read_only_fields = fields


class AutomationEdgeWriteSerializer(serializers.Serializer):

        # from_step is a KEY, not a pk: the client names the step it means ("send_sms"),
        # and the graph resolves it once, in one place.
        from_step = serializers.CharField(required = False,allow_null = True,allow_blank = True,default = None)
        to_step = serializers.CharField(max_length = 64)
        branch_key = serializers.CharField(max_length = 64,required = False,allow_blank = True,default = '')
        order = serializers.IntegerField(required = False,default = 0,min_value = 0)

        def validate_from_step(self,value):
                # "" is how a canvas says "the start of the flow"; the column spells
                # that None, and letting both through would make every edge check a
                # two-case comparison.
                return value or None

        def validate_to_step(self,value):
                value = value.strip()

                if not value:
                        raise serializers.ValidationError(_("Hedef adım anahtarı boş olamaz"))

                return value


class AutomationEdgeReadSerializer(serializers.ModelSerializer):

        from_step = serializers.SlugRelatedField(slug_field = 'key',read_only = True)
        to_step = serializers.SlugRelatedField(slug_field = 'key',read_only = True)

        class Meta:
                model = AutomationEdge
                fields = ['id','from_step','to_step','branch_key','order']
                read_only_fields = fields
