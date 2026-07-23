import json
import re
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
from app.billing import build_billing_display
from routers import pro as pro_module


NOW = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)


class NonClosingConnection(sqlite3.Connection):
    def close(self):
        pass

    def close_for_cleanup(self):
        super().close()


class BillingStatusUiTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False, factory=NonClosingConnection)
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close_for_cleanup)
        self.app_db_patch = patch.object(main, "app_db_conn", lambda row_factory=False: self.conn)
        self.crm_patch = patch.object(pro_module, "crm_db_conn", lambda: self.conn)
        self.utc_now_patch = patch.object(pro_module, "utc_now", return_value=NOW)
        self.env_patch = patch.dict(
            "os.environ",
            {"PRO_ENABLED": "true", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": ""},
            clear=True,
        )
        self.app_db_patch.start()
        self.crm_patch.start()
        self.utc_now_patch.start()
        self.env_patch.start()
        self.addCleanup(self.app_db_patch.stop)
        self.addCleanup(self.crm_patch.stop)
        self.addCleanup(self.utc_now_patch.stop)
        self.addCleanup(self.env_patch.stop)
        pro_module.ensure_auth_schema(self.conn)
        pro_module.ensure_shop_profile_schema(self.conn)
        pro_module.ensure_shop_subscription_schema(self.conn)
        self._email_counter = 0

    def create_user_shop(self, email="owner@example.com", shop_name="Alpha Shop") -> tuple[int, int]:
        cur = self.conn.execute(
            """
            INSERT INTO users (
              email, password_hash, first_name, last_name, is_active,
              email_verified_at, created_at, updated_at
            )
            VALUES (?, ?, 'Test', 'Owner', 1, ?, ?, ?)
            """,
            (email, pro_module.hash_password("correct-password"), NOW.isoformat(), NOW.isoformat(), NOW.isoformat()),
        )
        user_id = int(cur.lastrowid)
        shop_id = pro_module.create_shop_profile_for_user(self.conn, user_id, shop_name)
        self.conn.commit()
        return user_id, shop_id

    def authenticated_client(self, user_id: int) -> TestClient:
        session_id = f"billing-ui-session-{user_id}"
        self.conn.execute(
            """
            INSERT INTO auth_sessions (session_id, data_json, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, json.dumps({pro_module.AUTH_SESSION_USER_KEY: user_id}), NOW.isoformat(), NOW.isoformat()),
        )
        self.conn.commit()
        client = TestClient(main.app, base_url="http://localhost")
        client.cookies.set(main.SESSION_COOKIE_NAME, session_id)
        return client

    def insert_subscription(self, shop_id: int, status="active", **fields):
        values = {
            "trial_started_at": None,
            "trial_ends_at": None,
            "current_period_started_at": None,
            "current_period_ends_at": None,
            "cancel_at_period_end": 0,
            "canceled_at": None,
            "stripe_customer_id": None,
            "stripe_subscription_id": None,
            "stripe_price_id": None,
            **fields,
        }
        self.conn.execute(
            """
            INSERT INTO shop_subscriptions (
              shop_id, plan_code, status, trial_started_at, trial_ends_at,
              current_period_started_at, current_period_ends_at, cancel_at_period_end, canceled_at,
              access_grace_ends_at, stripe_customer_id, stripe_subscription_id,
              stripe_price_id, created_at, updated_at
            )
            VALUES (?, 'pro_solo', ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
            """,
            (
                shop_id,
                status,
                values["trial_started_at"],
                values["trial_ends_at"],
                values["current_period_started_at"],
                values["current_period_ends_at"],
                values["cancel_at_period_end"],
                values["canceled_at"],
                values["stripe_customer_id"],
                values["stripe_subscription_id"],
                values["stripe_price_id"],
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
        self.conn.commit()

    def account_html(self, status="active", **subscription_fields) -> str:
        self._email_counter += 1
        user_id, shop_id = self.create_user_shop(email=f"{status}-{self._email_counter}@example.com")
        if status:
            self.insert_subscription(shop_id, status, **subscription_fields)
        response = self.authenticated_client(user_id).get("/account/settings")
        self.assertEqual(response.status_code, 200)
        return response.text

    def billing_section(self, html: str) -> str:
        match = re.search(r'<section class="tm-account-section" id="billing-subscription">(.*?)<section class="tm-account-section">', html, re.S)
        if not match:
            raise AssertionError("billing section not found")
        return match.group(1)

    def test_active_subscription_displays_active_status_renewal_date_and_manage(self):
        html = self.account_html(
            "active",
            current_period_ends_at="2026-08-22T12:00:00+00:00",
            stripe_customer_id="cus_active_ui",
            stripe_subscription_id="sub_active_ui",
            stripe_price_id="price_secret_ui",
        )

        self.assertIn("Your TorqueMech Pro Solo subscription is active.", html)
        self.assertIn("Active", html)
        self.assertIn("08/22/2026", html)
        self.assertIn("Manage Subscription", html)

    def test_scheduled_cancellation_displays_end_date_and_access_remains_available(self):
        html = self.account_html(
            "active",
            current_period_ends_at="2026-08-22T12:00:00+00:00",
            cancel_at_period_end=1,
            stripe_customer_id="cus_cancel_ui",
            stripe_subscription_id="sub_cancel_ui",
        )

        self.assertIn("scheduled to end on 08/22/2026", html)
        self.assertIn("continue using TorqueMech Pro until then", html)
        self.assertIn("Full access", html)
        self.assertNotIn("creating or editing records requires an active subscription", html.lower())

    def test_cancellation_reversal_returns_to_normal_active_messaging(self):
        html = self.account_html(
            "active",
            current_period_ends_at="2026-08-22T12:00:00+00:00",
            cancel_at_period_end=0,
            stripe_customer_id="cus_reverse_ui",
            stripe_subscription_id="sub_reverse_ui",
        )

        self.assertIn("Your TorqueMech Pro Solo subscription is active.", html)
        self.assertNotIn("scheduled to end", html)

    def test_canceled_subscription_displays_read_only_and_reactivate(self):
        html = self.account_html(
            "canceled",
            canceled_at="2026-07-20T12:00:00+00:00",
            stripe_customer_id="cus_canceled_ui",
            stripe_subscription_id="sub_canceled_ui",
        )

        self.assertIn("Your subscription has ended. Your TorqueMech information is still available in read-only mode.", html)
        self.assertIn("Existing records remain viewable", html)
        self.assertIn("Reactivate", html)
        self.assertNotIn("Manage Subscription", html)

    def test_canceled_subscription_without_customer_offers_subscribe(self):
        html = self.account_html("canceled")

        self.assertIn("Subscribe", html)
        self.assertNotIn("Reactivate", html)

    def test_past_due_displays_payment_warning(self):
        html = self.account_html("past_due", stripe_customer_id="cus_past_due_ui")

        self.assertIn("We could not confirm your latest payment. Update your billing information to restore full access.", html)
        self.assertIn("Past due", html)
        self.assertIn("Manage Subscription", html)

    def test_unpaid_displays_payment_required_message(self):
        html = self.account_html("unpaid", stripe_customer_id="cus_unpaid_ui")

        self.assertIn("Payment is required to continue using TorqueMech Pro.", html)
        self.assertIn("Payment required", html)

    def test_incomplete_statuses_use_friendly_wording(self):
        for status in ("incomplete", "incomplete_expired"):
            with self.subTest(status=status):
                html = self.account_html(status)
                billing_html = self.billing_section(html)
                self.assertNotIn(status, billing_html)
                self.assertRegex(billing_html, r"(Subscription setup was not completed|subscription has ended)")

    def test_trialing_displays_trial_end_date_days_remaining_and_subscribe(self):
        html = self.account_html(
            "trialing",
            trial_started_at=NOW.isoformat(),
            trial_ends_at="2026-07-28T12:00:00+00:00",
        )

        self.assertIn("Free trial", html)
        self.assertIn("07/28/2026", html)
        self.assertIn("5 days remaining", html)
        self.assertIn("Subscribe", html)

    def test_missing_and_malformed_date_fields_do_not_break_page(self):
        for value in (None, "not-a-date"):
            with self.subTest(value=value):
                html = self.account_html("active", current_period_ends_at=value)
                self.assertIn("Your TorqueMech Pro Solo subscription is active.", html)
                self.assertNotIn("not-a-date", html)

    def test_stripe_identifiers_never_appear_in_rendered_html(self):
        html = self.account_html(
            "active",
            current_period_ends_at="2026-08-22T12:00:00+00:00",
            stripe_customer_id="cus_should_not_render",
            stripe_subscription_id="sub_should_not_render",
            stripe_price_id="price_should_not_render",
        )

        self.assertNotIn("cus_should_not_render", html)
        self.assertNotIn("sub_should_not_render", html)
        self.assertNotIn("price_should_not_render", html)

    def test_one_shop_cannot_see_another_shops_billing_status(self):
        user_a, shop_a = self.create_user_shop(email="owner-a@example.com", shop_name="Alpha")
        _, shop_b = self.create_user_shop(email="owner-b@example.com", shop_name="Beta")
        self.insert_subscription(shop_a, "active", stripe_customer_id="cus_alpha", stripe_subscription_id="sub_alpha")
        self.insert_subscription(shop_b, "past_due", stripe_customer_id="cus_beta", stripe_subscription_id="sub_beta")

        html = self.authenticated_client(user_a).get("/account/settings").text

        self.assertIn("Active", html)
        self.assertNotIn("Past due", html)
        self.assertNotIn("cus_beta", html)
        self.assertNotIn("sub_beta", html)

    def test_manage_subscription_hidden_without_stripe_customer_id(self):
        html = self.account_html("active", current_period_ends_at="2026-08-22T12:00:00+00:00")

        self.assertNotIn("Manage Subscription", html)
        self.assertIn("Stripe subscription management", html)
        self.assertIn("Portal opens after checkout", html)

    def test_unauthenticated_users_cannot_view_billing_section(self):
        response = TestClient(main.app, base_url="http://localhost").get("/account/settings", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login?next=%2Faccount%2Fsettings")

    def test_read_only_ui_state_agrees_with_access_helper(self):
        user_id, shop_id = self.create_user_shop(email="readonly@example.com")
        self.insert_subscription(shop_id, "past_due")
        access = pro_module.shop_subscription_access_context(self.conn, shop_id, now=NOW)
        html = self.authenticated_client(user_id).get("/account/settings").text

        self.assertTrue(access["is_read_only"])
        self.assertIn("Read-only access", html)
        self.assertIn("Existing records remain viewable", html)

    def test_mobile_account_settings_page_renders_successfully(self):
        user_id, shop_id = self.create_user_shop(email="mobile@example.com")
        self.insert_subscription(shop_id, "active", current_period_ends_at="2026-08-22T12:00:00+00:00")
        response = self.authenticated_client(user_id).get(
            "/account/settings",
            headers={"user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Billing &amp; Subscription", response.text)

    def test_display_helper_calculates_trial_days_safely(self):
        access = pro_module.resolve_shop_access(
            {
                "shop_id": 7,
                "status": "trialing",
                "trial_ends_at": "2026-07-23T12:00:01+00:00",
                "cancel_at_period_end": 0,
            },
            now=NOW,
            shop_id=7,
        )

        display = build_billing_display(
            {"shop_id": 7, "status": "trialing", "trial_ends_at": "2026-07-23T12:00:01+00:00"},
            access,
            now=NOW,
        )

        self.assertEqual(display["days_remaining"], 1)
        self.assertEqual(display["display_status"], "Trial active")


if __name__ == "__main__":
    unittest.main()
