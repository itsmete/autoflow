"""
Shared fixture builders.

Every test needs some slice of the tenant -> branch -> user hierarchy, and building
it inline in each setUp is how the existing tests drifted apart (one used
password="pass", another asserted a nested serializer as a bare uuid). Build it here
once so a model change breaks one file, not fifteen.
"""
import uuid
from datetime import timedelta

from django.utils import timezone

from apps.automations.models import (
    Automation,
    AutomationAction,
    AutomationCondition,
    AutomationEdge,
    AutomationLog,
    AutomationRun,
    AutomationStep,
    AutomationStepRun,
    AutomationTrigger,
    Template,
    WebhookDelivery,
)
from apps.channels.models import Channel
from apps.tenants.models import Branch, Tenant
from apps.users.models import User

# Long enough to satisfy the 8-char minimum on the password-change serializer.
PASSWORD = "password123"


def make_tenant(name="Acme", slug=None):
    return Tenant.objects.create(name=name, slug=slug or f"acme-{uuid.uuid4().hex[:8]}")


def make_branch(tenant, name="Branch", **kwargs):
    return Branch.objects.create(tenant=tenant, name=name, **kwargs)


def make_user(role, tenant=None, branch=None, email=None, **kwargs):
    return User.objects.create_user(
        email=email or f"{role}-{uuid.uuid4().hex[:8]}@test.com",
        password=PASSWORD,
        role=role,
        tenant=tenant,
        branch=branch,
        **kwargs,
    )


def make_super_admin(**kwargs):
    # is_staff mirrors what UserRegisterSerializer.create does for this role: it is
    # django's admin flag, not the staff ROLE.
    return make_user("super_admin", is_staff=True, **kwargs)


def make_owner(tenant, **kwargs):
    return make_user("owner", tenant=tenant, **kwargs)


def make_branch_manager(tenant, branch, **kwargs):
    return make_user("branch_manager", tenant=tenant, branch=branch, **kwargs)


def make_staff(tenant, branch, **kwargs):
    return make_user("staff", tenant=tenant, branch=branch, **kwargs)


EMAIL_CREDENTIALS = {
    "host": "smtp.example.com",
    "port": 587,
    "username": "sender@example.com",
    "password": "secret",
    "use_tls": True,
}

SMS_CREDENTIALS = {
    "api_url": "https://sms.example.com/send",
    "auth_type": "bearer",
    "auth_credentials": {"token": "abc123"},
    "from_number": "+905551112233",
    "body_template": {"phone": "{to}", "text": "{message}"},
}


def make_channel(tenant, branch=None, channel_type="email", credentials=None, name="Chan"):
    return Channel.objects.create(
        name=name,
        tenant=tenant,
        branch=branch,
        channel_type=channel_type,
        credentials=credentials if credentials is not None else dict(EMAIL_CREDENTIALS),
    )


def make_automation(tenant, branch=None, allowed_roles=None, name="Automation", **kwargs):
    return Automation.objects.create(
        name=name,
        tenant=tenant,
        branch=branch,
        allowed_roles=allowed_roles if allowed_roles is not None else ["super_admin", "owner"],
        metadata={},
        **kwargs,
    )


def make_trigger(automation, trigger_type="manual", config=None, initial_data=None):
    return AutomationTrigger.objects.create(
        automation=automation,
        trigger_type=trigger_type,
        config=config if config is not None else {},
        initial_data=initial_data if initial_data is not None else {},
    )


def make_webhook_delivery(trigger, key="test-key", payload_hash="a" * 64,
                          expires_at=None):
    return WebhookDelivery.objects.create(
        trigger=trigger,
        key=key,
        payload_hash=payload_hash,
        expires_at=expires_at or timezone.now() + timedelta(days=1),
    )


def make_condition(automation, field="amount", operator="gt", value="100",
                   logical_operator="and", quantifier="", on_missing="fail", order=0):
    return AutomationCondition.objects.create(
        automation=automation,
        field=field,
        operator=operator,
        value=value,
        logical_operator=logical_operator,
        quantifier=quantifier,
        on_missing=on_missing,
        order=order,
    )


def make_action(automation, action_type="webhook", order=1, on_failure="stop",
                config=None, channel=None):
    if config is None:
        config = {
            "url": "https://example.com/hook",
            "method": "POST",
            "headers": {},
            "payload": {},
        }
    return AutomationAction.objects.create(
        automation=automation,
        action_type=action_type,
        order=order,
        on_failure=on_failure,
        config=config,
        channel=channel,
    )


def make_step(automation, key="1", step_type="action", action_type="webhook",
              config=None, channel=None, template=None, on_failure="stop",
              position=None):
    # action_type is only meaningful for step_type="action"; pass "" for the other
    # step types so a fixture cannot imply a provider the step never uses.
    if config is None:
        config = {
            "url": "https://example.com/hook",
            "method": "POST",
            "headers": {},
            "payload": {},
        }
    return AutomationStep.objects.create(
        automation=automation,
        step_type=step_type,
        key=key,
        action_type=action_type,
        config=config,
        channel=channel,
        template=template,
        on_failure=on_failure,
        position=position,
    )


def make_edge(automation, from_step, to_step, branch_key="", order=0):
    # from_step=None is the entry edge into the graph, not a missing argument.
    return AutomationEdge.objects.create(
        automation=automation,
        from_step=from_step,
        to_step=to_step,
        branch_key=branch_key,
        order=order,
    )


def make_log(automation, status="success", error_message="", execution_context=None):
    return AutomationLog.objects.create(
        automation=automation,
        status=status,
        error_message=error_message,
        execution_context=execution_context if execution_context is not None else {},
    )


def make_run(automation, status="running", mode="live", **kwargs):
    # started_at/finished_at are left to the caller: a run built as 'running' has no
    # business claiming a finish time, and a fixture that filled one in would hide
    # exactly the bug (a run closed while still running) the durable tests look for.
    return AutomationRun.objects.create(
        automation=automation,
        status=status,
        mode=mode,
        trigger_payload=kwargs.pop("trigger_payload", {}),
        context=kwargs.pop("context", {}),
        **kwargs,
    )


def make_step_run(run, step_key="1", step_type="action", status="success",
                  attempt=1, input=None, output=None, decision=None, error="",
                  duration_ms=None, step=None):
    return AutomationStepRun.objects.create(
        run=run,
        step=step,
        step_key=step_key,
        step_type=step_type,
        status=status,
        attempt=attempt,
        input=input if input is not None else {},
        output=output,
        decision=decision,
        error=error,
        duration_ms=duration_ms,
    )


def make_template(tenant, branch=None, name="Greeting", raw_text="Hi {{customer}}",
                  fields=None, channel_type="email"):
    if fields is None:
        fields = {
            "customer": {
                "type": "string",
                "required": True,
                "description": "Customer name",
                "source": "payload",
            }
        }
    return Template.objects.create(
        name=name,
        raw_text=raw_text,
        fields=fields,
        tenant=tenant,
        branch=branch,
        channel_type=channel_type,
    )
