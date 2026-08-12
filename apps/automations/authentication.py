"""
Webhook authentication for AutomationTrigger.

Two schemes live here.

  * SIGNATURE (preferred). The caller sends the trigger id, a unix timestamp and an
    HMAC-SHA256 of "<timestamp>.<raw body>" keyed by the trigger's secret. The secret
    itself never travels, the body is covered, and a captured request expires.

  * SHARED SECRET (legacy). The caller sends the raw secret in a header. Kept because
    triggers created before the signature scheme existed still hand this out, but it
    proves nothing about the body and a captured request is replayable forever.

Prefer the signature scheme for anything new.
"""
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from .models import AutomationTrigger,TriggerType
from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _

import hashlib
import hmac
import time


ID_HEADER = 'X-Webhook-Id'
TIMESTAMP_HEADER = 'X-Webhook-Timestamp'
SIGNATURE_HEADER = 'X-Webhook-Signature'
SECRET_HEADER = 'X-Webhook-Secret'

SIGNATURE_PREFIX = 'sha256='

# How far a signed request's timestamp may sit from now, in seconds. Bounds the
# window in which a captured request can be replayed; also tolerates ordinary clock
# skew between the caller and us.
DEFAULT_REPLAY_WINDOW = 300


def compute_signature(secret,timestamp,body):
        """
        The value a caller must put in X-Webhook-Signature.

        Signing "<timestamp>.<body>" rather than the body alone is what binds the
        timestamp to the request: otherwise an attacker could keep a captured body
        and pair it with a fresh timestamp of their own.
        """
        if isinstance(body,str):
                body = body.encode()

        message = f"{timestamp}.".encode() + body
        digest = hmac.new(secret.encode(),message,hashlib.sha256).hexdigest()

        return SIGNATURE_PREFIX + digest


def _replay_window():
        return getattr(settings,'AUTOFLOW_WEBHOOK_REPLAY_WINDOW',DEFAULT_REPLAY_WINDOW)


class TriggerSecretKeyAuthentication(BaseAuthentication):

        def authenticate(self, request):

                if request.headers.get(SIGNATURE_HEADER):
                        return self._authenticate_signature(request)

                if request.headers.get(SECRET_HEADER):
                        return self._authenticate_shared_secret(request)

                raise AuthenticationFailed(_("Secret key not found"))


        def _get_webhook_trigger(self,**lookup):
                try:
                        # JSON fields could be accessed with "__"
                        return AutomationTrigger.objects.get(trigger_type = TriggerType.WEBHOOK,**lookup)
                except AutomationTrigger.DoesNotExist:
                        raise AuthenticationFailed(_("Secret key doesn't matched"))
                except (DjangoValidationError,ValueError,TypeError):
                        # A malformed uuid in the id header reaches the ORM as a bad
                        # value. UUIDField.to_python turns that into Django's
                        # ValidationError - not a ValueError - and an uncaught one
                        # here is a 500 on an unauthenticated endpoint.
                        raise AuthenticationFailed(_("Secret key doesn't matched"))


        def _authenticate_shared_secret(self,request):
                secret_key = request.headers.get(SECRET_HEADER)

                obj = self._get_webhook_trigger(config__secret_key = secret_key)

                # authenticate methods return a tuple which is (User,auth), view can access via these keyword, e.g. request.auth = obj
                # We don't have user in trigger views, so needs to return None
                return (None, obj)


        def _authenticate_signature(self,request):
                trigger_id = request.headers.get(ID_HEADER)
                timestamp = request.headers.get(TIMESTAMP_HEADER)
                signature = request.headers.get(SIGNATURE_HEADER)

                if not trigger_id or not timestamp:
                        raise AuthenticationFailed(
                                _("A signed webhook must send both %(id)s and %(ts)s")
                                % {'id' : ID_HEADER , 'ts' : TIMESTAMP_HEADER}
                        )

                try:
                        sent_at = int(timestamp)
                except (ValueError,TypeError):
                        raise AuthenticationFailed(_("Invalid webhook timestamp"))

                window = _replay_window()
                if abs(time.time() - sent_at) > window:
                        raise AuthenticationFailed(_("Webhook timestamp is outside the accepted window"))

                obj = self._get_webhook_trigger(id = trigger_id)

                secret_key = (obj.config or {}).get('secret_key')
                if not secret_key:
                        raise AuthenticationFailed(_("Secret key doesn't matched"))

                expected = compute_signature(secret_key,sent_at,request.body)

                # compare_digest rather than ==: a plain comparison returns as soon as
                # two bytes differ, which leaks how much of a guess was correct.
                if not hmac.compare_digest(expected,signature):
                        raise AuthenticationFailed(_("Webhook signature doesn't match"))

                return (None, obj)
