import json
import re
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
from routers import pro as pro_module


class NonClosingConnection(sqlite3.Connection):
    def close(self):
        pass

    def close_for_cleanup(self):
        super().close()


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    if not match:
        raise AssertionError("csrf token not found")
    return match.group(1)


class ShopSubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False, factory=NonClosingConnection)
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close_for_cleanup)
        test_tmp_root = Path(main.BASE_DIR) / "tmp" / "test_email_outboxes"
        test_tmp_root.mkdir(parents=True, exist_ok=True)
        self.outbox_path = test_tmp_root / f"{self._testMethodName}.jsonl"
        self.outbox_path.unlink(missing_ok=True)
        self.addCleanup(lambda: self.outbox_path.unlink(missing_ok=True))
        self.app_db_patch = patch.object(main, "app_db_conn", lambda row_factory=False: self.conn)
        self.crm_patch = patch.object(pro_module, "crm_db_conn", lambda: self.conn)
        self.env_patch = patch.dict(
            "os.environ",
            {
                "PRO_ENABLED": "true",
                "PRO_ACCESS_CODE": "",
                "PRO_QA_KEY": "",
                "TORQUEMECH_EMAIL_TRANSPORT": "test",
                "TORQUEMECH_DEV_EMAIL_OUTBOX": str(self.outbox_path),
            },
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

    def create_verified_user_shop(self, email="owner@example.com", shop_name="Alpha Shop") -> tuple[int, int]:
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

    def signup(self, client, email="trial@example.com", shop_name="Trial Shop"):
        page = client.get("/signup")
        return client.post(
            "/signup",
            data={
                "csrf_token": csrf_from(page.text),
                "first_name": "Trial",
                "last_name": "Owner",
                "email": email,
                "password": "correct-password",
                "confirm_password": "correct-password",
                "shop_name": shop_name,
                "terms": "1",
            },
            follow_redirects=False,
        )

    def latest_verification_token(self):
        messages = [
            json.loads(line)
            for line in self.outbox_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        parsed = urlparse(messages[-1]["verification_url"])
        return parse_qs(parsed.query)["token"][0]

    def insert_subscription(self, shop_id: int, status: str, **fields):
        now = "2026-07-22T12:00:00+00:00"
        values = {
            "trial_started_at": None,
            "trial_ends_at": None,
            "current_period_started_at": None,
            "current_period_ends_at": None,
            "canceled_at": None,
            "access_grace_ends_at": None,
            **fields,
        }
        self.conn.execute(
            """
            INSERT INTO shop_subscriptions (
              shop_id, plan_code, status, trial_started_at, trial_ends_at,
              current_period_started_at, current_period_ends_at, canceled_at,
              access_grace_ends_at, stripe_customer_id, stripe_subscription_id,
              stripe_price_id, created_at, updated_at
            )
            VALUES (?, 'pro_solo', ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?)
            """,
            (
                shop_id,
                status,
                values["trial_started_at"],
                values["trial_ends_at"],
                values["current_period_started_at"],
                values["current_period_ends_at"],
                values["canceled_at"],
                values["access_grace_ends_at"],
                now,
                now,
            ),
        )
        self.conn.commit()
        return pro_module.load_shop_subscription(self.conn, shop_id)

    def test_existing_development_shop_has_full_access(self):
        _, shop_id = self.create_verified_user_shop()

        access = pro_module.shop_subscription_access_context(
            self.conn,
            shop_id,
            now=datetime(2026, 7, 22, 12, tzinfo=timezone.utc),
        )

        self.assertEqual(access["status"], "development")
        self.assertTrue(access["can_view"])
        self.assertTrue(access["can_write"])
        self.assertTrue(access["can_manage_billing"])

    def test_newly_verified_shop_receives_exactly_one_14_day_trial(self):
        self.create_verified_user_shop(email="seed@example.com", shop_name="Seed Shop")
        fixed_now = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
        client = TestClient(main.app, base_url="http://localhost")
        self.assertEqual(self.signup(client).headers["location"], "/check-email")
        token = self.latest_verification_token()

        with patch.object(pro_module, "utc_now", return_value=fixed_now):
            response = client.get(f"/verify-email?token={token}", follow_redirects=False)

        user = self.conn.execute("SELECT id FROM users WHERE email = 'trial@example.com'").fetchone()
        shop = self.conn.execute("SELECT id FROM shop_profile WHERE owner_user_id = ?", (user["id"],)).fetchone()
        rows = self.conn.execute("SELECT * FROM shop_subscriptions WHERE shop_id = ?", (shop["id"],)).fetchall()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "trialing")
        self.assertEqual(rows[0]["plan_code"], "pro_solo")
        self.assertEqual(pro_module.parse_utc_datetime(rows[0]["trial_started_at"]), fixed_now)
        self.assertEqual(pro_module.parse_utc_datetime(rows[0]["trial_ends_at"]), fixed_now + timedelta(days=14))

    def test_repeated_initialization_does_not_reset_or_extend_trial(self):
        _, shop_id = self.create_verified_user_shop()
        first_now = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)
        second_now = datetime(2026, 7, 25, 12, tzinfo=timezone.utc)

        first = pro_module.create_or_ensure_shop_subscription(self.conn, shop_id, now=first_now)
        second = pro_module.create_or_ensure_shop_subscription(self.conn, shop_id, now=second_now)
        count = self.conn.execute("SELECT COUNT(*) AS count FROM shop_subscriptions WHERE shop_id = ?", (shop_id,)).fetchone()["count"]

        self.assertEqual(count, 1)
        self.assertEqual(second["trial_started_at"], first["trial_started_at"])
        self.assertEqual(second["trial_ends_at"], first["trial_ends_at"])

    def test_active_trial_allows_writes_and_expired_trial_denies_writes(self):
        _, active_shop_id = self.create_verified_user_shop(email="active-trial@example.com", shop_name="Active Trial")
        _, expired_shop_id = self.create_verified_user_shop(email="expired-trial@example.com", shop_name="Expired Trial")
        now = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)
        active = self.insert_subscription(active_shop_id, "trialing", trial_ends_at="2026-07-23T12:00:00+00:00")
        expired = self.insert_subscription(expired_shop_id, "trialing", trial_ends_at="2026-07-21T12:00:00+00:00")

        self.assertTrue(pro_module.resolve_shop_access(active, now=now, shop_id=active_shop_id)["can_write"])
        self.assertFalse(pro_module.resolve_shop_access(expired, now=now, shop_id=expired_shop_id)["can_write"])

    def test_subscription_statuses_resolve_write_access(self):
        _, active_shop_id = self.create_verified_user_shop(email="active@example.com", shop_name="Active")
        _, canceled_shop_id = self.create_verified_user_shop(email="canceled@example.com", shop_name="Canceled")
        _, past_due_shop_id = self.create_verified_user_shop(email="pastdue@example.com", shop_name="Past Due")
        now = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)
        active = self.insert_subscription(active_shop_id, "active")
        canceled = self.insert_subscription(canceled_shop_id, "canceled", current_period_ends_at="2026-08-01T00:00:00+00:00")
        past_due = self.insert_subscription(past_due_shop_id, "past_due", access_grace_ends_at="2026-07-23T00:00:00+00:00")

        self.assertTrue(pro_module.resolve_shop_access(active, now=now, shop_id=active_shop_id)["can_write"])
        self.assertTrue(pro_module.resolve_shop_access(canceled, now=now, shop_id=canceled_shop_id)["can_write"])
        self.assertTrue(pro_module.resolve_shop_access(past_due, now=now, shop_id=past_due_shop_id)["can_write"])

    def test_subscription_records_remain_isolated_by_shop(self):
        _, shop_a = self.create_verified_user_shop(email="a@example.com", shop_name="Shop A")
        _, shop_b = self.create_verified_user_shop(email="b@example.com", shop_name="Shop B")
        now = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)
        self.insert_subscription(shop_a, "trialing", trial_ends_at="2026-07-23T12:00:00+00:00")
        self.insert_subscription(shop_b, "trialing", trial_ends_at="2026-07-21T12:00:00+00:00")

        access_a = pro_module.shop_subscription_access_context(self.conn, shop_a, now=now)
        access_b = pro_module.shop_subscription_access_context(self.conn, shop_b, now=now)

        self.assertTrue(access_a["can_write"])
        self.assertFalse(access_b["can_write"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) AS count FROM shop_subscriptions").fetchone()["count"], 2)

    def test_utc_date_handling_is_deterministic(self):
        _, shop_id = self.create_verified_user_shop()
        now = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)
        subscription = self.insert_subscription(
            shop_id,
            "trialing",
            trial_ends_at="2026-07-22T08:00:00-04:00",
        )

        access = pro_module.resolve_shop_access(subscription, now=now, shop_id=shop_id)

        self.assertFalse(access["can_write"])
        self.assertEqual(access["trial_days_remaining"], 0)


if __name__ == "__main__":
    unittest.main()
