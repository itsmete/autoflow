REGISTRY = {
        'AVAILABLE_SCRIPTS' : ['single_set','single_get','bulk_get','single_invalidate','bulk_invalidate'],
        
        'MODEL_DATA' : {
                'User' : {
                        'CACHED' : True,
                        
                        'TTL' : 3600,

                        'cache_key' : 'users/',
                        'idx_keys' : ['users/all','users/tenant:{tenant_id}','users/branch:{branch_id}'],
                },
                'Tenant' : {
                        'CACHED' : True,

                        'TTL' : 3600,

                        'cache_key' : 'tenants/',
                        'idx_keys' : ['tenants/all'],

                },
                'Branch' : {
                        'CACHED' : True,

                        'TTL' : 3600,

                        'cache_key' : 'branches/',
                        'idx_keys' : ['branch/all','branch/tenant:{tenant_id}'],
                },
                'Automation' : {
                        'CACHED' : True,

                        'TTL' : 3600,

                        'cache_key' : 'automations/',
                        'idx_keys' : ['automations/all','automations/tenant:{tenant_id}','automations/branch:{branch_id}'],

                        

                },
                'Channel' : {
                        'CACHED' : False,
                },
                'Template' : {
                        'CACHED' : True,

                        'TTL' : 3600,

                        'cache_key' : 'templates/',
                        'idx_keys' : ['templates/all','templates/tenant:{tenant_id}','templates:branch:{branch_id}'],
 
                },
                'AutomationTrigger': {
                        'CACHED': False,
                        'parent': ('Automation', 'automation_id'),
                },
                'AutomationCondition': {
                        'CACHED': False,
                        'parent': ('Automation', 'automation_id'),
                },
                'AutomationAction': {
                        'CACHED': False,
                        'parent': ('Automation', 'automation_id'),
                },
                'AutomationLog': {
                        'CACHED': False,
                        'parent': ('Automation', 'automation_id'),

                } 
        }



}

def get_model_data(model_name : str) -> dict:
        return REGISTRY['MODEL_DATA'].get(model_name)

def check_it_cacheable(model_name : str) -> bool:
        return REGISTRY['MODEL_DATA'].get(model_name).get('CACHED')

def get_cache_key(model_name : str) -> str:
        return REGISTRY['MODEL_DATA'].get(model_name).get('cache_key')

def get_idx_keys(model_name : str) -> list:
        return REGISTRY['MODEL_DATA'].get(model_name).get('idx_keys')

def get_ttl(model_name:str) -> int:
        return REGISTRY['MODEL_DATA'].get(model_name).get('TTL')

def resolve_idx_keys(model_name,tenant_id=None, branch_id=None):
        raw_keys = get_idx_keys(model_name)
        context = {
                'tenant_id': tenant_id,
                'branch_id': branch_id
        }
        clean = {k: v for k, v in context.items() if v is not None}
        resolved = []
        for key in raw_keys:
                try:
                        resolved.append(key.format(**clean))
                except KeyError:
                        pass
        return resolved

def get_parent_info(model_name: str) -> tuple | None:
        return REGISTRY['MODEL_DATA'].get(model_name, {}).get('parent')