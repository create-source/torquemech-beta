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


class NonClosingConnection(sqlite3.Connection):
    def close(self):
        pass

    def close_for_cleanup(self):
        super().close()


class OwnerAdminDashboardTests(unittest.TestCase):
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
            VALUES (?, 'pro_solo', ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
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
            {"status_key": "active", "trial_started_at": "2026-07-01T00:00:00+00:00", "monthly_amount": 25.0},
            {"status_key": "active", "trial_started_at": "2026-07-02T00:00:00+00:00", "monthly_amount": 25.0},
            {"status_key": "expired", "trial_started_at": "2026-07-03T00:00:00+00:00", "monthly_amount": None},
            {"status_key": "trial", "trial_started_at": "2026-07-04T00:00:00+00:00", "monthly_amount": None},
        ]
        metrics = pro_module.owner_admin_metrics(accounts, [], now=datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc))

        self.assertEqual(metrics["trial_to_paid_conversion"], 50.0)

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
