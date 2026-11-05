"""
Webhook delivery idempotency (Faz 2.D.1).

The contract under test: one delivery per (trigger, key) for 24 hours. A retry with
the same key and the same raw body is a no-op (200, `duplicate: true`), a different
body under the same key is a conflict (409), and both are decided by the database
unique constraint rather than by a check that two concurrent requests could both
pass.

Authentication is unchanged: everything here still has to arrive with either a valid
signature or the legacy shared secret. Deduplication happens after that.
"""
import hashlib
import json
import time
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.automations.authentication import compute_signature
from apps.automations.models import AutomationRun, WebhookDelivery
from apps.automations.tasks import cleanup_webhook_deliveries
from tests import factories

URL = '/api/v1/automations/webhook/'
DELAY = 'apps.automations.views.run_automation.delay'


def digest(raw):
    if isinstance(raw, str):
        raw = raw.encode()
    return hashlib.sha256(raw).hexdigest()


class WebhookIdempotencyBase(TestCase):

    def setUp(self):
        self.client = APIClient()
        # The endpoint is throttled and DRF keeps throttle history in the default
        # cache, which outlives a single test.
        cache.clear()

        self.tenant = factories.make_tenant()
        self.automation = factories.make_automation(self.tenant)
        self.trigger = factories.make_trigger(
            self.automation, trigger_type='webhook', config={}
        )
        self.secret = self.trigger.config['secret_key']

    def post_secret(self, body, key=None):
        headers = {'X-Webhook-Secret': self.secret}
        if key is not None:
            headers['Idempotency-Key'] = key

        return self.client.post(
            URL, data=body, content_type='application/json', headers=headers
        )

    def post_signed(self, body, key=None, trigger=None, secret=None,
                    timestamp=None):
        trigger = trigger or self.trigger
        secret = secret or (trigger.config or {}).get('secret_key')
        timestamp = int(time.time()) if timestamp is None else timestamp
        signature = compute_signature(secret, timestamp, body)

        headers = {
            'X-Webhook-Id': str(trigger.id),
            'X-Webhook-Timestamp': str(timestamp),
            'X-Webhook-Signature': signature,
        }
        if key is not None:
            headers['Idempotency-Key'] = key

        return self.client.post(
            URL, data=body, content_type='application/json', headers=headers
        )


