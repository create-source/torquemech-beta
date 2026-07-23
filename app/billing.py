from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo
from typing import Any


PRO_SOLO_PLAN_CODE = "pro_solo"
PRO_SOLO_PLAN_NAME = "TorqueMech Pro Solo"
SUPPORTED_WEBHOOK_EVENTS = {
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.paid",
    "invoice.payment_failed",
}
ENDED_SUBSCRIPTION_ACCESS_STATES = {
    "read_only_canceled",
    "read_only_trial_expired",
    "read_only_no_entitlement",
}
PAYMENT_PROBLEM_ACCESS_STATES = {"read_only_past_due", "read_only_unpaid"}
TERMINAL_SUBSCRIPTION_STATUSES = {"canceled", "incomplete_expired"}


class BillingConfigurationError(RuntimeError):
    pass


class BillingCustomerRequiredError(RuntimeError):
    pass


class BillingProviderError(RuntimeError):
    pass


class BillingSignatureError(RuntimeError):
    pass


@dataclass(frozen=True)
class SubscriptionAccess:
    access_state: str
    has_full_access: bool
    is_read_only: bool
    trial_started_at: datetime | None
    trial_ends_at: datetime | None
    trial_days_remaining: int
    stripe_subscription_status: str | None
    cancel_at_period_end: bool
    current_period_end: datetime | None
    message: str
    plan_code: str = PRO_SOLO_PLAN_CODE
    plan_name: str = PRO_SOLO_PLAN_NAME
    shop_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        access_ends_at = self.trial_ends_at if self.access_state == "trial_active" else self.current_period_end
        return {
            "access_state": self.access_state,
            "has_full_access": self.has_full_access,
            "is_read_only": self.is_read_only,
            "trial_started_at": self.trial_started_at.isoformat() if self.trial_started_at else None,
            "trial_ends_at": self.trial_ends_at.isoformat() if self.trial_ends_at else None,
            "trial_days_remaining": self.trial_days_remaining,
            "stripe_subscription_status": self.stripe_subscription_status,
            "cancel_at_period_end": self.cancel_at_period_end,
            "current_period_end": self.current_period_end.isoformat() if self.current_period_end else None,
            "current_period_ends_at": self.current_period_end.isoformat() if self.current_period_end else None,
            "message": self.message,
            "reason": self.access_state,
            "status": self.stripe_subscription_status or "none",
            "plan_code": self.plan_code,
            "plan_name": self.plan_name,
            "shop_id": self.shop_id,
            "can_view": bool(self.shop_id),
            "can_write": self.has_full_access,
            "can_manage_billing": bool(self.shop_id),
            "access_ends_at": access_ends_at.isoformat() if access_ends_at else None,
        }


