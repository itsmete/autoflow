from django.db import models
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