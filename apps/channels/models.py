from django.db import models
from core.models import BaseModel


class ChannelTypes(models.TextChoices):
        EMAIL = 'email' ,'E-posta'
        WHATSAPP = 'whatsapp' , 'Whatsapp'
        INSTAGRAM = 'instagram' , 'Instagram'
        SMS = 'sms' ,'SMS'

"""
Workflow


An automation is being triggered,
Passes the all conditions 
When it comes to action time :
        
        -> Fetches the channel_id from config (This also leads to the action serializer will be refactored, CHANNEL check in validate_config  ) 

        -> Finds the automation object

        -> Calls the correct provider based on the channel's type

        -> Provider does the job with credentials field



"""
class Channel(BaseModel):
        
        name = models.CharField(max_length=128)

        # non-tenant channels can only be created by Super Admin,otherwise,all roles must specify a tenant
        # Branch is optional , 
        # and in case that channel might transfer to antoher branch when the host branch is deleted ,on_delete is selected SET_NULL
        
        tenant = models.ForeignKey('tenants.Tenant',on_delete=models.CASCADE,null=True,blank=True)
        branch = models.ForeignKey('tenants.Branch',on_delete=models.SET_NULL,null=True,blank=True)

        channel_type = models.CharField(choices=ChannelTypes)
        
        credentials = models.JSONField() # API KEY, token etc, to communicate with target server