class SharedSecretIdempotencyTests(WebhookIdempotencyBase):
    """The legacy scheme still dedupes - the scheme decides authentication, not
    whether a retry is recognised."""

    @patch(DELAY)
    def test_first_delivery_is_accepted_and_the_retry_is_a_duplicate(self, mock_delay):
        raw = json.dumps({'order_id': 1})

        first = self.post_secret(raw, key='order-1')
        second = self.post_secret(raw, key='order-1')

        self.assertEqual(first.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertTrue(second.data['duplicate'])
        mock_delay.assert_called_once()

        delivery = WebhookDelivery.objects.get()
        self.assertEqual(delivery.key, 'order-1')
        self.assertEqual(delivery.payload_hash, digest(raw))

    @patch(DELAY)
    def test_a_duplicate_does_not_extend_the_original_expiry(self, mock_delay):
        raw = json.dumps({'order_id': 1})
        self.post_secret(raw, key='order-1')
        original = WebhookDelivery.objects.get().expires_at

        self.post_secret(raw, key='order-1')

        self.assertEqual(WebhookDelivery.objects.get().expires_at, original)

    @patch(DELAY)
    def test_the_same_key_with_a_different_body_is_a_conflict(self, mock_delay):
        self.assertEqual(
            self.post_secret(json.dumps({'order_id': 1}), key='order-1').status_code,
            status.HTTP_202_ACCEPTED,
        )

        conflict = self.post_secret(json.dumps({'order_id': 2}), key='order-1')

        self.assertEqual(conflict.status_code, status.HTTP_409_CONFLICT)
        mock_delay.assert_called_once()
        # A conflict is refused before dispatch, so it cannot leave a run behind.
        self.assertEqual(AutomationRun.objects.count(), 0)
        self.assertEqual(WebhookDelivery.objects.count(), 1)

    @patch(DELAY)
    def test_a_missing_key_falls_back_to_the_raw_body_hash(self, mock_delay):
        raw = json.dumps({'order_id': 1})

        first = self.post_secret(raw)
        duplicate = self.post_secret(raw)

        self.assertEqual(first.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(duplicate.status_code, status.HTTP_200_OK)
        mock_delay.assert_called_once()
        self.assertEqual(
            WebhookDelivery.objects.get().key, 'sha256:' + digest(raw)
        )

    @patch(DELAY)
    def test_a_different_body_without_a_key_is_a_new_delivery(self, mock_delay):
        self.assertEqual(
            self.post_secret(json.dumps({'order_id': 1})).status_code,
            status.HTTP_202_ACCEPTED,
        )
        self.assertEqual(
            self.post_secret(json.dumps({'order_id': 2})).status_code,
            status.HTTP_202_ACCEPTED,
        )

        self.assertEqual(mock_delay.call_count, 2)
        self.assertEqual(WebhookDelivery.objects.count(), 2)

    @patch(DELAY)
    def test_an_overlong_key_is_rejected_without_dispatching(self, mock_delay):
        response = self.post_secret(json.dumps({'order_id': 1}), key='k' * 129)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_delay.assert_not_called()
        self.assertEqual(WebhookDelivery.objects.count(), 0)

    @patch(DELAY)
    def test_a_key_is_trimmed_before_it_is_stored(self, mock_delay):
        self.post_secret(json.dumps({'order_id': 1}), key='  order-1  ')

        self.assertEqual(WebhookDelivery.objects.get().key, 'order-1')


class SignedIdempotencyTests(WebhookIdempotencyBase):
    """The signature scheme covers the exact raw bytes, so the digest is computed
    over the same bytes the caller signed."""

    @patch(DELAY)
    def test_a_signed_retry_is_a_duplicate(self, mock_delay):
        raw = json.dumps({'order_id': 1})

        first = self.post_signed(raw, key='order-1')
        second = self.post_signed(raw, key='order-1')

        self.assertEqual(first.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertTrue(second.data['duplicate'])
        mock_delay.assert_called_once()

    @patch(DELAY)
    def test_signed_bodies_that_differ_only_in_whitespace_are_distinct(self, mock_delay):
        # Both are valid signatures over their own bytes, and the digest is over the
        # raw bytes, not a canonicalized parse - so these are two deliveries.
        first = self.post_signed('{"order_id": 1}')
        second = self.post_signed('{ "order_id": 1 }')

        self.assertEqual(first.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(second.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(mock_delay.call_count, 2)
        self.assertEqual(WebhookDelivery.objects.count(), 2)

    @patch(DELAY)
    def test_a_signed_conflict_is_refused(self, mock_delay):
        self.post_signed(json.dumps({'order_id': 1}), key='order-1')

        conflict = self.post_signed(json.dumps({'order_id': 2}), key='order-1')

        self.assertEqual(conflict.status_code, status.HTTP_409_CONFLICT)
        mock_delay.assert_called_once()


class IdempotencyScopeTests(WebhookIdempotencyBase):
    """A key is scoped to its trigger, not global."""

    @patch(DELAY)
    def test_the_same_key_on_another_trigger_is_independent(self, mock_delay):
        other = factories.make_trigger(
            self.automation, trigger_type='webhook', config={}
        )
        raw = json.dumps({'order_id': 1})

        self.assertEqual(
            self.post_secret(raw, key='order-1').status_code,
            status.HTTP_202_ACCEPTED,
        )
        self.assertEqual(
            self.post_signed(raw, key='order-1', trigger=other).status_code,
            status.HTTP_202_ACCEPTED,
        )

        self.assertEqual(mock_delay.call_count, 2)
        self.assertEqual(WebhookDelivery.objects.count(), 2)

    @patch(DELAY)
    def test_the_same_key_in_another_tenant_is_independent(self, mock_delay):
        other_tenant = factories.make_tenant(name='Globex')
        other_automation = factories.make_automation(other_tenant)
        other = factories.make_trigger(
            other_automation, trigger_type='webhook', config={}
        )
        raw = json.dumps({'order_id': 1})

        self.assertEqual(
            self.post_secret(raw, key='order-1').status_code,
            status.HTTP_202_ACCEPTED,
        )
        self.assertEqual(
            self.post_signed(raw, key='order-1', trigger=other).status_code,
            status.HTTP_202_ACCEPTED,
        )

        self.assertEqual(mock_delay.call_count, 2)
        self.assertEqual(WebhookDelivery.objects.count(), 2)


class ExpiryAndCleanupTests(WebhookIdempotencyBase):

    @patch(DELAY)
    def test_an_expired_key_is_reclaimed_immediately_with_a_new_body(self, mock_delay):
        old_body = json.dumps({'order_id': 1})
        factories.make_webhook_delivery(
            self.trigger,
            key='order-1',
            payload_hash=digest(old_body),
            expires_at=timezone.now() - timedelta(seconds=1),
        )

        response = self.post_secret(json.dumps({'order_id': 2}), key='order-1')

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_delay.assert_called_once()
        delivery = WebhookDelivery.objects.get()
        self.assertEqual(delivery.payload_hash, digest(json.dumps({'order_id': 2})))
        self.assertGreater(delivery.expires_at, timezone.now())

    def test_cleanup_deletes_expired_rows_and_keeps_fresh_ones(self):
        expired = factories.make_webhook_delivery(
            self.trigger, key='expired', expires_at=timezone.now() - timedelta(hours=1)
        )
        fresh = factories.make_webhook_delivery(
            self.trigger, key='fresh', expires_at=timezone.now() + timedelta(hours=1)
        )

        removed = cleanup_webhook_deliveries()

        self.assertEqual(removed, 1)
        self.assertFalse(WebhookDelivery.objects.filter(pk=expired.pk).exists())
        self.assertTrue(WebhookDelivery.objects.filter(pk=fresh.pk).exists())

    def test_beat_schedule_points_at_the_real_cleanup_task(self):
        entries = settings.CELERY_BEAT_SCHEDULE
        names = {entry['task'] for entry in entries.values()}

        self.assertIn(cleanup_webhook_deliveries.name, names)
        self.assertEqual(
            cleanup_webhook_deliveries.name,
            'apps.automations.tasks.cleanup_webhook_deliveries',
        )


class ConstraintAndReleaseTests(WebhookIdempotencyBase):

    def test_the_database_rejects_a_second_row_for_the_same_trigger_and_key(self):
        factories.make_webhook_delivery(self.trigger, key='order-1')

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                factories.make_webhook_delivery(self.trigger, key='order-1')

    @patch(DELAY)
    def test_a_dispatch_failure_releases_the_new_claim_and_allows_a_retry(self, mock_delay):
        raw = json.dumps({'order_id': 1})
        mock_delay.side_effect = RuntimeError('broker unavailable')

        failed = self.post_secret(raw, key='order-1')

        # The project's exception handler turns the unhandled broker error into a
        # 500 rather than letting it escape the view.
        self.assertEqual(failed.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        # The claim is given back, otherwise the caller's retry would be answered
        # "duplicate" for a delivery that never happened.
        self.assertEqual(WebhookDelivery.objects.count(), 0)

        mock_delay.side_effect = None
        retry = self.post_secret(raw, key='order-1')

        self.assertEqual(retry.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(mock_delay.call_count, 2)

    @patch(DELAY)
    def test_a_conflict_leaves_the_existing_claim_untouched(self, mock_delay):
        raw = json.dumps({'order_id': 1})
        self.post_secret(raw, key='order-1')
        original = WebhookDelivery.objects.get()

        self.post_secret(json.dumps({'order_id': 2}), key='order-1')

        delivery = WebhookDelivery.objects.get()
        self.assertEqual(delivery.payload_hash, original.payload_hash)
        self.assertEqual(delivery.expires_at, original.expires_at)

    @patch(DELAY)
    def test_the_delivery_row_holds_no_payload(self, mock_delay):
        sensitive = 'customer-secret-value'
        self.post_secret(json.dumps({'note': sensitive}), key='order-1')

        delivery = WebhookDelivery.objects.get()
        field_names = {field.name for field in delivery._meta.get_fields()}

        self.assertNotIn('payload', field_names)
        self.assertNotIn('body', field_names)
        self.assertEqual(delivery.payload_hash, digest(json.dumps({'note': sensitive})))
        self.assertNotIn(sensitive, ' '.join(
            str(getattr(delivery, name)) for name in field_names if hasattr(delivery, name)
        ))


class NonWebhookPathsAreNotDedupedTests(TestCase):
    """Only webhook ingress is deduped; a manual run with the same header still
    runs twice."""

    def setUp(self):
        self.client = APIClient()
        self.tenant = factories.make_tenant()
        self.owner = factories.make_owner(self.tenant)
        self.automation = factories.make_automation(self.tenant)
        factories.make_trigger(self.automation, trigger_type='manual', config={})

    @patch(DELAY)
    def test_a_manual_run_ignores_the_idempotency_header(self, mock_delay):
        self.client.force_authenticate(user=self.owner)
        url = f'/api/v1/automations/{self.automation.id}/run/'

        first = self.client.post(url, {}, format='json',
                                 headers={'Idempotency-Key': 'same-key'})
        second = self.client.post(url, {}, format='json',
                                  headers={'Idempotency-Key': 'same-key'})

        self.assertEqual(first.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(second.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(mock_delay.call_count, 2)
        self.assertEqual(WebhookDelivery.objects.count(), 0)
