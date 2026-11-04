from django.db import models, transaction
from django.utils.translation import gettext_lazy as _
from core.models import BaseModel
import secrets
from apps.channels.models import ChannelTypes
from .engine import operators as ops
"""

Automation FLow : Trigger -> Condition -> Action 

Automation
        name
        tenant + branch (opt)
        is_active
        metadata

AutomationTrigger
        automation -> FK
        trigger_type -> (scheduled,manual,webbook,event)
        config -> JSONField (cron expression ,webhook url , event name)


AutomationCondition
        automation ->FK
        field
        operator (eq,gt,ls,contains)
        value

AutomationAction
        automation -> fk
        action_type -> email whatsapp webhook sms
        order -> Integer field (actions work in order)
        config -> JSONField (to,template,url)

        
AutomationLog
        Automation -> fk
        status -> (success,failed,pending)
        triggered_at
        error_message


"""




class TriggerType(models.TextChoices):
        SCHEDULED = 'scheduled', 'Zamanlanmış'
        MANUAL = 'manual','Manuel',
        WEBHOOK = 'webhook','Webhook'
        SIGNAL = 'signal', 'Sinyal'


class ConditionOperators(models.TextChoices):
        # The VALUES live in engine/operators.py, which is where they are actually
        # applied. Spelling them twice is how the two drift apart; and the dependency
        # has to point this way - the Django layer may know about the engine, the
        # engine may not know about Django (tests/test_engine_purity.py).
        EQUALS           = ops.EQUALS, 'Eşittir'
        NOT_EQUALS       = ops.NOT_EQUALS, 'Eşit Değildir'
        GREATER          = ops.GREATER, 'Büyüktür'
        GREATER_OR_EQUAL = ops.GREATER_OR_EQUAL, 'Büyük veya Eşittir'
        LESS             = ops.LESS, 'Küçüktür'
        LESS_OR_EQUAL    = ops.LESS_OR_EQUAL, 'Küçük veya Eşittir'

        CONTAINS         = ops.CONTAINS, 'İçerir'
        NOT_CONTAINS     = ops.NOT_CONTAINS, 'İçermez'
        STARTS_WITH      = ops.STARTS_WITH, 'İle Başlar'
        ENDS_WITH        = ops.ENDS_WITH, 'İle Biter'
        MATCHES          = ops.MATCHES, 'Desene Uyar'

        IN               = ops.IN, 'Listede'
        NOT_IN           = ops.NOT_IN, 'Listede Değil'
        BETWEEN          = ops.BETWEEN, 'Aralıkta'

        EXISTS           = ops.EXISTS, 'Mevcut'
        NOT_EXISTS       = ops.NOT_EXISTS, 'Mevcut Değil'
        IS_EMPTY         = ops.IS_EMPTY, 'Boş'
        IS_NOT_EMPTY     = ops.IS_NOT_EMPTY, 'Boş Değil'

        WITHIN_LAST      = ops.WITHIN_LAST, 'Son ... İçinde'
        OLDER_THAN       = ops.OLDER_THAN, 'Şundan Eski'


class Quantifier(models.TextChoices):
        # Turns a wildcard path into a question about a collection:
        # field='items[*].stock', quantifier='any', operator='lt', value=1
        SINGLE = ops.QUANTIFIER_NONE_SET, 'Tek değer'
        ANY    = ops.QUANTIFIER_ANY, 'Herhangi biri'
        ALL    = ops.QUANTIFIER_ALL, 'Hepsi'
        NONE   = ops.QUANTIFIER_NO, 'Hiçbiri'


class MissingFieldPolicy(models.TextChoices):
        # Raising was the only behaviour, so one optional field could fail an entire
        # automation.
        FAIL  = ops.ON_MISSING_FAIL, 'Hata ver'
        FALSE = ops.ON_MISSING_FALSE, 'Yanlış say'
        TRUE  = ops.ON_MISSING_TRUE, 'Doğru say'

class ActionTypes(models.TextChoices):
        EMAIL = 'email' ,'E-posta'
        WHATSAPP = 'whatsapp' , 'Whatsapp'
        INSTAGRAM = 'instagram' ,'Instagram'
        WEBHOOK = 'webhook' , 'Webhook'
        SMS = 'sms' ,'SMS'

