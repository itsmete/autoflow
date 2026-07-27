from django.dispatch import receiver
from django.db.models.signals import post_delete,pre_save
from .cache import check_it_cacheable,get_cache_key,resolve_idx_keys,get_parent_info
from .cache import service as cache_module
from django.apps import apps
"""
We cannot use post_save for invalidation after update 
since the "instance" points the updated instance, and its branch and tenant field might be changed,
and old idxs list belongs to previous tenant or branch remains live .

"""

def invalidate_instance_cache(instance, model_name):
        cache_key = f"{get_cache_key(model_name)}{instance.pk}"
        idxs = resolve_idx_keys(model_name, instance.tenant_id, instance.branch_id)
        cache_module.cache_service.single_invalidate(cache_key, idxs)


@receiver(pre_save)
def handle_pre_save_invalidation(sender,instance,**kwargs):
        if not instance.pk:
                #means it is created
                return
        

        parent_info = get_parent_info(sender.__name__)
        if parent_info:
                parent_model_path, fk_field = parent_info
                app_name, parent_model_name = parent_model_path.split('.')
                parent_pk = getattr(instance, fk_field)
                
                parent = apps.get_model(app_name, parent_model_name).objects.get(id=parent_pk)
                invalidate_instance_cache(parent, parent_model_name)
                return
        
        if not check_it_cacheable(sender.__name__):
                return
        
        try:
                old = sender.objects.get(pk = instance.pk)
        except sender.DoesNotExist:
                return
        
        invalidate_instance_cache(old,sender.__name__)
        
        
@receiver(post_delete)
def handle_post_delete_invalidation(sender,instance,**kwargs):

        parent_info = get_parent_info(sender.__name__)
        if parent_info:
                parent_model_path, fk_field = parent_info
                app_name, parent_model_name = parent_model_path.split('.')
                parent_pk = getattr(instance, fk_field)
                
                parent = apps.get_model(app_name, parent_model_name).objects.get(id=parent_pk)
                invalidate_instance_cache(parent, parent_model_name)
                return
        
        if not check_it_cacheable(sender.__name__):
                return

        invalidate_instance_cache(instance,sender.__name__)