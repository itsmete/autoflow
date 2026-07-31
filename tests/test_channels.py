"""
apps/channels: provider registry, credential schema validation, and the CRUD API.

A Channel holds the credentials an action needs to reach an external service, so the
validation here is the last gate before a payload leaves the system.
"""
from unittest.mock import ANY, MagicMock, patch

from django.test import TestCase
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.channels.exceptions import ChannelError
from apps.channels.models import Channel, ChannelTypes
from apps.channels.providers import (
    EmailProvider,
    InstagramProvider,
    SMSProvider,
    WhatsAppProvider,
    get_provider_class,
)
from apps.channels.providers.schema_registry import get_field_type
from apps.channels.providers.validators import validate_phone_number
from apps.channels.serializers import get_serializer_class
from tests import factories
from tests.utils import CacheTestMixin


class ProviderRegistryTests(TestCase):

    def test_every_channel_type_has_a_provider(self):
        for value, _label in ChannelTypes.choices:
            self.assertIsNotNone(
                get_provider_class(value), f"no provider registered for {value}"
            )

    def test_every_channel_type_has_a_credential_serializer(self):
        for value, _label in ChannelTypes.choices:
            self.assertIsNotNone(
                get_serializer_class(value), f"no serializer registered for {value}"
            )

    def test_unknown_type_returns_none_rather_than_raising(self):
        self.assertIsNone(get_provider_class("carrier_pigeon"))
        self.assertIsNone(get_serializer_class("carrier_pigeon"))

    def test_providers_declare_a_credentials_schema(self):
        for cls in (EmailProvider, InstagramProvider, SMSProvider, WhatsAppProvider):
            self.assertTrue(
                getattr(cls, "CREDENTIALS_SCHEMA", None),
                f"{cls.__name__} has no CREDENTIALS_SCHEMA",
            )

    def test_field_type_map(self):
        self.assertIs(get_field_type("string"), str)
        self.assertIs(get_field_type("integer"), int)
        self.assertIs(get_field_type("boolean"), bool)
        self.assertIs(get_field_type("dict"), dict)
        self.assertIs(get_field_type("list"), list)
        self.assertIsNone(get_field_type("nope"))


class PhoneValidatorTests(TestCase):

    def test_accepts_an_international_number(self):
        validate_phone_number("+90 555 111 22 33")
        validate_phone_number("+905551112233")

    def test_rejects_a_number_without_a_country_prefix(self):
        with self.assertRaises(ValidationError):
            validate_phone_number("05551112233")

    def test_rejects_letters(self):
        with self.assertRaises(ValidationError):
            validate_phone_number("+90555ABC2233")


class CredentialSchemaTests(TestCase):
    """The shared _validate_schema walk in channels/serializers/base.py."""

    def _validate(self, channel_type, credentials):
        return get_serializer_class(channel_type)().validate_credentials(credentials)

    def test_valid_email_credentials_pass_through(self):
        result = self._validate("email", dict(factories.EMAIL_CREDENTIALS))
        self.assertEqual(result["host"], "smtp.example.com")

    def test_a_missing_required_field_is_rejected(self):
        creds = dict(factories.EMAIL_CREDENTIALS)
        del creds["host"]
        with self.assertRaises(ValidationError):
            self._validate("email", creds)

    def test_a_wrong_type_is_rejected(self):
        creds = dict(factories.EMAIL_CREDENTIALS, port="587")  # string, not int
        with self.assertRaises(ValidationError):
            self._validate("email", creds)

    def test_a_failing_field_validator_is_rejected(self):
        # EmailProvider runs django's validate_email over `username`.
        creds = dict(factories.EMAIL_CREDENTIALS, username="not-an-email")
        with self.assertRaises(ValidationError):
            self._validate("email", creds)

    def test_a_value_outside_choices_is_rejected(self):
        creds = dict(factories.SMS_CREDENTIALS, auth_type="telepathy")
        with self.assertRaises(ValidationError):
            self._validate("sms", creds)

    def test_nested_dict_fields_are_walked(self):
        creds = dict(factories.SMS_CREDENTIALS)
        creds["auth_credentials"] = "not-a-dict"
        with self.assertRaises(ValidationError):
            self._validate("sms", creds)

    def test_unknown_keys_are_ignored_not_rejected(self):
        creds = dict(factories.EMAIL_CREDENTIALS, extra_field="whatever")
        result = self._validate("email", creds)
        self.assertIn("host", result)


