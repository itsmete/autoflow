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


        def get_scope_ids(self):
                """
                (tenant_id, branch_id) this row is filed under, for permission checks
                and for resolving which cache indexes it belongs to.

                Tenant and Branch override this: a Tenant has no tenant_id column, it
                IS the tenant. Reading `instance.tenant_id` off them raised
                AttributeError and crashed every Tenant update; overriding here keeps
                the special case in one place instead of repeating it at each caller.
                """
                return (getattr(self,'tenant_id',None), getattr(self,'branch_id',None))