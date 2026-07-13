import json
import re
import sqlite3
import unittest
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


class AuthShopIsolationTests(unittest.TestCase):
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
                "TORQUEMECH_BOOTSTRAP_TOKEN": "boot-secret",
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

    def client(self):
        return TestClient(main.app, base_url="http://localhost")

    def bootstrap_owner(self, client, email="owner@example.com", shop_name="Alpha Shop", bootstrap_token="boot-secret"):
        page = client.get("/admin/bootstrap")
        data = {
            "csrf_token": csrf_from(page.text),
            "bootstrap_token": bootstrap_token,
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": email,
            "password": "correct-password",
            "confirm_password": "correct-password",
            "shop_name": shop_name,
            "terms": "1",
        }
        return client.post("/admin/bootstrap", data=data, follow_redirects=False)

    def signup(self, client, email="user@example.com", shop_name="Beta Shop"):
        page = client.get("/signup")
        data = {
            "csrf_token": csrf_from(page.text),
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": email,
            "password": "correct-password",
            "confirm_password": "correct-password",
            "shop_name": shop_name,
            "terms": "1",
        }
        response = client.post("/signup", data=data, follow_redirects=False)
        return response

    def verify_user(self, email="user@example.com"):
        self.conn.execute(
            "UPDATE users SET email_verified_at = '2026-07-12T00:00:00' WHERE email = ?",
            (email,),
        )
        self.conn.commit()

    def outbox_messages(self):
        if not self.outbox_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.outbox_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def latest_verification_token(self):
        messages = self.outbox_messages()
        if not messages:
            raise AssertionError("verification outbox is empty")
        parsed = urlparse(messages[-1]["verification_url"])
        return parse_qs(parsed.query)["token"][0]

    def logout(self, client):
        page = client.get("/pro/shop-settings")
        token = csrf_from(page.text)
        return client.post("/logout", data={"csrf_token": token}, follow_redirects=False)

    def login(self, client, email="owner@example.com", password="correct-password", next_url=""):
        page = client.get(f"/login?next={next_url}" if next_url else "/login")
        return client.post(
            "/login",
            data={
                "csrf_token": csrf_from(page.text),
                "email": email,
                "password": password,
                "next": next_url,
            },
            follow_redirects=False,
        )

    def test_signup_success_password_hash_and_new_shop(self):
        client = self.client()
        self.bootstrap_owner(client, email="owner@example.com", shop_name="Alpha Shop")
        self.logout(client)
        response = self.signup(client, email="user@example.com", shop_name="Beta Shop")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/check-email")

        user = self.conn.execute("SELECT * FROM users WHERE email = 'user@example.com'").fetchone()
        shop = self.conn.execute("SELECT * FROM shop_profile WHERE owner_user_id = ?", (user["id"],)).fetchone()

        self.assertIsNotNone(user)
        self.assertNotEqual(user["password_hash"], "correct-password")
        self.assertTrue(user["password_hash"].startswith("pbkdf2_sha256$"))
        self.assertIsNone(user["email_verified_at"])
        self.assertTrue(user["verification_token_hash"])
        self.assertTrue(user["verification_token_expires_at"])
        self.assertIsNotNone(shop)
        self.assertEqual(shop["shop_name"], "Beta Shop")

    def test_signup_captures_verification_email_in_local_outbox(self):
        client = self.client()
        self.bootstrap_owner(client, email="owner@example.com", shop_name="Alpha Shop")
        self.logout(client)
        self.signup(client, email="user@example.com", shop_name="Beta Shop")

        messages = self.outbox_messages()

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["to"], "user@example.com")
        self.assertEqual(messages[0]["subject"], "Verify your TorqueMech account")
        self.assertIn("/verify-email?token=", messages[0]["verification_url"])
        self.assertIn(messages[0]["verification_url"], messages[0]["body"])
        self.assertTrue(messages[0]["token"])

    def test_valid_verification_token_verifies_account_and_clears_fields(self):
        client = self.client()
        self.bootstrap_owner(client, email="owner@example.com", shop_name="Alpha Shop")
        self.logout(client)
        self.signup(client, email="user@example.com", shop_name="Beta Shop")
        token = self.latest_verification_token()

        response = client.get(f"/verify-email?token={token}", follow_redirects=False)
        user = self.conn.execute("SELECT * FROM users WHERE email = 'user@example.com'").fetchone()
        settings = client.get("/pro/shop-settings?notice=email_verified")

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/pro/shop-settings?notice=email_verified")
        self.assertIsNotNone(user["email_verified_at"])
        self.assertIsNone(user["verification_token_hash"])
        self.assertIsNone(user["verification_token_expires_at"])
        self.assertEqual(settings.status_code, 200)
        self.assertIn("Your email has been verified. Complete your shop profile to get started.", settings.text)

    def test_invalid_verification_token_fails(self):
        client = self.client()
        self.bootstrap_owner(client, email="owner@example.com", shop_name="Alpha Shop")
        self.logout(client)
        self.signup(client, email="user@example.com", shop_name="Beta Shop")

        response = client.get("/verify-email?token=wrong-token")
        user = self.conn.execute("SELECT * FROM users WHERE email = 'user@example.com'").fetchone()

        self.assertEqual(response.status_code, 400)
        self.assertIn("Verification link invalid", response.text)
        self.assertIsNone(user["email_verified_at"])
        self.assertTrue(user["verification_token_hash"])

    def test_expired_verification_token_fails(self):
        client = self.client()
        self.bootstrap_owner(client, email="owner@example.com", shop_name="Alpha Shop")
        self.logout(client)
        self.signup(client, email="user@example.com", shop_name="Beta Shop")
        token = self.latest_verification_token()
        self.conn.execute(
            "UPDATE users SET verification_token_expires_at = '2026-01-01T00:00:00' WHERE email = 'user@example.com'"
        )
        self.conn.commit()

        response = client.get(f"/verify-email?token={token}")
        user = self.conn.execute("SELECT * FROM users WHERE email = 'user@example.com'").fetchone()

        self.assertEqual(response.status_code, 400)
        self.assertIn("Verification link invalid", response.text)
        self.assertIsNone(user["email_verified_at"])
        self.assertTrue(user["verification_token_hash"])

    def test_reused_verification_token_fails(self):
        client = self.client()
        self.bootstrap_owner(client, email="owner@example.com", shop_name="Alpha Shop")
        self.logout(client)
        self.signup(client, email="user@example.com", shop_name="Beta Shop")
        token = self.latest_verification_token()

        first = client.get(f"/verify-email?token={token}", follow_redirects=False)
        second = client.get(f"/verify-email?token={token}")
        user = self.conn.execute("SELECT * FROM users WHERE email = 'user@example.com'").fetchone()

        self.assertEqual(first.status_code, 303)
        self.assertEqual(second.status_code, 400)
        self.assertIn("Verification link invalid", second.text)
        self.assertIsNotNone(user["email_verified_at"])
        self.assertIsNone(user["verification_token_hash"])

    def test_unverified_signup_sees_check_email_and_cannot_open_pro(self):
        client = self.client()
        self.bootstrap_owner(client, email="owner@example.com", shop_name="Alpha Shop")
        self.logout(client)
        response = self.signup(client, email="user@example.com", shop_name="Beta Shop")
        check_email = client.get("/check-email")
        protected = client.get("/pro/shop-settings", follow_redirects=False)
        logout = client.post("/logout", data={"csrf_token": csrf_from(check_email.text)}, follow_redirects=False)

        self.assertEqual(response.headers["location"], "/check-email")
        self.assertIn("Check your email", check_email.text)
        self.assertIn("must be verified before you can continue", check_email.text)
        self.assertIn("Log Out", check_email.text)
        self.assertEqual(protected.status_code, 303)
        self.assertEqual(protected.headers["location"], "/check-email")
        self.assertEqual(logout.status_code, 303)
        self.assertEqual(logout.headers["location"], "/")

    def test_token_is_never_shown_on_normal_signup(self):
        client = self.client()
        first_page = client.get("/signup")
        self.assertEqual(first_page.status_code, 200)
        self.assertIn("TorqueMech setup is not yet complete.", first_page.text)
        self.assertNotIn("Setup token", first_page.text)
        self.assertNotIn("bootstrap_token", first_page.text)

        self.bootstrap_owner(client, email="owner@example.com", shop_name="Alpha Shop")
        self.logout(client)
        normal_page = client.get("/signup")

        self.assertEqual(normal_page.status_code, 200)
        self.assertIn("Create your Pro Solo account", normal_page.text)
        self.assertNotIn("Setup token", normal_page.text)
        self.assertNotIn("bootstrap_token", normal_page.text)

    def test_first_signup_blocked_without_configured_bootstrap_token(self):
        with patch.dict("os.environ", {"TORQUEMECH_BOOTSTRAP_TOKEN": ""}):
            client = self.client()
            page = client.get("/admin/bootstrap")
            response = client.post(
                "/admin/bootstrap",
                data={
                    "csrf_token": csrf_from(page.text),
                    "first_name": "Ada",
                    "last_name": "Lovelace",
                    "email": "owner@example.com",
                    "password": "correct-password",
                    "confirm_password": "correct-password",
                    "shop_name": "Alpha Shop",
                    "terms": "1",
                },
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Initial account setup is not enabled.", response.text)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"], 0)

    def test_first_owner_bootstrap_works_only_through_admin_bootstrap(self):
        client = self.client()
        client.get("/signup")
        bootstrap_page = client.get("/admin/bootstrap")
        public_response = client.post(
            "/signup",
            data={
                "csrf_token": csrf_from(bootstrap_page.text),
                "first_name": "Ada",
                "last_name": "Lovelace",
                "email": "owner@example.com",
                "password": "correct-password",
                "confirm_password": "correct-password",
                "shop_name": "Alpha Shop",
                "terms": "1",
                "bootstrap_token": "boot-secret",
            },
            follow_redirects=False,
        )

        self.assertEqual(public_response.status_code, 400)
        self.assertIn("TorqueMech setup is not yet complete.", public_response.text)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"], 0)

        response = self.bootstrap_owner(client, bootstrap_token="wrong-secret")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Setup token is invalid.", response.text)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"], 0)

        response = self.bootstrap_owner(client)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"], 1)

    def test_admin_bootstrap_claims_existing_shop(self):
        self.conn.execute(
            """
            INSERT INTO shop_profile (id, shop_name, owner_user_id, updated_at)
            VALUES (1, 'Existing Live Shop', NULL, '2026-07-01T00:00:00')
            """
        )
        self.conn.commit()
        client = self.client()

        response = self.bootstrap_owner(client, email="owner@example.com", shop_name="Claimed Shop")

        user = self.conn.execute("SELECT * FROM users WHERE email = 'owner@example.com'").fetchone()
        shop = self.conn.execute("SELECT * FROM shop_profile WHERE id = 1").fetchone()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(shop["owner_user_id"], user["id"])
        self.assertEqual(shop["shop_name"], "Existing Live Shop")

    def test_bootstrap_route_unavailable_after_first_owner_creation(self):
        client = self.client()
        self.bootstrap_owner(client, email="owner@example.com", shop_name="Live Shop")

        get_response = client.get("/admin/bootstrap")
        post_response = client.post("/admin/bootstrap", data={}, follow_redirects=False)

        self.assertEqual(get_response.status_code, 404)
        self.assertEqual(post_response.status_code, 404)

    def test_bootstrap_token_cannot_reassign_existing_shop_after_first_user(self):
        client_one = self.client()
        self.bootstrap_owner(client_one, email="one@example.com", shop_name="Live Shop")
        first_user = self.conn.execute("SELECT * FROM users WHERE email = 'one@example.com'").fetchone()
        live_shop = self.conn.execute("SELECT * FROM shop_profile WHERE owner_user_id = ?", (first_user["id"],)).fetchone()

        client_two = self.client()
        response = self.signup(
            client_two,
            email="two@example.com",
            shop_name="Second Shop",
        )
        second_user = self.conn.execute("SELECT * FROM users WHERE email = 'two@example.com'").fetchone()
        second_shop = self.conn.execute("SELECT * FROM shop_profile WHERE owner_user_id = ?", (second_user["id"],)).fetchone()
        unchanged_live_shop = self.conn.execute("SELECT * FROM shop_profile WHERE id = ?", (live_shop["id"],)).fetchone()

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/check-email")
        self.assertEqual(unchanged_live_shop["owner_user_id"], first_user["id"])
        self.assertNotEqual(second_shop["id"], live_shop["id"])
        self.assertEqual(second_shop["shop_name"], "Second Shop")

    def test_later_signups_receive_separate_blank_shop(self):
        client_one = self.client()
        self.bootstrap_owner(client_one, email="one@example.com", shop_name="Live Shop")
        first_shop = self.conn.execute("SELECT * FROM shop_profile ORDER BY id ASC LIMIT 1").fetchone()
        self.conn.execute(
            "UPDATE shop_profile SET shop_phone = '5592223333', shop_address = '742 Cedar Ave' WHERE id = ?",
            (first_shop["id"],),
        )
        self.conn.commit()

        client_two = self.client()
        self.signup(client_two, email="two@example.com", shop_name="Second Shop")
        second_user = self.conn.execute("SELECT * FROM users WHERE email = 'two@example.com'").fetchone()
        second_shop = self.conn.execute("SELECT * FROM shop_profile WHERE owner_user_id = ?", (second_user["id"],)).fetchone()

        self.assertNotEqual(second_shop["id"], first_shop["id"])
        self.assertEqual(second_shop["shop_name"], "Second Shop")
        self.assertEqual(second_shop["shop_phone"], "")
        self.assertEqual(second_shop["shop_address"], "")

    def test_duplicate_email_rejected(self):
        client = self.client()
        self.bootstrap_owner(client)
        self.logout(client)
        response = self.signup(client, email=" OWNER@example.com ", shop_name="Second Shop")

        self.assertEqual(response.status_code, 400)
        self.assertIn("An account with this email already exists.", response.text)
        count = self.conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"]
        self.assertEqual(count, 1)

    def test_login_invalid_logout_and_pro_redirect(self):
        client = self.client()
        self.bootstrap_owner(client)
        self.logout(client)

        protected = client.get("/pro/calendar", follow_redirects=False)
        self.assertEqual(protected.status_code, 303)
        self.assertEqual(protected.headers["location"], "/login?next=%2Fpro%2Fcalendar")

        invalid = self.login(client, password="wrong-password")
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("Email or password is incorrect.", invalid.text)

        valid = self.login(client, next_url="/pro/calendar")
        self.assertEqual(valid.status_code, 303)
        self.assertEqual(valid.headers["location"], "/pro/calendar")

        logged_out = self.logout(client)
        self.assertEqual(logged_out.status_code, 303)
        self.assertEqual(logged_out.headers["location"], "/")

    def test_safe_return_url_rejects_external_redirect(self):
        client = self.client()
        self.bootstrap_owner(client)
        self.logout(client)
        response = self.login(client, next_url="https://evil.example/pro")

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/pro/dashboard")

    def test_production_session_cookie_flags_and_rotation(self):
        client = TestClient(main.app, base_url="https://torquemech.com")
        page = client.get("/signup")
        self.assertIn("TorqueMech setup is not yet complete.", page.text)
        bootstrap_page = client.get("/admin/bootstrap")
        pre_session = client.cookies.get(main.SESSION_COOKIE_NAME)
        response = client.post(
            "/admin/bootstrap",
            data={
                "csrf_token": csrf_from(bootstrap_page.text),
                "bootstrap_token": "boot-secret",
                "first_name": "Ada",
                "last_name": "Lovelace",
                "email": "owner@example.com",
                "password": "correct-password",
                "confirm_password": "correct-password",
                "shop_name": "Alpha Shop",
                "terms": "1",
            },
            follow_redirects=False,
        )
        post_session = client.cookies.get(main.SESSION_COOKIE_NAME)
        set_cookie = response.headers.get("set-cookie", "")

        self.assertEqual(response.status_code, 303)
        self.assertTrue(pre_session)
        self.assertTrue(post_session)
        self.assertNotEqual(pre_session, post_session)
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("Secure", set_cookie)
        self.assertIn("SameSite=lax", set_cookie)
        self.assertIsNone(
            self.conn.execute("SELECT session_id FROM auth_sessions WHERE session_id = ?", (pre_session,)).fetchone()
        )
        self.assertIsNotNone(
            self.conn.execute("SELECT session_id FROM auth_sessions WHERE session_id = ?", (post_session,)).fetchone()
        )

    def test_logout_invalidates_server_side_session(self):
        client = self.client()
        self.bootstrap_owner(client)
        session_id = client.cookies.get(main.SESSION_COOKIE_NAME)
        self.assertIsNotNone(
            self.conn.execute("SELECT session_id FROM auth_sessions WHERE session_id = ?", (session_id,)).fetchone()
        )

        response = self.logout(client)

        self.assertEqual(response.status_code, 303)
        self.assertIsNone(
            self.conn.execute("SELECT session_id FROM auth_sessions WHERE session_id = ?", (session_id,)).fetchone()
        )

    def test_shop_settings_and_calendar_are_isolated(self):
        client_one = self.client()
        self.bootstrap_owner(client_one, email="one@example.com", shop_name="Alpha Shop")
        client_one.post(
            "/pro/shop-settings",
            data={"shop_name": "Alpha Updated", "default_labor_rate": "125"},
            follow_redirects=False,
        )
        appointment = client_one.post(
            "/pro/calendar",
            data={
                "customer_name": "Alpha Customer",
                "customer_phone": "5551112222",
                "vehicle_label": "2010 Honda Accord",
                "service_name": "Brake Inspection",
                "requested_date": "2026-07-13",
                "requested_time": "09:00",
                "status": "Requested",
            },
            follow_redirects=False,
        )
        self.assertEqual(appointment.status_code, 303)
        appointment_id = self.conn.execute(
            "SELECT id FROM service_appointments WHERE customer_name = 'Alpha Customer'"
        ).fetchone()["id"]

        client_two = self.client()
        self.signup(client_two, email="two@example.com", shop_name="Beta Shop")
        self.verify_user("two@example.com")
        settings_two = client_two.get("/pro/shop-settings")
        calendar_two = client_two.get("/pro/calendar")
        cross_update = client_two.post(
            f"/pro/calendar/{appointment_id}/status",
            data={"status": "Confirmed"},
            follow_redirects=False,
        )

        self.assertEqual(settings_two.status_code, 200)
        self.assertIn("Beta Shop", settings_two.text)
        self.assertNotIn("Alpha Updated", settings_two.text)
        self.assertEqual(calendar_two.status_code, 200)
        self.assertNotIn("Alpha Customer", calendar_two.text)
        self.assertEqual(cross_update.status_code, 404)

    def test_public_booking_page_remains_accessible(self):
        client = self.client()
        self.bootstrap_owner(client, email="booker@example.com", shop_name="Booking Shop")
        self.logout(client)

        response = client.get("/book/booking-shop")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Schedule service", response.text)


