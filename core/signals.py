"""
Cache invalidation driven by model signals.

These receivers are connected WITHOUT a sender, so they run for every model in the
project - ours, django's own, and django_celery_beat's. Everything here therefore has
to survive a model the cache registry has never heard of. The original version called
`REGISTRY['MODEL_DATA'].get(name).get('CACHED')` and raised AttributeError on the
first CrontabSchedule save, which made it impossible to create a scheduled automation.

Why both pre_save and post_save:

  pre_save  invalidates the OLD scope. An update may move a row to a different tenant
            or branch, and the stale list for where it used to live has to go. Only
            the row still in the database knows the old values.

  post_save invalidates the NEW scope. This is also what covers INSERTs: a brand new
            row was never a member of any cached list, so nothing pre_save does can
            reach the index that now needs to be dropped. Without it, newly created
            objects stayed invisible in list endpoints until the TTL expired.
"""
from django.dispatch import receiver
from django.db.models.signals import post_delete,post_save,pre_save
from .cache import check_it_cacheable,get_cache_key,resolve_idx_keys,get_parent_info
from .cache import service as cache_module
from django.apps import apps
import logging


logger = logging.getLogger(__name__)


def invalidate_instance_cache(instance, model_name):
        prefix = get_cache_key(model_name)
        if not prefix:
                return

        # BaseModel.get_scope_ids(), overridden by Tenant and Branch. Keeping the
        # Tenant/Branch special case on the models means it cannot drift between the
        # signal layer and the mixin the way it did before - reading tenant_id off a
        # Tenant here raised AttributeError and crashed every Tenant update.
        get_scope = getattr(instance,'get_scope_ids',None)
        if get_scope is None:
                return
        tenant_id, branch_id = get_scope()

        cache_key = f"{prefix}{instance.pk}"
        idxs = resolve_idx_keys(model_name, tenant_id, branch_id)

        if cache_module.cache_service is None:
                return

        cache_module.cache_service.single_invalidate(cache_key, idxs)


def invalidate_parent_cache(instance, parent_info):
        """
        Child rows (triggers, conditions, actions, logs) are not cached themselves;
        they are serialized into their parent Automation, so a change to any of them
        must drop the parent's entry.
        """
        parent_model_path, fk_field = parent_info
        app_name, parent_model_name = parent_model_path.split('.')

        parent_pk = getattr(instance, fk_field, None)
        if parent_pk is None:
                return

        try:
                parent = apps.get_model(app_name, parent_model_name).objects.get(pk=parent_pk)
        except Exception:
                # Cascading delete: the parent may already be gone, and a missing
                # parent means its cache entry is being invalidated by its own signal.
                logger.debug(
                        "cache: parent %s/%s for %s not resolvable, skipping invalidation",
                        parent_model_path, parent_pk, instance.__class__.__name__,
                )
                return

        invalidate_instance_cache(parent, parent_model_name)


def invalidate_for(sender, instance):
        """Shared body: resolve to a parent if the model has one, else the model itself."""
        model_name = sender.__name__

        parent_info = get_parent_info(model_name)
        if parent_info:
                invalidate_parent_cache(instance, parent_info)
                return

        if not check_it_cacheable(model_name):
                return

        invalidate_instance_cache(instance, model_name)


@receiver(pre_save)
def handle_pre_save_invalidation(sender,instance,**kwargs):
        # `instance.pk` is NOT a create/update discriminator here: BaseModel declares
        # id = UUIDField(default=uuid.uuid4), so the pk is populated in __init__, long
        # before the row exists. _state.adding is the flag that actually tracks it.
        if instance._state.adding:
                return

        model_name = sender.__name__
        if not (get_parent_info(model_name) or check_it_cacheable(model_name)):
                return

        # Invalidate against the row as it is stored right now, not as it is about to
        # be written - the tenant/branch may be changing in this very save.
        try:
                old = sender.objects.get(pk=instance.pk)
        except Exception:
                return

        invalidate_for(sender, old)


@receiver(post_save)
def handle_post_save_invalidation(sender,instance,**kwargs):
        invalidate_for(sender, instance)


@receiver(post_delete)
def handle_post_delete_invalidation(sender,instance,**kwargs):
        invalidate_for(sender, instance)
