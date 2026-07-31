from django.conf import settings
from redis.exceptions import NoScriptError
from . import REGISTRY
import logging


cache_service = None



logger = logging.getLogger(__name__)
                           


class CachingService():

        @staticmethod
        def handle_script_error(func):
                def wrapper(*args,**kwargs):
                        self = args[0]
                        try: 
                                return func(*args,**kwargs)
                        except NoScriptError:
                                self.load_scripts()
                                try:
                                        return func(*args, **kwargs)
                                except Exception:
                                        logger.exception("Script reload failed")
                                        return None
                        except Exception:
                                logger.exception("Cache operation failed")
                                return None
                return wrapper


        def __init__(self,redis_client):

                self.r = redis_client
                self.scripts = {}
        
        def load_scripts(self):
                for name in REGISTRY['AVAILABLE_SCRIPTS']:
                        content = (settings.SCRIPTS_DIR / f"{name}.lua").read_text()
                        self.scripts[name] = self.r.script_load(content)



        @handle_script_error
        def get(self,key):
                data = self.r.evalsha(
                        self.scripts['single_get'],
                        1,
                        key
                )
                return data
                

        @handle_script_error
        def set(self,key,idxs,data,ttl):
                keys = [key] + idxs
                args = [data,str(ttl)]
                data = self.r.evalsha(
                        self.scripts['single_set'],
                        len(keys),
                        *keys,
                        *args
                )

        @handle_script_error
        def bulk_get(self,idxs):
                data = self.r.evalsha(
                        self.scripts['bulk_get'],
                        len(idxs),
                        *idxs

                )
                return data
        
        @handle_script_error
        def bulk_set(self,items):
                pipe = self.r.pipeline()
                for item in items:
                        keys = [item["cache_key"]] + item['idx_keys']
                        args = [item["data"], str(item["ttl"])]
                        pipe.evalsha(
                                self.scripts["single_set"],
                                len(keys),
                                *keys,
                                *args
                        )
                results = pipe.execute(raise_on_error=False)
                if any(isinstance(r, Exception) for r in results):
                        return None
                return True


        @handle_script_error
        def single_invalidate(self,key,idxs):
                keys = [key] + idxs

                self.r.evalsha(
                        self.scripts['single_invalidate'],
                        len(keys),
                        *keys
                )
                return True


       