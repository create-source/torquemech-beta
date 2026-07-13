import os
import json
import re
import sqlite3
import unittest
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


def auth_test_conn():
    conn = sqlite3.connect(":memory:", check_same_thread=False, factory=NonClosingConnection)
    conn.row_factory = sqlite3.Row
    pro_module.ensure_auth_schema(conn)
    pro_module.ensure_shop_profile_schema(conn)
    now = "2026-07-12T00:00:00"
    conn.execute(
        """
        INSERT INTO users (
          email, password_hash, first_name, last_name, is_active,
          email_verified_at, created_at, updated_at
        )
        VALUES ('owner@example.com', ?, 'Test', 'Owner', 1, ?, ?, ?)
        """,
        (pro_module.hash_password("correct-password"), now, now, now),
    )
    user_id = int(conn.execute("SELECT id FROM users WHERE email = 'owner@example.com'").fetchone()["id"])
    pro_module.create_shop_profile_for_user(conn, user_id, "Access Test Shop")
    conn.commit()
    return conn, user_id


def authenticated_client(conn, user_id, base_url="https://torquemech.com"):
    now = "2026-07-12T00:00:00"
    session_id = f"access-test-session-{user_id}"
    conn.execute(
        """
        INSERT INTO auth_sessions (session_id, data_json, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (session_id, json.dumps({pro_module.AUTH_SESSION_USER_KEY: user_id}), now, now),
    )
    conn.commit()
    client = TestClient(main.app, base_url=base_url)
    client.cookies.set(main.SESSION_COOKIE_NAME, session_id)
    return client


class ProAccessGateTests(unittest.TestCase):
    def test_public_pro_routes_are_blocked_when_pro_is_not_enabled(self):
        with patch.dict(os.environ, {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": ""}):
            client = TestClient(main.app, base_url="https://torquemech.com")

            pro_response = client.get("/pro")
            customers_response = client.get("/pro/customers")

        self.assertEqual(pro_response.status_code, 403)
        self.assertIn("TorqueMech Pro is in private development.", pro_response.text)
        self.assertEqual(customers_response.status_code, 403)
        self.assertIn("TorqueMech Pro is in private development.", customers_response.text)

    def test_public_homepage_does_not_link_to_pro(self):
        with patch.dict(os.environ, {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": ""}):
            client = TestClient(main.app, base_url="https://torquemech.com")
            response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('href="/pro', response.text)
        self.assertNotIn("Pro Dashboard", response.text)

    def test_localhost_bypasses_gate_for_development(self):
        with patch.dict(os.environ, {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": ""}):
            client = TestClient(main.app, base_url="http://localhost")
            response = client.get("/pro")

        self.assertNotEqual(response.status_code, 403)

    def test_access_code_unlocks_public_pro_access(self):
        with patch.dict(os.environ, {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": "shop-test", "PRO_QA_KEY": ""}):
            client = TestClient(main.app, base_url="https://torquemech.com")

            locked_response = client.get("/pro")
            bad_code_response = client.post("/pro", data={"pro_access_code": "wrong"})
            good_code_response = client.post("/pro", data={"pro_access_code": "shop-test"}, follow_redirects=False)
            unlocked_response = client.get("/pro")

        self.assertEqual(locked_response.status_code, 403)
        self.assertIn("Access code", locked_response.text)
        self.assertEqual(bad_code_response.status_code, 403)
        self.assertEqual(good_code_response.status_code, 303)
        self.assertNotEqual(unlocked_response.status_code, 403)

    def test_qa_key_unlocks_public_pro_access_and_sets_cookie(self):
        conn, _ = auth_test_conn()
        self.addCleanup(conn.close_for_cleanup)
        with patch.object(main, "app_db_conn", lambda row_factory=False: conn), patch.object(
            pro_module, "crm_db_conn", lambda: conn
        ), patch.dict(os.environ, {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": "qa-secret"}):
            client = TestClient(main.app, base_url="https://torquemech.com")

            locked_response = client.get("/pro")
            bad_key_response = client.get("/pro?qa_key=wrong")
            good_key_response = client.get("/pro?qa_key=qa-secret", follow_redirects=False)
            persisted_response = client.get("/pro/customers", follow_redirects=False)

        self.assertEqual(locked_response.status_code, 403)
        self.assertIn("TorqueMech Pro is in private development.", locked_response.text)
        self.assertEqual(bad_key_response.status_code, 403)
        self.assertIn("TorqueMech Pro is in private development.", bad_key_response.text)
        self.assertEqual(good_key_response.status_code, 303)
        self.assertEqual(good_key_response.headers["location"], "/login?next=%2Fpro%3Fqa_key%3Dqa-secret")
        self.assertIn(main.PRO_QA_ACCESS_COOKIE, good_key_response.cookies)
        self.assertNotIn("qa-secret", good_key_response.text)
        self.assertNotIn("qa-secret", good_key_response.headers.get("set-cookie", ""))
        self.assertEqual(persisted_response.status_code, 303)
        self.assertEqual(persisted_response.headers["location"], "/login?next=%2Fpro%2Fcustomers")

    def test_qa_key_cookie_does_not_store_raw_key(self):
        conn, _ = auth_test_conn()
        self.addCleanup(conn.close_for_cleanup)
        with patch.object(main, "app_db_conn", lambda row_factory=False: conn), patch.object(
            pro_module, "crm_db_conn", lambda: conn
        ), patch.dict(os.environ, {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": "qa-secret"}):
            client = TestClient(main.app, base_url="https://torquemech.com")
            response = client.get("/pro?qa_key=qa-secret", follow_redirects=False)

        cookie_value = response.cookies.get(main.PRO_QA_ACCESS_COOKIE)
        self.assertTrue(cookie_value)
        self.assertNotEqual(cookie_value, "qa-secret")

    def test_qa_gate_logs_only_boolean_diagnostics(self):
        conn, _ = auth_test_conn()
        self.addCleanup(conn.close_for_cleanup)
        with patch.object(main, "app_db_conn", lambda row_factory=False: conn), patch.object(
            pro_module, "crm_db_conn", lambda: conn
        ), patch.dict(os.environ, {"PRO_ENABLED": "false", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": "qa-secret"}):
            client = TestClient(main.app, base_url="https://torquemech.com")
            with self.assertLogs("torquemech.pro_gate", level="WARNING") as logs:
                response = client.get("/pro?qa_key=qa-secret", follow_redirects=False)

        joined_logs = "\n".join(logs.output)
        self.assertNotEqual(response.status_code, 403)
        self.assertIn("pro_qa_key_present=True", joined_logs)
        self.assertIn("qa_key_param_present=True", joined_logs)
        self.assertIn("qa_key_matched=True", joined_logs)
        self.assertIn("access_allowed=True", joined_logs)
        self.assertNotIn("qa-secret", joined_logs)

    def test_unauthenticated_user_is_redirected_to_login_with_safe_next(self):
        conn, _ = auth_test_conn()
        self.addCleanup(conn.close_for_cleanup)
        with patch.object(main, "app_db_conn", lambda row_factory=False: conn), patch.object(
            pro_module, "crm_db_conn", lambda: conn
        ), patch.dict(os.environ, {"PRO_ENABLED": "true", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": ""}):
            client = TestClient(main.app, base_url="https://torquemech.com")
            response = client.get("/pro/calendar", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login?next=%2Fpro%2Fcalendar")

    def test_authenticated_user_can_access_pro_routes(self):
        conn, user_id = auth_test_conn()
        self.addCleanup(conn.close_for_cleanup)
        with patch.object(main, "app_db_conn", lambda row_factory=False: conn), patch.object(
            pro_module, "crm_db_conn", lambda: conn
        ), patch.dict(os.environ, {"PRO_ENABLED": "true", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": ""}):
            client = authenticated_client(conn, user_id)
            response = client.get("/pro/shop-settings")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Access Test Shop", response.text)

    def test_login_next_accepts_safe_url_and_rejects_external_url(self):
        conn, _ = auth_test_conn()
        self.addCleanup(conn.close_for_cleanup)
        with patch.object(main, "app_db_conn", lambda row_factory=False: conn), patch.object(
            pro_module, "crm_db_conn", lambda: conn
        ), patch.dict(os.environ, {"PRO_ENABLED": "true", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": ""}):
            client = TestClient(main.app, base_url="https://torquemech.com")
            safe_page = client.get("/login?next=/pro/calendar")
            safe_response = client.post(
                "/login",
                data={
                    "csrf_token": csrf_from(safe_page.text),
                    "email": "owner@example.com",
                    "password": "correct-password",
                    "next": "/pro/calendar",
                },
                follow_redirects=False,
            )
            client = TestClient(main.app, base_url="https://torquemech.com")
            unsafe_page = client.get("/login?next=https://evil.example/pro")
            unsafe_response = client.post(
                "/login",
                data={
                    "csrf_token": csrf_from(unsafe_page.text),
                    "email": "owner@example.com",
                    "password": "correct-password",
                    "next": "https://evil.example/pro",
                },
                follow_redirects=False,
            )

        self.assertEqual(safe_response.status_code, 303)
        self.assertEqual(safe_response.headers["location"], "/pro/calendar")
        self.assertEqual(unsafe_response.status_code, 303)
        self.assertEqual(unsafe_response.headers["location"], "/pro/dashboard")

    def test_public_booking_route_remains_public(self):
        conn, _ = auth_test_conn()
        self.addCleanup(conn.close_for_cleanup)
        with patch.object(main, "app_db_conn", lambda row_factory=False: conn), patch.object(
            pro_module, "crm_db_conn", lambda: conn
        ), patch.dict(os.environ, {"PRO_ENABLED": "true", "PRO_ACCESS_CODE": "", "PRO_QA_KEY": ""}):
            client = TestClient(main.app, base_url="https://torquemech.com")
            booking_response = client.get("/book/access-test-shop")
            approval_response = client.get("/pro/customers/1/vehicles/1/approvals/1", follow_redirects=False)

        self.assertEqual(booking_response.status_code, 200)
        self.assertIn("Schedule service", booking_response.text)
        self.assertEqual(approval_response.status_code, 303)
        self.assertEqual(
            approval_response.headers["location"],
            "/login?next=%2Fpro%2Fcustomers%2F1%2Fvehicles%2F1%2Fapprovals%2F1",
        )


if __name__ == "__main__":
    unittest.main()