class OnFailureChoices(models.TextChoices):
        STOP = 'stop' , 'Durdur'
        CONTINUE = 'continue' , 'Devam Et'
        RETRY = 'retry' , 'Yeniden dene'


class StepTypes(models.TextChoices):
        # One carrier for every kind of step the graph can hold. 'action' is the
        # AutomationAction special case, the rest are the reason the graph exists:
        # branching, waiting and the AI/MCP steps cannot be expressed as a list.
        ACTION = 'action', _("Aksiyon")
        BRANCH = 'branch', _("Dallanma")
        WAIT = 'wait', _("Bekleme")
        FILTER = 'filter', _("Filtre")
        SET_VARIABLE = 'set_variable', _("Değişken Ata")
        LOOP = 'loop', _("Döngü")
        RUN_AUTOMATION = 'run_automation', _("Alt Otomasyon")
        HUMAN_APPROVAL = 'human_approval', _("İnsan Onayı")
        AI_GENERATE = 'ai_generate', _("AI İçerik Üretimi")
        AI_CLASSIFY = 'ai_classify', _("AI Sınıflandırma")
        AI_AGENT = 'ai_agent', _("AI Ajan")
        MCP_CALL = 'mcp_call', _("MCP Çağrısı")


class AutomationStatus(models.TextChoices):
        SUCCESS = 'success' , 'Başarılı'
        FAILURE = 'failure' , 'Başarısız'
        PENDING = 'pending' , 'Bekliyor'
        SKIPPED = 'skipped' , 'Atlandı'


class LogicalOperator(models.TextChoices):
    AND = ops.AND, 'Ve'
    OR = ops.OR, 'Veya'



class Automation(BaseModel):
        name = models.CharField(max_length=128)
        tenant = models.ForeignKey('tenants.Tenant',on_delete=models.SET_NULL,null=True,blank=True,related_name='automations')
        branch = models.ForeignKey('tenants.Branch',on_delete=models.SET_NULL,null=True,blank=True,related_name='automations')
        # cross-app references need app name as a prefix

        allowed_roles = models.JSONField() # branch_manager , owner etc.

        metadata = models.JSONField()



class AutomationTrigger(BaseModel):
        automation = models.ForeignKey('Automation',on_delete=models.CASCADE,related_name='triggers')
        trigger_type = models.CharField(choices=TriggerType)
        config = models.JSONField()

        initial_data = models.JSONField(null=True)

        # How many times beat has fired this trigger. Only a 'scheduled' trigger of
        # type 'n' reads it, to know when its repeat budget is spent - config['count']
        # was validated and then never compared against anything, so 'n' behaved
        # exactly like 'forever'. Runs started manually or by webhook do not count.
        run_count = models.PositiveIntegerField(default=0)

        #Model save() is overrided (instead of serializer), since the creation of secret key
        # is a model level rule, that should be in models
        # Serializers are in API level, whoever do not use API , API rules are neglected
        def save(self,*args ,**kwargs):
                if self.trigger_type == 'webhook' and not self.config.get('secret_key'):
                        self.config['secret_key'] = secrets.token_urlsafe(32) 
                super().save(*args,**kwargs)


class AutomationCondition(BaseModel):
        automation = models.ForeignKey('Automation',on_delete=models.CASCADE,related_name='conditions')

        # A path into the payload, not just a key: 'order_amount', but also
        # 'customer.address.city' or 'items[*].stock'. See engine/paths.py.
        field = models.CharField(max_length=200)

        # JSON rather than text, so a number is a number, a list is a list and a bool
        # is a bool. As a CharField, gt/lt had to int() both sides - 10.5 was a crash
        # and a date was impossible - and a list could only be a comma-joined string.
        value = models.JSONField(null=True,blank=True)

        operator = models.CharField(choices=ConditionOperators)

        quantifier = models.CharField(
                choices=Quantifier.choices,
                default=Quantifier.SINGLE,
                max_length=4,
                blank=True
        )

        on_missing = models.CharField(
                choices=MissingFieldPolicy.choices,
                default=MissingFieldPolicy.FAIL,
                max_length=5
        )

        # logical operator is for comparing with the other condition objs
        logical_operator = models.CharField(
                choices=LogicalOperator.choices,
                default=LogicalOperator.AND,
                max_length=3
        )

        # Grouping depends on the order the conditions are read in, and the queryset
        # had none - so the MEANING of a condition set depended on whatever order the
        # database happened to return. Same reason AutomationAction carries one.
        order = models.PositiveIntegerField(default=0)

        class Meta:
                ordering = ['order','created_at']