class SMSProviderTests(TestCase):

    def test_auth_handlers_cover_every_declared_choice(self):
        choices = SMSProvider.CREDENTIALS_SCHEMA["auth_type"]["choices"]
        for choice in choices:
            self.assertIn(choice, SMSProvider.AUTH_HANDLERS)

    def test_bearer_auth_builds_an_authorization_header(self):
        handler = SMSProvider.AUTH_HANDLERS["bearer"]
        self.assertEqual(
            handler({"token": "abc"}), {"headers": {"Authorization": "Bearer abc"}}
        )

    def test_basic_auth_builds_a_credentials_tuple(self):
        handler = SMSProvider.AUTH_HANDLERS["basic"]
        self.assertEqual(
            handler({"username": "u", "password": "p"}), {"auth": ("u", "p")}
        )

    @patch("apps.channels.providers.sms.requests.request")
    def test_send_formats_the_body_template_from_credentials_and_payload(self, mock_request):
        SMSProvider().send(
            dict(factories.SMS_CREDENTIALS),
            {"to": "+905559998877", "message": "hello"},
        )

        _args, kwargs = mock_request.call_args
        self.assertEqual(kwargs["url"], factories.SMS_CREDENTIALS["api_url"])
        self.assertEqual(kwargs["data"], {"phone": "+905559998877", "text": "hello"})
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer abc123"})

    @patch("apps.channels.providers.sms.requests.request", side_effect=OSError("boom"))
    def test_transport_failures_become_channel_errors(self, _mock_request):
        with self.assertRaises(ChannelError):
            SMSProvider().send(
                dict(factories.SMS_CREDENTIALS), {"to": "+905559998877", "message": "hi"}
            )


class EmailProviderTests(TestCase):

    @patch("apps.channels.providers.email.EmailBackend")
    def test_send_uses_the_credentials_as_smtp_settings(self, mock_backend):
        mock_backend.return_value.send_messages.return_value = 1

        result = EmailProvider().send(
            dict(factories.EMAIL_CREDENTIALS),
            {"to": "someone@example.com", "subject": "Hi", "body": "Hello"},
        )

        self.assertTrue(result)
        mock_backend.assert_called_once_with(
            **factories.EMAIL_CREDENTIALS, fail_silently=False
        )
        message = mock_backend.return_value.send_messages.call_args[0][0][0]
        self.assertEqual(message.to, ["someone@example.com"])
        self.assertEqual(message.subject, "Hi")
        self.assertEqual(message.from_email, "sender@example.com")

    @patch("apps.channels.providers.email.EmailBackend")
    def test_a_send_that_delivers_nothing_reports_false(self, mock_backend):
        mock_backend.return_value.send_messages.return_value = 0
        result = EmailProvider().send(
            dict(factories.EMAIL_CREDENTIALS),
            {"to": "someone@example.com", "subject": "Hi", "body": "Hello"},
        )
        self.assertFalse(result)

    @patch("apps.channels.providers.email.EmailBackend", side_effect=OSError("no smtp"))
    def test_transport_failures_become_channel_errors(self, _mock_backend):
        # Regression: the except branch logged with logger.error(msg, str(e)) and
        # referenced `to`/`subject` which were never assigned when the backend blew
        # up first, so the real error was replaced by an UnboundLocalError.
        with self.assertRaises(ChannelError):
            EmailProvider().send(
                dict(factories.EMAIL_CREDENTIALS),
                {"to": "someone@example.com", "subject": "Hi", "body": "Hello"},
            )


