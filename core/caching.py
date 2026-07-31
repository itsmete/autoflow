from django.core.cache import cache


"""
Caching Strategy of AutoFlow.

        BASE
                * Cache must be protected by username and password , provided .env

                * Development -> Redis, to write functions of redis-specific services
                * Prod -> Redis

        KEY ARCHITECTURE

                User Model:

                        auth/users/<uuid:pk>
                        auth/users -> List of all Users (Not filtered by role) ->

                Tenant Model:
                        tenants/<uuid:pk>
                        tenats/
                        
                        

                Branch Model:
                        branchs/<uuid:pk>
                        branchs/

                Automations:

                        automations/<uuid:pk>
                        automations/ -> All models related with Automation (main) model. (Automation,AutomationLog,AutomationAction etc.)
                        automations/all -> Index Set
                        automations/tenant:<uuid> -> Idx set.

                Channels:
                        channels/<uuid:pk>
                        channels/

                Templates:
                        templates/<uuid:pk>
                        templates/
                        
                


        ROADMAP
                I. Cache QuerySets and templates 
                        * Templates must be cached with their versions.
                        * 







"""


class CacheService():
        
        client = cache.client.get_client()

        def __init__(self):
                pass


