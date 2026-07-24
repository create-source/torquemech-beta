import hashlib
import hmac
import json
import os
import re
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


class FailingPortalSession:
    @classmethod
    def create(cls, **kwargs):
        raise RuntimeError("stripe portal unavailable")


class FailingStripe:
    api_key = ""
    checkout = type("Checkout", (), {"Session": FailingCheckoutSession})
    billing_portal = type("BillingPortal", (), {"Session": FailingPortalSession})


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    if not match:
        raise AssertionError("csrf token not found")
    return match.group(1)


def csrf_from_form(html: str, action: str) -> str:
    escaped_action = re.escape(action)
    match = re.search(
        rf'<form[^>]+action="{escaped_action}"[^>]*>.*?name="csrf_token" value="([^"]+)"',
        html,
        re.S,
    )
    if not match:
        raise AssertionError(f"csrf token not found for form action {action}")
    token = match.group(1)
    if not token.strip():
        raise AssertionError(f"empty csrf token for form action {action}")
    return token


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
            "current_period_started_at": None,
            "current_period_ends_at": None,
            "cancel_at_period_end": 0,
            "canceled_at": None,
            "stripe_customer_id": None,
            "stripe_subscription_id": None,
            "stripe_price_id": None,
        }
        defaults.update(fields)
        self.conn.execute(
            """
            INSERT INTO shop_subscriptions (
              shop_id, plan_code, status, trial_started_at, trial_ends_at,
              current_period_started_at, current_period_ends_at, cancel_at_period_end, canceled_at,
              access_grace_ends_at, stripe_customer_id, stripe_subscription_id,
              stripe_price_id, created_at, updated_at
            )
            VALUES (?, 'pro_solo', ?, NULL, NULL, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
            """,
            (
                shop_id,
                defaults["status"],
                defaults["current_period_started_at"],
                defaults["current_period_ends_at"],
                defaults["cancel_at_period_end"],
                defaults["canceled_at"],
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

    def subscription_event(
        self,
        shop_id: int,
        status="active",
        *,
        event_type="customer.subscription.updated",
        cancel_at_period_end=False,
        stripe_subscription_id="sub_123",
        stripe_customer_id="cus_123",
        current_period_start=1784707200,
        current_period_end=1787385600,
        canceled_at=None,
        metadata_shop_id=None,
    ) -> dict:
        subscription = {
            "id": stripe_subscription_id,
            "customer": stripe_customer_id,
            "status": status,
            "cancel_at_period_end": cancel_at_period_end,
            "metadata": {"shop_id": str(metadata_shop_id if metadata_shop_id is not None else shop_id), "plan_code": "pro_solo"},
            "current_period_start": current_period_start,
            "current_period_end": current_period_end,
            "items": {"data": [{"price": {"id": "price_pro_solo_monthly"}}]},
        }
        if canceled_at is not None:
            subscription["canceled_at"] = canceled_at
        return {
            "id": "evt_sub_updated",
            "type": event_type,
            "data": {"object": subscription},
        }

    def invoice_event(
        self,
        shop_id: int,
        *,
        event_type="invoice.paid",
        stripe_subscription_id="sub_123",
        stripe_customer_id="cus_123",
        metadata_shop_id=None,
    ) -> dict:
        return {
            "id": "evt_invoice",
            "type": event_type,
            "data": {
                "object": {
                    "customer": stripe_customer_id,
                    "subscription": stripe_subscription_id,
                    "subscription_details": {
                        "metadata": {
                            "shop_id": str(metadata_shop_id if metadata_shop_id is not None else shop_id),
                            "plan_code": "pro_solo",
                        }
                    },
                }
            },
        }

    def test_missing_configuration_behavior(self):
        user_id, _ = self.create_user_shop()
        client = self.authenticated_client(user_id)
        page = client.get("/account/settings")

        response = client.post("/pro/billing/checkout", data={"csrf_token": csrf_from_form(page.text, "/pro/billing/checkout")})

        self.assertEqual(response.status_code, 503)
        self.assertIn("We could not open billing", response.text)
        self.assertIn("We could not open billing right now. Return to Account Settings and try again in a moment.", response.text)
        self.assertNotIn("Stripe billing is not configured", response.text)
        self.assertNotIn("STRIPE_SECRET_KEY", response.text)
        self.assertIn("data-billing-status-page", response.text)
        self.assertIn('href="/account/settings"', response.text)
        self.assertIn('method="post"', response.text)
        self.assertIn('action="/pro/billing/checkout"', response.text)
        self.assertTrue(csrf_from_form(response.text, "/pro/billing/checkout"))
        self.assertIn("Try Checkout Again", response.text)

    def test_checkout_success_status_page_shows_branded_actions(self):
        client = TestClient(main.app, base_url="http://localhost")

        response = client.get("/pro/billing/checkout/success")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Checkout complete", response.text)
        self.assertIn("Your checkout was completed successfully.", response.text)
        self.assertIn("Account Settings will reflect the latest subscription status as Stripe confirmation is received.", response.text)
        self.assertNotIn("webhook", response.text.lower())
        self.assertIn("TorqueMech Pro Solo", response.text)
        self.assertIn('href="/account/settings"', response.text)
        self.assertIn('href="/pro/dashboard"', response.text)
        self.assertIn("Back to Account Settings", response.text)
        self.assertIn("Open Pro Dashboard", response.text)
        self.assertIn("tm-billing-status-card--success", response.text)
        self.assertIn("data-billing-status-page", response.text)

    def test_checkout_cancel_status_page_shows_retry_form(self):
        client = TestClient(main.app, base_url="http://localhost")

        response = client.get("/pro/billing/checkout/cancel")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Checkout canceled", response.text)
        self.assertIn("Checkout was canceled, and no subscription change was completed.", response.text)
        self.assertNotIn("charged", response.text.lower())
        self.assertIn('href="/account/settings"', response.text)
        self.assertIn("Back to Account Settings", response.text)
        self.assertIn('method="post"', response.text)
        self.assertIn('action="/pro/billing/checkout"', response.text)
        self.assertTrue(csrf_from_form(response.text, "/pro/billing/checkout"))
        self.assertIn("Try Subscribing Again", response.text)
        self.assertIn("tm-billing-status-card--neutral", response.text)
        self.assertIn("data-billing-status-page", response.text)

    def test_checkout_cancel_retry_token_reaches_checkout_route_behavior(self):
        user_id, _ = self.create_user_shop()
        client = self.authenticated_client(user_id)
        page = client.get("/pro/billing/checkout/cancel")
        token = csrf_from_form(page.text, "/pro/billing/checkout")

        response = client.post("/pro/billing/checkout", data={"csrf_token": token})

        self.assertEqual(response.status_code, 503)
        self.assertIn("We could not open billing", response.text)
        self.assertNotIn("Your billing session expired", response.text)

    def test_checkout_error_retry_token_reaches_checkout_route_behavior(self):
        user_id, _ = self.create_user_shop()
        client = self.authenticated_client(user_id)
        page = client.get("/account/settings")
        first = client.post("/pro/billing/checkout", data={"csrf_token": csrf_from_form(page.text, "/pro/billing/checkout")})
        token = csrf_from_form(first.text, "/pro/billing/checkout")

        retry = client.post("/pro/billing/checkout", data={"csrf_token": token})

        self.assertEqual(first.status_code, 503)
        self.assertEqual(retry.status_code, 503)
        self.assertIn("We could not open billing", retry.text)
        self.assertNotIn("Your billing session expired", retry.text)

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
        page = client.get("/account/settings")
        with patch.object(pro_module, "StripeBillingService", return_value=FakeService()):
            response = client.post(
                f"/pro/billing/checkout?shop_id={shop_b}",
                data={"csrf_token": csrf_from_form(page.text, "/pro/billing/checkout")},
                follow_redirects=False,
            )

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

    def test_portal_uses_stored_customer_id(self):
        _, shop_id = self.create_user_shop()
        self.insert_subscription(shop_id, status="active", stripe_customer_id="cus_stored", stripe_subscription_id="sub_123")
        service = billing.StripeBillingService(config=self.stripe_config(), stripe_api=FakeStripe)

        session = service.create_customer_portal_session(
            self.conn,
            shop_id=shop_id,
            return_url="https://torquemech.test/account/settings#billing-subscription",
        )

        self.assertEqual(session["url"], "https://billing.stripe.test/session")
        self.assertEqual(FakePortalSession.calls[-1]["customer"], "cus_stored")
        self.assertEqual(
            FakePortalSession.calls[-1]["return_url"],
            "https://torquemech.test/account/settings#billing-subscription",
        )

    def test_portal_route_redirects_to_mocked_stripe_url(self):
        user_id, shop_id = self.create_user_shop()
        self.insert_subscription(shop_id, status="active", stripe_customer_id="cus_route", stripe_subscription_id="sub_123")
        client = self.authenticated_client(user_id)
        page = client.get("/account/settings")
        service = billing.StripeBillingService(config=self.stripe_config(), stripe_api=FakeStripe)

        with patch.object(pro_module, "StripeBillingService", return_value=service):
            response = client.post(
                "/pro/billing/portal",
                data={"csrf_token": csrf_from(page.text)},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "https://billing.stripe.test/session")
        self.assertEqual(FakePortalSession.calls[-1]["customer"], "cus_route")

    def test_portal_route_ignores_browser_submitted_customer_id_and_return_url(self):
        user_a, shop_a = self.create_user_shop(email="a@example.com", shop_name="A")
        _, shop_b = self.create_user_shop(email="b@example.com", shop_name="B")
        self.insert_subscription(shop_a, status="active", stripe_customer_id="cus_a", stripe_subscription_id="sub_a")
        self.insert_subscription(shop_b, status="active", stripe_customer_id="cus_b", stripe_subscription_id="sub_b")
        client = self.authenticated_client(user_a)
        page = client.get("/account/settings")
        service = billing.StripeBillingService(config=self.stripe_config(), stripe_api=FakeStripe)

        with patch.object(pro_module, "StripeBillingService", return_value=service):
            response = client.post(
                "/pro/billing/portal?shop_id={}&return_url=https://evil.test/after".format(shop_b),
                data={
                    "csrf_token": csrf_from(page.text),
                    "customer": "cus_b",
                    "customer_id": "cus_b",
                    "stripe_customer_id": "cus_b",
                    "return_url": "https://evil.test/form",
                },
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(FakePortalSession.calls[-1]["customer"], "cus_a")
        self.assertEqual(
            FakePortalSession.calls[-1]["return_url"],
            "http://localhost/account/settings#billing-subscription",
        )

    def test_shop_without_customer_id_cannot_open_portal(self):
        user_id, shop_id = self.create_user_shop()
        self.insert_subscription(shop_id, status="active", stripe_customer_id=None, stripe_subscription_id="sub_123")
        client = self.authenticated_client(user_id)
        page = client.get("/account/settings")
        service = billing.StripeBillingService(config=self.stripe_config(), stripe_api=FakeStripe)

        with patch.object(pro_module, "StripeBillingService", return_value=service):
            response = client.post(
                "/pro/billing/portal",
                data={"csrf_token": csrf_from(page.text)},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Billing management is not available for this shop yet.", response.text)
        self.assertIn("Return to Account Settings", response.text)
        self.assertNotIn("Billing is not active for this shop yet.", response.text)
        self.assertNotIn("sub_123", response.text)
        self.assertEqual(FakePortalSession.calls, [])

    def test_unauthenticated_portal_request_redirects_to_login(self):
        client = TestClient(main.app, base_url="http://localhost")

        response = client.post("/pro/billing/portal", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login?next=%2Faccount%2Fsettings")
        self.assertEqual(FakePortalSession.calls, [])

    def test_portal_configuration_failure_returns_friendly_error(self):
        user_id, shop_id = self.create_user_shop()
        self.insert_subscription(shop_id, status="active", stripe_customer_id="cus_route", stripe_subscription_id="sub_123")
        client = self.authenticated_client(user_id)
        page = client.get("/account/settings")

        response = client.post(
            "/pro/billing/portal",
            data={"csrf_token": csrf_from(page.text)},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 503)
        self.assertIn("We could not open billing", response.text)
        self.assertNotIn("Stripe billing is not configured", response.text)
        self.assertNotIn("sk_test", response.text)
        self.assertNotIn('action="/pro/billing/portal"', response.text)
        self.assertNotIn('action="/pro/billing/checkout"', response.text)
        self.assertNotIn("Try Checkout Again", response.text)
        self.assertEqual(FakePortalSession.calls, [])

    def test_portal_stripe_failure_returns_friendly_error(self):
        user_id, shop_id = self.create_user_shop()
        self.insert_subscription(shop_id, status="active", stripe_customer_id="cus_route", stripe_subscription_id="sub_123")
        client = self.authenticated_client(user_id)
        page = client.get("/account/settings")
        service = billing.StripeBillingService(config=self.stripe_config(), stripe_api=FailingStripe)

        with patch.object(pro_module, "StripeBillingService", return_value=service):
            response = client.post(
                "/pro/billing/portal",
                data={"csrf_token": csrf_from(page.text)},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 502)
        self.assertIn("We could not open billing", response.text)
        self.assertIn("Return to Account Settings", response.text)
        self.assertNotIn("Stripe Billing Portal is temporarily unavailable", response.text)
        self.assertNotIn("stripe portal unavailable", response.text)
        self.assertNotIn("cus_route", response.text)

    def test_account_settings_manage_subscription_button_visibility(self):
        user_id, shop_id = self.create_user_shop()
        self.insert_subscription(shop_id, status="active", stripe_customer_id="cus_route", stripe_subscription_id="sub_123")
        client = self.authenticated_client(user_id)

        page = client.get("/account/settings")

        self.assertEqual(page.status_code, 200)
        self.assertIn("Manage Subscription", page.text)
        self.assertIn('action="/pro/billing/portal"', page.text)
        self.assertIn('name="csrf_token"', page.text)
        self.assertIn("Payment methods, invoices, and cancellation are handled securely by Stripe.", page.text)

    def test_account_settings_hides_manage_subscription_for_ended_subscription(self):
        user_id, shop_id = self.create_user_shop()
        self.insert_subscription(shop_id, status="canceled", stripe_customer_id="cus_route", stripe_subscription_id="sub_123")
        client = self.authenticated_client(user_id)

        page = client.get("/account/settings")

        self.assertEqual(page.status_code, 200)
        self.assertNotIn("Manage Subscription", page.text)

    def test_checkout_stripe_failure_returns_friendly_error(self):
        user_id, shop_id = self.create_user_shop()
        client = self.authenticated_client(user_id)
        page = client.get("/account/settings")
        service = billing.StripeBillingService(config=self.stripe_config(), stripe_api=FailingStripe)

        with patch.object(pro_module, "StripeBillingService", return_value=service):
            response = client.post("/pro/billing/checkout", data={"csrf_token": csrf_from_form(page.text, "/pro/billing/checkout")})

        count = self.conn.execute("SELECT COUNT(*) AS count FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)).fetchone()["count"]
        self.assertEqual(response.status_code, 502)
        self.assertIn("We could not open billing", response.text)
        self.assertIn("Try Checkout Again", response.text)
        self.assertTrue(csrf_from_form(response.text, "/pro/billing/checkout"))
        self.assertNotIn("Stripe Checkout is temporarily unavailable", response.text)
        self.assertNotIn("stripe unavailable", response.text)
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
        self.insert_subscription(shop_id, stripe_customer_id="cus_123", stripe_subscription_id="sub_123")
        event = self.subscription_event(shop_id)

        first = billing.handle_webhook_event(self.conn, event)
        second = billing.handle_webhook_event(self.conn, event)

        count = self.conn.execute("SELECT COUNT(*) AS count FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)).fetchone()["count"]
        self.assertTrue(first["processed"])
        self.assertTrue(second["processed"])
        self.assertEqual(count, 1)

    def test_subscription_status_synchronization(self):
        _, shop_id = self.create_user_shop()
        self.insert_subscription(shop_id, stripe_customer_id="cus_123", stripe_subscription_id="sub_123")

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
        self.insert_subscription(shop_id, stripe_customer_id="cus_123", stripe_subscription_id="sub_123")

        billing.handle_webhook_event(
            self.conn,
            self.subscription_event(shop_id, event_type="customer.subscription.created", cancel_at_period_end=False),
        )

        row = self.conn.execute("SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)).fetchone()
        self.assertEqual(row["cancel_at_period_end"], 0)

    def test_subscription_updated_persists_cancel_at_period_end_true_and_false(self):
        _, shop_id = self.create_user_shop()
        self.insert_subscription(shop_id, stripe_customer_id="cus_123", stripe_subscription_id="sub_123")

        billing.handle_webhook_event(self.conn, self.subscription_event(shop_id, cancel_at_period_end=True))
        row = self.conn.execute("SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)).fetchone()
        self.assertEqual(row["cancel_at_period_end"], 1)

        billing.handle_webhook_event(self.conn, self.subscription_event(shop_id, cancel_at_period_end=False))
        row = self.conn.execute("SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)).fetchone()
        self.assertEqual(row["cancel_at_period_end"], 0)

    def test_cancel_at_period_end_access_before_and_after_period_end(self):
        _, shop_id = self.create_user_shop()
        self.insert_subscription(shop_id, stripe_customer_id="cus_123", stripe_subscription_id="sub_123")

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
        self.insert_subscription(shop_id, stripe_customer_id="cus_123", stripe_subscription_id="sub_123")

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
        self.assertEqual(row["cancel_at_period_end"], 0)

    def test_deleted_subscription_uses_metadata_fallback_when_stripe_ids_are_missing_locally(self):
        _, shop_id = self.create_user_shop()
        self.insert_subscription(
            shop_id,
            status="active",
            cancel_at_period_end=1,
            current_period_ends_at="2026-07-23T19:10:24+00:00",
        )

        result = billing.handle_webhook_event(
            self.conn,
            self.subscription_event(
                shop_id,
                status="canceled",
                event_type="customer.subscription.deleted",
                cancel_at_period_end=True,
                stripe_customer_id="cus_metadata_deleted",
                stripe_subscription_id="sub_metadata_deleted",
                metadata_shop_id=shop_id,
            ),
        )

        row = self.conn.execute("SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)).fetchone()
        access = billing.resolve_subscription_access(
            dict(row),
            shop_id=shop_id,
            now=pro_module.parse_utc_datetime("2026-07-24T12:00:00+00:00"),
        )
        self.assertTrue(result["processed"])
        self.assertEqual(row["status"], "canceled")
        self.assertEqual(row["cancel_at_period_end"], 0)
        self.assertEqual(row["stripe_customer_id"], "cus_metadata_deleted")
        self.assertEqual(row["stripe_subscription_id"], "sub_metadata_deleted")
        self.assertEqual(access.access_state, "read_only_canceled")
        self.assertFalse(access.has_full_access)

    def test_lifecycle_active_update_uses_stored_stripe_identifiers(self):
        _, shop_id = self.create_user_shop()
        self.insert_subscription(shop_id, status="past_due", stripe_customer_id="cus_lifecycle", stripe_subscription_id="sub_lifecycle")

        result = billing.handle_webhook_event(
            self.conn,
            self.subscription_event(
                shop_id,
                status="active",
                stripe_customer_id="cus_lifecycle",
                stripe_subscription_id="sub_lifecycle",
            ),
        )

        row = self.conn.execute("SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)).fetchone()
        access = billing.resolve_subscription_access(dict(row), shop_id=shop_id, now=pro_module.parse_utc_datetime("2026-08-01T12:00:00+00:00"))
        self.assertTrue(result["processed"])
        self.assertEqual(row["status"], "active")
        self.assertEqual(row["stripe_customer_id"], "cus_lifecycle")
        self.assertEqual(row["stripe_subscription_id"], "sub_lifecycle")
        self.assertTrue(access.has_full_access)

    def test_lifecycle_duplicate_subscription_update_is_idempotent(self):
        _, shop_id = self.create_user_shop()
        self.insert_subscription(shop_id, status="past_due", stripe_customer_id="cus_duplicate", stripe_subscription_id="sub_duplicate")
        event = self.subscription_event(
            shop_id,
            status="active",
            stripe_customer_id="cus_duplicate",
            stripe_subscription_id="sub_duplicate",
        )

        first = billing.handle_webhook_event(self.conn, event)
        second = billing.handle_webhook_event(self.conn, event)

        rows = self.conn.execute("SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)).fetchall()
        self.assertTrue(first["processed"])
        self.assertTrue(second["processed"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "active")
        self.assertEqual(rows[0]["stripe_subscription_id"], "sub_duplicate")

    def test_lifecycle_scheduled_cancellation_preserves_access_until_period_end(self):
        _, shop_id = self.create_user_shop()
        self.insert_subscription(shop_id, status="active", stripe_customer_id="cus_canceling", stripe_subscription_id="sub_canceling")

        billing.handle_webhook_event(
            self.conn,
            self.subscription_event(
                shop_id,
                status="active",
                cancel_at_period_end=True,
                stripe_customer_id="cus_canceling",
                stripe_subscription_id="sub_canceling",
            ),
        )

        row = self.conn.execute("SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)).fetchone()
        before = billing.resolve_subscription_access(dict(row), shop_id=shop_id, now=pro_module.parse_utc_datetime("2026-08-01T12:00:00+00:00"))
        after = billing.resolve_subscription_access(dict(row), shop_id=shop_id, now=pro_module.parse_utc_datetime("2026-08-23T12:00:00+00:00"))
        self.assertEqual(row["cancel_at_period_end"], 1)
        self.assertEqual(before.access_state, "subscribed_canceling")
        self.assertTrue(before.has_full_access)
        self.assertEqual(after.access_state, "read_only_canceled")
        self.assertFalse(after.has_full_access)

    def test_lifecycle_cancellation_reversal_clears_pending_state_and_canceled_at(self):
        _, shop_id = self.create_user_shop()
        self.insert_subscription(
            shop_id,
            status="active",
            cancel_at_period_end=1,
            canceled_at="2026-07-30T12:00:00+00:00",
            stripe_customer_id="cus_reverse",
            stripe_subscription_id="sub_reverse",
        )

        billing.handle_webhook_event(
            self.conn,
            self.subscription_event(
                shop_id,
                status="active",
                cancel_at_period_end=False,
                canceled_at=0,
                stripe_customer_id="cus_reverse",
                stripe_subscription_id="sub_reverse",
            ),
        )

        row = self.conn.execute("SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)).fetchone()
        access = billing.resolve_subscription_access(dict(row), shop_id=shop_id, now=pro_module.parse_utc_datetime("2026-08-01T12:00:00+00:00"))
        self.assertEqual(row["cancel_at_period_end"], 0)
        self.assertIsNone(row["canceled_at"])
        self.assertEqual(access.access_state, "subscribed_active")
        self.assertTrue(access.has_full_access)

    def test_lifecycle_deleted_subscription_is_read_only(self):
        _, shop_id = self.create_user_shop()
        self.insert_subscription(shop_id, status="active", stripe_customer_id="cus_deleted", stripe_subscription_id="sub_deleted")

        billing.handle_webhook_event(
            self.conn,
            self.subscription_event(
                shop_id,
                status="canceled",
                event_type="customer.subscription.deleted",
                cancel_at_period_end=True,
                canceled_at=1784707200,
                stripe_customer_id="cus_deleted",
                stripe_subscription_id="sub_deleted",
            ),
        )

        row = self.conn.execute("SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)).fetchone()
        access = billing.resolve_subscription_access(dict(row), shop_id=shop_id, now=pro_module.parse_utc_datetime("2026-08-01T12:00:00+00:00"))
        self.assertEqual(row["status"], "canceled")
        self.assertEqual(row["cancel_at_period_end"], 0)
        self.assertTrue(str(row["canceled_at"]).startswith("2026-07-22T"))
        self.assertEqual(access.access_state, "read_only_canceled")
        self.assertFalse(access.has_full_access)

    def test_lifecycle_stale_subscription_update_after_deleted_is_ignored(self):
        _, shop_id = self.create_user_shop()
        self.insert_subscription(shop_id, status="active", stripe_customer_id="cus_stale", stripe_subscription_id="sub_stale")
        billing.handle_webhook_event(
            self.conn,
            self.subscription_event(
                shop_id,
                status="canceled",
                event_type="customer.subscription.deleted",
                stripe_customer_id="cus_stale",
                stripe_subscription_id="sub_stale",
                canceled_at=1784707200,
            ),
        )

        result = billing.handle_webhook_event(
            self.conn,
            self.subscription_event(
                shop_id,
                status="active",
                stripe_customer_id="cus_stale",
                stripe_subscription_id="sub_stale",
            ),
        )

        row = self.conn.execute("SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)).fetchone()
        self.assertFalse(result["processed"])
        self.assertEqual(row["status"], "canceled")
        self.assertEqual(row["stripe_subscription_id"], "sub_stale")

    def test_lifecycle_invoice_after_canceled_subscription_is_ignored(self):
        _, shop_id = self.create_user_shop()
        self.insert_subscription(shop_id, status="active", stripe_customer_id="cus_invoice_canceled", stripe_subscription_id="sub_invoice_canceled")
        billing.handle_webhook_event(
            self.conn,
            self.subscription_event(
                shop_id,
                status="canceled",
                event_type="customer.subscription.deleted",
                stripe_customer_id="cus_invoice_canceled",
                stripe_subscription_id="sub_invoice_canceled",
                canceled_at=1784707200,
            ),
        )

        result = billing.handle_webhook_event(
            self.conn,
            self.invoice_event(
                shop_id,
                event_type="invoice.paid",
                stripe_customer_id="cus_invoice_canceled",
                stripe_subscription_id="sub_invoice_canceled",
            ),
        )

        row = self.conn.execute("SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)).fetchone()
        self.assertFalse(result["processed"])
        self.assertEqual(row["status"], "canceled")

    def test_lifecycle_invoice_metadata_only_spoofing_is_ignored(self):
        _, shop_id = self.create_user_shop()

        result = billing.handle_webhook_event(
            self.conn,
            self.invoice_event(
                shop_id,
                event_type="invoice.paid",
                stripe_customer_id="cus_unknown_invoice",
                stripe_subscription_id="sub_unknown_invoice",
                metadata_shop_id=shop_id,
            ),
        )

        rows = self.conn.execute("SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)).fetchall()
        self.assertFalse(result["processed"])
        self.assertEqual(rows, [])

    def test_lifecycle_past_due_unpaid_and_incomplete_expired_are_read_only(self):
        states = [
            ("past_due", "read_only_past_due"),
            ("unpaid", "read_only_unpaid"),
            ("incomplete_expired", "read_only_canceled"),
        ]
        for status, access_state in states:
            with self.subTest(status=status):
                _, shop_id = self.create_user_shop(email=f"{status}@example.com", shop_name=status)
                self.insert_subscription(shop_id, status="active", stripe_customer_id=f"cus_{status}", stripe_subscription_id=f"sub_{status}")
                billing.handle_webhook_event(
                    self.conn,
                    self.subscription_event(
                        shop_id,
                        status=status,
                        stripe_customer_id=f"cus_{status}",
                        stripe_subscription_id=f"sub_{status}",
                    ),
                )
                row = self.conn.execute("SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)).fetchone()
                access = billing.resolve_subscription_access(dict(row), shop_id=shop_id, now=pro_module.parse_utc_datetime("2026-08-01T12:00:00+00:00"))
                self.assertEqual(row["status"], status)
                self.assertEqual(access.access_state, access_state)
                self.assertFalse(access.has_full_access)

    def test_lifecycle_reactivated_subscription_regains_access(self):
        _, shop_id = self.create_user_shop()
        self.insert_subscription(shop_id, status="past_due", stripe_customer_id="cus_reactivated", stripe_subscription_id="sub_reactivated")

        billing.handle_webhook_event(
            self.conn,
            self.subscription_event(
                shop_id,
                status="active",
                stripe_customer_id="cus_reactivated",
                stripe_subscription_id="sub_reactivated",
            ),
        )

        row = self.conn.execute("SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)).fetchone()
        access = billing.resolve_subscription_access(dict(row), shop_id=shop_id, now=pro_module.parse_utc_datetime("2026-08-01T12:00:00+00:00"))
        self.assertEqual(access.access_state, "subscribed_active")
        self.assertTrue(access.has_full_access)

    def test_lifecycle_unknown_stripe_identifiers_are_ignored(self):
        _, shop_id = self.create_user_shop()
        self.insert_subscription(shop_id, status="active", stripe_customer_id="cus_known", stripe_subscription_id="sub_known")

        result = billing.handle_webhook_event(
            self.conn,
            self.subscription_event(
                shop_id,
                status="canceled",
                stripe_customer_id="cus_unknown",
                stripe_subscription_id="sub_unknown",
            ),
        )

        row = self.conn.execute("SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)).fetchone()
        self.assertFalse(result["processed"])
        self.assertEqual(result["reason"], "no_shop")
        self.assertEqual(row["status"], "active")
        self.assertEqual(row["stripe_customer_id"], "cus_known")
        self.assertEqual(row["stripe_subscription_id"], "sub_known")

    def test_lifecycle_subscription_created_metadata_only_spoofing_is_ignored(self):
        _, shop_id = self.create_user_shop()

        result = billing.handle_webhook_event(
            self.conn,
            self.subscription_event(
                shop_id,
                event_type="customer.subscription.created",
                stripe_customer_id="cus_metadata_only",
                stripe_subscription_id="sub_metadata_only",
                metadata_shop_id=shop_id,
            ),
        )

        rows = self.conn.execute("SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)).fetchall()
        self.assertFalse(result["processed"])
        self.assertEqual(rows, [])

    def test_lifecycle_metadata_mismatch_is_ignored(self):
        _, shop_a = self.create_user_shop(email="metadata-a@example.com", shop_name="Metadata A")
        _, shop_b = self.create_user_shop(email="metadata-b@example.com", shop_name="Metadata B")
        self.insert_subscription(shop_a, status="active", stripe_customer_id="cus_a_meta", stripe_subscription_id="sub_a_meta")
        self.insert_subscription(shop_b, status="active", stripe_customer_id="cus_b_meta", stripe_subscription_id="sub_b_meta")

        result = billing.handle_webhook_event(
            self.conn,
            self.subscription_event(
                shop_b,
                status="canceled",
                stripe_customer_id="cus_a_meta",
                stripe_subscription_id="sub_a_meta",
                metadata_shop_id=shop_b,
            ),
        )

        row_a = self.conn.execute("SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_a,)).fetchone()
        row_b = self.conn.execute("SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_b,)).fetchone()
        self.assertFalse(result["processed"])
        self.assertEqual(row_a["status"], "active")
        self.assertEqual(row_b["status"], "active")

    def test_lifecycle_conflicting_customer_and_subscription_ids_are_ignored(self):
        _, shop_a = self.create_user_shop(email="conflict-a@example.com", shop_name="Conflict A")
        _, shop_b = self.create_user_shop(email="conflict-b@example.com", shop_name="Conflict B")
        self.insert_subscription(shop_a, status="active", stripe_customer_id="cus_conflict_a", stripe_subscription_id="sub_conflict_a")
        self.insert_subscription(shop_b, status="active", stripe_customer_id="cus_conflict_b", stripe_subscription_id="sub_conflict_b")

        result = billing.handle_webhook_event(
            self.conn,
            self.subscription_event(
                shop_a,
                status="canceled",
                stripe_customer_id="cus_conflict_b",
                stripe_subscription_id="sub_conflict_a",
            ),
        )

        row_a = self.conn.execute("SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_a,)).fetchone()
        row_b = self.conn.execute("SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_b,)).fetchone()
        self.assertFalse(result["processed"])
        self.assertEqual(row_a["status"], "active")
        self.assertEqual(row_b["status"], "active")

    def test_lifecycle_malformed_event_fails_safely(self):
        _, shop_id = self.create_user_shop()
        self.insert_subscription(shop_id, status="active", stripe_customer_id="cus_safe", stripe_subscription_id="sub_safe")

        result = billing.handle_webhook_event(
            self.conn,
            {"id": "evt_bad", "type": "customer.subscription.updated", "data": {"object": "not-a-subscription"}},
        )

        row = self.conn.execute("SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)).fetchone()
        self.assertFalse(result["processed"])
        self.assertEqual(row["status"], "active")

    def test_lifecycle_subscription_objects_can_be_attribute_based(self):
        class StripeObject:
            def __init__(self, **values):
                self.__dict__.update(values)

        _, shop_id = self.create_user_shop()
        self.insert_subscription(shop_id, status="past_due", stripe_customer_id="cus_attr", stripe_subscription_id="sub_attr")
        event = StripeObject(
            type="customer.subscription.updated",
            data=StripeObject(
                object=StripeObject(
                    id="sub_attr",
                    customer="cus_attr",
                    status="active",
                    cancel_at_period_end=False,
                    metadata={"shop_id": str(shop_id)},
                    current_period_start=1784707200,
                    current_period_end=1787385600,
                    items={"data": [{"price": {"id": "price_attr"}}]},
                )
            ),
        )

        result = billing.handle_webhook_event(self.conn, event)

        row = self.conn.execute("SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)).fetchone()
        self.assertTrue(result["processed"])
        self.assertEqual(row["status"], "active")
        self.assertEqual(row["stripe_price_id"], "price_attr")

    def test_lifecycle_webhooks_do_not_call_stripe_api_clients(self):
        _, shop_id = self.create_user_shop()
        self.insert_subscription(shop_id, status="active", stripe_customer_id="cus_no_api", stripe_subscription_id="sub_no_api")

        result = billing.handle_webhook_event(
            self.conn,
            self.subscription_event(
                shop_id,
                status="past_due",
                stripe_customer_id="cus_no_api",
                stripe_subscription_id="sub_no_api",
            ),
        )

        self.assertTrue(result["processed"])
        self.assertEqual(FakeCheckoutSession.calls, [])
        self.assertEqual(FakePortalSession.calls, [])

    def test_lifecycle_cancellation_preserves_shop_operational_records(self):
        _, shop_id = self.create_user_shop()
        self.insert_subscription(shop_id, status="active", stripe_customer_id="cus_preserve", stripe_subscription_id="sub_preserve")
        pro_module.ensure_customer_status_schema(self.conn)
        now = "2026-07-22T12:00:00+00:00"
        self.conn.execute(
            """
            INSERT INTO customers (shop_id, first_name, last_name, phone, email, customer_status, created_at, updated_at)
            VALUES (?, 'Pat', 'Driver', '5550100', 'pat@example.com', 'active', ?, ?)
            """,
            (shop_id, now, now),
        )
        self.conn.commit()

        billing.handle_webhook_event(
            self.conn,
            self.subscription_event(
                shop_id,
                status="canceled",
                event_type="customer.subscription.deleted",
                stripe_customer_id="cus_preserve",
                stripe_subscription_id="sub_preserve",
                canceled_at=1784707200,
            ),
        )

        count = self.conn.execute("SELECT COUNT(*) AS count FROM customers WHERE shop_id = ?", (shop_id,)).fetchone()["count"]
        row = self.conn.execute("SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)).fetchone()
        self.assertEqual(count, 1)
        self.assertEqual(row["status"], "canceled")

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

    def test_checkout_session_completed_can_reactivate_after_cancellation_with_new_subscription(self):
        _, shop_id = self.create_user_shop()
        self.insert_subscription(
            shop_id,
            status="canceled",
            canceled_at="2026-07-22T12:00:00+00:00",
            stripe_customer_id="cus_recheckout",
            stripe_subscription_id="sub_old_recheckout",
        )
        event = {
            "id": "evt_checkout_reactivation",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "client_reference_id": str(shop_id),
                    "customer": "cus_recheckout",
                    "subscription": self.subscription_event(
                        shop_id,
                        event_type="customer.subscription.created",
                        stripe_customer_id="cus_recheckout",
                        stripe_subscription_id="sub_new_recheckout",
                    )["data"]["object"],
                }
            },
        }

        result = billing.handle_webhook_event(self.conn, event)

        row = self.conn.execute("SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)).fetchone()
        self.assertTrue(result["processed"])
        self.assertEqual(row["status"], "active")
        self.assertEqual(row["stripe_subscription_id"], "sub_new_recheckout")
        self.assertIsNone(row["canceled_at"])

    def test_shop_isolation(self):
        _, shop_a = self.create_user_shop(email="a@example.com", shop_name="A")
        _, shop_b = self.create_user_shop(email="b@example.com", shop_name="B")
        self.insert_subscription(shop_a, status="trialing", stripe_customer_id="cus_a", stripe_subscription_id="sub_a")
        self.insert_subscription(shop_b, status="trialing", stripe_customer_id="cus_b")

        billing.handle_webhook_event(
            self.conn,
            self.subscription_event(
                shop_a,
                status="active",
                stripe_customer_id="cus_a",
                stripe_subscription_id="sub_a",
            ),
        )

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