@dataclass(frozen=True)
class StripeBillingConfig:
    secret_key: str
    publishable_key: str
    webhook_secret: str
    pro_solo_monthly_price_id: str

    @classmethod
    def from_env(cls) -> "StripeBillingConfig":
        return cls(
            secret_key=(os.getenv("STRIPE_SECRET_KEY") or "").strip(),
            publishable_key=(os.getenv("STRIPE_PUBLISHABLE_KEY") or "").strip(),
            webhook_secret=(os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip(),
            pro_solo_monthly_price_id=(os.getenv("STRIPE_PRO_SOLO_MONTHLY_PRICE_ID") or "").strip(),
        )

    def require_checkout(self) -> None:
        missing = [
            name
            for name, value in (
                ("STRIPE_SECRET_KEY", self.secret_key),
                ("STRIPE_PRO_SOLO_MONTHLY_PRICE_ID", self.pro_solo_monthly_price_id),
            )
            if not value
        ]
        if missing:
            raise BillingConfigurationError(f"Stripe billing is not configured. Missing: {', '.join(missing)}.")

    def require_portal(self) -> None:
        if not self.secret_key:
            raise BillingConfigurationError("Stripe billing is not configured. Missing: STRIPE_SECRET_KEY.")

    def require_webhook(self) -> None:
        if not self.webhook_secret:
            raise BillingConfigurationError("Stripe webhooks are not configured. Missing: STRIPE_WEBHOOK_SECRET.")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_utc_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def bool_from_subscription_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    raw = str(value or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def remaining_trial_days(trial_ends_at: datetime | None, now: datetime) -> int:
    if not trial_ends_at:
        return 0
    seconds = (trial_ends_at - now).total_seconds()
    if seconds <= 0:
        return 0
    return max(1, math.ceil(seconds / 86400))


def format_billing_display_date(value: Any, *, display_tz: tzinfo | None = None) -> str:
    parsed = parse_utc_datetime(value)
    if not parsed:
        return ""
    if display_tz is not None:
        parsed = parsed.astimezone(display_tz)
    return parsed.strftime("%m/%d/%Y")


def billing_status_label(
    *,
    status: str | None,
    access_state: str,
    cancel_at_period_end: bool,
    cancellation_date_display: str,
) -> str:
    normalized = str(status or "").strip().lower()
    if access_state == "trial_active":
        return "Trial active"
    if access_state == "subscribed_canceling":
        return f"Cancels on {cancellation_date_display}" if cancellation_date_display else "Cancellation scheduled"
    if access_state == "subscribed_active":
        return "Active"
    if access_state == "read_only_past_due" or normalized == "past_due":
        return "Past due"
    if access_state == "read_only_unpaid" or normalized == "unpaid":
        return "Payment required"
    if normalized == "incomplete":
        return "Incomplete subscription"
    if normalized == "paused":
        return "Paused"
    if access_state in ENDED_SUBSCRIPTION_ACCESS_STATES or normalized in {"canceled", "incomplete_expired"}:
        return "Subscription ended"
    if cancel_at_period_end and cancellation_date_display:
        return f"Cancels on {cancellation_date_display}"
    return "Read-only access" if access_state.startswith("read_only") else "Active"


def build_billing_display(
    subscription: dict[str, Any] | None,
    access: dict[str, Any] | SubscriptionAccess | None,
    *,
    now: datetime | None = None,
    display_tz: tzinfo | None = None,
) -> dict[str, Any]:
    access_dict = access.to_dict() if isinstance(access, SubscriptionAccess) else dict(access or {})
    subscription = dict(subscription or {})
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    access_state = str(access_dict.get("access_state") or "read_only_no_entitlement")
    status = str(subscription.get("status") or access_dict.get("stripe_subscription_status") or "").strip().lower()
    cancel_at_period_end = bool_from_subscription_value(
        subscription.get("cancel_at_period_end", access_dict.get("cancel_at_period_end"))
    )
    trial_ends_at = parse_utc_datetime(subscription.get("trial_ends_at") or access_dict.get("trial_ends_at"))
    current_period_end = parse_utc_datetime(
        subscription.get("current_period_ends_at")
        or subscription.get("current_period_end")
        or access_dict.get("current_period_ends_at")
        or access_dict.get("current_period_end")
    )
    canceled_at = parse_utc_datetime(subscription.get("canceled_at"))
    plan_code = str(subscription.get("plan_code") or access_dict.get("plan_code") or PRO_SOLO_PLAN_CODE)
    plan_display_name = PRO_SOLO_PLAN_NAME if plan_code == PRO_SOLO_PLAN_CODE else str(access_dict.get("plan_name") or plan_code or PRO_SOLO_PLAN_NAME)
    trial_end_display = format_billing_display_date(trial_ends_at, display_tz=display_tz)
    renewal_date_display = format_billing_display_date(current_period_end, display_tz=display_tz)
    cancellation_source = current_period_end if cancel_at_period_end else canceled_at
    cancellation_date_display = format_billing_display_date(cancellation_source, display_tz=display_tz)
    days_remaining = remaining_trial_days(trial_ends_at, current)
    has_customer = bool(str(subscription.get("stripe_customer_id") or "").strip())
    is_read_only = bool(access_dict.get("is_read_only"))
    show_manage_subscription = bool(has_customer and access_state in {
        "subscribed_active",
        "subscribed_canceling",
        "trial_active",
        "read_only_past_due",
        "read_only_unpaid",
    })
    ended_or_setup_problem = access_state in ENDED_SUBSCRIPTION_ACCESS_STATES or status in {
        "canceled",
        "incomplete",
        "incomplete_expired",
        "paused",
    }
    show_reactivate = bool(ended_or_setup_problem and (has_customer or str(subscription.get("stripe_subscription_id") or "").strip()))
    show_subscribe = bool(access_state in ENDED_SUBSCRIPTION_ACCESS_STATES or status in {"incomplete", "incomplete_expired", "paused"} or not subscription)
    if show_reactivate:
        show_subscribe = False

    display_status = billing_status_label(
        status=status,
        access_state=access_state,
        cancel_at_period_end=cancel_at_period_end,
        cancellation_date_display=cancellation_date_display,
    )
    status_tone = "neutral"
    if access_state in {"subscribed_active", "trial_active"}:
        status_tone = "success"
    elif access_state == "subscribed_canceling":
        status_tone = "warning"
    elif access_state in PAYMENT_PROBLEM_ACCESS_STATES or status in {"incomplete", "paused"}:
        status_tone = "warning"
    elif is_read_only:
        status_tone = "danger" if status == "unpaid" else "muted"

    if access_state == "trial_active":
        summary_message = f"Your free trial for {plan_display_name} is active."
    elif access_state == "subscribed_canceling":
        if cancellation_date_display:
            summary_message = f"Your subscription is scheduled to end on {cancellation_date_display}. You can continue using TorqueMech Pro until then."
        else:
            summary_message = "Your subscription is scheduled to end after the current billing period. You can continue using TorqueMech Pro until then."
    elif access_state == "subscribed_active":
        summary_message = f"Your {plan_display_name} subscription is active."
    elif access_state == "read_only_past_due":
        summary_message = "We could not confirm your latest payment. Update your billing information to restore full access."
    elif access_state == "read_only_unpaid":
        summary_message = "Payment is required to continue using TorqueMech Pro."
    elif status == "incomplete":
        summary_message = "Subscription setup was not completed. Finish subscribing to unlock full access."
    elif status == "paused":
        summary_message = "Your subscription is paused. Resume billing to restore full access."
    else:
        summary_message = "Your subscription has ended. Your TorqueMech information is still available in read-only mode."

    return {
        "plan_display_name": plan_display_name,
        "display_status": display_status,
        "status_tone": status_tone,
        "summary_message": summary_message,
        "trial_end_display": trial_end_display,
        "renewal_date_display": renewal_date_display,
        "cancellation_date_display": cancellation_date_display,
        "days_remaining": days_remaining,
        "is_read_only": is_read_only,
        "read_only_message": "Existing records remain viewable, but creating or editing records requires an active subscription.",
        "show_manage_subscription": show_manage_subscription,
        "show_subscribe": show_subscribe,
        "show_reactivate": show_reactivate,
        "stripe_management_available": has_customer,
        "is_trial": access_state == "trial_active",
        "is_scheduled_cancellation": access_state == "subscribed_canceling",
        "is_payment_problem": access_state in PAYMENT_PROBLEM_ACCESS_STATES,
        "is_ended": access_state in ENDED_SUBSCRIPTION_ACCESS_STATES or status in {"canceled", "incomplete_expired"},
        "setup_incomplete": status in {"incomplete", "incomplete_expired"},
        "access_state": access_state,
    }


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def stripe_result_to_dict(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return dict(result)
    for method_name in ("to_dict_recursive", "to_dict"):
        method = getattr(result, method_name, None)
        if callable(method):
            converted = method()
            if isinstance(converted, dict):
                return converted
    try:
        return dict(result)
    except (TypeError, ValueError, KeyError):
        try:
            return {
                key: value
                for key, value in vars(result).items()
                if not key.startswith("_")
            }
        except TypeError:
            return {}


def stripe_object_value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    try:
        return obj[key]
    except (TypeError, KeyError, IndexError):
        return getattr(obj, key, default)


def normalize_stripe_id(value: Any) -> str:
    return str(value or "").strip()


def normalize_subscription_object(subscription: Any) -> dict[str, Any]:
    normalized = stripe_result_to_dict(subscription)
    items = normalized.get("items")
    if items is not None and not isinstance(items, dict):
        normalized["items"] = stripe_result_to_dict(items)
    customer = normalized.get("customer")
    if customer is not None and not isinstance(customer, str):
        normalized["customer"] = stripe_object_value(customer, "id", customer)
    return normalized


def load_subscription(conn: sqlite3.Connection, shop_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM shop_subscriptions WHERE shop_id = ? LIMIT 1",
        (shop_id,),
    ).fetchone()
    return row_to_dict(row)


def load_subscription_by_stripe_subscription_id(conn: sqlite3.Connection, stripe_subscription_id: str) -> dict[str, Any] | None:
    subscription_id = normalize_stripe_id(stripe_subscription_id)
    if not subscription_id:
        return None
    row = conn.execute(
        "SELECT * FROM shop_subscriptions WHERE stripe_subscription_id = ? LIMIT 1",
        (subscription_id,),
    ).fetchone()
    return row_to_dict(row)


def load_subscription_by_stripe_customer_id(conn: sqlite3.Connection, stripe_customer_id: str) -> dict[str, Any] | None:
    customer_id = normalize_stripe_id(stripe_customer_id)
    if not customer_id:
        return None
    row = conn.execute(
        "SELECT * FROM shop_subscriptions WHERE stripe_customer_id = ? LIMIT 1",
        (customer_id,),
    ).fetchone()
    return row_to_dict(row)


def resolve_subscription_for_stripe_event(
    conn: sqlite3.Connection,
    *,
    stripe_subscription_id: str,
    stripe_customer_id: str,
    metadata_shop_id_value: Any = None,
    allow_metadata_fallback: bool = False,
    allow_subscription_replacement: bool = False,
) -> dict[str, Any] | None:
    subscription_id = normalize_stripe_id(stripe_subscription_id)
    customer_id = normalize_stripe_id(stripe_customer_id)
    by_subscription = load_subscription_by_stripe_subscription_id(conn, subscription_id)
    by_customer = load_subscription_by_stripe_customer_id(conn, customer_id)

    if by_subscription and by_customer and by_subscription.get("shop_id") != by_customer.get("shop_id"):
        return None

    resolved = by_subscription or by_customer
    if resolved:
        stored_subscription_id = normalize_stripe_id(resolved.get("stripe_subscription_id"))
        stored_customer_id = normalize_stripe_id(resolved.get("stripe_customer_id"))
        if (
            subscription_id
            and stored_subscription_id
            and subscription_id != stored_subscription_id
            and not allow_subscription_replacement
        ):
            return None
        if customer_id and stored_customer_id and customer_id != stored_customer_id:
            return None
        metadata_shop_id = None
        try:
            metadata_shop_id = int(metadata_shop_id_value or 0) or None
        except (TypeError, ValueError):
            metadata_shop_id = None
        if metadata_shop_id and int(resolved.get("shop_id") or 0) != metadata_shop_id:
            return None
        return resolved

    if allow_metadata_fallback:
        try:
            shop_id = int(metadata_shop_id_value or 0)
        except (TypeError, ValueError):
            shop_id = 0
        if shop_id > 0:
            return load_subscription(conn, shop_id) or {"shop_id": shop_id}
    return None


def is_terminal_subscription_row(subscription: dict[str, Any] | None) -> bool:
    if not subscription:
        return False
    status = str(subscription.get("status") or "").strip().lower()
    if status == "incomplete_expired":
        return True
    if status != "canceled":
        return False
    if bool_from_subscription_value(subscription.get("cancel_at_period_end")):
        current_period_end = parse_utc_datetime(subscription.get("current_period_ends_at"))
        if current_period_end and current_period_end > datetime.now(timezone.utc):
            return False
    return True


def require_existing_subscription(conn: sqlite3.Connection, shop_id: int) -> dict[str, Any]:
    subscription = load_subscription(conn, shop_id)
    if subscription:
        return subscription
    raise BillingCustomerRequiredError("Billing is not active for this shop yet.")


def resolve_subscription_access(
    subscription: dict[str, Any] | None,
    *,
    now: datetime | None = None,
    shop_id: int | None = None,
) -> SubscriptionAccess:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    current_shop_id = shop_id
    if current_shop_id is None and subscription:
        try:
            current_shop_id = int(subscription.get("shop_id") or 0) or None
        except (TypeError, ValueError):
            current_shop_id = None

    if not current_shop_id:
        return SubscriptionAccess(
            access_state="read_only_no_entitlement",
            has_full_access=False,
            is_read_only=True,
            trial_started_at=None,
            trial_ends_at=None,
            trial_days_remaining=0,
            stripe_subscription_status=None,
            cancel_at_period_end=False,
            current_period_end=None,
            message="No shop entitlement is available for this account.",
            shop_id=None,
        )

    if not subscription:
        return SubscriptionAccess(
            access_state="read_only_no_entitlement",
            has_full_access=False,
            is_read_only=True,
            trial_started_at=None,
            trial_ends_at=None,
            trial_days_remaining=0,
            stripe_subscription_status=None,
            cancel_at_period_end=False,
            current_period_end=None,
            message="Start a trial or subscription to unlock full access.",
            shop_id=current_shop_id,
        )

    status = str(subscription.get("status") or "").strip().lower() or None
    trial_started_at = parse_utc_datetime(subscription.get("trial_started_at"))
    trial_ends_at = parse_utc_datetime(subscription.get("trial_ends_at"))
    current_period_end = parse_utc_datetime(
        subscription.get("current_period_ends_at")
        or subscription.get("current_period_end")
        or subscription.get("subscription_current_period_end")
    )
    cancel_at_period_end = bool_from_subscription_value(
        subscription.get("cancel_at_period_end", subscription.get("subscription_cancel_at_period_end"))
    )
    trial_days = remaining_trial_days(trial_ends_at, current)
    plan_code = str(subscription.get("plan_code") or PRO_SOLO_PLAN_CODE)
    has_durable_cancel_flag = "cancel_at_period_end" in subscription

    # Legacy rows created before a cancel_at_period_end column still represented
    # scheduled cancellation as status=canceled with a future period end.
    legacy_canceling = (
        not has_durable_cancel_flag
        and status == "canceled"
        and current_period_end is not None
        and current_period_end > current
    )

    if status == "development":
        return SubscriptionAccess(
            access_state="subscribed_active",
            has_full_access=True,
            is_read_only=False,
            trial_started_at=trial_started_at,
            trial_ends_at=trial_ends_at,
            trial_days_remaining=trial_days,
            stripe_subscription_status=status,
            cancel_at_period_end=cancel_at_period_end,
            current_period_end=current_period_end,
            message="Your subscription is active.",
            plan_code=plan_code,
            shop_id=current_shop_id,
        )
    if status == "trialing" and trial_ends_at and trial_ends_at > current:
        return SubscriptionAccess(
            access_state="trial_active",
            has_full_access=True,
            is_read_only=False,
            trial_started_at=trial_started_at,
            trial_ends_at=trial_ends_at,
            trial_days_remaining=trial_days,
            stripe_subscription_status=status,
            cancel_at_period_end=cancel_at_period_end,
            current_period_end=current_period_end,
            message="Your trial is active.",
            plan_code=plan_code,
            shop_id=current_shop_id,
        )
    if status == "active" and cancel_at_period_end and current_period_end and current_period_end <= current:
        return SubscriptionAccess(
            access_state="read_only_canceled",
            has_full_access=False,
            is_read_only=True,
            trial_started_at=trial_started_at,
            trial_ends_at=trial_ends_at,
            trial_days_remaining=trial_days,
            stripe_subscription_status=status,
            cancel_at_period_end=cancel_at_period_end,
            current_period_end=current_period_end,
            message="This subscription has ended.",
            plan_code=plan_code,
            shop_id=current_shop_id,
        )
    if status == "active":
        state = "subscribed_canceling" if cancel_at_period_end and current_period_end and current_period_end > current else "subscribed_active"
        return SubscriptionAccess(
            access_state=state,
            has_full_access=True,
            is_read_only=False,
            trial_started_at=trial_started_at,
            trial_ends_at=trial_ends_at,
            trial_days_remaining=trial_days,
            stripe_subscription_status=status,
            cancel_at_period_end=cancel_at_period_end,
            current_period_end=current_period_end,
            message="Your subscription remains active until the current period ends." if state == "subscribed_canceling" else "Your subscription is active.",
            plan_code=plan_code,
            shop_id=current_shop_id,
        )
    if status == "trialing" and not trial_ends_at and current_period_end and current_period_end > current:
        return SubscriptionAccess(
            access_state="subscribed_active",
            has_full_access=True,
            is_read_only=False,
            trial_started_at=trial_started_at,
            trial_ends_at=trial_ends_at,
            trial_days_remaining=0,
            stripe_subscription_status=status,
            cancel_at_period_end=cancel_at_period_end,
            current_period_end=current_period_end,
            message="Your Stripe trial is active.",
            plan_code=plan_code,
            shop_id=current_shop_id,
        )
    if (status == "canceled" and cancel_at_period_end and current_period_end and current_period_end > current) or legacy_canceling:
        return SubscriptionAccess(
            access_state="subscribed_canceling",
            has_full_access=True,
            is_read_only=False,
            trial_started_at=trial_started_at,
            trial_ends_at=trial_ends_at,
            trial_days_remaining=trial_days,
            stripe_subscription_status=status,
            cancel_at_period_end=True,
            current_period_end=current_period_end,
            message="Your subscription remains active until the current period ends.",
            plan_code=plan_code,
            shop_id=current_shop_id,
        )

    if status == "past_due":
        state, message = "read_only_past_due", "Payment is past due. Full access resumes after billing is updated."
    elif status == "unpaid":
        state, message = "read_only_unpaid", "Payment is unpaid. Full access resumes after billing is updated."
    elif status in {"canceled", "incomplete_expired"}:
        state, message = "read_only_canceled", "This subscription has ended."
    elif status == "incomplete":
        state, message = "read_only_no_entitlement", "Subscription setup is incomplete. Update billing to unlock full access."
    elif status == "paused":
        state, message = "read_only_no_entitlement", "This subscription is paused. Update billing to unlock full access."
    elif status == "trialing":
        state, message = "read_only_trial_expired", "Your trial has expired."
    else:
        state, message = "read_only_no_entitlement", "Start a trial or subscription to unlock full access."
    return SubscriptionAccess(
        access_state=state,
        has_full_access=False,
        is_read_only=True,
        trial_started_at=trial_started_at,
        trial_ends_at=trial_ends_at,
        trial_days_remaining=trial_days,
        stripe_subscription_status=status,
        cancel_at_period_end=cancel_at_period_end,
        current_period_end=current_period_end,
        message=message,
        plan_code=plan_code,
        shop_id=current_shop_id,
    )


def stripe_client() -> Any:
    try:
        import stripe
    except ImportError as exc:
        raise BillingConfigurationError("Stripe billing is unavailable because the stripe package is not installed.") from exc
    return stripe


class StripeBillingService:
    def __init__(self, config: StripeBillingConfig | None = None, stripe_api: Any | None = None):
        self.config = config or StripeBillingConfig.from_env()
        self.stripe_api = stripe_api

    def _stripe(self) -> Any:
        api = self.stripe_api or stripe_client()
        api.api_key = self.config.secret_key
        return api

    def create_checkout_session(
        self,
        conn: sqlite3.Connection,
        *,
        shop_id: int,
        shop_email: str = "",
        success_url: str,
        cancel_url: str,
    ) -> dict[str, Any]:
        self.config.require_checkout()
        subscription = load_subscription(conn, shop_id)
        customer_id = str((subscription or {}).get("stripe_customer_id") or "").strip()
        session_params: dict[str, Any] = {
            "mode": "subscription",
            "line_items": [{"price": self.config.pro_solo_monthly_price_id, "quantity": 1}],
            "success_url": success_url,
            "cancel_url": cancel_url,
            "client_reference_id": str(shop_id),
            "metadata": {"shop_id": str(shop_id), "plan_code": PRO_SOLO_PLAN_CODE},
            "subscription_data": {
                "metadata": {"shop_id": str(shop_id), "plan_code": PRO_SOLO_PLAN_CODE}
            },
        }
        if customer_id:
            session_params["customer"] = customer_id
        elif shop_email:
            session_params["customer_email"] = shop_email
        try:
            session = self._stripe().checkout.Session.create(**session_params)
        except Exception as exc:
            raise BillingProviderError("Stripe Checkout is temporarily unavailable. Please try again.") from exc
        return stripe_result_to_dict(session)

    def create_customer_portal_session(
        self,
        conn: sqlite3.Connection,
        *,
        shop_id: int,
        return_url: str,
    ) -> dict[str, Any]:
        self.config.require_portal()
        subscription = load_subscription(conn, shop_id)
        customer_id = str((subscription or {}).get("stripe_customer_id") or "").strip()
        if not customer_id:
            raise BillingCustomerRequiredError("Billing is not active for this shop yet.")
        try:
            session = self._stripe().billing_portal.Session.create(customer=customer_id, return_url=return_url)
        except Exception as exc:
            raise BillingProviderError("Stripe Billing Portal is temporarily unavailable. Please try again.") from exc
        return stripe_result_to_dict(session)


def parse_stripe_signature_header(signature_header: str) -> tuple[str, list[str]]:
    timestamp = ""
    signatures: list[str] = []
    for part in str(signature_header or "").split(","):
        key, _, value = part.partition("=")
        if key == "t":
            timestamp = value
        elif key == "v1":
            signatures.append(value)
    return timestamp, signatures


def verify_webhook_payload(
    raw_body: bytes,
    signature_header: str,
    *,
    webhook_secret: str,
    tolerance_seconds: int = 300,
    now: int | None = None,
) -> dict[str, Any]:
    secret = str(webhook_secret or "").strip()
    if not secret:
        raise BillingConfigurationError("Stripe webhooks are not configured. Missing: STRIPE_WEBHOOK_SECRET.")
    timestamp, signatures = parse_stripe_signature_header(signature_header)
    if not timestamp or not signatures:
        raise BillingSignatureError("Invalid Stripe webhook signature.")
    try:
        timestamp_int = int(timestamp)
    except ValueError as exc:
        raise BillingSignatureError("Invalid Stripe webhook signature.") from exc
    current = int(now if now is not None else time.time())
    if tolerance_seconds and abs(current - timestamp_int) > tolerance_seconds:
        raise BillingSignatureError("Invalid Stripe webhook signature.")
    signed_payload = f"{timestamp}.".encode("utf-8") + raw_body
    expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, signature) for signature in signatures):
        raise BillingSignatureError("Invalid Stripe webhook signature.")
    try:
        event = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise BillingSignatureError("Invalid Stripe webhook payload.") from exc
    if not isinstance(event, dict):
        raise BillingSignatureError("Invalid Stripe webhook payload.")
    return event


def stripe_timestamp(value: Any) -> str | None:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def metadata_shop_id(obj: dict[str, Any]) -> int | None:
    metadata = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
    raw = metadata.get("shop_id") or obj.get("client_reference_id")
    try:
        shop_id = int(raw)
    except (TypeError, ValueError):
        return None
    return shop_id if shop_id > 0 else None


def status_from_subscription(subscription: dict[str, Any], fallback: str = "active") -> str:
    status = str(subscription.get("status") or fallback or "active").strip().lower()
    if status == "canceled":
        return "canceled"
    return status or "active"


def update_subscription_for_shop(
    conn: sqlite3.Connection,
    *,
    shop_id: int,
    status: str,
    cancel_at_period_end: bool | int | None = None,
    stripe_customer_id: str | None = None,
    stripe_subscription_id: str | None = None,
    stripe_price_id: str | None = None,
    current_period_started_at: str | None = None,
    current_period_ends_at: str | None = None,
    canceled_at: str | None = None,
    current_period_started_at_provided: bool = False,
    current_period_ends_at_provided: bool = False,
    canceled_at_provided: bool = False,
) -> dict[str, Any] | None:
    existing = load_subscription(conn, shop_id) or {}
    now = utc_now_iso()
    if cancel_at_period_end is None:
        stored_cancel_at_period_end = bool_from_subscription_value(existing.get("cancel_at_period_end"))
    else:
        stored_cancel_at_period_end = bool_from_subscription_value(cancel_at_period_end)
    values = {
        "plan_code": PRO_SOLO_PLAN_CODE,
        "status": status or existing.get("status") or "active",
        "cancel_at_period_end": stored_cancel_at_period_end,
        "current_period_started_at": current_period_started_at if current_period_started_at_provided else (current_period_started_at or existing.get("current_period_started_at")),
        "current_period_ends_at": current_period_ends_at if current_period_ends_at_provided else (current_period_ends_at or existing.get("current_period_ends_at")),
        "canceled_at": canceled_at if canceled_at_provided else (canceled_at if canceled_at is not None else existing.get("canceled_at")),
        "stripe_customer_id": stripe_customer_id or existing.get("stripe_customer_id"),
        "stripe_subscription_id": stripe_subscription_id or existing.get("stripe_subscription_id"),
        "stripe_price_id": stripe_price_id or existing.get("stripe_price_id"),
        "updated_at": now,
    }
    if existing:
        conn.execute(
            """
            UPDATE shop_subscriptions
            SET plan_code = ?,
                status = ?,
                current_period_started_at = ?,
                current_period_ends_at = ?,
                cancel_at_period_end = ?,
                canceled_at = ?,
                stripe_customer_id = ?,
                stripe_subscription_id = ?,
                stripe_price_id = ?,
                updated_at = ?
            WHERE shop_id = ?
            """,
            (
                values["plan_code"],
                values["status"],
                values["current_period_started_at"],
                values["current_period_ends_at"],
                values["cancel_at_period_end"],
                values["canceled_at"],
                values["stripe_customer_id"],
                values["stripe_subscription_id"],
                values["stripe_price_id"],
                values["updated_at"],
                shop_id,
            ),
        )
    else:
        conn.execute(
            """
            INSERT INTO shop_subscriptions (
              shop_id, plan_code, status, trial_started_at, trial_ends_at,
              current_period_started_at, current_period_ends_at, cancel_at_period_end, canceled_at,
              access_grace_ends_at, stripe_customer_id, stripe_subscription_id,
              stripe_price_id, created_at, updated_at
            )
            VALUES (?, ?, ?, NULL, NULL, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
            """,
            (
                shop_id,
                values["plan_code"],
                values["status"],
                values["current_period_started_at"],
                values["current_period_ends_at"],
                values["cancel_at_period_end"],
                values["canceled_at"],
                values["stripe_customer_id"],
                values["stripe_subscription_id"],
                values["stripe_price_id"],
                now,
                values["updated_at"],
            ),
        )
    return load_subscription(conn, shop_id)


def price_id_from_subscription(subscription: dict[str, Any]) -> str | None:
    items = subscription.get("items")
    data = items.get("data") if isinstance(items, dict) else None
    if not isinstance(data, list) or not data:
        return None
    price = data[0].get("price") if isinstance(data[0], dict) else None
    if isinstance(price, dict):
        return str(price.get("id") or "").strip() or None
    return None


def sync_checkout_session_completed(conn: sqlite3.Connection, session: dict[str, Any]) -> dict[str, Any] | None:
    subscription = session.get("subscription")
    if isinstance(subscription, dict):
        return sync_subscription_object(
            conn,
            subscription,
            allow_metadata_fallback=True,
            allow_subscription_replacement=True,
        )
    shop_id = metadata_shop_id(session)
    if not shop_id:
        return None
    return update_subscription_for_shop(
        conn,
        shop_id=shop_id,
        status="active",
        stripe_customer_id=str(session.get("customer") or "").strip() or None,
        stripe_subscription_id=str(session.get("subscription") or "").strip() or None,
    )


def sync_subscription_object(
    conn: sqlite3.Connection,
    subscription: dict[str, Any],
    *,
    deleted: bool = False,
    allow_metadata_fallback: bool = False,
    allow_subscription_replacement: bool = False,
) -> dict[str, Any] | None:
    subscription = normalize_subscription_object(subscription)
    subscription_id = normalize_stripe_id(subscription.get("id"))
    customer_id = normalize_stripe_id(subscription.get("customer"))
    metadata = subscription.get("metadata") if isinstance(subscription.get("metadata"), dict) else {}
    resolved_subscription = resolve_subscription_for_stripe_event(
        conn,
        stripe_subscription_id=subscription_id,
        stripe_customer_id=customer_id,
        metadata_shop_id_value=metadata.get("shop_id"),
        allow_metadata_fallback=allow_metadata_fallback,
        allow_subscription_replacement=allow_subscription_replacement,
    )
    if not resolved_subscription:
        return None
    if (
        not deleted
        and not allow_subscription_replacement
        and is_terminal_subscription_row(resolved_subscription)
        and status_from_subscription(subscription) not in TERMINAL_SUBSCRIPTION_STATUSES
    ):
        return None
    shop_id = int(resolved_subscription.get("shop_id") or 0)
    if not shop_id:
        return None
    canceled_at = stripe_timestamp(subscription.get("canceled_at") or subscription.get("cancel_at"))
    current_period_start_present = "current_period_start" in subscription
    current_period_end_present = "current_period_end" in subscription
    canceled_at_present = "canceled_at" in subscription or "cancel_at" in subscription
    if allow_subscription_replacement and status_from_subscription(subscription) not in TERMINAL_SUBSCRIPTION_STATUSES:
        canceled_at_present = True
        canceled_at = None
    return update_subscription_for_shop(
        conn,
        shop_id=shop_id,
        status="canceled" if deleted else status_from_subscription(subscription),
        cancel_at_period_end=False if deleted else subscription.get("cancel_at_period_end"),
        stripe_customer_id=customer_id or None,
        stripe_subscription_id=subscription_id or None,
        stripe_price_id=price_id_from_subscription(subscription),
        current_period_started_at=stripe_timestamp(subscription.get("current_period_start")),
        current_period_ends_at=stripe_timestamp(subscription.get("current_period_end")),
        canceled_at=canceled_at,
        current_period_started_at_provided=current_period_start_present,
        current_period_ends_at_provided=current_period_end_present,
        canceled_at_provided=canceled_at_present,
    )


def sync_invoice_event(conn: sqlite3.Connection, invoice: dict[str, Any], *, paid: bool) -> dict[str, Any] | None:
    subscription_details = invoice.get("subscription_details")
    metadata = subscription_details.get("metadata") if isinstance(subscription_details, dict) else {}
    if not isinstance(metadata, dict):
        metadata = {}
    invoice_metadata = invoice.get("metadata") if isinstance(invoice.get("metadata"), dict) else {}
    metadata_shop_id_value = (metadata or invoice_metadata).get("shop_id")
    resolved_subscription = resolve_subscription_for_stripe_event(
        conn,
        stripe_subscription_id=str(invoice.get("subscription") or "").strip(),
        stripe_customer_id=str(invoice.get("customer") or "").strip(),
        metadata_shop_id_value=metadata_shop_id_value,
    )
    if not resolved_subscription or is_terminal_subscription_row(resolved_subscription):
        return None
    shop_id = int(resolved_subscription.get("shop_id") or 0)
    if not shop_id:
        return None
    status = "active" if paid else "past_due"
    return update_subscription_for_shop(
        conn,
        shop_id=shop_id,
        status=status,
        stripe_customer_id=str(invoice.get("customer") or "").strip() or None,
        stripe_subscription_id=str(invoice.get("subscription") or "").strip() or None,
    )


def handle_webhook_event(conn: sqlite3.Connection, event: dict[str, Any]) -> dict[str, Any]:
    event = stripe_result_to_dict(event)
    event_type = str(event.get("type") or "").strip()
    if event_type not in SUPPORTED_WEBHOOK_EVENTS:
        return {"processed": False, "reason": "unsupported"}
    data = event.get("data")
    data = stripe_result_to_dict(data) if data is not None else {}
    obj = data.get("object") if isinstance(data, dict) else {}
    if obj is not None and not isinstance(obj, dict):
        obj = stripe_result_to_dict(obj)
    if not isinstance(obj, dict):
        obj = {}
    synced: dict[str, Any] | None = None
    if event_type == "checkout.session.completed":
        synced = sync_checkout_session_completed(conn, obj)
    elif event_type == "customer.subscription.created":
        synced = sync_subscription_object(conn, obj)
    elif event_type == "customer.subscription.updated":
        synced = sync_subscription_object(conn, obj)
    elif event_type == "customer.subscription.deleted":
        synced = sync_subscription_object(conn, obj, deleted=True)
    elif event_type == "invoice.paid":
        synced = sync_invoice_event(conn, obj, paid=True)
    elif event_type == "invoice.payment_failed":
        synced = sync_invoice_event(conn, obj, paid=False)
    if synced:
        conn.commit()
        return {"processed": True, "shop_id": synced.get("shop_id"), "status": synced.get("status")}
    return {"processed": False, "reason": "no_shop"}