class EmailVerificationTokenPersistenceTests(unittest.TestCase):
    def setUp(self):
        test_root = Path(main.BASE_DIR) / "tmp" / "test_email_verification_persistence"
        test_root.mkdir(parents=True, exist_ok=True)
        self.primary_db = (test_root / f"{self._testMethodName}_primary.db").resolve()
        self.fallback_db = (test_root / f"{self._testMethodName}_fallback.db").resolve()
        self.marker_path = (test_root / f"{self._testMethodName}_active_db.txt").resolve()
        self.outbox_path = (test_root / f"{self._testMethodName}_outbox.jsonl").resolve()
        for path in (self.primary_db, self.fallback_db, self.marker_path, self.outbox_path):
            path.unlink(missing_ok=True)
            self.addCleanup(lambda p=path: p.unlink(missing_ok=True))
        self.marker_path.write_text(str(self.fallback_db), encoding="utf-8")
        self.patches = [
            patch.dict(
                "os.environ",
                {
                    "PRO_ENABLED": "true",
                    "PRO_ACCESS_CODE": "",
                    "PRO_QA_KEY": "",
                    "TORQUEMECH_BOOTSTRAP_TOKEN": "boot-secret",
                    "TORQUEMECH_EMAIL_TRANSPORT": "test",
                    "TORQUEMECH_DEV_EMAIL_OUTBOX": str(self.outbox_path),
                },
            ),
            patch.object(main, "DB_PATH", str(self.primary_db)),
            patch.object(main, "LOCAL_FALLBACK_DB_PATH", str(self.fallback_db)),
            patch.object(main, "LOCAL_DB_MARKER_PATH", self.marker_path),
            patch.object(main, "USE_LOCAL_SQLITE_COMPAT", True),
            patch.object(pro_module, "DB_PATH", str(self.primary_db)),
            patch.object(pro_module, "LOCAL_FALLBACK_DB_PATH", str(self.fallback_db)),
            patch.object(pro_module, "LOCAL_DB_MARKER_PATH", self.marker_path),
            patch.object(pro_module, "USE_LOCAL_SQLITE_COMPAT", True),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def client(self):
        return TestClient(main.app, base_url="http://localhost")

    def bootstrap_owner(self, client):
        page = client.get("/admin/bootstrap")
        return client.post(
            "/admin/bootstrap",
            data={
                "csrf_token": csrf_from(page.text),
                "bootstrap_token": "boot-secret",
                "first_name": "Ada",
                "last_name": "Lovelace",
                "email": "owner@example.com",
                "password": "correct-password",
                "confirm_password": "correct-password",
                "shop_name": "Alpha Shop",
                "terms": "1",
            },
            follow_redirects=False,
        )

    def logout(self, client):
        page = client.get("/pro/shop-settings")
        return client.post("/logout", data={"csrf_token": csrf_from(page.text)}, follow_redirects=False)

    def signup(self, client):
        page = client.get("/signup")
        return client.post(
            "/signup",
            data={
                "csrf_token": csrf_from(page.text),
                "first_name": "Grace",
                "last_name": "Hopper",
                "email": "user@example.com",
                "password": "correct-password",
                "confirm_password": "correct-password",
                "shop_name": "Beta Shop",
                "terms": "1",
            },
            follow_redirects=False,
        )

    def outbox_message(self):
        messages = [
            json.loads(line)
            for line in self.outbox_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(messages), 1)
        return messages[0]

    def fallback_user(self):
        conn = sqlite3.connect(self.fallback_db)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute("SELECT * FROM users WHERE email = 'user@example.com'").fetchone()
        finally:
            conn.close()

    def test_exact_outbox_verification_url_survives_local_db_reload(self):
        client = self.client()
        self.assertEqual(self.bootstrap_owner(client).status_code, 303)
        self.logout(client)
        self.assertEqual(self.signup(client).headers["location"], "/check-email")
        message = self.outbox_message()
        verification_url = message["verification_url"]
        token = parse_qs(urlparse(verification_url).query)["token"][0]
        user_before = self.fallback_user()

        self.assertEqual(pro_module.verification_token_hash(token), user_before["verification_token_hash"])
        self.assertIsNone(user_before["email_verified_at"])
        self.assertTrue(user_before["verification_token_hash"])
        self.assertTrue(user_before["verification_token_expires_at"])

        self.marker_path.unlink(missing_ok=True)
        verify_response = self.client().get(verification_url, follow_redirects=False)
        user_after = self.fallback_user()

        self.assertEqual(verify_response.status_code, 303)
        self.assertEqual(verify_response.headers["location"], "/pro/shop-settings?notice=email_verified")
        self.assertIsNotNone(user_after["email_verified_at"])
        self.assertIsNone(user_after["verification_token_hash"])
        self.assertIsNone(user_after["verification_token_expires_at"])


if __name__ == "__main__":
    unittest.main()