class ChannelAPITests(CacheTestMixin, TestCase):

    def setUp(self):
        super().setUp()
        self.client = APIClient()

        self.tenant = factories.make_tenant()
        self.other_tenant = factories.make_tenant(name="Globex")
        self.branch = factories.make_branch(self.tenant, "A")
        self.other_branch = factories.make_branch(self.other_tenant, "Z")

        self.super_admin = factories.make_super_admin()
        self.owner = factories.make_owner(self.tenant)
        self.manager = factories.make_branch_manager(self.tenant, self.branch)
        self.staff = factories.make_staff(self.tenant, self.branch)

        self.channel = factories.make_channel(self.tenant, self.branch)
        self.foreign_channel = factories.make_channel(
            self.other_tenant, self.other_branch, name="Foreign"
        )

    def _payload(self, **overrides):
        payload = {
            "name": "New Channel",
            "tenant": str(self.tenant.id),
            "branch": str(self.branch.id),
            "channel_type": "email",
            "credentials": dict(factories.EMAIL_CREDENTIALS),
        }
        payload.update(overrides)
        return payload

    # -- list ------------------------------------------------------------------

    def test_super_admin_lists_every_channel(self):
        self.client.force_authenticate(user=self.super_admin)
        response = self.client.get('/api/v1/channels/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

    def test_owner_only_sees_their_tenant(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get('/api/v1/channels/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['name'], "Chan")

    def test_branch_manager_only_sees_their_branch(self):
        self.client.force_authenticate(user=self.manager)
        response = self.client.get('/api/v1/channels/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_anonymous_users_are_rejected(self):
        response = self.client.get('/api/v1/channels/')
        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    # -- create ----------------------------------------------------------------

    def test_owner_can_create_a_channel_in_their_tenant(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post('/api/v1/channels/', self._payload(), format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Channel.objects.filter(name="New Channel").count(), 1)

    def test_creating_in_another_tenant_is_rejected(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            '/api/v1/channels/',
            self._payload(tenant=str(self.other_tenant.id), branch=str(self.other_branch.id)),
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_an_unknown_channel_type_is_rejected(self):
        self.client.force_authenticate(user=self.super_admin)
        response = self.client.post(
            '/api/v1/channels/', self._payload(channel_type="telepathy"), format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_credentials_are_rejected(self):
        self.client.force_authenticate(user=self.super_admin)
        bad = dict(factories.EMAIL_CREDENTIALS)
        del bad["password"]
        response = self.client.post(
            '/api/v1/channels/', self._payload(credentials=bad), format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_staff_may_not_create_channels(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.post('/api/v1/channels/', self._payload(), format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # -- detail ----------------------------------------------------------------

    def test_owner_can_read_their_own_channel(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(f'/api/v1/channels/{self.channel.id}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['name'], "Chan")

    def test_owner_cannot_read_another_tenants_channel(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(f'/api/v1/channels/{self.foreign_channel.id}/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_update_replaces_the_credentials(self):
        self.client.force_authenticate(user=self.owner)
        new_creds = dict(factories.EMAIL_CREDENTIALS, host="smtp2.example.com")
        response = self.client.put(
            f'/api/v1/channels/{self.channel.id}/',
            self._payload(name="Renamed", credentials=new_creds),
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.channel.refresh_from_db()
        self.assertEqual(self.channel.name, "Renamed")
        self.assertEqual(self.channel.credentials["host"], "smtp2.example.com")

    def test_delete_removes_the_channel(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.delete(f'/api/v1/channels/{self.channel.id}/')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Channel.objects.filter(pk=self.channel.pk).exists())

    def test_deleting_another_tenants_channel_is_refused(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.delete(f'/api/v1/channels/{self.foreign_channel.id}/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Channel.objects.filter(pk=self.foreign_channel.pk).exists())
