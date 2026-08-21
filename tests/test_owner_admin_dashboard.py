import json
import os
import re
import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
from routers import pro as pro_module


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    if not match:
        raise AssertionError("csrf token not found")
    return match.group(1)


class NonClosingConnection(sqlite3.Connection):
    def execute(self, sql, parameters=(), /):
        if str(sql).strip().startswith("UPDATE shop_profile SET is_test_account = ?"):
            self.test_account_update_params.append(parameters)
        return super().execute(sql, parameters)

    def close(self):
        pass

    def close_for_cleanup(self):
        super().close()


class OwnerAdminDashboardTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False, factory=NonClosingConnection)
        self.conn.row_factory = sqlite3.Row
        self.conn.test_account_update_params = []
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

    def create_user_shop(self, email: str, shop_name: str, created_at: str = "2026-08-05T12:00:00+00:00"):
        cur = self.conn.execute(
            """
            INSERT INTO users (
              email, password_hash, first_name, last_name, is_active,
              email_verified_at, created_at, updated_at
            )
            VALUES (?, ?, 'Test', 'Owner', 1, ?, ?, ?)
            """,
            (pro_module.normalize_email(email), pro_module.hash_password("correct-password"), created_at, created_at, created_at),
        )
        user_id = int(cur.lastrowid)
        shop_id = pro_module.create_shop_profile_for_user(self.conn, user_id, shop_name)
        self.conn.commit()
        return user_id, shop_id

    def insert_subscription(self, shop_id: int, status="trialing", **fields):
        now = "2026-08-05T12:00:00+00:00"
        defaults = {
            "trial_started_at": None,
            "trial_ends_at": None,
            "current_period_started_at": None,
            "current_period_ends_at": None,
            "cancel_at_period_end": 0,
            "canceled_at": None,
            "stripe_customer_id": None,
            "stripe_subscription_id": None,
            "stripe_price_id": None,
            "recurring_unit_amount": None,
            "currency": None,
            "billing_interval": None,
            "billing_interval_count": None,
            "quantity": None,
            "first_paid_at": None,
        }
        defaults.update(fields)
        self.conn.execute(
            """
            INSERT INTO shop_subscriptions (
              shop_id, plan_code, status, trial_started_at, trial_ends_at,
              current_period_started_at, current_period_ends_at, cancel_at_period_end, canceled_at,
              access_grace_ends_at, stripe_customer_id, stripe_subscription_id,
              stripe_price_id, recurring_unit_amount, currency, billing_interval,
              billing_interval_count, quantity, first_paid_at, created_at, updated_at
            )
            VALUES (?, 'pro_solo', ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                shop_id,
                status,
                defaults["trial_started_at"],
                defaults["trial_ends_at"],
                defaults["current_period_started_at"],
                defaults["current_period_ends_at"],
                defaults["cancel_at_period_end"],
                defaults["canceled_at"],
                defaults["stripe_customer_id"],
                defaults["stripe_subscription_id"],
                defaults["stripe_price_id"],
                defaults["recurring_unit_amount"],
                defaults["currency"],
                defaults["billing_interval"],
                defaults["billing_interval_count"],
                defaults["quantity"],
                defaults["first_paid_at"],
                now,
                now,
            ),
        )
        self.conn.commit()

    def authenticated_client(self, user_id: int) -> TestClient:
        now = "2026-08-05T12:00:00+00:00"
        session_id = f"owner-admin-session-{user_id}"
        self.conn.execute(
            """
            INSERT INTO auth_sessions (session_id, data_json, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, json.dumps({pro_module.AUTH_SESSION_USER_KEY: user_id}), now, now),
        )
        self.conn.commit()
        client = TestClient(main.app, base_url="https://torquemech.com")
        client.cookies.set(main.SESSION_COOKIE_NAME, session_id)
        return client

    def seed_dashboard_accounts(self):
        trial_user, trial_shop = self.create_user_shop("trial@example.com", "Trial Shop", "2026-08-03T12:00:00+00:00")
        paid_user, paid_shop = self.create_user_shop("paid@example.com", "Paid Shop", "2026-07-10T12:00:00+00:00")
        canceled_user, canceled_shop = self.create_user_shop("canceled@example.com", "Canceled Shop", "2026-06-10T12:00:00+00:00")
        expired_user, expired_shop = self.create_user_shop("expired@example.com", "Expired Shop", "2026-05-10T12:00:00+00:00")
        self.insert_subscription(
            trial_shop,
            "trialing",
            trial_started_at="2026-08-03T12:00:00+00:00",
            trial_ends_at="2026-08-25T12:00:00+00:00",
        )
        self.insert_subscription(
            paid_shop,
            "active",
            trial_started_at="2026-07-10T12:00:00+00:00",
            trial_ends_at="2026-07-24T12:00:00+00:00",
            current_period_ends_at="2026-09-10T12:00:00+00:00",
            stripe_price_id="price_paid",
            recurring_unit_amount=2900,
            currency="usd",
            billing_interval="month",
            billing_interval_count=1,
            quantity=1,
            first_paid_at="2026-07-24T12:00:00+00:00",
        )
        self.insert_subscription(
            canceled_shop,
            "canceled",
            trial_started_at="2026-06-10T12:00:00+00:00",
            trial_ends_at="2026-06-24T12:00:00+00:00",
            canceled_at="2026-07-01T12:00:00+00:00",
        )
        self.insert_subscription(
            expired_shop,
            "trialing",
            trial_started_at="2026-05-10T12:00:00+00:00",
            trial_ends_at="2026-05-24T12:00:00+00:00",
        )
        return {
            "trial": (trial_user, trial_shop),
            "paid": (paid_user, paid_shop),
            "canceled": (canceled_user, canceled_shop),
            "expired": (expired_user, expired_shop),
        }

    def test_anonymous_access_is_denied(self):
        self.create_user_shop("existing@example.com", "Existing Shop")
        response = TestClient(main.app, base_url="https://torquemech.com").get("/pro/admin", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login?next=%2Fpro%2Fadmin")

    def test_authenticated_non_admin_access_is_denied(self):
        user_id, _shop_id = self.create_user_shop("user@example.com", "Normal Shop")
        with patch.dict(os.environ, {"TORQUEMECH_ADMIN_EMAILS": "owner@example.com"}, clear=False):
            response = self.authenticated_client(user_id).get("/pro/admin")

        self.assertEqual(response.status_code, 403)
        self.assertNotIn("Platform Dashboard", response.text)

    def test_allowlisted_admin_can_access_dashboard(self):
        user_id, _shop_id = self.create_user_shop("Owner@Example.COM", "Owner Shop")
        with patch.dict(os.environ, {"TORQUEMECH_ADMIN_EMAILS": " owner@example.com "}, clear=False):
            response = self.authenticated_client(user_id).get("/pro/admin")

        self.assertEqual(response.status_code, 200)
        self.assertIn("TorqueMech Platform Dashboard", response.text)

    def test_missing_admin_allowlist_fails_closed(self):
        user_id, _shop_id = self.create_user_shop("owner@example.com", "Owner Shop")
        response = self.authenticated_client(user_id).get("/pro/admin")

        self.assertEqual(response.status_code, 403)

    def test_status_classification(self):
        now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)

        self.assertEqual(
            pro_module.subscription_status_bucket(
                {"shop_id": 1, "status": "trialing", "trial_ends_at": "2026-08-25T12:00:00+00:00"},
                now=now,
            )["label"],
            "Trial",
        )
        self.assertEqual(
            pro_module.subscription_status_bucket(
                {"shop_id": 1, "status": "active", "current_period_ends_at": "2026-09-25T12:00:00+00:00"},
                now=now,
            )["label"],
            "Active paid",
        )
        self.assertEqual(
            pro_module.subscription_status_bucket({"shop_id": 1, "status": "canceled"}, now=now)["label"],
            "Canceled",
        )
        self.assertEqual(
            pro_module.subscription_status_bucket(
                {"shop_id": 1, "status": "trialing", "trial_ends_at": "2026-08-01T12:00:00+00:00"},
                now=now,
            )["label"],
            "Expired trial",
        )

    def test_metric_totals_with_controlled_fixture_data(self):
        self.seed_dashboard_accounts()
        context = pro_module.owner_admin_dashboard_context(
            self.conn,
            now=datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(context["metrics"]["total_signups"], 4)
        self.assertEqual(context["metrics"]["new_signups_month"], 1)
        self.assertEqual(context["metrics"]["active_trials"], 1)
        self.assertEqual(context["metrics"]["active_paid"], 1)
        self.assertEqual(context["metrics"]["canceled"], 1)
        self.assertEqual(context["metrics"]["expired_trials"], 1)
        self.assertEqual([account["shop_name"] for account in context["accounts"]][:2], ["Trial Shop", "Paid Shop"])

    def test_mrr_calculation_normalizes_recurring_prices(self):
        self.assertEqual(
            pro_module.subscription_monthly_amount(
                {
                    "stripe_price_unit_amount": 120000,
                    "stripe_price_recurring_interval": "year",
                    "stripe_price_recurring_interval_count": 1,
                }
            ),
            100.0,
        )
        self.assertEqual(
            pro_module.subscription_monthly_amount(
                {
                    "stripe_price_unit_amount": 9000,
                    "stripe_price_recurring_interval": "month",
                    "stripe_price_recurring_interval_count": 3,
                }
            ),
            30.0,
        )
        self.assertIsNone(pro_module.subscription_monthly_amount({"stripe_price_id": "price_without_amount"}))
        metrics = pro_module.owner_admin_metrics(
            [
                {"status_key": "active", "trial_started_at": "2026-07-01T00:00:00+00:00", "monthly_amount": 100.0},
                {"status_key": "active", "trial_started_at": "2026-07-02T00:00:00+00:00", "monthly_amount": 30.0},
                {"status_key": "trial", "trial_started_at": "2026-08-01T00:00:00+00:00", "monthly_amount": None},
            ],
            [],
            now=datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(metrics["mrr"], 130.0)

    def test_trial_to_paid_conversion_calculation(self):
        accounts = [
            {"status_key": "active", "trial_started_at": "2026-07-01T00:00:00+00:00", "trial_ends_at": "2026-07-15T00:00:00+00:00", "first_paid_at": "2026-07-15T01:00:00+00:00", "monthly_amount": 25.0},
            {"status_key": "active", "trial_started_at": "2026-07-02T00:00:00+00:00", "trial_ends_at": "2026-07-16T00:00:00+00:00", "first_paid_at": "2026-07-16T01:00:00+00:00", "monthly_amount": 25.0},
            {"status_key": "expired", "trial_started_at": "2026-07-03T00:00:00+00:00", "trial_ends_at": "2026-07-17T00:00:00+00:00", "monthly_amount": None},
            {"status_key": "trial", "trial_started_at": "2026-08-14T00:00:00+00:00", "trial_ends_at": "2026-08-28T00:00:00+00:00", "monthly_amount": None},
        ]
        metrics = pro_module.owner_admin_metrics(accounts, [], now=datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc))

        self.assertAlmostEqual(metrics["trial_to_paid_conversion"], 66.66666666666666)
        self.assertEqual(metrics["eligible_completed_trials"], 3)
        self.assertEqual(metrics["paid_conversions"], 2)

    def test_active_trials_excluded_from_conversion_denominator(self):
        metrics = pro_module.owner_admin_metrics(
            [
                {"status_key": "trial", "trial_ends_at": "2026-08-25T00:00:00+00:00", "monthly_amount": None},
                {"status_key": "expired", "trial_ends_at": "2026-07-01T00:00:00+00:00", "monthly_amount": None},
            ],
            [],
            now=datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(metrics["eligible_completed_trials"], 1)
        self.assertEqual(metrics["paid_conversions"], 0)
        self.assertEqual(metrics["trial_to_paid_conversion"], 0.0)

    def test_quantity_and_yearly_mrr_normalization_and_exclusions(self):
        accounts = [
            {"status_key": "active", "monthly_amount": 58.0, "is_test_account": False},
            {"status_key": "active", "monthly_amount": 100.0, "is_test_account": False},
            {"status_key": "active", "monthly_amount": 999.0, "is_test_account": True},
            {"status_key": "canceled", "monthly_amount": 25.0, "is_test_account": False},
            {"status_key": "payment_issue", "monthly_amount": 25.0, "is_test_account": False},
        ]
        metrics = pro_module.owner_admin_metrics(accounts, [], now=datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc))

        self.assertEqual(
            pro_module.subscription_monthly_amount(
                {"recurring_unit_amount": 2900, "billing_interval": "month", "billing_interval_count": 1, "quantity": 2}
            ),
            58.0,
        )
        self.assertEqual(
            pro_module.subscription_monthly_amount(
                {"recurring_unit_amount": 120000, "billing_interval": "year", "billing_interval_count": 1, "quantity": 1}
            ),
            100.0,
        )
        self.assertEqual(metrics["mrr"], 158.0)
        self.assertEqual(metrics["active_paid"], 2)
        self.assertEqual(metrics["test_accounts"], 1)

    def test_status_filtering_and_existing_shop_isolation_remain_intact(self):
        seeded = self.seed_dashboard_accounts()
        admin_user, _admin_shop = self.create_user_shop("admin@example.com", "Admin Shop")
        with patch.dict(os.environ, {"TORQUEMECH_ADMIN_EMAILS": "admin@example.com"}, clear=False):
            client = self.authenticated_client(admin_user)
            admin_response = client.get("/pro/admin?status=expired")
            shop_response = client.get("/pro/shop-settings")

        self.assertEqual(admin_response.status_code, 200)
        self.assertIn("Expired Shop", admin_response.text)
        self.assertNotIn("Trial Shop", admin_response.text)
        self.assertEqual(shop_response.status_code, 200)
        self.assertIn("Admin Shop", shop_response.text)
        self.assertNotIn("Paid Shop", shop_response.text)
        self.assertGreater(seeded["paid"][1], 0)

    def test_mark_unmark_test_account_requires_admin_and_csrf(self):
        normal_user, shop_id = self.create_user_shop("normal@example.com", "Normal Shop")
        admin_user, _ = self.create_user_shop("admin@example.com", "Admin Shop")
        normal_client = self.authenticated_client(normal_user)
        admin_client = self.authenticated_client(admin_user)

        with patch.dict(os.environ, {"TORQUEMECH_ADMIN_EMAILS": "admin@example.com"}, clear=False):
            denied = normal_client.post(
                f"/pro/admin/accounts/{shop_id}/test-account",
                data={"csrf_token": "anything", "is_test_account": "1"},
                follow_redirects=False,
            )
            missing_csrf = admin_client.post(
                f"/pro/admin/accounts/{shop_id}/test-account",
                data={"is_test_account": "1"},
                follow_redirects=False,
            )
            page = admin_client.get("/pro/admin")
            marked = admin_client.post(
                f"/pro/admin/accounts/{shop_id}/test-account",
                data={"csrf_token": csrf_from(page.text), "is_test_account": "1"},
                follow_redirects=False,
            )
            marked_row = self.conn.execute("SELECT is_test_account FROM shop_profile WHERE id = ?", (shop_id,)).fetchone()
            unmarked_page = admin_client.get("/pro/admin?account_type=all")
            unmarked = admin_client.post(
                f"/pro/admin/accounts/{shop_id}/test-account",
                data={"csrf_token": csrf_from(unmarked_page.text), "is_test_account": "0", "account_filter": "all"},
                follow_redirects=False,
            )

        row = self.conn.execute("SELECT is_test_account FROM shop_profile WHERE id = ?", (shop_id,)).fetchone()
        self.assertIn(denied.status_code, {303, 403})
        self.assertEqual(missing_csrf.status_code, 403)
        self.assertEqual(marked.status_code, 303)
        self.assertIn("notice=Account+marked+as+test", marked.headers["location"])
        self.assertEqual(marked_row["is_test_account"], 1)
        self.assertEqual(unmarked.status_code, 303)
        self.assertEqual(row["is_test_account"], 0)
        self.assertEqual([params[0] for params in self.conn.test_account_update_params], [1, 0])
        self.assertTrue(all(type(params[0]) is int for params in self.conn.test_account_update_params))

    def test_test_accounts_are_labeled_and_filtered(self):
        self.seed_dashboard_accounts()
        test_user, test_shop = self.create_user_shop("test@example.com", "QA Shop")
        self.insert_subscription(test_shop, "active", recurring_unit_amount=2900, billing_interval="month", billing_interval_count=1, quantity=1)
        self.conn.execute("UPDATE shop_profile SET is_test_account = 1 WHERE id = ?", (test_shop,))
        self.conn.commit()
        admin_user, _ = self.create_user_shop("admin@example.com", "Admin Shop")

        with patch.dict(os.environ, {"TORQUEMECH_ADMIN_EMAILS": "admin@example.com"}, clear=False):
            client = self.authenticated_client(admin_user)
            real_page = client.get("/pro/admin")
            test_page = client.get("/pro/admin?account_type=test")
            all_page = client.get("/pro/admin?account_type=all")

        self.assertEqual(real_page.status_code, 200)
        self.assertNotIn("QA Shop", real_page.text)
        self.assertIn("QA Shop", test_page.text)
        self.assertIn("Test account", test_page.text)
        self.assertIn("QA Shop", all_page.text)
        self.assertGreater(test_user, 0)

    def test_stripe_sync_requires_admin_csrf_and_handles_failure(self):
        admin_user, shop_id = self.create_user_shop("admin@example.com", "Admin Shop")
        self.insert_subscription(
            shop_id,
            "active",
            stripe_customer_id="cus_sync",
            stripe_subscription_id="sub_sync",
        )

        class FailingSyncService:
            def retrieve_subscription(self, stripe_subscription_id):
                raise pro_module.BillingProviderError("sk_test_should_not_leak")

        with patch.dict(os.environ, {"TORQUEMECH_ADMIN_EMAILS": "admin@example.com"}, clear=False):
            client = self.authenticated_client(admin_user)
            no_csrf = client.post("/pro/admin/sync-stripe-data")
            page = client.get("/pro/admin")
            with patch.object(pro_module, "StripeBillingService", return_value=FailingSyncService()):
                response = client.post(
                    "/pro/admin/sync-stripe-data",
                    data={"csrf_token": csrf_from(page.text)},
                    follow_redirects=False,
                )

        self.assertEqual(no_csrf.status_code, 403)
        self.assertEqual(response.status_code, 303)
        self.assertIn("failed", response.headers["location"])
        self.assertNotIn("sk_test", response.headers["location"])

    def test_stripe_sync_updates_matching_subscription_billing_fields(self):
        admin_user, shop_id = self.create_user_shop("admin@example.com", "Admin Shop")
        self.insert_subscription(
            shop_id,
            "active",
            stripe_customer_id="cus_sync",
            stripe_subscription_id="sub_sync",
        )

        class SyncService:
            def retrieve_subscription(self, stripe_subscription_id):
                return {
                    "id": stripe_subscription_id,
                    "customer": "cus_sync",
                    "status": "active",
                    "current_period_start": 1784707200,
                    "current_period_end": 1787385600,
                    "items": {
                        "data": [
                            {
                                "quantity": 3,
                                "price": {
                                    "id": "price_synced",
                                    "unit_amount": 3500,
                                    "currency": "usd",
                                    "recurring": {"interval": "month", "interval_count": 1},
                                },
                            }
                        ]
                    },
                }

        with patch.dict(os.environ, {"TORQUEMECH_ADMIN_EMAILS": "admin@example.com"}, clear=False):
            client = self.authenticated_client(admin_user)
            page = client.get("/pro/admin")
            with patch.object(pro_module, "StripeBillingService", return_value=SyncService()):
                response = client.post(
                    "/pro/admin/sync-stripe-data",
                    data={"csrf_token": csrf_from(page.text)},
                    follow_redirects=False,
                )

        row = self.conn.execute("SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)).fetchone()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(row["stripe_price_id"], "price_synced")
        self.assertEqual(row["recurring_unit_amount"], 3500)
        self.assertEqual(row["billing_interval"], "month")
        self.assertEqual(row["quantity"], 3)

    def test_dashboard_sql_uses_sqlite_postgres_compatible_patterns(self):
        source = Path(pro_module.__file__).resolve()
        text = source.read_text(encoding="utf-8")
        sql_match = re.search(r"def owner_admin_account_rows.*?conn\.execute\(\s*\"\"\"(.*?)\"\"\"", text, re.S)
        self.assertIsNotNone(sql_match)
        sql = sql_match.group(1).lower()

        self.assertNotIn("date_trunc", sql)
        self.assertNotIn("::", sql)
        self.assertNotIn("interval '", sql)
        self.assertIn("left join users", sql)
        self.assertIn("left join shop_subscriptions", sql)


if __name__ == "__main__":
    unittest.main()
