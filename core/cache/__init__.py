"""
Cache registry.

CACHE SEMANTICS - read this before touching anything here.

There are exactly two kinds of key, and they mean different things:

  1. DATA KEY      '<cache_key><pk>'      e.g. 'users/9eec6444-...'
     A single serialized object. Written by both the detail path and the list path.

  2. INDEX SET     '<cache_key><scope>'   e.g. 'users/all', 'users/tenant:<uuid>'
     The COMPLETE answer to one scoped list query, stored as a set of data keys.
     "Complete" is the whole point: if the set exists, it is the entire result set
     for that scope, and bulk_get may serve it without touching the database.

Two rules follow from that, and both were violated by the original implementation:

  * Only a LIST query may write an index, and only into the single index matching
    its own scope. A detail fetch must never add itself to an index - a one-member
    set that claims to be "all users" is worse than no cache at all.
  * Invalidation DELETES an index, it does not SREM one member out of it. Removing
    a member leaves a set that still claims completeness while being wrong.

An empty result set is deliberately not cached: Redis cannot hold an empty set, so
"empty answer" and "no cache" would be indistinguishable. Empty lists fall through
to the database every time, which is cheap and always correct.
"""

REGISTRY = {
        # Must match the .lua files on disk exactly - load_scripts() validates this
        # and refuses to start with a mismatch rather than failing later at evalsha.
        'AVAILABLE_SCRIPTS' : ['single_set','single_get','bulk_set','bulk_get','single_invalidate'],

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
                        # Only super_admin may list tenants, so 'all' is the only scope
                        # that can ever be requested. get_scope_index_key() returns None
                        # for any other scope, which falls back to the database.
                        'idx_keys' : ['tenants/all'],

                },
                'Branch' : {
                        'CACHED' : True,

                        'TTL' : 3600,

                        'cache_key' : 'branches/',
                        # These used to read 'branch/...' while cache_key was 'branches/',
                        # so nothing ever hit. Every idx_key MUST start with cache_key.
                        'idx_keys' : ['branches/all','branches/tenant:{tenant_id}','branches/branch:{branch_id}'],
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
                        # 'templates:branch:' (colon) was a typo for 'templates/branch:'
                        'idx_keys' : ['templates/all','templates/tenant:{tenant_id}','templates/branch:{branch_id}'],

                },
                'AutomationTrigger': {
                        'CACHED': False,
                        'parent': ('automations.Automation', 'automation_id'),
                },
                'AutomationCondition': {
                        'CACHED': False,
                        'parent': ('automations.Automation', 'automation_id'),
                },
                'AutomationAction': {
                        'CACHED': False,
                        'parent': ('automations.Automation', 'automation_id'),
                },
                # Faz 1.B: the step graph is serialized into the same Automation payload,
                # so a step or edge write has to drop the parent's cache entry for
                # exactly the same reason an action write does.
                'AutomationStep': {
                        'CACHED': False,
                        'parent': ('automations.Automation', 'automation_id'),
                },
                'AutomationEdge': {
                        'CACHED': False,
                        'parent': ('automations.Automation', 'automation_id'),
                },
                'AutomationLog': {
                        'CACHED': False,
                        'parent': ('automations.Automation', 'automation_id'),

                }
        }



}


# Every lookup below tolerates an unknown model name and returns a falsy value.
# This is not defensive noise: core/signals.py is connected to pre_save/post_delete
# WITHOUT a sender, so these are called for every model in the project - including
# django.contrib.sessions, django_celery_beat and anything added later. The original
# `.get(name).get('CACHED')` raised AttributeError on all of them.

def get_model_data(model_name : str) -> dict:
        return REGISTRY['MODEL_DATA'].get(model_name) or {}

def check_it_cacheable(model_name : str) -> bool:
        return bool(get_model_data(model_name).get('CACHED'))

def get_cache_key(model_name : str) -> str | None:
        return get_model_data(model_name).get('cache_key')

def get_idx_keys(model_name : str) -> list:
        return get_model_data(model_name).get('idx_keys') or []

def get_ttl(model_name:str) -> int | None:
        return get_model_data(model_name).get('TTL')

def resolve_idx_keys(model_name,tenant_id=None, branch_id=None):
        """
        Every index this object could belong to, given its tenant/branch.

        Used for INVALIDATION only - never to decide where to write a list result.
        Patterns whose placeholders cannot be filled are skipped, so an object with
        no branch simply does not produce a 'branch:' key.
        """
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

def get_scope_index_key(model_name, scope, tenant_id=None, branch_id=None):
        """
        The single index a list query of `scope` is allowed to read and write.

        scope is 'all' | 'tenant' | 'branch'.

        Returns None when the registry does not declare an index for that scope,
        which the caller must treat as "not cacheable, go to the database". That
        guard is what makes a cache_key/idx_keys mismatch degrade into a plain
        cache miss instead of silently writing keys nobody ever reads.
        """
        prefix = get_cache_key(model_name)
        if not prefix:
                return None

        if scope == 'all':
                key = f"{prefix}all"
        elif scope == 'tenant' and tenant_id is not None:
                key = f"{prefix}tenant:{tenant_id}"
        elif scope == 'branch' and branch_id is not None:
                key = f"{prefix}branch:{branch_id}"
        else:
                return None

        # Only hand back a key the registry actually declares.
        if key not in resolve_idx_keys(model_name, tenant_id, branch_id):
                return None

        return key

def get_parent_info(model_name: str) -> tuple | None:
        return get_model_data(model_name).get('parent')
