"""
Webhook delivery idempotency, kept outside the view.

The view's job is to authenticate, read the body and answer HTTP. Deciding whether a
delivery is new, a retry or a conflict is an ORM concern, and it has enough edge
cases - a unique constraint, an expired row that must be reclaimed, and a claim that
must be released when dispatch fails - that inlining it would bury the request
handling. It lives here rather than in `engine/` because it is Django, and the engine
is not allowed to import Django (tests/test_engine_purity.py).
"""
import hashlib
from datetime import timedelta

from django.utils import timezone

from .models import WebhookDelivery


# The three answers a claim can have. A string enum would be heavier than the
# problem: these are compared in one place, the view.
CREATED = 'created'
DUPLICATE = 'duplicate'
CONFLICT = 'conflict'


def payload_digest(raw_body):
        """
        SHA-256 of the exact bytes received.

        Deliberately not a hash of the parsed JSON: `{"a": 1}` and `{ "a": 1 }` are
        the same object but different bytes, and canonicalizing would mean this
        module had to agree with every caller on a canonical form. The caller that
        signs a body is already signing the raw bytes, so this matches what they
        signed.
        """
        if isinstance(raw_body,str):
                raw_body = raw_body.encode()

        return hashlib.sha256(raw_body).hexdigest()


def claim_webhook_delivery(trigger,key,payload_hash,ttl_seconds):
        """
        Claim `(trigger, key)` for this delivery, atomically.

        Returns `(state, delivery)`. `CREATED` means the caller owns the right to
        dispatch; `DUPLICATE` and `CONFLICT` mean somebody else already does.

        `get_or_create` is the whole concurrency story: the database unique
        constraint decides the winner, so two identical requests arriving together
        cannot both see "no row" and both dispatch. Checking for an existing row
        first and inserting after would reintroduce exactly that race.

        An expired row is removed with a conditional DELETE and then claimed through
        a second get_or_create. Two requests that both notice it is expired still
        produce only one winner: only one DELETE matches, and the INSERT is arbitrated
        by the unique constraint. A cleanup pass that deletes the row first is
        harmless - our DELETE removes nothing and the get_or_create simply creates
        the new row. A reclaim starts a fresh 24-hour window (it is a new delivery);
        a duplicate or conflict never touches `expires_at`, so retrying cannot extend
        how long the key is remembered.
        """
        now = timezone.now()
        expires_at = now + timedelta(seconds = ttl_seconds)

        delivery, created = WebhookDelivery.objects.get_or_create(
                trigger = trigger,
                key = key,
                defaults = {'payload_hash' : payload_hash,'expires_at' : expires_at},
        )

        if created:
                return (CREATED,delivery)

        if delivery.expires_at <= now:
                WebhookDelivery.objects.filter(
                        pk = delivery.pk,
                        expires_at__lte = now,
                ).delete()

                delivery, created = WebhookDelivery.objects.get_or_create(
                        trigger = trigger,
                        key = key,
                        defaults = {'payload_hash' : payload_hash,'expires_at' : expires_at},
                )

                if created:
                        return (CREATED,delivery)

        if delivery.payload_hash == payload_hash:
                return (DUPLICATE,delivery)

        return (CONFLICT,delivery)


def release_webhook_delivery(delivery,payload_hash):
        """
        Give back a claim whose dispatch failed, so the caller can retry.

        The filter is not just the pk: if the row was reclaimed by a later request
        in the meantime, its `payload_hash` differs and we must leave that newer
        claim alone. Releasing somebody else's delivery would allow the very
        duplicate this table exists to prevent.
        """
        WebhookDelivery.objects.filter(
                pk = delivery.pk,
                payload_hash = payload_hash,
        ).delete()
