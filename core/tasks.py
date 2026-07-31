from celery import shared_task
from .cache import check_it_cacheable,get_ttl,resolve_idx_keys,get_cache_key
from .cache import service as cache_module
import json

@shared_task
def single_cache(model_name,key,json_data,idxs):
        if not check_it_cacheable(model_name):
                return 

        cache_module.cache_service.set(
                key,
                idxs,
                json_data,
                get_ttl(model_name)
        )


@shared_task
def bulk_cache(model_name , json_data):
        
        objs = json.loads(json_data)

        datalist = []

        for obj in objs:
                pk = obj.get('id')
                
                tenant_id = obj.get('tenant')
                branch_id = obj.get('branch')
                
                ttl = get_ttl(model_name)
                
                cache_key = f"{get_cache_key(model_name)}{pk}"

                idxs = resolve_idx_keys(model_name,tenant_id,branch_id)

                item = {
                        "cache_key" : cache_key,
                        "idx_keys" : idxs,
                        "data" : json.dumps(obj),
                        "ttl" : ttl
                }
                datalist.append(item)

        cache_module.cache_service.bulk_set(datalist)
