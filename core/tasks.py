from celery import shared_task
from .cache import check_it_cacheable,get_ttl,get_cache_key
from .cache import service as cache_module
import json


@shared_task
def single_cache(model_name,key,json_data):
        """Cache one serialized object. Writes no index - see core/cache/__init__.py."""
        if not check_it_cacheable(model_name):
                return

        cache_module.cache_service.set(
                key,
                json_data,
                get_ttl(model_name)
        )


@shared_task
def bulk_cache(model_name,index_key,json_data):
        """
        Store a complete scoped list answer under `index_key`.

        The index key is decided by the CALLER, because only the caller knows which
        scope its queryset covered. The previous version derived the indexes from each
        object instead, via resolve_idx_keys(), which wrote every object into every
        index it qualified for - including the global 'all'. A branch manager listing
        their own branch therefore published a 2-of-4 result as the complete answer
        for super_admin, who then got a silent cache hit on half the rows.
        """
        if not check_it_cacheable(model_name):
                return

        objs = json.loads(json_data)
        prefix = get_cache_key(model_name)

        items = [
                (f"{prefix}{obj.get('id')}", json.dumps(obj))
                for obj in objs
        ]

        cache_module.cache_service.bulk_set(
                index_key,
                items,
                get_ttl(model_name)
        )
