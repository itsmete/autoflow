from django.conf import settings
from redis.exceptions import NoScriptError
from . import REGISTRY
import functools
import logging


cache_service = None



logger = logging.getLogger(__name__)



class MissingScriptError(RuntimeError):
        """A .lua file declared in AVAILABLE_SCRIPTS is not on disk."""



def handle_script_error(func):
        """
        A cache must never be able to break a request.

        Any failure here - redis down, script gone, bad reply - degrades to None, and
        every caller reads None as "cache could not answer, use the database". The
        failure is logged with the operation name so it is visible in the logs instead
        of vanishing the way the original bare `except Exception` let it.
        """
        @functools.wraps(func)
        def wrapper(self,*args,**kwargs):
                try:
                        return func(self,*args,**kwargs)
                except NoScriptError:
                        # Redis was restarted or SCRIPT FLUSHed; re-register and retry once.
                        try:
                                self.load_scripts(force=True)
                                return func(self,*args,**kwargs)
                        except Exception:
                                logger.exception("cache: %s failed after script reload",func.__name__)
                                return None
                except Exception:
                        logger.exception("cache: %s failed",func.__name__)
                        return None
        return wrapper


class CachingService():

        def __init__(self,redis_client):

                self.r = redis_client
                self.scripts = {}

        def load_scripts(self,force=False):
                """
                Register every declared .lua file with redis and remember its sha.

                Called lazily by _sha() rather than at startup, so a redis outage at
                boot cannot stop Django from starting. It also means a service that
                was constructed before redis came up heals itself on first use - the
                old code built the service in AppConfig.ready() but never loaded the
                scripts, leaving self.scripts empty and every operation raising a
                KeyError that got swallowed. The cache was a silent no-op.
                """
                if self.scripts and not force:
                        return

                missing = [
                        name for name in REGISTRY['AVAILABLE_SCRIPTS']
                        if not (settings.SCRIPTS_DIR / f"{name}.lua").exists()
                ]
                if missing:
                        # Fail loudly: a declared-but-absent script is a packaging bug,
                        # not a runtime condition to paper over.
                        raise MissingScriptError(
                                f"missing lua scripts in {settings.SCRIPTS_DIR}: {', '.join(missing)}"
                        )

                loaded = {}
                for name in REGISTRY['AVAILABLE_SCRIPTS']:
                        content = (settings.SCRIPTS_DIR / f"{name}.lua").read_text()
                        loaded[name] = self.r.script_load(content)

                self.scripts = loaded

        def _sha(self,name):
                self.load_scripts()
                return self.scripts[name]


        @handle_script_error
        def get(self,key):
                """One object by data key, or None."""
                return self.r.evalsha(
                        self._sha('single_get'),
                        1,
                        key
                )


        @handle_script_error
        def set(self,key,data,ttl):
                """
                Cache one object. Takes no index keys on purpose - see the module
                docstring in core/cache/__init__.py: only list queries write indexes.
                """
                self.r.evalsha(
                        self._sha('single_set'),
                        1,
                        key,
                        data,
                        str(ttl)
                )
                return True


        @handle_script_error
        def bulk_get(self,index_key):
                """
                The complete cached answer for one scope, or None on a miss.

                Takes a single key, not a list. The old signature accepted an iterable
                and splatted it into evalsha, so passing the string 'users/all' turned
                into nine single-character keys.
                """
                return self.r.evalsha(
                        self._sha('bulk_get'),
                        1,
                        index_key
                )


        @handle_script_error
        def bulk_set(self,index_key,items,ttl):
                """
                Replace one scope's cached answer.

                `items` is an iterable of (data_key, payload). Runs as a single script
                so the index is never observable half-built, and so the old index is
                replaced rather than merged with.
                """
                args = [str(ttl)]
                for data_key,payload in items:
                        args.append(data_key)
                        args.append(payload)

                if len(args) == 1:
                        # Nothing to store. Redis cannot hold an empty set, so caching
                        # this would be indistinguishable from a cold miss.
                        return False

                self.r.evalsha(
                        self._sha('bulk_set'),
                        1,
                        index_key,
                        *args
                )
                return True


        @handle_script_error
        def single_invalidate(self,key,idxs):
                """Drop one object and every list answer that could have contained it."""
                keys = [key] + list(idxs)

                self.r.evalsha(
                        self._sha('single_invalidate'),
                        len(keys),
                        *keys
                )
                return True
