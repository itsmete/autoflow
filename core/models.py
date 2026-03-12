import uuid
from django.db import models


class BaseModel(models.Model):
        
        id = models.UUIDField(
                primary_key=True,
                default=uuid.uuid4,
                editable=False
        )

        created_at = models.DateTimeField(auto_now_add=True)

        updated_at = models.DateTimeField(auto_now=True)


        is_active = models.BooleanField(default=True)


        class Meta:
                abstract = True # Meaning that it is not considered for db migrations
                # just for inheritance, DRY


        def get_tenant(self):
                return self.tenant if hasattr(self,'tenant') and self.tenant is not None else self.branch.tenant if hasattr(self,'branch') and self.branch is not None else None