class AutomationAction(BaseModel):
        automation = models.ForeignKey('Automation',on_delete=models.CASCADE,related_name='actions')
        action_type = models.CharField(choices=ActionTypes)

        channel = models.ForeignKey('channels.Channel',null=True,blank=True,related_name='automation_actions',on_delete=models.SET_NULL)

        # Optional message body generator. BaseAction.prepare() has always read
        # self.action_model.template, but the field was never declared - every action
        # execution died with AttributeError before reaching its provider.
        template = models.ForeignKey('Template',null=True,blank=True,related_name='actions',on_delete=models.SET_NULL)

        order = models.PositiveIntegerField()

        on_failure = models.CharField(choices=OnFailureChoices)
        config = models.JSONField()



class AutomationStep(BaseModel):
        # A node in the step graph. AutomationAction stays readable for a release
        # (see ROADMAP §5.1): an action is just this model with step_type='action',
        # which is why the fields overlap on purpose.
        automation = models.ForeignKey(Automation,on_delete = models.CASCADE,related_name = 'steps',verbose_name = _("Otomasyon"))

        step_type = models.CharField(choices = StepTypes.choices,max_length = 32,default = StepTypes.ACTION,verbose_name = _("Adım Tipi"))

        # How edges and expressions refer to this step: "1", "send_sms". Unique per
        # automation, because the graph has no other stable name to point at.
        key = models.CharField(max_length = 64,verbose_name = _("Adım Anahtarı"))

        # Only meaningful for step_type='action', but kept on the node rather than in
        # config: it is what selects the provider.
        action_type = models.CharField(choices = ActionTypes.choices,max_length = 32,blank = True,default = '',verbose_name = _("Aksiyon Tipi"))

        config = models.JSONField(default = dict,blank = True,verbose_name = _("Adım Ayarları"))

        channel = models.ForeignKey('channels.Channel',on_delete = models.SET_NULL,null = True,blank = True,related_name = 'automation_steps',verbose_name = _("Kanal"))

        template = models.ForeignKey('Template',on_delete = models.SET_NULL,null = True,blank = True,related_name = 'automation_steps',verbose_name = _("Şablon"))

        on_failure = models.CharField(choices = OnFailureChoices.choices,default = OnFailureChoices.STOP,max_length = 16,verbose_name = _("Hata Durumunda"))

        # Canvas coordinates. The frontend needs somewhere to remember where a box was
        # dragged; it is presentation data, so it is not part of the graph itself.
        position = models.JSONField(null = True,blank = True,verbose_name = _("Canvas Konumu"))

        class Meta:
                verbose_name = _("Otomasyon Adımı")
                verbose_name_plural = _("Otomasyon Adımları")
                ordering = ['created_at']
                constraints = [
                        models.UniqueConstraint(fields = ['automation','key'],name = 'unique_step_key_per_automation'),
                ]

        def __str__(self):
                return f"{self.automation.name} - {self.key} ({self.step_type})"



class AutomationEdge(BaseModel):
        # Branching and merging live here, not in a field on the step: "next step" as
        # an integer cannot express a fan-out or a merge, and every attempt to encode
        # one on top of order eventually re-implements a graph badly.
        automation = models.ForeignKey(Automation,on_delete = models.CASCADE,related_name = 'edges',verbose_name = _("Otomasyon"))

        # null = the entry edge: the graph starts here. The alternative (a synthetic
        # START step) would make every query and every migration special-case a node
        # that never executes.
        from_step = models.ForeignKey(AutomationStep,on_delete = models.CASCADE,related_name = 'outgoing',null = True,blank = True,verbose_name = _("Kaynak Adım"))

        to_step = models.ForeignKey(AutomationStep,on_delete = models.CASCADE,related_name = 'incoming',verbose_name = _("Hedef Adım"))

        # 'true'/'false' for a branch step, a path name, or '' for a plain successor.
        branch_key = models.CharField(max_length = 64,blank = True,default = '',verbose_name = _("Dal Anahtarı"))

        order = models.PositiveIntegerField(default = 0,verbose_name = _("Sıra"))

        class Meta:
                verbose_name = _("Otomasyon Bağlantısı")
                verbose_name_plural = _("Otomasyon Bağlantıları")
                ordering = ['order','created_at']

        def __str__(self):
                return f"{self.automation.name}: {self.from_step.key if self.from_step else 'START'} -> {self.to_step.key}"



