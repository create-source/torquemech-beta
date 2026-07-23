import hashlib
import hmac
import json
import os
import sqlite3
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
from app import billing
from db import PostgresCompatConnection
from routers import pro as pro_module


class NonClosingConnection(sqlite3.Connection):
    def close(self):
        pass

    def close_for_cleanup(self):
        super().close()


class FakeCheckoutSession:
    calls = []

    @classmethod
    def create(cls, **kwargs):
        cls.calls.append(kwargs)
        return {"id": "cs_test_123", "url": "https://checkout.stripe.test/session"}


class StripeLikeSession:
    def __getitem__(self, key):
        if key in {"id", "url"}:
            return {"id": "cs_stripe_object", "url": "https://checkout.stripe.test/object"}[key]
        raise KeyError(key)

    def to_dict_recursive(self):
        return {"id": "cs_stripe_object", "url": "https://checkout.stripe.test/object"}


class StripeLikeCheckoutSession:
    calls = []

    @classmethod
    def create(cls, **kwargs):
        cls.calls.append(kwargs)
        return StripeLikeSession()


class FakePortalSession:
    calls = []

    @classmethod
    def create(cls, **kwargs):
        cls.calls.append(kwargs)
        return {"id": "bps_test_123", "url": "https://billing.stripe.test/session"}


class FakeStripe:
    api_key = ""
    checkout = type("Checkout", (), {"Session": FakeCheckoutSession})
    billing_portal = type("BillingPortal", (), {"Session": FakePortalSession})


class StripeLikeObjectStripe:
    api_key = ""
    checkout = type("Checkout", (), {"Session": StripeLikeCheckoutSession})
    billing_portal = type("BillingPortal", (), {"Session": FakePortalSession})


class FailingCheckoutSession:
    @classmethod
    def create(cls, **kwargs):
        raise RuntimeError("stripe unavailable")


class FailingStripe:
    api_key = ""
    checkout = type("Checkout", (), {"Session": FailingCheckoutSession})
    billing_portal = type("BillingPortal", (), {"Session": FakePortalSession})


class StripeBillingTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False, factory=NonClosingConnection)
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close_for_cleanup)
        self.app_db_patch = patch.object(main, "app_db_conn", lambda row_factory=False: self.conn)
        self.crm_patch = patch.object(pro_module, "crm_db_conn", lambda: self.conn)
        self.env_patch = patch.dict(
            os.environ,
            {
                "PRO_ENABLED": "true",
                "PRO_ACCESS_CODE": "",
                "PRO_QA_KEY": "",
            },
            clear=True,
        )
        self.app_db_patch.start()
        self.crm_patch.start()
        self.env_patch.start()
        self.addCleanup(self.app_db_patch.stop)
        self.addCleanup(self.crm_patch.stop)
        self.addCleanup(self.env_patch.stop)
        pro_module.ensure_auth_schema(self.conn)
        pro_module.ensure_shop_profile_schema(self.conn)
        pro_module.ensure_shop_subscription_schema(self.conn)
        FakeCheckoutSession.calls.clear()
        StripeLikeCheckoutSession.calls.clear()
        FakePortalSession.calls.clear()

    def create_user_shop(self, email="owner@example.com", shop_name="Alpha Shop") -> tuple[int, int]:
        now = "2026-07-22T12:00:00+00:00"
        cur = self.conn.execute(
            """
            INSERT INTO users (
              email, password_hash, first_name, last_name, is_active,
              email_verified_at, created_at, updated_at
            )
            VALUES (?, ?, 'Test', 'Owner', 1, ?, ?, ?)
            """,
            (email, pro_module.hash_password("correct-password"), now, now, now),
        )
        user_id = int(cur.lastrowid)
        shop_id = pro_module.create_shop_profile_for_user(self.conn, user_id, shop_name)
        self.conn.commit()
        return user_id, shop_id

    def authenticated_client(self, user_id: int) -> TestClient:
        now = "2026-07-22T12:00:00+00:00"
        session_id = f"billing-session-{user_id}"
        self.conn.execute(
            """
            INSERT INTO auth_sessions (session_id, data_json, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, json.dumps({pro_module.AUTH_SESSION_USER_KEY: user_id}), now, now),
        )
        self.conn.commit()
        client = TestClient(main.app, base_url="http://localhost")
        client.cookies.set(main.SESSION_COOKIE_NAME, session_id)
        return client

    def insert_subscription(self, shop_id: int, **fields):
        now = "2026-07-22T12:00:00+00:00"
        defaults = {
            "status": "trialing",
            "stripe_customer_id": None,
            "stripe_subscription_id": None,
            "stripe_price_id": None,
        }
        defaults.update(fields)
        self.conn.execute(
            """
            INSERT INTO shop_subscriptions (
              shop_id, plan_code, status, trial_started_at, trial_ends_at,
              current_period_started_at, current_period_ends_at, canceled_at,
              access_grace_ends_at, stripe_customer_id, stripe_subscription_id,
              stripe_price_id, created_at, updated_at
            )
            VALUES (?, 'pro_solo', ?, NULL, NULL, NULL, NULL, NULL, NULL, ?, ?, ?, ?, ?)
            """,
            (
                shop_id,
                defaults["status"],
                defaults["stripe_customer_id"],
                defaults["stripe_subscription_id"],
                defaults["stripe_price_id"],
                now,
                now,
            ),
        )
        self.conn.commit()

    def stripe_config(self):
        return billing.StripeBillingConfig(
            secret_key="sk_test_123",
            publishable_key="pk_test_123",
            webhook_secret="whsec_test_123",
            pro_solo_monthly_price_id="price_pro_solo_monthly",
        )

    def signed_payload(self, payload: dict, secret="whsec_test_123") -> tuple[bytes, str]:
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        timestamp = str(int(time.time()))
        signature = hmac.new(secret.encode("utf-8"), f"{timestamp}.".encode("utf-8") + raw, hashlib.sha256).hexdigest()
        return raw, f"t={timestamp},v1={signature}"

    def subscription_event(self, shop_id: int, status="active", *, event_type="customer.subscription.updated", cancel_at_period_end=False) -> dict:
        return {
            "id": "evt_sub_updated",
            "type": event_type,
            "data": {
                "object": {
                    "id": "sub_123",
                    "customer": "cus_123",
                    "status": status,
                    "cancel_at_period_end": cancel_at_period_end,
                    "metadata": {"shop_id": str(shop_id), "plan_code": "pro_solo"},
                    "current_period_start": 1784707200,
                    "current_period_end": 1787385600,
                    "items": {"data": [{"price": {"id": "price_pro_solo_monthly"}}]},
                }
            },
        }

    def test_missing_configuration_behavior(self):
        user_id, _ = self.create_user_shop()
        client = self.authenticated_client(user_id)

        response = client.post("/pro/billing/checkout")

        self.assertEqual(response.status_code, 503)
        self.assertIn("Stripe billing is not configured", response.text)
        self.assertIn("data-billing-status-page", response.text)
        self.assertIn('href="/account/settings"', response.text)

    def test_checkout_success_status_page_preserves_message_and_destination(self):
        client = TestClient(main.app, base_url="http://localhost")

        response = client.get("/pro/billing/checkout/success")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Subscription checkout complete", response.text)
        self.assertIn("Your billing status will update as soon as Stripe confirms the subscription.", response.text)
        self.assertIn("TorqueMech Pro Solo", response.text)
        self.assertIn('href="/pro/dashboard"', response.text)
        self.assertIn("data-billing-status-page", response.text)

    def test_checkout_cancel_status_page_preserves_message_and_destination(self):
        client = TestClient(main.app, base_url="http://localhost")

        response = client.get("/pro/billing/checkout/cancel")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Checkout canceled", response.text)
        self.assertIn("No subscription changes were made.", response.text)
        self.assertIn('href="/account/settings"', response.text)
        self.assertIn("data-billing-status-page", response.text)

    def test_checkout_uses_authenticated_shop(self):
        _, shop_id = self.create_user_shop()
        service = billing.StripeBillingService(config=self.stripe_config(), stripe_api=FakeStripe)

        session = service.create_checkout_session(
            self.conn,
            shop_id=shop_id,
            shop_email="owner@example.com",
            success_url="https://torquemech.test/success",
            cancel_url="https://torquemech.test/cancel",
        )

        self.assertEqual(session["url"], "https://checkout.stripe.test/session")
        self.assertEqual(FakeCheckoutSession.calls[-1]["client_reference_id"], str(shop_id))
        self.assertEqual(FakeCheckoutSession.calls[-1]["metadata"]["shop_id"], str(shop_id))
        self.assertEqual(FakeCheckoutSession.calls[-1]["line_items"][0]["price"], "price_pro_solo_monthly")

    def test_shop_with_no_subscription_row_can_create_checkout(self):
        _, shop_id = self.create_user_shop()
        service = billing.StripeBillingService(config=self.stripe_config(), stripe_api=FakeStripe)

        session = service.create_checkout_session(
            self.conn,
            shop_id=shop_id,
            shop_email="owner@example.com",
            success_url="https://torquemech.test/success",
            cancel_url="https://torquemech.test/cancel",
        )

        self.assertEqual(session["url"], "https://checkout.stripe.test/session")
        self.assertEqual(FakeCheckoutSession.calls[-1]["client_reference_id"], str(shop_id))

    def test_checkout_omits_stripe_customer_when_none_exists(self):
        _, shop_id = self.create_user_shop()
        service = billing.StripeBillingService(config=self.stripe_config(), stripe_api=FakeStripe)

        service.create_checkout_session(
            self.conn,
            shop_id=shop_id,
            shop_email="owner@example.com",
            success_url="https://torquemech.test/success",
            cancel_url="https://torquemech.test/cancel",
        )

        self.assertNotIn("customer", FakeCheckoutSession.calls[-1])
        self.assertEqual(FakeCheckoutSession.calls[-1]["customer_email"], "owner@example.com")

    def test_existing_stripe_customer_is_reused(self):
        _, shop_id = self.create_user_shop()
        self.insert_subscription(shop_id, stripe_customer_id="cus_existing")
        service = billing.StripeBillingService(config=self.stripe_config(), stripe_api=FakeStripe)

        service.create_checkout_session(
            self.conn,
            shop_id=shop_id,
            shop_email="owner@example.com",
            success_url="https://torquemech.test/success",
            cancel_url="https://torquemech.test/cancel",
        )

        self.assertEqual(FakeCheckoutSession.calls[-1]["customer"], "cus_existing")
        self.assertNotIn("customer_email", FakeCheckoutSession.calls[-1])

    def test_checkout_does_not_create_fake_active_subscription(self):
        _, shop_id = self.create_user_shop()
        service = billing.StripeBillingService(config=self.stripe_config(), stripe_api=FakeStripe)

        service.create_checkout_session(
            self.conn,
            shop_id=shop_id,
            shop_email="owner@example.com",
            success_url="https://torquemech.test/success",
            cancel_url="https://torquemech.test/cancel",
        )

        count = self.conn.execute("SELECT COUNT(*) AS count FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)).fetchone()["count"]
        self.assertEqual(count, 0)

    def test_checkout_accepts_stripe_object_response(self):
        _, shop_id = self.create_user_shop()
        service = billing.StripeBillingService(config=self.stripe_config(), stripe_api=StripeLikeObjectStripe)

        session = service.create_checkout_session(
            self.conn,
            shop_id=shop_id,
            shop_email="owner@example.com",
            success_url="https://torquemech.test/success",
            cancel_url="https://torquemech.test/cancel",
        )

        self.assertEqual(session["id"], "cs_stripe_object")
        self.assertEqual(session["url"], "https://checkout.stripe.test/object")

    def test_checkout_cannot_target_another_shop(self):
        user_a, shop_a = self.create_user_shop(email="a@example.com", shop_name="A")
        _, shop_b = self.create_user_shop(email="b@example.com", shop_name="B")
        captured = []

        class FakeService:
            def create_checkout_session(self, conn, *, shop_id, shop_email, success_url, cancel_url):
                captured.append(shop_id)
                return {"url": "https://checkout.stripe.test/session"}

        client = self.authenticated_client(user_a)
        with patch.object(pro_module, "StripeBillingService", return_value=FakeService()):
            response = client.post(f"/pro/billing/checkout?shop_id={shop_b}", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(captured, [shop_a])

    def test_portal_requires_customer_id(self):
        _, shop_id = self.create_user_shop()
        service = billing.StripeBillingService(config=self.stripe_config(), stripe_api=FakeStripe)

        with self.assertRaises(billing.BillingCustomerRequiredError):
            service.create_customer_portal_session(self.conn, shop_id=shop_id, return_url="https://torquemech.test/account")

    def test_portal_requires_stored_customer_id(self):
        _, shop_id = self.create_user_shop()
        self.insert_subscription(shop_id, stripe_customer_id=None)
        service = billing.StripeBillingService(config=self.stripe_config(), stripe_api=FakeStripe)

        with self.assertRaises(billing.BillingCustomerRequiredError):
            service.create_customer_portal_session(self.conn, shop_id=shop_id, return_url="https://torquemech.test/account")

    def test_checkout_stripe_failure_returns_friendly_error(self):
        user_id, shop_id = self.create_user_shop()
        client = self.authenticated_client(user_id)
        service = billing.StripeBillingService(config=self.stripe_config(), stripe_api=FailingStripe)

        with patch.object(pro_module, "StripeBillingService", return_value=service):
            response = client.post("/pro/billing/checkout")

        count = self.conn.execute("SELECT COUNT(*) AS count FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)).fetchone()["count"]
        self.assertEqual(response.status_code, 502)
        self.assertIn("Stripe Checkout is temporarily unavailable", response.text)
        self.assertEqual(count, 0)

    def test_valid_webhook_signature(self):
        payload = {"id": "evt_123", "type": "invoice.paid", "data": {"object": {}}}
        raw, signature = self.signed_payload(payload)

        event = billing.verify_webhook_payload(raw, signature, webhook_secret="whsec_test_123")

        self.assertEqual(event["id"], "evt_123")

    def test_invalid_webhook_signature(self):
        payload = {"id": "evt_123", "type": "invoice.paid", "data": {"object": {}}}
        raw, _ = self.signed_payload(payload)

        with self.assertRaises(billing.BillingSignatureError):
            billing.verify_webhook_payload(raw, "t=123,v1=bad", webhook_secret="whsec_test_123", tolerance_seconds=0)

    def test_duplicate_webhook_idempotency(self):
        _, shop_id = self.create_user_shop()
        event = self.subscription_event(shop_id)

        first = billing.handle_webhook_event(self.conn, event)
        second = billing.handle_webhook_event(self.conn, event)

        count = self.conn.execute("SELECT COUNT(*) AS count FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)).fetchone()["count"]
        self.assertTrue(first["processed"])
        self.assertTrue(second["processed"])
        self.assertEqual(count, 1)

    def test_subscription_status_synchronization(self):
        _, shop_id = self.create_user_shop()

        result = billing.handle_webhook_event(self.conn, self.subscription_event(shop_id, status="past_due"))

        row = self.conn.execute("SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)).fetchone()
        self.assertTrue(result["processed"])
        self.assertEqual(row["status"], "past_due")
        self.assertEqual(row["stripe_customer_id"], "cus_123")
        self.assertEqual(row["stripe_subscription_id"], "sub_123")
        self.assertEqual(row["stripe_price_id"], "price_pro_solo_monthly")
        self.assertEqual(row["cancel_at_period_end"], 0)
        self.assertTrue(row["current_period_ends_at"].startswith("2026-08-22T"))

    def test_subscription_created_persists_cancel_at_period_end_false(self):
        _, shop_id = self.create_user_shop()

        billing.handle_webhook_event(
            self.conn,
            self.subscription_event(shop_id, event_type="customer.subscription.created", cancel_at_period_end=False),
        )

        row = self.conn.execute("SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)).fetchone()
        self.assertEqual(row["cancel_at_period_end"], 0)

    def test_subscription_updated_persists_cancel_at_period_end_true_and_false(self):
        _, shop_id = self.create_user_shop()

        billing.handle_webhook_event(self.conn, self.subscription_event(shop_id, cancel_at_period_end=True))
        row = self.conn.execute("SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)).fetchone()
        self.assertEqual(row["cancel_at_period_end"], 1)

        billing.handle_webhook_event(self.conn, self.subscription_event(shop_id, cancel_at_period_end=False))
        row = self.conn.execute("SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)).fetchone()
        self.assertEqual(row["cancel_at_period_end"], 0)

    def test_cancel_at_period_end_access_before_and_after_period_end(self):
        _, shop_id = self.create_user_shop()

        billing.handle_webhook_event(self.conn, self.subscription_event(shop_id, cancel_at_period_end=True))
        row = self.conn.execute("SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)).fetchone()

        before = billing.resolve_subscription_access(
            dict(row),
            shop_id=shop_id,
            now=pro_module.parse_utc_datetime("2026-08-01T12:00:00+00:00"),
        )
        after = billing.resolve_subscription_access(
            dict(row),
            shop_id=shop_id,
            now=pro_module.parse_utc_datetime("2026-08-23T12:00:00+00:00"),
        )

        self.assertEqual(before.access_state, "subscribed_canceling")
        self.assertTrue(before.has_full_access)
        self.assertEqual(after.access_state, "read_only_canceled")
        self.assertFalse(after.has_full_access)

    def test_deleted_subscription_persists_canceled_state_and_cancel_flag(self):
        _, shop_id = self.create_user_shop()

        result = billing.handle_webhook_event(
            self.conn,
            self.subscription_event(
                shop_id,
                status="canceled",
                event_type="customer.subscription.deleted",
                cancel_at_period_end=True,
            ),
        )

        row = self.conn.execute("SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)).fetchone()
        self.assertTrue(result["processed"])
        self.assertEqual(row["status"], "canceled")
        self.assertEqual(row["cancel_at_period_end"], 1)

    def test_checkout_session_completed_uses_expanded_subscription_object_when_available(self):
        _, shop_id = self.create_user_shop()
        event = {
            "id": "evt_checkout",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "client_reference_id": str(shop_id),
                    "customer": "cus_session",
                    "subscription": self.subscription_event(
                        shop_id,
                        event_type="customer.subscription.created",
                        cancel_at_period_end=True,
                    )["data"]["object"],
                }
            },
        }

        billing.handle_webhook_event(self.conn, event)

        row = self.conn.execute("SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)).fetchone()
        self.assertEqual(row["stripe_subscription_id"], "sub_123")
        self.assertEqual(row["cancel_at_period_end"], 1)

    def test_shop_isolation(self):
        _, shop_a = self.create_user_shop(email="a@example.com", shop_name="A")
        _, shop_b = self.create_user_shop(email="b@example.com", shop_name="B")
        self.insert_subscription(shop_b, status="trialing", stripe_customer_id="cus_b")

        billing.handle_webhook_event(self.conn, self.subscription_event(shop_a, status="active"))

        row_a = self.conn.execute("SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_a,)).fetchone()
        row_b = self.conn.execute("SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_b,)).fetchone()
        self.assertEqual(row_a["status"], "active")
        self.assertEqual(row_b["status"], "trialing")
        self.assertEqual(row_b["stripe_customer_id"], "cus_b")

    def test_webhook_route_accepts_valid_signature_and_rejects_invalid_signature(self):
        _, shop_id = self.create_user_shop()
        raw, signature = self.signed_payload(self.subscription_event(shop_id))
        client = TestClient(main.app, base_url="https://torquemech.com")

        with patch.dict(os.environ, {"STRIPE_WEBHOOK_SECRET": "whsec_test_123"}):
            valid = client.post("/pro/billing/webhook", content=raw, headers={"stripe-signature": signature})
            invalid = client.post("/pro/billing/webhook", content=raw, headers={"stripe-signature": "t=1,v1=bad"})

        self.assertEqual(valid.status_code, 200)
        self.assertEqual(invalid.status_code, 400)

    def test_postgresql_portability(self):
        source = (Path(__file__).resolve().parent.parent / "app" / "billing.py").read_text(encoding="utf-8")
        self.assertNotIn("CREATE TABLE", source)
        self.assertNotIn("ALTER TABLE", source)
        conn = object.__new__(PostgresCompatConnection)
        sql, params = conn._translate_sql(
            """
            UPDATE shop_subscriptions
            SET status = ?, stripe_customer_id = ?
            WHERE shop_id = ?
            """,
            ("active", "cus_123", 7),
        )
        self.assertIn("UPDATE shop_subscriptions", sql)
        self.assertIn("%s", sql)
        self.assertEqual(params, ("active", "cus_123", 7))


if __name__ == "__main__":
    unittest.main()
