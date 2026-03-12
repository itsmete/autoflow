from django.db import models
from core.models import BaseModel


class Tenant(BaseModel):

        name = models.CharField(max_length=255)

        slug = models.SlugField(
                unique=True
                # "Burger-Palace" -> burger-palace
        )


        
        metadata = models.JSONField(default=dict,blank=True)


        def __str__(self):
                return self.name
        
        class Meta:
                db_table = 'tenants' # defining table name for DB






class Branch(BaseModel):

        tenant = models.ForeignKey(
                'Tenant',
                on_delete=models.CASCADE,
                related_name='branches' 
                # let us tentant.branches.all()
        )

        name = models.CharField(max_length=255)


        # pyhsical informations
        address = models.TextField(blank=True)
        phone = models.CharField(max_length=20,blank=True)


        is_hq = models.BooleanField(default=False) # is Headquarters


        metadata = models.JSONField(default=dict,blank=True)


        def __str__(self):
                return f"{self.tenant.name} - {self.name}"
        
        class Meta:
                db_table = 'branches'
    



     














