from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
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


class BillingConfigurationError(RuntimeError):
    pass


class BillingCustomerRequiredError(RuntimeError):
    pass


class BillingProviderError(RuntimeError):
    pass


class BillingSignatureError(RuntimeError):
    pass


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
        return {
            key: value
            for key, value in vars(result).items()
            if not key.startswith("_")
        }


def load_subscription(conn: sqlite3.Connection, shop_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM shop_subscriptions WHERE shop_id = ? LIMIT 1",
        (shop_id,),
    ).fetchone()
    return row_to_dict(row)


def require_existing_subscription(conn: sqlite3.Connection, shop_id: int) -> dict[str, Any]:
    subscription = load_subscription(conn, shop_id)
    if subscription:
        return subscription
    raise BillingCustomerRequiredError("Billing is not active for this shop yet.")


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
    stripe_customer_id: str | None = None,
    stripe_subscription_id: str | None = None,
    stripe_price_id: str | None = None,
    current_period_started_at: str | None = None,
    current_period_ends_at: str | None = None,
    canceled_at: str | None = None,
) -> dict[str, Any] | None:
    existing = load_subscription(conn, shop_id) or {}
    now = utc_now_iso()
    values = {
        "plan_code": PRO_SOLO_PLAN_CODE,
        "status": status or existing.get("status") or "active",
        "current_period_started_at": current_period_started_at or existing.get("current_period_started_at"),
        "current_period_ends_at": current_period_ends_at or existing.get("current_period_ends_at"),
        "canceled_at": canceled_at if canceled_at is not None else existing.get("canceled_at"),
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
              current_period_started_at, current_period_ends_at, canceled_at,
              access_grace_ends_at, stripe_customer_id, stripe_subscription_id,
              stripe_price_id, created_at, updated_at
            )
            VALUES (?, ?, ?, NULL, NULL, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
            """,
            (
                shop_id,
                values["plan_code"],
                values["status"],
                values["current_period_started_at"],
                values["current_period_ends_at"],
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


def sync_subscription_object(conn: sqlite3.Connection, subscription: dict[str, Any], *, deleted: bool = False) -> dict[str, Any] | None:
    shop_id = metadata_shop_id(subscription)
    if not shop_id:
        return None
    canceled_at = stripe_timestamp(subscription.get("canceled_at"))
    return update_subscription_for_shop(
        conn,
        shop_id=shop_id,
        status="canceled" if deleted else status_from_subscription(subscription),
        stripe_customer_id=str(subscription.get("customer") or "").strip() or None,
        stripe_subscription_id=str(subscription.get("id") or "").strip() or None,
        stripe_price_id=price_id_from_subscription(subscription),
        current_period_started_at=stripe_timestamp(subscription.get("current_period_start")),
        current_period_ends_at=stripe_timestamp(subscription.get("current_period_end")),
        canceled_at=canceled_at,
    )


def sync_invoice_event(conn: sqlite3.Connection, invoice: dict[str, Any], *, paid: bool) -> dict[str, Any] | None:
    subscription_details = invoice.get("subscription_details")
    metadata = subscription_details.get("metadata") if isinstance(subscription_details, dict) else {}
    if not isinstance(metadata, dict):
        metadata = {}
    enriched = dict(invoice)
    enriched["metadata"] = metadata or invoice.get("metadata") or {}
    shop_id = metadata_shop_id(enriched)
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
    event_type = str(event.get("type") or "").strip()
    if event_type not in SUPPORTED_WEBHOOK_EVENTS:
        return {"processed": False, "reason": "unsupported"}
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    obj = data.get("object") if isinstance(data.get("object"), dict) else {}
    synced: dict[str, Any] | None = None
    if event_type == "checkout.session.completed":
        synced = sync_checkout_session_completed(conn, obj)
    elif event_type in {"customer.subscription.created", "customer.subscription.updated"}:
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
