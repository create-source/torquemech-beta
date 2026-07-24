import json
import os
import re
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
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


class SubscriptionWriteEnforcementTests(unittest.TestCase):
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
        pro_module.ensure_customer_status_schema(self.conn)
        pro_module.ensure_calendar_schema(self.conn)
        pro_module.ensure_repair_records_schema(self.conn)
        pro_module.ensure_repair_completion_schema(self.conn)
        pro_module.ensure_findings_records_schema(self.conn)
        pro_module.ensure_maintenance_records_schema(self.conn)
        pro_module.ensure_invoices_schema(self.conn)
        pro_module.ensure_repair_estimate_documents_schema(self.conn)

    def create_user_shop(self, *, email="owner@example.com", shop_name="Alpha Shop"):
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
        self.conn.execute("UPDATE shop_profile SET booking_slug = ? WHERE id = ?", (f"shop-{shop_id}", shop_id))
        self.conn.commit()
        return user_id, shop_id

    def authenticated_client(self, user_id: int) -> TestClient:
        now = "2026-07-22T12:00:00+00:00"
        session_id = f"subscription-write-session-{user_id}"
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

    def insert_subscription(self, shop_id: int, status="trialing", **fields):
        now = "2026-07-22T12:00:00+00:00"
        values = {
            "trial_started_at": None,
            "trial_ends_at": None,
            "current_period_started_at": None,
            "current_period_ends_at": None,
            "cancel_at_period_end": 0,
            "canceled_at": None,
            "access_grace_ends_at": None,
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
            VALUES (?, 'pro_solo', ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?)
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
                values["access_grace_ends_at"],
                now,
                now,
            ),
        )
        self.conn.commit()

    def seed_shop_records(self, shop_id: int):
        now = "2026-07-22T12:00:00+00:00"
        customer_id = int(
            self.conn.execute(
                """
                INSERT INTO customers (shop_id, first_name, last_name, phone, email, customer_status, notes, created_at, updated_at)
                VALUES (?, 'Ada', 'Lovelace', '5551234567', 'ada@example.com', 'active', '', ?, ?)
                """,
                (shop_id, now, now),
            ).lastrowid
        )
        vehicle_id = int(
            self.conn.execute(
                """
                INSERT INTO customer_vehicles (shop_id, customer_id, year, make, model, engine, vin, license_plate, mileage, notes, created_at, updated_at)
                VALUES (?, ?, 2018, 'Toyota', 'Camry', '', '', '', 42000, '', ?, ?)
                """,
                (shop_id, customer_id, now, now),
            ).lastrowid
        )
        repair_id = int(
            self.conn.execute(
                """
                INSERT INTO repair_records (vehicle_id, customer_id, repair_name, repair_date, mileage, labor_hours, labor_rate, parts_cost, labor_cost, total_cost, status, created_at)
                VALUES (?, ?, 'Brake pads', '2026-07-22', 42000, 1.0, 120.0, 80.0, 120.0, 200.0, 'Completed', ?)
                """,
                (vehicle_id, customer_id, now),
            ).lastrowid
        )
        invoice_id = int(
            self.conn.execute(
                """
                INSERT INTO invoices (invoice_number, repair_record_id, customer_id, vehicle_id, labor_total, parts_total, grand_total, payment_status, created_at)
                VALUES ('INV-1001', ?, ?, ?, 120, 80, 200, 'Unpaid', ?)
                """,
                (repair_id, customer_id, vehicle_id, now),
            ).lastrowid
        )
        self.conn.execute(
            """
            INSERT INTO repair_completions (
              repair_record_id, completed_at, torque_verified, fluids_verified, leaks_checked,
              codes_cleared, road_test_completed, customer_concern_resolved,
              final_inspection_passed, completion_date, completion_mileage
              , created_at, updated_at
            )
            VALUES (?, ?, 1, 1, 1, 1, 1, 1, 1, '2026-07-22', 42000, ?, ?)
            """,
            (repair_id, now, now, now),
        )
        self.conn.execute(
            """
            INSERT INTO invoice_items (
              invoice_id, repair_record_id, labor_total_override, parts_total_override,
              repair_notes_override, created_at
            )
            VALUES (?, ?, NULL, NULL, NULL, ?)
            """,
            (invoice_id, repair_id, now),
        )
        estimate_dir = pro_module.configured_storage_paths().estimate_pdfs_dir
        estimate_dir.mkdir(parents=True, exist_ok=True)
        estimate_path = estimate_dir / f"estimate-{customer_id}-{vehicle_id}.pdf"
        estimate_path.write_bytes(b"%PDF-1.4\n% test estimate\n")
        self.addCleanup(lambda: estimate_path.unlink(missing_ok=True))
        estimate_id = int(
            self.conn.execute(
                """
                INSERT INTO repair_estimate_documents (
                  customer_id, vehicle_id, estimate_date, customer_name, vehicle_label,
                  related_title, estimate_total, approval_status, pdf_path, created_at
                )
                VALUES (?, ?, '2026-07-22', 'Ada Lovelace', '2018 Toyota Camry', 'Brake pads', 200, 'pending', ?, ?)
                """,
                (customer_id, vehicle_id, str(estimate_path), now),
            ).lastrowid
        )
        maintenance_id = int(
            self.conn.execute(
                """
                INSERT INTO maintenance_records (
                  customer_id, vehicle_id, shop_id, service_type, date_performed,
                  mileage_performed, interval_miles, interval_months, notes, created_at, updated_at
                )
                VALUES (?, ?, ?, 'Oil Change', '2026-07-01', 41000, 5000, 6, '', ?, ?)
                """,
                (customer_id, vehicle_id, shop_id, now, now),
            ).lastrowid
        )
        self.conn.commit()
        return {
            "customer_id": customer_id,
            "vehicle_id": vehicle_id,
            "repair_id": repair_id,
            "invoice_id": invoice_id,
            "estimate_id": estimate_id,
            "maintenance_id": maintenance_id,
        }

    def expired_trial_client_with_records(self):
        user_id, shop_id = self.create_user_shop()
        self.insert_subscription(
            shop_id,
            "trialing",
            trial_started_at="2026-07-01T12:00:00+00:00",
            trial_ends_at="2026-07-02T12:00:00+00:00",
        )
        records = self.seed_shop_records(shop_id)
        return self.authenticated_client(user_id), shop_id, records

    def test_read_only_shop_can_view_existing_records_and_billing(self):
        client, _, records = self.expired_trial_client_with_records()
        customer_id = records["customer_id"]
        vehicle_id = records["vehicle_id"]
        repair_id = records["repair_id"]
        invoice_id = records["invoice_id"]
        estimate_id = records["estimate_id"]

        paths = [
            "/pro/dashboard",
            "/pro/customers",
            f"/pro/customers/{customer_id}",
            f"/pro/customers/{customer_id}/vehicles/{vehicle_id}",
            f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{repair_id}",
            f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/invoices/{invoice_id}",
            "/account/settings",
        ]
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 200)

        invoice_pdf = client.get(f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/invoices/{invoice_id}/pdf")
        estimate_pdf = client.get(f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/estimates/{estimate_id}/pdf")
        self.assertEqual(invoice_pdf.status_code, 200)
        self.assertEqual(invoice_pdf.headers["content-type"], "application/pdf")
        self.assertEqual(estimate_pdf.status_code, 200)
        self.assertEqual(estimate_pdf.headers["content-type"], "application/pdf")

    def test_read_only_browser_writes_redirect_to_billing_notice(self):
        client, _, records = self.expired_trial_client_with_records()
        customer_id = records["customer_id"]
        vehicle_id = records["vehicle_id"]
        repair_id = records["repair_id"]
        invoice_id = records["invoice_id"]
        maintenance_id = records["maintenance_id"]
        blocked_posts = [
            ("/pro/customers", {"first_name": "Grace"}),
            (f"/pro/customers/{customer_id}", {"first_name": "Ada", "last_name": "Byron"}),
            (f"/pro/customers/{customer_id}/vehicles", {"make": "Honda", "model": "Civic"}),
            ("/pro/estimate-conversion/create", {"estimate_payload": "{}"}),
            (f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/findings", {"finding": "Noise", "severity": "Low", "status": "Open"}),
            (f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/repairs", {"repair_name": "Inspection"}),
            (f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{repair_id}/workflow-status", {"repair_work_status": "in_progress"}),
            (f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/invoices/{invoice_id}/edit", {"payment_status": "Paid"}),
            (f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/maintenance/{maintenance_id}", {"service_type": "Oil Change"}),
            ("/pro/calendar", {"customer_name": "Ada", "vehicle_label": "Camry", "service_name": "Oil Change"}),
            ("/pro/shop-settings", {"shop_name": "New Name"}),
        ]

        first_location = ""
        for path, data in blocked_posts:
            with self.subTest(path=path):
                response = client.post(path, data=data, follow_redirects=False)
                self.assertEqual(response.status_code, 303)
                self.assertTrue(response.headers["location"].startswith("/account/settings?subscription_notice=read_only"))
                first_location = first_location or response.headers["location"]

        page = client.get(first_location)
        self.assertIn("read-only mode", page.text)

    def test_read_only_json_write_returns_403_code(self):
        client, _, _ = self.expired_trial_client_with_records()

        response = client.post(
            "/pro/customers",
            json={"first_name": "Json"},
            headers={"accept": "application/json"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "subscription_read_only")

    def test_upload_write_is_blocked_before_file_processing(self):
        client, _, records = self.expired_trial_client_with_records()
        path = (
            f"/pro/customers/{records['customer_id']}/vehicles/{records['vehicle_id']}"
            f"/repairs/{records['repair_id']}/completion"
        )

        response = client.post(
            path,
            data={"completion_date": "2026-07-22"},
            files={"after_repair_photos": ("after.jpg", b"not-real-image", "image/jpeg")},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303)
        self.assertIn("subscription_notice=read_only", response.headers["location"])

    def test_allowed_billing_webhook_and_auth_routes_remain_reachable(self):
        client, _, _ = self.expired_trial_client_with_records()

        self.assertIn(client.post("/pro/billing/checkout").status_code, {400, 503})
        self.assertIn(client.post("/pro/billing/portal").status_code, {400, 503})
        self.assertIn(client.post("/pro/billing/webhook", content=b"{}").status_code, {400, 503})
        self.assertEqual(client.get("/login").status_code, 200)
        self.assertEqual(client.get("/forgot-password").status_code, 200)

    def test_active_trial_active_subscription_and_canceling_subscription_can_write(self):
        states = [
            ("trial", "trialing", {"trial_ends_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}),
            ("active", "active", {}),
            ("canceling", "active", {"cancel_at_period_end": 1, "current_period_ends_at": (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()}),
        ]
        for label, status, fields in states:
            with self.subTest(label=label):
                user_id, shop_id = self.create_user_shop(email=f"{label}@example.com", shop_name=label)
                self.insert_subscription(shop_id, status, **fields)
                client = self.authenticated_client(user_id)
                response = client.post("/pro/customers", data={"first_name": label}, follow_redirects=False)
                self.assertEqual(response.status_code, 303)
                count = self.conn.execute("SELECT COUNT(*) AS count FROM customers WHERE shop_id = ?", (shop_id,)).fetchone()["count"]
                self.assertEqual(count, 1)

    def test_non_entitled_states_and_missing_data_fail_closed(self):
        states = [
            ("expired", "trialing", {"trial_ends_at": "2026-07-01T00:00:00+00:00"}),
            ("past_due", "past_due", {}),
            ("unpaid", "unpaid", {}),
            ("canceled", "canceled", {"cancel_at_period_end": 1, "current_period_ends_at": "2026-07-01T00:00:00+00:00"}),
            ("missing", None, {}),
        ]
        for label, status, fields in states:
            with self.subTest(label=label):
                user_id, shop_id = self.create_user_shop(email=f"{label}@example.com", shop_name=label)
                if status is not None:
                    self.insert_subscription(shop_id, status, **fields)
                client = self.authenticated_client(user_id)
                response = client.post("/pro/customers", data={"first_name": label}, follow_redirects=False)
                self.assertEqual(response.status_code, 303)
                count = self.conn.execute("SELECT COUNT(*) AS count FROM customers WHERE shop_id = ?", (shop_id,)).fetchone()["count"]
                self.assertEqual(count, 0)

    def test_one_shop_active_subscription_does_not_grant_other_shop_write_access(self):
        _, active_shop_id = self.create_user_shop(email="active@example.com", shop_name="Active")
        user_b, shop_b = self.create_user_shop(email="blocked@example.com", shop_name="Blocked")
        self.insert_subscription(active_shop_id, "active")
        self.insert_subscription(shop_b, "past_due")
        client = self.authenticated_client(user_b)

        response = client.post("/pro/customers", data={"first_name": "Blocked"}, follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        count = self.conn.execute("SELECT COUNT(*) AS count FROM customers WHERE shop_id = ?", (shop_b,)).fetchone()["count"]
        self.assertEqual(count, 0)

    def test_public_booking_page_stays_viewable_but_submit_is_neutrally_unavailable(self):
        _, shop_id = self.create_user_shop()
        self.insert_subscription(shop_id, "past_due")
        client = TestClient(main.app, base_url="http://localhost")

        page = client.get(f"/book/shop-{shop_id}")
        response = client.post(
            f"/book/shop-{shop_id}",
            data={
                "customer_name": "Public Customer",
                "customer_phone": "5551234567",
                "vehicle_year": "2018",
                "vehicle_make": "Toyota",
                "vehicle_model": "Camry",
                "service_name": "Oil Change",
                "requested_date": "2026-08-10",
                "requested_time": "10:00",
            },
        )

        self.assertEqual(page.status_code, 200)
        self.assertEqual(response.status_code, 503)
        self.assertIn("temporarily unavailable", response.text)
        self.assertNotRegex(response.text, re.compile("subscription|billing|expired", re.IGNORECASE))
        count = self.conn.execute("SELECT COUNT(*) AS count FROM service_appointments WHERE shop_id = ?", (shop_id,)).fetchone()["count"]
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