def sync_steps_from_actions(automation):
        """
        Rewrite the step graph of `automation` from its AutomationAction rows.

        A linear chain: one entry edge (from_step=None) into the first step, then
        step[i-1] -> step[i]. This is the bridge that lets the actions API keep
        working while the graph becomes the thing that executes (ROADMAP §5.1).

        Destructive by design - the graph is derived state here, and deriving it
        incrementally from rows that may have been reordered is how the two drift
        apart. Returns (steps, edges).
        """
        with transaction.atomic():
                automation.edges.all().delete()
                automation.steps.all().delete()

                actions = list(automation.actions.order_by('order','created_at'))

                steps = [
                        AutomationStep.objects.create(
                                automation = automation,
                                step_type = StepTypes.ACTION,
                                key = str(idx),
                                action_type = action.action_type,
                                config = action.config,
                                channel = action.channel,
                                template = action.template,
                                on_failure = action.on_failure,
                        )
                        for idx, action in enumerate(actions, start = 1)
                ]

                edges = []
                if steps:
                        edges.append(AutomationEdge.objects.create(
                                automation = automation,
                                from_step = None,
                                to_step = steps[0],
                                branch_key = '',
                                order = 0,
                        ))
                        for i in range(1,len(steps)):
                                edges.append(AutomationEdge.objects.create(
                                        automation = automation,
                                        from_step = steps[i - 1],
                                        to_step = steps[i],
                                        branch_key = '',
                                        order = i,
                                ))

        return steps, edges


def sync_actions_from_steps(automation):
        """
        Rewrite the AutomationAction rows of `automation` from its action steps.

        The mirror image of sync_steps_from_actions(), and what makes the graph
        authorable today: the legacy executor still reads AutomationAction, so a
        client that posts `steps` would otherwise produce an automation that never
        runs. Every step_type='action' step becomes one row, in the order the steps
        were created (which is the order they were submitted in).

        Destructive by design - derived state, exactly like the reverse direction.
        A branching graph loses its branching here: the old executor has no concept
        of it, so its action steps run one after another until Faz 1.C executes the
        graph itself (ROADMAP §5.1). Returns the rows.
        """
        with transaction.atomic():
                automation.actions.all().delete()

                steps = automation.steps.filter(
                        step_type = StepTypes.ACTION
                ).order_by('created_at')

                actions = [
                        AutomationAction.objects.create(
                                automation = automation,
                                action_type = step.action_type,
                                channel = step.channel,
                                template = step.template,
                                order = idx,
                                on_failure = step.on_failure,
                                config = step.config,
                        )
                        for idx,step in enumerate(steps,start = 1)
                ]

        return actions




class AutomationLog(BaseModel):
        automation = models.ForeignKey('Automation',on_delete=models.CASCADE,related_name='logs')   
        status = models.CharField(choices=AutomationStatus)

        triggered_at = models.DateTimeField(auto_now_add=True)
        error_message = models.TextField(blank=True)


        execution_context = models.JSONField()

        # Why the conditions came out the way they did: every condition, its actual
        # value, its result, and which ones were short-circuited. Answers "why did
        # this customer not get the message", which a bare status never could.
        decision = models.JSONField(null=True,blank=True)



class Template(BaseModel):

        name = models.CharField(max_length=128)

        raw_text = models.TextField(max_length=4096)

        fields = models.JSONField()

        version = models.PositiveIntegerField(default=1)

        tenant = models.ForeignKey('tenants.Tenant' ,on_delete=models.CASCADE)
        branch = models.ForeignKey('tenants.Branch', null=True, blank=True,on_delete=models.SET_NULL)
        
        channel_type = models.CharField(choices=ChannelTypes)