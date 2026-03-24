from django.db import models
from core.models import BaseModel
import secrets
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
        EQUALS      = 'eq', 'Eşittir'
        NOT_EQUALS  = 'neq', 'Eşit Değildir'
        GREATER     = 'gt', 'Büyüktür'
        LESS        = 'lt', 'Küçüktür'
        CONTAINS    = 'contains', 'İçerir'
        IN          = 'in', 'Listede'

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
    AND = 'and', 'Ve'
    OR = 'or', 'Veya'



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

        #Model save() is overrided (instead of serializer), since the creation of secret key
        # is a model level rule, that should be in models
        # Serializers are in API level, whoever do not use API , API rules are neglected
        def save(self,*args ,**kwargs):
                if self.trigger_type == 'webhook' and not self.config.get('secret_key'):
                        self.config['secret_key'] = secrets.token_urlsafe(32) 
                super().save(*args,**kwargs)


class AutomationCondition(BaseModel):
        automation = models.ForeignKey('Automation',on_delete=models.CASCADE,related_name='conditions')

        # order_amount , customer_type etc.
        field = models.CharField(max_length=100)
        value = models.CharField(max_length=255)

        operator = models.CharField(choices=ConditionOperators)

        # logical operator is for comparing with the other condition objs
        logical_operator = models.CharField(
                choices=LogicalOperator.choices,
                default=LogicalOperator.AND,
                max_length=3
        )



class AutomationAction(BaseModel):
        automation = models.ForeignKey('Automation',on_delete=models.CASCADE,related_name='actions')
        action_type = models.CharField(choices=ActionTypes)

        channel = models.ForeignKey('channels.Channel',null=True,blank=True,related_name='automation_actions',on_delete=models.SET_NULL)

        order = models.PositiveIntegerField()

        on_failure = models.CharField(choices=OnFailureChoices)
        config = models.JSONField()




class AutomationLog(BaseModel):
        automation = models.ForeignKey('Automation',on_delete=models.CASCADE,related_name='logs')   
        status = models.CharField(choices=AutomationStatus)

        triggered_at = models.DateTimeField(auto_now_add=True)
        error_message = models.TextField(blank=True)


        execution_context = models.JSONField()


