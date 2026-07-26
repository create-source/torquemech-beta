import json
import re
import sqlite3
import unittest
import html
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

import main
import db
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


def verification_urls_from_text(text: str) -> list[str]:
    return re.findall(r"https?://[^\s\"'<>]+/verify-email\?token=[^\s\"'<>]+", text)


def future_weekday(target_weekday: int, *, min_days: int = 14) -> str:
    candidate = pro_module.shop_today() + timedelta(days=min_days)
    offset = (target_weekday - candidate.weekday()) % 7
    if offset == 0:
        offset = 7
    return (candidate + timedelta(days=offset)).isoformat()


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
        pro_module.ensure_shop_subscription_schema(self.conn)
        pro_module.ensure_calendar_schema(self.conn)

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
        shop = self.conn.execute(
            """
            SELECT sp.id
            FROM shop_profile sp
            JOIN users u ON u.id = sp.owner_user_id
            WHERE u.email = ?
            LIMIT 1
            """,
            (email,),
        ).fetchone()
        if shop:
            pro_module.create_or_ensure_shop_subscription(self.conn, int(shop["id"]))
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

    def latest_reset_token(self):
        messages = [message for message in self.outbox_messages() if message.get("reset_url")]
        if not messages:
            raise AssertionError("password reset outbox is empty")
        parsed = urlparse(messages[-1]["reset_url"])
        return parse_qs(parsed.query)["token"][0]

    def latest_verification_token_for(self, email):
        messages = [message for message in self.outbox_messages() if message.get("to") == email]
        if not messages:
            raise AssertionError(f"verification outbox is empty for {email}")
        parsed = urlparse(messages[-1]["verification_url"])
        return parse_qs(parsed.query)["token"][0]

    def request_password_reset(self, client, email="owner@example.com"):
        page = client.get("/forgot-password")
        return client.post(
            "/forgot-password",
            data={"csrf_token": csrf_from(page.text), "email": email},
            follow_redirects=False,
        )

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

    def test_signup_sends_verification_email_by_smtp_transport(self):
        smtp_calls = []

        class FakeSMTP:
            def __init__(self, server, port, timeout=None):
                smtp_calls.append(("connect", server, port, timeout))

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                smtp_calls.append(("close",))

            def starttls(self):
                smtp_calls.append(("starttls",))

            def login(self, username, password):
                smtp_calls.append(("login", username, password))

            def send_message(self, message, from_addr=None, to_addrs=None):
                smtp_calls.append(("send_message", message, from_addr, to_addrs))

        with self.assertLogs("uvicorn.error", level="INFO") as captured_logs, \
            patch.dict("os.environ", {"TORQUEMECH_EMAIL_TRANSPORT": "smtp"}), \
            patch.object(main, "SMTP_SERVER", "smtp.example.test"), \
            patch.object(main, "SMTP_PORT", 2525), \
            patch.object(main, "SMTP_USER", "smtp-user"), \
            patch.object(main, "SMTP_PASS", "smtp-pass"), \
            patch.object(main, "FEEDBACK_EMAIL", "mailer@updates.torquemech.com"), \
            patch.object(main.smtplib, "SMTP", FakeSMTP):
            client = self.client()
            self.bootstrap_owner(client, email="owner@example.com", shop_name="Alpha Shop")
            self.logout(client)
            response = self.signup(client, email="user@example.com", shop_name="Beta Shop")

        send_call = next(call for call in smtp_calls if call[0] == "send_message")
        message = send_call[1]
        log_output = "\n".join(captured_logs.output)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/check-email")
        self.assertFalse(self.outbox_path.exists())
        self.assertIn(("connect", "smtp.example.test", 2525, 10), smtp_calls)
        self.assertIn(("starttls",), smtp_calls)
        self.assertIn(("login", "smtp-user", "smtp-pass"), smtp_calls)
        self.assertEqual(message["From"], "TorqueMech <no-reply@updates.torquemech.com>")
        self.assertEqual(message["Sender"], "mailer@updates.torquemech.com")
        self.assertEqual(message["To"], "user@example.com")
        self.assertEqual(message["Subject"], "Verify your TorqueMech account")
        self.assertEqual(send_call[2], "mailer@updates.torquemech.com")
        self.assertEqual(send_call[3], ["user@example.com"])
        smtp_body = "\n".join(
            part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8")
            for part in message.walk()
            if part.get_content_type() in {"text/plain", "text/html"}
        )
        self.assertIn("/verify-email?token=", smtp_body)
        self.assertNotEqual(message["From"], "smtp-user")
        self.assertIn("VERIFICATION_EMAIL_TRANSPORT_SELECTED transport=smtp", log_output)
        self.assertIn("host=smtp.example.test port=2525", log_output)
        self.assertIn("sender=mailer@updates.torquemech.com", log_output)
        self.assertIn("recipient=user@example.com", log_output)
        self.assertIn("VERIFICATION_EMAIL_DELIVERY_ENTERED transport=smtp", log_output)
        self.assertIn("EMAIL_SMTP_CONNECTING", log_output)
        self.assertIn("EMAIL_SMTP_CONNECTED", log_output)
        self.assertIn("EMAIL_SMTP_STARTTLS_START", log_output)
        self.assertIn("EMAIL_SMTP_STARTTLS_OK", log_output)
        self.assertIn("EMAIL_SMTP_AUTH_START", log_output)
        self.assertIn("EMAIL_SMTP_AUTH_OK", log_output)
        self.assertIn("EMAIL_SMTP_SEND_START", log_output)
        self.assertIn("VERIFICATION_EMAIL_SMTP_ACCEPTED", log_output)
        self.assertNotIn("smtp-pass", log_output)
        self.assertNotIn("smtp-user", log_output)
        self.assertNotIn("/verify-email?token=", log_output)

    def test_signup_sends_verification_email_by_resend_transport(self):
        resend_calls = []

        class FakeEmails:
            @staticmethod
            def send(payload):
                resend_calls.append(payload)
                return {"id": "email_resend_123"}

        class FakeResend:
            api_key = ""
            Emails = FakeEmails

        with self.assertLogs("uvicorn.error", level="INFO") as captured_logs, \
            patch.dict(
                "os.environ",
                {
                    "TORQUEMECH_EMAIL_TRANSPORT": "resend",
                    "RESEND_API_KEY": "re_secret_test_key",
                },
            ), \
            patch.object(main, "FEEDBACK_EMAIL", "TorqueMech <verify@updates.torquemech.com>"), \
            patch.object(main, "resend", FakeResend):
            client = self.client()
            self.bootstrap_owner(client, email="owner@example.com", shop_name="Alpha Shop")
            self.logout(client)
            response = self.signup(client, email="user@example.com", shop_name="Beta Shop")

        log_output = "\n".join(captured_logs.output)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/check-email")
        self.assertFalse(self.outbox_path.exists())
        self.assertEqual(FakeResend.api_key, "re_secret_test_key")
        self.assertEqual(len(resend_calls), 1)
        payload = resend_calls[0]
        self.assertEqual(payload["from"], "TorqueMech <verify@updates.torquemech.com>")
        self.assertEqual(payload["to"], ["user@example.com"])
        self.assertEqual(payload["subject"], "Verify your TorqueMech account")
        self.assertIn("/verify-email?token=", payload["html"])
        self.assertIn("/verify-email?token=", payload["text"])
        self.assertIn("VERIFICATION_EMAIL_TRANSPORT_SELECTED transport=resend", log_output)
        self.assertIn("VERIFICATION_EMAIL_DELIVERY_ENTERED transport=resend", log_output)
        self.assertIn("VERIFICATION_EMAIL_RESEND_ACCEPTED", log_output)
        self.assertIn("resend_email_id=email_resend_123", log_output)
        self.assertNotIn("re_secret_test_key", log_output)
        self.assertNotIn("/verify-email?token=", log_output)

    def test_resend_transport_missing_api_key_returns_controlled_failure(self):
        class FakeEmails:
            @staticmethod
            def send(payload):
                raise AssertionError("Resend should not be called without an API key")

        class FakeResend:
            api_key = ""
            Emails = FakeEmails

        client = self.client()
        self.bootstrap_owner(client, email="owner@example.com", shop_name="Alpha Shop")
        self.logout(client)
        self.signup(client, email="user@example.com", shop_name="Beta Shop")
        original_token = self.latest_verification_token()
        self.conn.execute(
            "UPDATE users SET verification_email_last_sent_at = '2026-01-01T00:00:00' WHERE email = 'user@example.com'"
        )
        self.conn.commit()
        page = client.get("/check-email")

        with self.assertLogs("uvicorn.error", level="INFO") as captured_logs, \
            patch.dict("os.environ", {"TORQUEMECH_EMAIL_TRANSPORT": "resend", "RESEND_API_KEY": ""}), \
            patch.object(main, "FEEDBACK_EMAIL", "verify@updates.torquemech.com"), \
            patch.object(main, "resend", FakeResend):
            response = client.post(
                "/check-email/resend",
                data={"csrf_token": csrf_from(page.text)},
                follow_redirects=False,
            )
        user = self.conn.execute("SELECT * FROM users WHERE email = 'user@example.com'").fetchone()
        log_output = "\n".join(captured_logs.output)

        self.assertEqual(response.status_code, 503)
        self.assertIn("could not send a verification email", response.text)
        self.assertEqual(main.verification_token_hash(original_token), user["verification_token_hash"])
        self.assertIn("VERIFICATION_EMAIL_RESEND_NOT_CONFIGURED missing=api_key", log_output)
        self.assertNotIn("/verify-email?token=", log_output)

    def test_resend_transport_api_failure_returns_controlled_failure(self):
        class FakeEmails:
            @staticmethod
            def send(payload):
                raise RuntimeError("resend api unavailable")

        class FakeResend:
            api_key = ""
            Emails = FakeEmails

        client = self.client()
        self.bootstrap_owner(client, email="owner@example.com", shop_name="Alpha Shop")
        self.logout(client)
        self.signup(client, email="user@example.com", shop_name="Beta Shop")
        original_token = self.latest_verification_token()
        self.conn.execute(
            "UPDATE users SET verification_email_last_sent_at = '2026-01-01T00:00:00' WHERE email = 'user@example.com'"
        )
        self.conn.commit()
        page = client.get("/check-email")

        with self.assertLogs("uvicorn.error", level="INFO") as captured_logs, \
            patch.dict(
                "os.environ",
                {
                    "TORQUEMECH_EMAIL_TRANSPORT": "resend",
                    "RESEND_API_KEY": "re_secret_test_key",
                },
            ), \
            patch.object(main, "FEEDBACK_EMAIL", "verify@updates.torquemech.com"), \
            patch.object(main, "resend", FakeResend):
            response = client.post(
                "/check-email/resend",
                data={"csrf_token": csrf_from(page.text)},
                follow_redirects=False,
            )
        user = self.conn.execute("SELECT * FROM users WHERE email = 'user@example.com'").fetchone()
        log_output = "\n".join(captured_logs.output)

        self.assertEqual(response.status_code, 503)
        self.assertIn("could not send a verification email", response.text)
        self.assertEqual(main.verification_token_hash(original_token), user["verification_token_hash"])
        self.assertIn("VERIFICATION_EMAIL_RESEND_EXCEPTION sender=verify@updates.torquemech.com recipient=user@example.com", log_output)
        self.assertIn("RuntimeError: resend api unavailable", log_output)
        self.assertNotIn("re_secret_test_key", log_output)
        self.assertNotIn("/verify-email?token=", log_output)

    def test_verification_email_contains_one_clean_clickable_url(self):
        client = self.client()
        self.bootstrap_owner(client, email="owner@example.com", shop_name="Alpha Shop")
        self.logout(client)
        padded_token = "  clean-token-123  "
        token_hash = main.verification_token_hash("clean-token-123")
        expires_at = "2099-01-01T00:00:00"

        with patch.object(main, "new_verification_token_record", return_value=(padded_token, token_hash, expires_at)):
            self.signup(client, email="user@example.com", shop_name="Beta Shop")

        message = self.outbox_messages()[0]
        body = message["body"]
        urls = verification_urls_from_text(body)
        self.assertEqual(len(urls), 1)
        parsed = urlparse(urls[0])
        token_values = parse_qs(parsed.query).get("token", [])

        self.assertEqual(urls[0], message["verification_url"])
        self.assertEqual(parsed.path, "/verify-email")
        self.assertEqual(token_values, ["clean-token-123"])
        self.assertNotIn("%20", urls[0])
        self.assertNotIn(" token=", urls[0])
        self.assertNotIn("clean-token-123+", urls[0])

    def test_smtp_delivery_exception_is_logged_without_secrets(self):
        class FailingSMTP:
            def __init__(self, server, port, timeout=None):
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                pass

            def starttls(self):
                pass

            def login(self, username, password):
                pass

            def send_message(self, message, from_addr=None, to_addrs=None):
                raise main.smtplib.SMTPException("mailbox temporarily unavailable")

        with self.assertLogs("uvicorn.error", level="INFO") as captured_logs, \
            patch.dict("os.environ", {"TORQUEMECH_EMAIL_TRANSPORT": "smtp"}), \
            patch.object(main, "SMTP_SERVER", "smtp.example.test"), \
            patch.object(main, "SMTP_PORT", 2525), \
            patch.object(main, "SMTP_USER", "smtp-user"), \
            patch.object(main, "SMTP_PASS", "smtp-pass"), \
            patch.object(main, "FEEDBACK_EMAIL", "mailer@updates.torquemech.com"), \
            patch.object(main.smtplib, "SMTP", FailingSMTP):
            client = self.client()
            self.bootstrap_owner(client, email="owner@example.com", shop_name="Alpha Shop")
            self.logout(client)
            response = self.signup(client, email="user@example.com", shop_name="Beta Shop")

        log_output = "\n".join(captured_logs.output)
        self.assertEqual(response.status_code, 303)
        self.assertIn("VERIFICATION_EMAIL_SMTP_EXCEPTION host=smtp.example.test port=2525 sender=mailer@updates.torquemech.com recipient=user@example.com", log_output)
        self.assertIn("SMTPException: mailbox temporarily unavailable", log_output)
        self.assertNotIn("smtp-pass", log_output)
        self.assertNotIn("smtp-user", log_output)
        self.assertNotIn("/verify-email?token=", log_output)

    def test_valid_verification_token_verifies_account_and_clears_fields(self):
        client = self.client()
        self.bootstrap_owner(client, email="owner@example.com", shop_name="Alpha Shop")
        self.logout(client)
        self.signup(client, email="user@example.com", shop_name="Beta Shop")
        token = self.latest_verification_token()

        response = client.get(f"/verify-email?token={token}", follow_redirects=False)
        user = self.conn.execute("SELECT * FROM users WHERE email = 'user@example.com'").fetchone()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Your email has been verified", response.text)
        self.assertIn("Your TorqueMech account is ready. Complete your shop profile to finish setting up your Pro workspace.", response.text)
        self.assertIn('href="/pro/shop-settings"', response.text)
        self.assertIn("Set Up Your Shop", response.text)
        self.assertIn("Log Out", response.text)
        self.assertIsNotNone(user["email_verified_at"])
        self.assertIsNone(user["verification_token_hash"])
        self.assertIsNone(user["verification_token_expires_at"])

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

        self.assertEqual(first.status_code, 200)
        self.assertIn("Your email has been verified", first.text)
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

    def test_resend_verification_email_generates_fresh_token_for_current_unverified_user(self):
        client = self.client()
        self.bootstrap_owner(client, email="owner@example.com", shop_name="Alpha Shop")
        self.logout(client)
        self.signup(client, email="user@example.com", shop_name="Beta Shop")
        original_token = self.latest_verification_token()
        self.conn.execute(
            "UPDATE users SET verification_email_last_sent_at = '2026-01-01T00:00:00' WHERE email = 'user@example.com'"
        )
        self.conn.commit()
        page = client.get("/check-email")

        response = client.post(
            "/check-email/resend",
            data={"csrf_token": csrf_from(page.text)},
            follow_redirects=False,
        )
        messages = self.outbox_messages()
        fresh_token = self.latest_verification_token()
        old_verify = client.get(f"/verify-email?token={original_token}")
        fresh_verify = client.get(f"/verify-email?token={fresh_token}", follow_redirects=False)

        self.assertEqual(response.status_code, 200)
        self.assertIn("A fresh verification email has been sent.", response.text)
        self.assertIn('data-cooldown-remaining="60"', response.text)
        self.assertIn("window.setInterval", response.text)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[-1]["to"], "user@example.com")
        self.assertNotEqual(original_token, fresh_token)
        self.assertEqual(old_verify.status_code, 400)
        self.assertEqual(fresh_verify.status_code, 200)
        self.assertIn("Your email has been verified", fresh_verify.text)
        self.assertIn('href="/pro/shop-settings"', fresh_verify.text)

    def test_resend_verification_email_uses_cooldown(self):
        client = self.client()
        self.bootstrap_owner(client, email="owner@example.com", shop_name="Alpha Shop")
        self.logout(client)
        self.signup(client, email="user@example.com", shop_name="Beta Shop")
        page = client.get("/check-email")

        response = client.post(
            "/check-email/resend",
            data={"csrf_token": csrf_from(page.text)},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 429)
        self.assertIn("Please wait", response.text)
        self.assertIn('data-resend-button', response.text)
        self.assertIn('data-resend-countdown', response.text)
        self.assertIn("remaining -= 1", response.text)
        self.assertIn("button.disabled = false", response.text)
        self.assertEqual(len(self.outbox_messages()), 1)

    def test_resend_verification_email_failure_does_not_replace_existing_token(self):
        client = self.client()
        self.bootstrap_owner(client, email="owner@example.com", shop_name="Alpha Shop")
        self.logout(client)
        self.signup(client, email="user@example.com", shop_name="Beta Shop")
        original_token = self.latest_verification_token()
        self.conn.execute(
            "UPDATE users SET verification_email_last_sent_at = '2026-01-01T00:00:00' WHERE email = 'user@example.com'"
        )
        self.conn.commit()
        page = client.get("/check-email")

        with patch.dict("os.environ", {"TORQUEMECH_EMAIL_TRANSPORT": "unsupported"}):
            response = client.post(
                "/check-email/resend",
                data={"csrf_token": csrf_from(page.text)},
                follow_redirects=False,
            )
        user = self.conn.execute("SELECT * FROM users WHERE email = 'user@example.com'").fetchone()

        self.assertEqual(response.status_code, 503)
        self.assertIn("could not send a verification email", response.text)
        self.assertNotIn("A fresh verification email has been sent.", response.text)
        self.assertEqual(len(self.outbox_messages()), 1)
        self.assertEqual(main.verification_token_hash(original_token), user["verification_token_hash"])

    def test_resend_verification_email_refused_by_smtp_shows_failure_not_success(self):
        class RefusingSMTP:
            def __init__(self, server, port, timeout=None):
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                pass

            def starttls(self):
                pass

            def login(self, username, password):
                pass

            def send_message(self, message, from_addr=None, to_addrs=None):
                return {"user@example.com": (550, b"mailbox unavailable")}

        client = self.client()
        self.bootstrap_owner(client, email="owner@example.com", shop_name="Alpha Shop")
        self.logout(client)
        self.signup(client, email="user@example.com", shop_name="Beta Shop")
        original_token = self.latest_verification_token()
        self.conn.execute(
            "UPDATE users SET verification_email_last_sent_at = '2026-01-01T00:00:00' WHERE email = 'user@example.com'"
        )
        self.conn.commit()
        page = client.get("/check-email")

        with self.assertLogs("uvicorn.error", level="INFO") as captured_logs, \
            patch.dict("os.environ", {"TORQUEMECH_EMAIL_TRANSPORT": "smtp"}), \
            patch.object(main, "SMTP_SERVER", "smtp.example.test"), \
            patch.object(main, "SMTP_PORT", 2525), \
            patch.object(main, "SMTP_USER", "smtp-user"), \
            patch.object(main, "SMTP_PASS", "smtp-pass"), \
            patch.object(main, "FEEDBACK_EMAIL", "mailer@updates.torquemech.com"), \
            patch.object(main.smtplib, "SMTP", RefusingSMTP):
            response = client.post(
                "/check-email/resend",
                data={"csrf_token": csrf_from(page.text)},
                follow_redirects=False,
            )
        user = self.conn.execute("SELECT * FROM users WHERE email = 'user@example.com'").fetchone()
        log_output = "\n".join(captured_logs.output)

        self.assertEqual(response.status_code, 503)
        self.assertIn("We could not send a verification email right now. Please try again.", response.text)
        self.assertNotIn("A fresh verification email has been sent.", response.text)
        self.assertIn("VERIFICATION_EMAIL_SMTP_REFUSED", log_output)
        self.assertIn("recipient=user@example.com", log_output)
        self.assertNotIn("smtp-pass", log_output)
        self.assertNotIn("/verify-email?token=", log_output)
        self.assertEqual(main.verification_token_hash(original_token), user["verification_token_hash"])

    def test_resend_verification_email_requires_current_signed_in_unverified_user(self):
        client = self.client()
        self.bootstrap_owner(client, email="owner@example.com", shop_name="Alpha Shop")
        self.logout(client)

        response = client.post("/check-email/resend", data={"csrf_token": "anything"}, follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login")

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

    def test_signup_password_fields_have_accessible_show_hide_controls(self):
        client = self.client()
        self.bootstrap_owner(client, email="owner@example.com", shop_name="Alpha Shop")
        self.logout(client)

        page = client.get("/signup")

        self.assertIn('id="password" name="password" type="password"', page.text)
        self.assertIn('id="confirm_password" name="confirm_password" type="password"', page.text)
        self.assertIn('aria-label="Show password"', page.text)
        self.assertIn('aria-pressed="false"', page.text)
        self.assertIn('aria-controls="password"', page.text)
        self.assertIn('aria-label="Show confirm password"', page.text)
        self.assertIn('aria-controls="confirm_password"', page.text)
        self.assertIn('data-password-toggle="password"', page.text)
        self.assertIn('data-password-toggle="confirm_password"', page.text)
        self.assertIn('class="tm-eye"', page.text)
        self.assertIn('class="tm-eye-off"', page.text)
        self.assertIn('viewBox="0 0 24 24"', page.text)
        self.assertNotIn('>Show</button>', page.text)
        self.assertNotIn('>Hide</button>', page.text)

    def test_signup_password_validation_targets_password_and_confirm_separately(self):
        client = self.client()
        self.bootstrap_owner(client, email="owner@example.com", shop_name="Alpha Shop")
        self.logout(client)
        page = client.get("/signup")

        mismatch = client.post(
            "/signup",
            data={
                "csrf_token": csrf_from(page.text),
                "first_name": "Ada",
                "last_name": "Lovelace",
                "email": "user@example.com",
                "password": "Password1234",
                "confirm_password": "Different1234",
                "shop_name": "Beta Shop",
                "terms": "1",
            },
            follow_redirects=False,
        )

        self.assertEqual(mismatch.status_code, 400)
        self.assertIn('id="password_error" data-password-error hidden></span>', mismatch.text)
        self.assertIn("Passwords must match.", mismatch.text)

        valid_page = client.get("/signup")
        valid = client.post(
            "/signup",
            data={
                "csrf_token": csrf_from(valid_page.text),
                "first_name": "Ada",
                "last_name": "Lovelace",
                "email": "user@example.com",
                "password": "Password1234",
                "confirm_password": "Password1234",
                "shop_name": "Beta Shop",
                "terms": "1",
            },
            follow_redirects=False,
        )

        self.assertEqual(valid.status_code, 303)
        self.assertEqual(valid.headers["location"], "/check-email")

    def test_signup_page_clears_password_length_message_client_side(self):
        client = self.client()
        self.bootstrap_owner(client, email="owner@example.com", shop_name="Alpha Shop")
        self.logout(client)

        page = client.get("/signup")

        self.assertIn('id="password_error"', page.text)
        self.assertIn('data-password-error', page.text)
        self.assertIn('passwordInput?.addEventListener("input", validateSignupPasswords)', page.text)
        self.assertIn('password.length < 8 ? "Password must be at least 8 characters." : ""', page.text)
        self.assertIn('setFieldError(passwordError', page.text)
        self.assertIn('setFieldError(\n      confirmPasswordError', page.text)

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

    def test_account_settings_unauthenticated_access_is_blocked(self):
        client = self.client()

        response = client.get("/account/settings", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login?next=%2Faccount%2Fsettings")

    def test_account_settings_page_shows_email_and_nav_link(self):
        client = self.client()
        self.bootstrap_owner(client)

        response = client.get("/account/settings")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Account Settings", response.text)
        self.assertIn("owner@example.com", response.text)
        self.assertIn('href="/account/settings">Account Settings</a>', response.text)
        self.assertIn('data-password-toggle="current_password"', response.text)
        self.assertIn('data-password-toggle="new_password"', response.text)
        self.assertIn('data-password-toggle="confirm_new_password"', response.text)

    def test_account_settings_account_created_date_displays(self):
        client = self.client()
        self.bootstrap_owner(client)
        self.conn.execute(
            "UPDATE users SET created_at = '2026-07-13T09:30:00' WHERE email = 'owner@example.com'"
        )
        self.conn.commit()

        response = client.get("/account/settings")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Account created", response.text)
        self.assertIn("July 13, 2026", response.text)

    def test_account_settings_missing_account_created_date_displays_not_available(self):
        client = self.client()
        self.bootstrap_owner(client)
        self.conn.execute("UPDATE users SET created_at = '' WHERE email = 'owner@example.com'")
        self.conn.commit()

        response = client.get("/account/settings")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Account created", response.text)
        self.assertIn("Not available", response.text)

    def test_account_settings_sign_in_email_is_not_duplicated(self):
        client = self.client()
        self.bootstrap_owner(client)

        response = client.get("/account/settings")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text.count("owner@example.com"), 1)

    def test_account_settings_profile_values_load_correctly(self):
        client = self.client()
        self.bootstrap_owner(client)
        self.conn.execute(
            "UPDATE users SET first_name = 'Grace', last_name = 'Hopper', phone = '5552223333' WHERE email = 'owner@example.com'"
        )
        self.conn.commit()

        response = client.get("/account/settings")

        self.assertEqual(response.status_code, 200)
        self.assertIn('value="Grace Hopper"', response.text)
        self.assertIn('value="(555) 222-3333"', response.text)
        self.assertIn("Profile Information", response.text)
        self.assertIn("Security &amp; Login", response.text)

    def test_account_settings_profile_save_updates_current_user(self):
        client = self.client()
        self.bootstrap_owner(client)
        page = client.get("/account/settings")

        response = client.post(
            "/account/settings",
            data={
                "csrf_token": csrf_from(page.text),
                "action": "save_profile",
                "full_name": "  Grace Brewster Hopper  ",
                "phone": "5552223333",
            },
            follow_redirects=False,
        )
        user = self.conn.execute("SELECT * FROM users WHERE email = 'owner@example.com'").fetchone()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Your profile has been updated.", response.text)
        self.assertEqual(user["first_name"], "Grace")
        self.assertEqual(user["last_name"], "Brewster Hopper")
        self.assertEqual(user["phone"], "(555) 222-3333")

    def test_account_settings_phone_validation_preserves_entered_value(self):
        client = self.client()
        self.bootstrap_owner(client)
        page = client.get("/account/settings")

        response = client.post(
            "/account/settings",
            data={
                "csrf_token": csrf_from(page.text),
                "action": "save_profile",
                "full_name": "Grace Hopper",
                "phone": "555-12",
            },
            follow_redirects=False,
        )
        user = self.conn.execute("SELECT * FROM users WHERE email = 'owner@example.com'").fetchone()

        self.assertEqual(response.status_code, 400)
        self.assertIn("Enter a 10-digit US phone number.", response.text)
        self.assertIn('value="555-12"', response.text)
        self.assertNotEqual(user["phone"], "555-12")

    def test_account_settings_profile_save_ignores_submitted_user_identity(self):
        client_one = self.client()
        self.bootstrap_owner(client_one, email="one@example.com", shop_name="Alpha Shop")
        self.logout(client_one)
        client_two = self.client()
        self.signup(client_two, email="two@example.com", shop_name="Beta Shop")
        self.verify_user("two@example.com")
        user_one = self.conn.execute("SELECT * FROM users WHERE email = 'one@example.com'").fetchone()
        page = client_two.get("/account/settings")

        response = client_two.post(
            "/account/settings",
            data={
                "csrf_token": csrf_from(page.text),
                "action": "save_profile",
                "user_id": str(user_one["id"]),
                "email": "one@example.com",
                "full_name": "Beta Owner",
                "phone": "5553334444",
            },
            follow_redirects=False,
        )
        first_user = self.conn.execute("SELECT * FROM users WHERE email = 'one@example.com'").fetchone()
        second_user = self.conn.execute("SELECT * FROM users WHERE email = 'two@example.com'").fetchone()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(first_user["first_name"], "Ada")
        self.assertEqual(first_user["last_name"], "Lovelace")
        self.assertEqual(first_user["phone"], None)
        self.assertEqual(second_user["first_name"], "Beta")
        self.assertEqual(second_user["last_name"], "Owner")
        self.assertEqual(second_user["phone"], "(555) 333-4444")

    def test_account_settings_verified_email_displays_verified(self):
        client = self.client()
        self.bootstrap_owner(client)

        response = client.get("/account/settings")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Verified", response.text)
        self.assertNotIn("Resend Verification Email", response.text)

    def test_account_settings_unverified_email_displays_not_verified(self):
        client = self.client()
        self.bootstrap_owner(client, email="owner@example.com", shop_name="Alpha Shop")
        self.logout(client)
        self.signup(client, email="user@example.com", shop_name="Beta Shop")

        response = client.get("/account/settings")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Not verified", response.text)
        self.assertIn("Resend Verification Email", response.text)

    def test_account_settings_resend_verification_creates_new_request(self):
        client = self.client()
        self.bootstrap_owner(client, email="owner@example.com", shop_name="Alpha Shop")
        self.logout(client)
        self.signup(client, email="user@example.com", shop_name="Beta Shop")
        original_token = self.latest_verification_token()
        self.conn.execute(
            "UPDATE users SET verification_email_last_sent_at = '2026-01-01T00:00:00' WHERE email = 'user@example.com'"
        )
        self.conn.commit()
        page = client.get("/account/settings")

        response = client.post(
            "/account/settings/resend-verification",
            data={"csrf_token": csrf_from(page.text)},
            follow_redirects=False,
        )
        messages = self.outbox_messages()
        fresh_token = self.latest_verification_token()
        user = self.conn.execute("SELECT * FROM users WHERE email = 'user@example.com'").fetchone()

        self.assertEqual(response.status_code, 200)
        self.assertIn("A fresh verification email has been sent.", response.text)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[-1]["to"], "user@example.com")
        self.assertNotEqual(original_token, fresh_token)
        self.assertEqual(main.verification_token_hash(fresh_token), user["verification_token_hash"])
        self.assertIsNone(user["email_verified_at"])

    def test_change_password_wrong_current_password_is_rejected(self):
        client = self.client()
        self.bootstrap_owner(client)
        page = client.get("/account/settings")

        response = client.post(
            "/account/settings",
            data={
                "csrf_token": csrf_from(page.text),
                "current_password": "wrong-password",
                "new_password": "new-correct-password",
                "confirm_new_password": "new-correct-password",
            },
            follow_redirects=False,
        )
        unchanged_login = self.login(client, password="correct-password")

        self.assertEqual(response.status_code, 400)
        self.assertIn("Current password is incorrect.", response.text)
        self.assertEqual(unchanged_login.status_code, 303)

    def test_change_password_confirmation_mismatch_is_rejected(self):
        client = self.client()
        self.bootstrap_owner(client)
        page = client.get("/account/settings")

        response = client.post(
            "/account/settings",
            data={
                "csrf_token": csrf_from(page.text),
                "current_password": "correct-password",
                "new_password": "new-correct-password",
                "confirm_new_password": "different-password",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Passwords must match.", response.text)

    def test_change_password_same_password_is_rejected(self):
        client = self.client()
        self.bootstrap_owner(client)
        page = client.get("/account/settings")

        response = client.post(
            "/account/settings",
            data={
                "csrf_token": csrf_from(page.text),
                "current_password": "correct-password",
                "new_password": "correct-password",
                "confirm_new_password": "correct-password",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("New password must be different from your current password.", response.text)

    def test_change_password_success_keeps_user_signed_in(self):
        client = self.client()
        self.bootstrap_owner(client)
        page = client.get("/account/settings")

        response = client.post(
            "/account/settings",
            data={
                "csrf_token": csrf_from(page.text),
                "current_password": "correct-password",
                "new_password": "new-correct-password",
                "confirm_new_password": "new-correct-password",
            },
            follow_redirects=False,
        )
        dashboard = client.get("/pro/dashboard")
        user = self.conn.execute("SELECT * FROM users WHERE email = 'owner@example.com'").fetchone()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Your password has been changed.", response.text)
        self.assertEqual(dashboard.status_code, 200)
        self.assertNotEqual(user["password_hash"], "new-correct-password")
        self.assertTrue(pro_module.verify_password("new-correct-password", user["password_hash"]))

    def test_changed_password_rejects_old_password_and_accepts_new_password(self):
        client = self.client()
        self.bootstrap_owner(client)
        page = client.get("/account/settings")
        client.post(
            "/account/settings",
            data={
                "csrf_token": csrf_from(page.text),
                "current_password": "correct-password",
                "new_password": "new-correct-password",
                "confirm_new_password": "new-correct-password",
            },
            follow_redirects=False,
        )
        self.logout(client)

        old_login = self.login(client, password="correct-password")
        new_login = self.login(client, password="new-correct-password")

        self.assertEqual(old_login.status_code, 400)
        self.assertIn("Email or password is incorrect.", old_login.text)
        self.assertEqual(new_login.status_code, 303)
        self.assertEqual(new_login.headers["location"], "/pro/dashboard")

    def test_change_email_wrong_current_password_is_rejected(self):
        client = self.client()
        self.bootstrap_owner(client)
        page = client.get("/account/settings")

        response = client.post(
            "/account/settings",
            data={
                "csrf_token": csrf_from(page.text),
                "action": "change_email",
                "email_current_password": "wrong-password",
                "new_email": "new-owner@example.com",
                "confirm_new_email": "new-owner@example.com",
            },
            follow_redirects=False,
        )
        user = self.conn.execute("SELECT * FROM users WHERE email = 'owner@example.com'").fetchone()

        self.assertEqual(response.status_code, 400)
        self.assertIn("Current password is incorrect.", response.text)
        self.assertIn('value="new-owner@example.com"', response.text)
        self.assertIsNone(user["pending_email"])

    def test_change_email_mismatch_current_and_duplicate_are_rejected(self):
        owner = self.client()
        self.bootstrap_owner(owner)
        other = self.client()
        self.signup(other, email="used@example.com", shop_name="Beta Shop")
        page = owner.get("/account/settings")

        mismatch = owner.post(
            "/account/settings",
            data={
                "csrf_token": csrf_from(page.text),
                "action": "change_email",
                "email_current_password": "correct-password",
                "new_email": "new-owner@example.com",
                "confirm_new_email": "different@example.com",
            },
            follow_redirects=False,
        )
        current = owner.post(
            "/account/settings",
            data={
                "csrf_token": csrf_from(page.text),
                "action": "change_email",
                "email_current_password": "correct-password",
                "new_email": "owner@example.com",
                "confirm_new_email": "owner@example.com",
            },
            follow_redirects=False,
        )
        duplicate = owner.post(
            "/account/settings",
            data={
                "csrf_token": csrf_from(page.text),
                "action": "change_email",
                "email_current_password": "correct-password",
                "new_email": "used@example.com",
                "confirm_new_email": "used@example.com",
            },
            follow_redirects=False,
        )

        self.assertEqual(mismatch.status_code, 400)
        self.assertIn("Email addresses must match.", mismatch.text)
        self.assertEqual(current.status_code, 400)
        self.assertIn("Enter a different email address.", current.text)
        self.assertEqual(duplicate.status_code, 400)
        self.assertIn(main.EMAIL_CHANGE_DUPLICATE_MESSAGE, html.unescape(duplicate.text))

    def test_change_email_remains_unchanged_until_correct_verification_then_login_switches(self):
        client = self.client()
        self.bootstrap_owner(client)
        page = client.get("/account/settings")

        response = client.post(
            "/account/settings",
            data={
                "csrf_token": csrf_from(page.text),
                "action": "change_email",
                "email_current_password": "correct-password",
                "new_email": "new-owner@example.com",
                "confirm_new_email": "new-owner@example.com",
            },
            follow_redirects=False,
        )
        before = self.conn.execute("SELECT * FROM users WHERE email = 'owner@example.com'").fetchone()
        token = self.latest_verification_token_for("new-owner@example.com")
        verify = client.get(f"/verify-email?token={token}", follow_redirects=False)
        after = self.conn.execute("SELECT * FROM users WHERE email = 'new-owner@example.com'").fetchone()
        self.logout(client)
        old_login = self.login(client, email="owner@example.com", password="correct-password")
        new_login = self.login(client, email="new-owner@example.com", password="correct-password")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Check your new email address to complete the change.", response.text)
        self.assertEqual(before["email"], "owner@example.com")
        self.assertEqual(before["pending_email"], "new-owner@example.com")
        self.assertEqual(verify.status_code, 200)
        self.assertIn("✓ Email Address Updated", verify.text)
        self.assertIn("Your sign-in email has been successfully changed.", verify.text)
        self.assertIn("New sign-in email:", verify.text)
        self.assertIn("new-owner@example.com", verify.text)
        self.assertIn("Continue to Dashboard", verify.text)
        self.assertIn("Sign In", verify.text)
        self.assertIsNotNone(after["email_verified_at"])
        self.assertIsNone(after["pending_email"])
        self.assertIsNone(after["pending_email_token_hash"])
        self.assertEqual(old_login.status_code, 400)
        self.assertEqual(new_login.status_code, 303)

    def test_change_email_second_verification_click_shows_already_used(self):
        client = self.client()
        self.bootstrap_owner(client)
        page = client.get("/account/settings")
        client.post(
            "/account/settings",
            data={
                "csrf_token": csrf_from(page.text),
                "action": "change_email",
                "email_current_password": "correct-password",
                "new_email": "new-owner@example.com",
                "confirm_new_email": "new-owner@example.com",
            },
            follow_redirects=False,
        )
        token = self.latest_verification_token_for("new-owner@example.com")

        first_click = client.get(f"/verify-email?token={token}", follow_redirects=False)
        second_click = client.get(f"/verify-email?token={token}", follow_redirects=False)

        self.assertEqual(first_click.status_code, 200)
        self.assertIn("✓ Email Address Updated", first_click.text)
        self.assertEqual(second_click.status_code, 200)
        self.assertIn("This verification link has already been used. Your email address has already been updated.", second_click.text)
        self.assertIn("Go to Account Settings", second_click.text)

    def test_change_email_expired_or_invalid_token_is_rejected(self):
        client = self.client()
        self.bootstrap_owner(client)
        page = client.get("/account/settings")
        client.post(
            "/account/settings",
            data={
                "csrf_token": csrf_from(page.text),
                "action": "change_email",
                "email_current_password": "correct-password",
                "new_email": "new-owner@example.com",
                "confirm_new_email": "new-owner@example.com",
            },
            follow_redirects=False,
        )
        token = self.latest_verification_token_for("new-owner@example.com")
        self.conn.execute("UPDATE users SET pending_email_token_expires_at = '2026-01-01T00:00:00'")
        self.conn.commit()

        expired = client.get(f"/verify-email?token={token}", follow_redirects=False)
        invalid = client.get("/verify-email?token=not-a-real-token", follow_redirects=False)
        user = self.conn.execute("SELECT * FROM users WHERE email = 'owner@example.com'").fetchone()

        self.assertEqual(expired.status_code, 400)
        self.assertIn("This verification link has expired. Return to Account Settings and request a new verification email.", expired.text)
        self.assertIn("Go to Account Settings", expired.text)
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("Verification link invalid", invalid.text)
        self.assertIn("This verification link is invalid or malformed.", invalid.text)
        self.assertEqual(user["pending_email"], "new-owner@example.com")

    def test_pending_email_change_can_be_resent_and_canceled(self):
        client = self.client()
        self.bootstrap_owner(client)
        page = client.get("/account/settings")
        client.post(
            "/account/settings",
            data={
                "csrf_token": csrf_from(page.text),
                "action": "change_email",
                "email_current_password": "correct-password",
                "new_email": "new-owner@example.com",
                "confirm_new_email": "new-owner@example.com",
            },
            follow_redirects=False,
        )
        first_token = self.latest_verification_token_for("new-owner@example.com")
        page = client.get("/account/settings")

        resend = client.post(
            "/account/settings",
            data={"csrf_token": csrf_from(page.text), "action": "resend_email_change"},
            follow_redirects=False,
        )
        second_token = self.latest_verification_token_for("new-owner@example.com")
        cancel_page = client.get("/account/settings")
        cancel = client.post(
            "/account/settings",
            data={"csrf_token": csrf_from(cancel_page.text), "action": "cancel_email_change"},
            follow_redirects=False,
        )
        user = self.conn.execute("SELECT * FROM users WHERE email = 'owner@example.com'").fetchone()

        self.assertEqual(resend.status_code, 200)
        self.assertIn("A fresh change-verification email has been sent.", resend.text)
        self.assertNotEqual(first_token, second_token)
        self.assertEqual(cancel.status_code, 200)
        self.assertIn("Pending email change canceled.", cancel.text)
        self.assertIsNone(user["pending_email"])
        self.assertEqual(user["email"], "owner@example.com")

    def test_password_last_changed_updates_after_signed_in_password_change(self):
        client = self.client()
        self.bootstrap_owner(client)
        page = client.get("/account/settings")

        response = client.post(
            "/account/settings",
            data={
                "csrf_token": csrf_from(page.text),
                "current_password": "correct-password",
                "new_password": "new-correct-password",
                "confirm_new_password": "new-correct-password",
            },
            follow_redirects=False,
        )
        user = self.conn.execute("SELECT * FROM users WHERE email = 'owner@example.com'").fetchone()

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(user["password_changed_at"])
        self.assertNotIn("Not available", response.text)

    def test_password_last_changed_updates_after_forgot_password_reset(self):
        client = self.client()
        self.bootstrap_owner(client)
        self.logout(client)
        self.request_password_reset(client)
        token = self.latest_reset_token()
        page = client.get(f"/reset-password?token={token}")

        response = client.post(
            "/reset-password",
            data={
                "csrf_token": csrf_from(page.text),
                "token": token,
                "password": "new-correct-password",
                "confirm_password": "new-correct-password",
            },
            follow_redirects=False,
        )
        user = self.conn.execute("SELECT * FROM users WHERE email = 'owner@example.com'").fetchone()

        self.assertEqual(response.status_code, 303)
        self.assertIsNotNone(user["password_changed_at"])

    def test_sign_out_all_devices_invalidates_older_sessions(self):
        first = self.client()
        self.bootstrap_owner(first)
        second = self.client()
        self.login(second, email="owner@example.com", password="correct-password")
        page = first.get("/account/settings")

        response = first.post(
            "/account/settings",
            data={
                "csrf_token": csrf_from(page.text),
                "action": "sign_out_all",
                "signout_current_password": "correct-password",
            },
            follow_redirects=False,
        )
        login_page = first.get(response.headers["location"])
        old_session_dashboard = second.get("/pro/dashboard", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login?signed_out_all=1")
        self.assertIn("You have been signed out of all devices.", login_page.text)
        self.assertEqual(old_session_dashboard.status_code, 303)
        self.assertIn("/login", old_session_dashboard.headers["location"])

    def test_email_change_preserves_cross_account_shop_isolation(self):
        first = self.client()
        self.bootstrap_owner(first, email="one@example.com", shop_name="Alpha Shop")
        second = self.client()
        self.signup(second, email="two@example.com", shop_name="Beta Shop")
        self.verify_user("two@example.com")
        page = first.get("/account/settings")
        first.post(
            "/account/settings",
            data={
                "csrf_token": csrf_from(page.text),
                "action": "change_email",
                "email_current_password": "correct-password",
                "new_email": "one-new@example.com",
                "confirm_new_email": "one-new@example.com",
            },
            follow_redirects=False,
        )
        token = self.latest_verification_token_for("one-new@example.com")
        first.get(f"/verify-email?token={token}")

        first_settings = first.get("/pro/shop-settings")
        second_settings = second.get("/pro/shop-settings")

        self.assertEqual(first_settings.status_code, 200)
        self.assertIn("Alpha Shop", first_settings.text)
        self.assertNotIn("Beta Shop", first_settings.text)
        self.assertEqual(second_settings.status_code, 200)
        self.assertIn("Beta Shop", second_settings.text)
        self.assertNotIn("Alpha Shop", second_settings.text)

    def test_forgot_password_page_loads(self):
        client = self.client()

        response = client.get("/forgot-password")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Reset your password", response.text)
        self.assertIn("Send reset link", response.text)
        self.assertIn("Back to sign in", response.text)

    def test_known_email_password_reset_request_shows_generic_confirmation(self):
        client = self.client()
        self.bootstrap_owner(client)
        self.logout(client)

        response = self.request_password_reset(client, "owner@example.com")
        token = self.latest_reset_token()
        user = self.conn.execute("SELECT * FROM users WHERE email = 'owner@example.com'").fetchone()
        reset_row = self.conn.execute("SELECT * FROM password_reset_tokens WHERE user_id = ?", (user["id"],)).fetchone()

        self.assertEqual(response.status_code, 200)
        self.assertIn("If an account exists for this email, we’ve sent password reset instructions.", response.text)
        self.assertEqual(main.password_reset_token_hash(token), reset_row["token_hash"])
        self.assertNotIn(token, reset_row["token_hash"])

    def test_unknown_email_password_reset_request_shows_same_generic_confirmation(self):
        client = self.client()
        self.bootstrap_owner(client)
        self.logout(client)

        known = self.request_password_reset(client, "owner@example.com")
        unknown = self.request_password_reset(client, "missing@example.com")

        self.assertEqual(known.status_code, 200)
        self.assertEqual(unknown.status_code, 200)
        self.assertIn("If an account exists for this email, we’ve sent password reset instructions.", known.text)
        self.assertIn("If an account exists for this email, we’ve sent password reset instructions.", unknown.text)

    def test_valid_password_reset_token_opens_form(self):
        client = self.client()
        self.bootstrap_owner(client)
        self.logout(client)
        self.request_password_reset(client)
        token = self.latest_reset_token()

        response = client.get(f"/reset-password?token={token}")

        self.assertEqual(response.status_code, 200)
        self.assertIn("New password", response.text)
        self.assertIn("Confirm new password", response.text)
        self.assertIn("Reset password", response.text)
        self.assertIn('data-password-toggle="password"', response.text)
        self.assertIn('data-password-toggle="confirm_password"', response.text)

    def test_expired_password_reset_token_is_rejected(self):
        client = self.client()
        self.bootstrap_owner(client)
        self.logout(client)
        self.request_password_reset(client)
        token = self.latest_reset_token()
        self.conn.execute("UPDATE password_reset_tokens SET expires_at = '2026-01-01T00:00:00'")
        self.conn.commit()

        response = client.get(f"/reset-password?token={token}")

        self.assertEqual(response.status_code, 400)
        self.assertIn("Reset link invalid", response.text)
        self.assertIn("Request another reset email", response.text)

    def test_invalid_password_reset_token_is_rejected(self):
        client = self.client()
        self.bootstrap_owner(client)
        self.logout(client)

        response = client.get("/reset-password?token=wrong-token")

        self.assertEqual(response.status_code, 400)
        self.assertIn("Reset link invalid", response.text)

    def test_password_reset_mismatch_is_rejected(self):
        client = self.client()
        self.bootstrap_owner(client)
        self.logout(client)
        self.request_password_reset(client)
        token = self.latest_reset_token()
        page = client.get(f"/reset-password?token={token}")

        response = client.post(
            "/reset-password",
            data={
                "csrf_token": csrf_from(page.text),
                "token": token,
                "password": "new-password",
                "confirm_password": "different-password",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Passwords must match.", response.text)

    def test_successful_password_reset_allows_new_password_and_rejects_old_password(self):
        client = self.client()
        self.bootstrap_owner(client)
        self.logout(client)
        self.request_password_reset(client)
        token = self.latest_reset_token()
        page = client.get(f"/reset-password?token={token}")

        response = client.post(
            "/reset-password",
            data={
                "csrf_token": csrf_from(page.text),
                "token": token,
                "password": "new-correct-password",
                "confirm_password": "new-correct-password",
            },
            follow_redirects=False,
        )
        success_page = client.get(response.headers["location"])
        old_login = self.login(client, password="correct-password")
        new_login = self.login(client, password="new-correct-password")

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login?reset=success")
        self.assertIn("Your password has been reset. You can now sign in.", success_page.text)
        self.assertEqual(old_login.status_code, 400)
        self.assertIn("Email or password is incorrect.", old_login.text)
        self.assertEqual(new_login.status_code, 303)
        self.assertEqual(new_login.headers["location"], "/pro/dashboard")

    def test_used_password_reset_token_cannot_be_reused(self):
        client = self.client()
        self.bootstrap_owner(client)
        self.logout(client)
        self.request_password_reset(client)
        token = self.latest_reset_token()
        page = client.get(f"/reset-password?token={token}")
        first = client.post(
            "/reset-password",
            data={
                "csrf_token": csrf_from(page.text),
                "token": token,
                "password": "new-correct-password",
                "confirm_password": "new-correct-password",
            },
            follow_redirects=False,
        )

        second = client.get(f"/reset-password?token={token}")

        self.assertEqual(first.status_code, 303)
        self.assertEqual(second.status_code, 400)
        self.assertIn("Reset link invalid", second.text)

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
        requested_date = future_weekday(0)
        client_one.post(
            "/pro/shop-settings",
            data={"shop_name": "Alpha Updated", "default_labor_rate": "125"},
            follow_redirects=False,
        )
        calendar_one_page = client_one.get("/pro/calendar")
        appointment = client_one.post(
            "/pro/calendar",
            data={
                "csrf_token": csrf_from(calendar_one_page.text),
                "customer_name": "Alpha Customer",
                "customer_phone": "5551112222",
                "vehicle_label": "2010 Honda Accord",
                "service_name": "Brake Inspection",
                "requested_date": requested_date,
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
            data={"csrf_token": csrf_from(calendar_two.text), "status": "Confirmed"},
            follow_redirects=False,
        )

        self.assertEqual(settings_two.status_code, 200)
        self.assertIn("Beta Shop", settings_two.text)
        self.assertNotIn("Alpha Updated", settings_two.text)
        self.assertEqual(calendar_two.status_code, 200)
        self.assertNotIn("Alpha Customer", calendar_two.text)
        self.assertEqual(cross_update.status_code, 404)

    def test_same_shop_appointment_access_and_csrf_protected_status_update(self):
        client = self.client()
        self.bootstrap_owner(client, email="alpha-calendar@example.com", shop_name="Alpha Shop")
        shop_id = self.shop_id_for_email("alpha-calendar@example.com")
        appointment_id = self.seed_service_appointment_for_shop(
            shop_id,
            customer_name="Alpha Calendar Customer",
            status="Requested",
        )

        page = client.get("/pro/calendar")
        missing_csrf = client.post(
            f"/pro/calendar/{appointment_id}/status",
            data={"status": "Confirmed"},
            follow_redirects=False,
        )
        invalid_csrf = client.post(
            f"/pro/calendar/{appointment_id}/status",
            data={"csrf_token": "bad-token", "status": "Confirmed"},
            follow_redirects=False,
        )
        valid = client.post(
            f"/pro/calendar/{appointment_id}/status",
            data={"csrf_token": csrf_from(page.text), "status": "Confirmed"},
            follow_redirects=False,
        )
        row = self.conn.execute(
            "SELECT status FROM service_appointments WHERE id = ?",
            (appointment_id,),
        ).fetchone()

        self.assertEqual(page.status_code, 200)
        self.assertIn("Alpha Calendar Customer", page.text)
        self.assertEqual(missing_csrf.status_code, 403)
        self.assertEqual(invalid_csrf.status_code, 403)
        self.assertEqual(valid.status_code, 303)
        self.assertEqual(valid.headers["location"], "/pro/calendar?notice=confirmed_email_missing")
        self.assertEqual(row["status"], "Confirmed")

    def test_same_shop_appointment_confirmation_email_succeeds(self):
        client = self.client()
        self.bootstrap_owner(client, email="appt-email@example.com", shop_name="Alpha Shop")
        shop_id = self.shop_id_for_email("appt-email@example.com")
        self.conn.execute(
            """
            UPDATE shop_profile
            SET shop_email = 'Service@Alpha.Example',
                shop_phone = '5551234567',
                shop_address = '742 Evergreen Terrace',
                appointment_confirmation_template = 'Hi {customer_name}\n\n{shop_name} confirmed {service} for {vehicle} on {appointment_date} at {appointment_time}. Visit {shop_address}. Call {shop_phone}.'
            WHERE id = ?
            """,
            (shop_id,),
        )
        self.conn.commit()
        appointment_id = self.seed_service_appointment_for_shop(
            shop_id,
            customer_name="Alpha Appointment",
            customer_email="Alpha.Customer@Example.com",
            status="Requested",
        )
        page = client.get("/pro/calendar")
        sent_messages = []

        with patch.object(
            pro_module.email_service,
            "send_email",
            side_effect=lambda message, config=None, **kwargs: sent_messages.append(message) or pro_module.email_service.EmailSendResult(success=True, transport="test"),
        ) as send_email:
            response = client.post(
                f"/pro/calendar/{appointment_id}/status",
                data={"csrf_token": csrf_from(page.text), "status": "Confirmed"},
                follow_redirects=False,
            )
        row = self.conn.execute(
            "SELECT status FROM service_appointments WHERE id = ?",
            (appointment_id,),
        ).fetchone()

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/pro/calendar?notice=confirmed_email_sent")
        self.assertEqual(row["status"], "Confirmed")
        self.assertEqual(send_email.call_count, 1)
        self.assertEqual(len(sent_messages), 1)
        message = sent_messages[0]
        self.assertEqual(message.recipients, ["alpha.customer@example.com"])
        self.assertIn("Appointment Confirmed for", message.subject)
        self.assertIn("Alpha Shop", message.subject)
        self.assertIn("Hi Alpha Appointment", message.text_body)
        self.assertIn("Alpha Shop confirmed Brake Inspection", message.text_body)
        self.assertIn("2010 Honda Accord", message.text_body)
        self.assertIn("742 Evergreen Terrace", message.text_body)
        self.assertIn("(555) 123-4567", message.text_body)
        self.assertIn("Alpha Shop confirmed Brake Inspection", message.html_body)
        self.assertEqual(message.reply_to, "service@alpha.example")

    def test_appointment_confirmation_retry_uses_linked_customer_email_and_ignores_invalid_reply_to(self):
        client = self.client()
        self.bootstrap_owner(client, email="appt-retry@example.com", shop_name="Alpha Shop")
        shop_id = self.shop_id_for_email("appt-retry@example.com")
        customer_id, vehicle_id = self.seed_customer_vehicle_for_shop(shop_id, first_name="Linked", vehicle_make="Honda")
        self.conn.execute("UPDATE customers SET email = 'linked@example.com' WHERE id = ?", (customer_id,))
        self.conn.execute("UPDATE shop_profile SET shop_email = 'not-an-email' WHERE id = ?", (shop_id,))
        self.conn.commit()
        appointment_id = pro_module.create_service_appointment(
            self.conn,
            {
                "customer_name": "Linked Owner",
                "customer_phone": "5551112222",
                "customer_email": "",
                "vehicle_label": "",
                "customer_id": customer_id,
                "vehicle_id": vehicle_id,
                "service_name": "Brake Inspection",
                "requested_date": future_weekday(0),
                "requested_time": "09:00",
                "status": "Confirmed",
            },
            shop_id=shop_id,
        )
        page = client.get("/pro/calendar")
        sent_messages = []

        with patch.object(
            pro_module.email_service,
            "send_email",
            side_effect=lambda message, config=None, **kwargs: sent_messages.append(message) or pro_module.email_service.EmailSendResult(success=True, transport="test"),
        ):
            response = client.post(
                f"/pro/calendar/{appointment_id}/confirmation-email",
                data={"csrf_token": csrf_from(page.text)},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/pro/calendar?notice=confirmation_email_sent")
        self.assertIn("Send Email", page.text)
        self.assertEqual(sent_messages[0].recipients, ["linked@example.com"])
        self.assertIsNone(sent_messages[0].reply_to)

    def test_appointment_confirmation_email_missing_or_invalid_recipient_fails_without_provider(self):
        client = self.client()
        self.bootstrap_owner(client, email="appt-missing@example.com", shop_name="Alpha Shop")
        shop_id = self.shop_id_for_email("appt-missing@example.com")
        missing_id = self.seed_service_appointment_for_shop(
            shop_id,
            customer_name="Missing Email",
            customer_email="",
            status="Requested",
        )
        invalid_id = self.seed_service_appointment_for_shop(
            shop_id,
            customer_name="Invalid Email",
            customer_email="not-an-email",
            status="Requested",
            requested_time="10:00",
        )
        page = client.get("/pro/calendar")

        with patch.object(pro_module.email_service, "send_email") as missing_send:
            missing = client.post(
                f"/pro/calendar/{missing_id}/status",
                data={"csrf_token": csrf_from(page.text), "status": "Confirmed"},
                follow_redirects=False,
            )
        page = client.get("/pro/calendar")
        with patch.object(pro_module.email_service, "send_email") as invalid_send:
            invalid = client.post(
                f"/pro/calendar/{invalid_id}/status",
                data={"csrf_token": csrf_from(page.text), "status": "Confirmed"},
                follow_redirects=False,
            )
        rows = {
            int(row["id"]): row["status"]
            for row in self.conn.execute(
                "SELECT id, status FROM service_appointments WHERE id IN (?, ?)",
                (missing_id, invalid_id),
            ).fetchall()
        }

        self.assertEqual(missing.status_code, 303)
        self.assertEqual(missing.headers["location"], "/pro/calendar?notice=confirmed_email_missing")
        missing_send.assert_not_called()
        self.assertEqual(invalid.status_code, 303)
        self.assertEqual(invalid.headers["location"], "/pro/calendar?notice=confirmed_email_failed")
        invalid_send.assert_not_called()
        self.assertEqual(rows[missing_id], "Confirmed")
        self.assertEqual(rows[invalid_id], "Confirmed")

    def test_appointment_confirmation_email_requires_valid_csrf_before_provider(self):
        client = self.client()
        self.bootstrap_owner(client, email="appt-csrf@example.com", shop_name="Alpha Shop")
        appointment_id = self.seed_service_appointment_for_shop(
            self.shop_id_for_email("appt-csrf@example.com"),
            customer_email="csrf@example.com",
            status="Requested",
        )
        url = f"/pro/calendar/{appointment_id}/status"
        retry_url = f"/pro/calendar/{appointment_id}/confirmation-email"

        with patch.object(pro_module.email_service, "send_email") as send_email:
            missing = client.post(url, data={"status": "Confirmed"}, follow_redirects=False)
            invalid = client.post(url, data={"csrf_token": "bad-token", "status": "Confirmed"}, follow_redirects=False)
            retry_missing = client.post(retry_url, data={}, follow_redirects=False)
            retry_invalid = client.post(retry_url, data={"csrf_token": "bad-token"}, follow_redirects=False)

        self.assertEqual(missing.status_code, 403)
        self.assertEqual(invalid.status_code, 403)
        self.assertEqual(retry_missing.status_code, 403)
        self.assertEqual(retry_invalid.status_code, 403)
        send_email.assert_not_called()

    def test_cross_shop_appointment_confirmation_email_is_rejected_without_provider(self):
        client_one = self.client()
        self.bootstrap_owner(client_one, email="appt-alpha@example.com", shop_name="Alpha Shop")
        alpha_shop = self.shop_id_for_email("appt-alpha@example.com")
        appointment_id = self.seed_service_appointment_for_shop(
            alpha_shop,
            customer_name="Alpha Pending",
            customer_email="alpha-appt@example.com",
            status="Requested",
        )
        confirmed_id = self.seed_service_appointment_for_shop(
            alpha_shop,
            customer_name="Alpha Confirmed",
            customer_email="alpha-confirmed@example.com",
            status="Confirmed",
            requested_time="10:00",
        )
        client_two = self.client()
        self.signup(client_two, email="appt-beta@example.com", shop_name="Beta Shop")
        self.verify_user("appt-beta@example.com")
        page_two = client_two.get("/pro/calendar")
        csrf_token = csrf_from(page_two.text)

        with patch.object(pro_module.email_service, "send_email") as send_email:
            cross_confirm = client_two.post(
                f"/pro/calendar/{appointment_id}/status",
                data={"csrf_token": csrf_token, "status": "Confirmed"},
                follow_redirects=False,
            )
            cross_retry = client_two.post(
                f"/pro/calendar/{confirmed_id}/confirmation-email",
                data={"csrf_token": csrf_token},
                follow_redirects=False,
            )

        self.assertEqual(cross_confirm.status_code, 404)
        self.assertEqual(cross_retry.status_code, 404)
        send_email.assert_not_called()

    def test_appointment_confirmation_email_rejects_cross_shop_linked_customer_or_vehicle(self):
        client = self.client()
        self.bootstrap_owner(client, email="appt-linked-alpha@example.com", shop_name="Alpha Shop")
        alpha_shop = self.shop_id_for_email("appt-linked-alpha@example.com")
        client_two = self.client()
        self.signup(client_two, email="appt-linked-beta@example.com", shop_name="Beta Shop")
        self.verify_user("appt-linked-beta@example.com")
        beta_shop = self.shop_id_for_email("appt-linked-beta@example.com")
        beta_customer_id, beta_vehicle_id = self.seed_customer_vehicle_for_shop(beta_shop, first_name="Beta")
        appointment_id = self.seed_service_appointment_for_shop(
            alpha_shop,
            customer_name="Bad Link",
            customer_email="valid@example.com",
            status="Requested",
        )
        self.conn.execute(
            "UPDATE service_appointments SET customer_id = ?, vehicle_id = ? WHERE id = ?",
            (beta_customer_id, beta_vehicle_id, appointment_id),
        )
        self.conn.commit()
        page = client.get("/pro/calendar")

        with patch.object(pro_module.email_service, "send_email") as send_email:
            response = client.post(
                f"/pro/calendar/{appointment_id}/status",
                data={"csrf_token": csrf_from(page.text), "status": "Confirmed"},
                follow_redirects=False,
            )
        row = self.conn.execute(
            "SELECT status FROM service_appointments WHERE id = ?",
            (appointment_id,),
        ).fetchone()

        self.assertEqual(response.status_code, 404)
        self.assertEqual(row["status"], "Requested")
        send_email.assert_not_called()

    def test_appointment_confirmation_email_provider_failures_keep_status_confirmed(self):
        client = self.client()
        self.bootstrap_owner(client, email="appt-provider@example.com", shop_name="Alpha Shop")
        shop_id = self.shop_id_for_email("appt-provider@example.com")
        config_failure_id = self.seed_service_appointment_for_shop(
            shop_id,
            customer_name="Config Failure",
            customer_email="config@example.com",
            status="Requested",
        )
        provider_failure_id = self.seed_service_appointment_for_shop(
            shop_id,
            customer_name="Provider Failure",
            customer_email="provider@example.com",
            status="Requested",
            requested_time="10:00",
        )
        page = client.get("/pro/calendar")

        with patch.object(
            pro_module,
            "appointment_email_service_config",
            return_value=pro_module.email_service.EmailServiceConfig(transport="smtp", smtp_server="", smtp_pass="", from_address="sender@example.com"),
        ):
            config_failure = client.post(
                f"/pro/calendar/{config_failure_id}/status",
                data={"csrf_token": csrf_from(page.text), "status": "Confirmed"},
                follow_redirects=False,
            )
        page = client.get("/pro/calendar")
        with patch.object(
            pro_module.email_service,
            "send_email",
            return_value=pro_module.email_service.EmailSendResult(success=False, transport="test", error_category="provider_exception", provider_related=True),
        ) as send_email:
            provider_failure = client.post(
                f"/pro/calendar/{provider_failure_id}/status",
                data={"csrf_token": csrf_from(page.text), "status": "Confirmed"},
                follow_redirects=False,
            )
        rows = {
            int(row["id"]): row["status"]
            for row in self.conn.execute(
                "SELECT id, status FROM service_appointments WHERE id IN (?, ?)",
                (config_failure_id, provider_failure_id),
            ).fetchall()
        }

        self.assertEqual(config_failure.status_code, 303)
        self.assertEqual(config_failure.headers["location"], "/pro/calendar?notice=confirmed_email_failed")
        self.assertEqual(provider_failure.status_code, 303)
        self.assertEqual(provider_failure.headers["location"], "/pro/calendar?notice=confirmed_email_failed")
        self.assertEqual(send_email.call_count, 1)
        self.assertEqual(rows[config_failure_id], "Confirmed")
        self.assertEqual(rows[provider_failure_id], "Confirmed")

    def test_appointment_confirmation_email_button_requires_customer_email(self):
        client = self.client()
        self.bootstrap_owner(client, email="appt-button@example.com", shop_name="Alpha Shop")
        shop_id = self.shop_id_for_email("appt-button@example.com")
        self.seed_service_appointment_for_shop(
            shop_id,
            customer_name="With Email",
            customer_email="with@example.com",
            status="Confirmed",
        )
        self.seed_service_appointment_for_shop(
            shop_id,
            customer_name="Without Email",
            customer_email="",
            status="Confirmed",
            requested_time="10:00",
        )
        page = client.get("/pro/calendar")

        self.assertIn("Send Email", page.text)
        without_card = page.text.split("Without Email", 1)[1].split("</article>", 1)[0]
        self.assertNotIn("Send Email", without_card)
        self.assertIn("Copy Text Message", without_card)
        self.assertNotIn("Add Contact Info", without_card)

    def test_same_shop_appointment_cancellation_email_succeeds(self):
        client = self.client()
        self.bootstrap_owner(client, email="cancel-email@example.com", shop_name="Alpha Shop")
        shop_id = self.shop_id_for_email("cancel-email@example.com")
        self.conn.execute(
            """
            UPDATE shop_profile
            SET shop_email = 'Service@Alpha.Example',
                shop_phone = '5551234567',
                shop_address = '742 Evergreen Terrace',
                appointment_cancellation_template = 'Hi {customer_name}\n\n{shop_name} canceled {service} for {vehicle} on {appointment_date} at {appointment_time}. Visit {shop_address}. Call {shop_phone}.'
            WHERE id = ?
            """,
            (shop_id,),
        )
        self.conn.commit()
        appointment_id = self.seed_service_appointment_for_shop(
            shop_id,
            customer_name="Alpha Appointment",
            customer_email="Alpha.Customer@Example.com",
            status="Confirmed",
        )
        page = client.get("/pro/calendar")
        sent_messages = []

        with patch.object(
            pro_module.email_service,
            "send_email",
            side_effect=lambda message, config=None, **kwargs: sent_messages.append(message) or pro_module.email_service.EmailSendResult(success=True, transport="test"),
        ) as send_email:
            response = client.post(
                f"/pro/calendar/{appointment_id}/cancel",
                data={"csrf_token": csrf_from(page.text)},
                follow_redirects=False,
            )
        row = self.conn.execute(
            "SELECT status FROM service_appointments WHERE id = ?",
            (appointment_id,),
        ).fetchone()

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/pro/calendar?notice=cancelled_email_sent")
        self.assertEqual(row["status"], "Cancelled")
        self.assertEqual(send_email.call_count, 1)
        self.assertEqual(len(sent_messages), 1)
        message = sent_messages[0]
        self.assertEqual(message.recipients, ["alpha.customer@example.com"])
        self.assertIn("Appointment Canceled for", message.subject)
        self.assertIn("Alpha Shop", message.subject)
        self.assertIn("Hi Alpha Appointment", message.text_body)
        self.assertIn("Alpha Shop canceled Brake Inspection", message.text_body)
        self.assertIn("2010 Honda Accord", message.text_body)
        self.assertIn("742 Evergreen Terrace", message.text_body)
        self.assertIn("(555) 123-4567", message.text_body)
        self.assertIn("Alpha Shop canceled Brake Inspection", message.html_body)
        self.assertEqual(message.reply_to, "service@alpha.example")

    def test_appointment_cancellation_email_missing_or_invalid_recipient_fails_without_provider(self):
        client = self.client()
        self.bootstrap_owner(client, email="cancel-missing@example.com", shop_name="Alpha Shop")
        shop_id = self.shop_id_for_email("cancel-missing@example.com")
        missing_id = self.seed_service_appointment_for_shop(
            shop_id,
            customer_name="Missing Email",
            customer_email="",
            status="Confirmed",
        )
        invalid_id = self.seed_service_appointment_for_shop(
            shop_id,
            customer_name="Invalid Email",
            customer_email="not-an-email",
            status="Confirmed",
            requested_time="10:00",
        )
        page = client.get("/pro/calendar")

        with patch.object(pro_module.email_service, "send_email") as missing_send:
            missing = client.post(
                f"/pro/calendar/{missing_id}/cancel",
                data={"csrf_token": csrf_from(page.text)},
                follow_redirects=False,
            )
        page = client.get("/pro/calendar")
        with patch.object(pro_module.email_service, "send_email") as invalid_send:
            invalid = client.post(
                f"/pro/calendar/{invalid_id}/cancel",
                data={"csrf_token": csrf_from(page.text)},
                follow_redirects=False,
            )
        rows = {
            int(row["id"]): row["status"]
            for row in self.conn.execute(
                "SELECT id, status FROM service_appointments WHERE id IN (?, ?)",
                (missing_id, invalid_id),
            ).fetchall()
        }

        self.assertEqual(missing.status_code, 303)
        self.assertEqual(missing.headers["location"], "/pro/calendar?notice=cancelled_email_missing")
        missing_send.assert_not_called()
        self.assertEqual(invalid.status_code, 303)
        self.assertEqual(invalid.headers["location"], "/pro/calendar?notice=cancelled_email_failed")
        invalid_send.assert_not_called()
        self.assertEqual(rows[missing_id], "Cancelled")
        self.assertEqual(rows[invalid_id], "Cancelled")

    def test_appointment_cancellation_email_requires_valid_csrf_before_provider(self):
        client = self.client()
        self.bootstrap_owner(client, email="cancel-csrf@example.com", shop_name="Alpha Shop")
        appointment_id = self.seed_service_appointment_for_shop(
            self.shop_id_for_email("cancel-csrf@example.com"),
            customer_email="csrf@example.com",
            status="Confirmed",
        )
        retry_url = f"/pro/calendar/{appointment_id}/cancellation-email"

        with patch.object(pro_module.email_service, "send_email") as send_email:
            missing = client.post(f"/pro/calendar/{appointment_id}/cancel", data={}, follow_redirects=False)
            invalid = client.post(f"/pro/calendar/{appointment_id}/cancel", data={"csrf_token": "bad-token"}, follow_redirects=False)
            retry_missing = client.post(retry_url, data={}, follow_redirects=False)
            retry_invalid = client.post(retry_url, data={"csrf_token": "bad-token"}, follow_redirects=False)

        self.assertEqual(missing.status_code, 403)
        self.assertEqual(invalid.status_code, 403)
        self.assertEqual(retry_missing.status_code, 403)
        self.assertEqual(retry_invalid.status_code, 403)
        send_email.assert_not_called()

    def test_cross_shop_appointment_cancellation_email_is_rejected_without_provider(self):
        client_one = self.client()
        self.bootstrap_owner(client_one, email="cancel-alpha@example.com", shop_name="Alpha Shop")
        alpha_shop = self.shop_id_for_email("cancel-alpha@example.com")
        appointment_id = self.seed_service_appointment_for_shop(
            alpha_shop,
            customer_name="Alpha Confirmed",
            customer_email="alpha-cancel@example.com",
            status="Confirmed",
        )
        cancelled_id = self.seed_service_appointment_for_shop(
            alpha_shop,
            customer_name="Alpha Cancelled",
            customer_email="alpha-cancelled@example.com",
            status="Cancelled",
            requested_time="10:00",
        )
        client_two = self.client()
        self.signup(client_two, email="cancel-beta@example.com", shop_name="Beta Shop")
        self.verify_user("cancel-beta@example.com")
        page_two = client_two.get("/pro/calendar")
        csrf_token = csrf_from(page_two.text)

        with patch.object(pro_module.email_service, "send_email") as send_email:
            cross_cancel = client_two.post(
                f"/pro/calendar/{appointment_id}/cancel",
                data={"csrf_token": csrf_token},
                follow_redirects=False,
            )
            cross_retry = client_two.post(
                f"/pro/calendar/{cancelled_id}/cancellation-email",
                data={"csrf_token": csrf_token},
                follow_redirects=False,
            )

        self.assertEqual(cross_cancel.status_code, 303)
        self.assertIn("error=", cross_cancel.headers["location"])
        self.assertEqual(cross_retry.status_code, 404)
        send_email.assert_not_called()

    def test_appointment_cancellation_email_rejects_cross_shop_linked_customer_or_vehicle(self):
        client = self.client()
        self.bootstrap_owner(client, email="cancel-linked-alpha@example.com", shop_name="Alpha Shop")
        alpha_shop = self.shop_id_for_email("cancel-linked-alpha@example.com")
        client_two = self.client()
        self.signup(client_two, email="cancel-linked-beta@example.com", shop_name="Beta Shop")
        self.verify_user("cancel-linked-beta@example.com")
        beta_shop = self.shop_id_for_email("cancel-linked-beta@example.com")
        beta_customer_id, beta_vehicle_id = self.seed_customer_vehicle_for_shop(beta_shop, first_name="Beta")
        appointment_id = self.seed_service_appointment_for_shop(
            alpha_shop,
            customer_name="Bad Cancel Link",
            customer_email="valid@example.com",
            status="Confirmed",
        )
        self.conn.execute(
            "UPDATE service_appointments SET customer_id = ?, vehicle_id = ? WHERE id = ?",
            (beta_customer_id, beta_vehicle_id, appointment_id),
        )
        self.conn.commit()
        page = client.get("/pro/calendar")

        with patch.object(pro_module.email_service, "send_email") as send_email:
            response = client.post(
                f"/pro/calendar/{appointment_id}/cancel",
                data={"csrf_token": csrf_from(page.text)},
                follow_redirects=False,
            )
        row = self.conn.execute(
            "SELECT status FROM service_appointments WHERE id = ?",
            (appointment_id,),
        ).fetchone()

        self.assertEqual(response.status_code, 404)
        self.assertEqual(row["status"], "Confirmed")
        send_email.assert_not_called()

    def test_appointment_cancellation_email_provider_failures_keep_status_cancelled(self):
        client = self.client()
        self.bootstrap_owner(client, email="cancel-provider@example.com", shop_name="Alpha Shop")
        shop_id = self.shop_id_for_email("cancel-provider@example.com")
        config_failure_id = self.seed_service_appointment_for_shop(
            shop_id,
            customer_name="Config Failure",
            customer_email="config@example.com",
            status="Confirmed",
        )
        provider_failure_id = self.seed_service_appointment_for_shop(
            shop_id,
            customer_name="Provider Failure",
            customer_email="provider@example.com",
            status="Confirmed",
            requested_time="10:00",
        )
        page = client.get("/pro/calendar")

        with patch.object(
            pro_module,
            "appointment_email_service_config",
            return_value=pro_module.email_service.EmailServiceConfig(transport="smtp", smtp_server="", smtp_pass="", from_address="sender@example.com"),
        ):
            config_failure = client.post(
                f"/pro/calendar/{config_failure_id}/cancel",
                data={"csrf_token": csrf_from(page.text)},
                follow_redirects=False,
            )
        page = client.get("/pro/calendar")
        with patch.object(
            pro_module.email_service,
            "send_email",
            return_value=pro_module.email_service.EmailSendResult(success=False, transport="test", error_category="provider_exception", provider_related=True),
        ) as send_email:
            provider_failure = client.post(
                f"/pro/calendar/{provider_failure_id}/cancel",
                data={"csrf_token": csrf_from(page.text)},
                follow_redirects=False,
            )
        rows = {
            int(row["id"]): row["status"]
            for row in self.conn.execute(
                "SELECT id, status FROM service_appointments WHERE id IN (?, ?)",
                (config_failure_id, provider_failure_id),
            ).fetchall()
        }

        self.assertEqual(config_failure.status_code, 303)
        self.assertEqual(config_failure.headers["location"], "/pro/calendar?notice=cancelled_email_failed")
        self.assertEqual(provider_failure.status_code, 303)
        self.assertEqual(provider_failure.headers["location"], "/pro/calendar?notice=cancelled_email_failed")
        self.assertEqual(send_email.call_count, 1)
        self.assertEqual(rows[config_failure_id], "Cancelled")
        self.assertEqual(rows[provider_failure_id], "Cancelled")

    def test_appointment_cancellation_retry_succeeds_and_rejects_non_cancelled(self):
        client = self.client()
        self.bootstrap_owner(client, email="cancel-retry@example.com", shop_name="Alpha Shop")
        shop_id = self.shop_id_for_email("cancel-retry@example.com")
        cancelled_id = self.seed_service_appointment_for_shop(
            shop_id,
            customer_name="Cancelled Email",
            customer_email="cancelled@example.com",
            status="Cancelled",
        )
        confirmed_id = self.seed_service_appointment_for_shop(
            shop_id,
            customer_name="Confirmed Email",
            customer_email="confirmed@example.com",
            status="Confirmed",
            requested_time="10:00",
        )
        page = client.get("/pro/calendar")
        sent_messages = []

        with patch.object(
            pro_module.email_service,
            "send_email",
            side_effect=lambda message, config=None, **kwargs: sent_messages.append(message) or pro_module.email_service.EmailSendResult(success=True, transport="test"),
        ) as send_email:
            retry = client.post(
                f"/pro/calendar/{cancelled_id}/cancellation-email",
                data={"csrf_token": csrf_from(page.text)},
                follow_redirects=False,
            )
            non_cancelled = client.post(
                f"/pro/calendar/{confirmed_id}/cancellation-email",
                data={"csrf_token": csrf_from(page.text)},
                follow_redirects=False,
            )

        self.assertEqual(retry.status_code, 303)
        self.assertEqual(retry.headers["location"], "/pro/calendar?notice=cancellation_email_sent")
        self.assertEqual(non_cancelled.status_code, 404)
        self.assertEqual(send_email.call_count, 1)
        self.assertEqual(sent_messages[0].recipients, ["cancelled@example.com"])

    def test_appointment_cancellation_email_button_requires_customer_email(self):
        client = self.client()
        self.bootstrap_owner(client, email="cancel-button@example.com", shop_name="Alpha Shop")
        shop_id = self.shop_id_for_email("cancel-button@example.com")
        self.seed_service_appointment_for_shop(
            shop_id,
            customer_name="Canceled With Email",
            customer_email="with@example.com",
            status="Cancelled",
        )
        self.seed_service_appointment_for_shop(
            shop_id,
            customer_name="Canceled Without Email",
            customer_email="",
            status="Cancelled",
            requested_time="10:00",
        )
        self.seed_service_appointment_for_shop(
            shop_id,
            customer_name="Confirmed With Email",
            customer_email="confirmed@example.com",
            status="Confirmed",
            requested_time="11:00",
        )
        page = client.get("/pro/calendar")

        self.assertIn("Send Email", page.text)
        without_card = page.text.split("Canceled Without Email", 1)[1].split("</article>", 1)[0]
        confirmed_card = page.text.split("Confirmed With Email", 1)[1].split("</article>", 1)[0]
        self.assertNotIn("Send Email", without_card)
        self.assertIn("Copy Text Message", without_card)
        self.assertNotIn("Add Contact Info", without_card)
        self.assertIn("Send Email", confirmed_card)

    def test_cancelled_missing_email_shows_edit_customer_with_continuation_context(self):
        client = self.client()
        self.bootstrap_owner(client, email="cancel-edit-link@example.com", shop_name="Alpha Shop")
        shop_id = self.shop_id_for_email("cancel-edit-link@example.com")
        customer_id, vehicle_id = self.seed_customer_vehicle_for_shop(shop_id, first_name="Missing", email="", phone="")
        appointment_id = self.seed_service_appointment_for_shop(
            shop_id,
            customer_name="Missing Owner",
            customer_phone="",
            customer_email="",
            status="Cancelled",
        )
        self.link_appointment_customer(appointment_id, customer_id, vehicle_id)

        page = client.get("/pro/calendar")
        card = page.text.split("Missing Owner", 1)[1].split("</article>", 1)[0]
        match = re.search(r'href="([^"]+)">Add Contact Info</a>', card)
        self.assertIsNotNone(match)
        edit_url = html.unescape(match.group(1))
        parsed = urlparse(edit_url)
        query = parse_qs(parsed.query)

        self.assertEqual(parsed.path, f"/pro/customers/{customer_id}")
        self.assertEqual(query["appointment_id"], [str(appointment_id)])
        self.assertEqual(query["appointment_action"], [pro_module.CUSTOMER_APPOINTMENT_CANCELLATION_EMAIL_ACTION])
        self.assertTrue(query["appointment_token"][0])
        self.assertNotIn("customer_email", query)
        self.assertNotIn("@", parsed.query)
        self.assertNotIn("Send Email", card)

    def test_unlinked_cancelled_missing_email_shows_add_customer_continuation(self):
        client = self.client()
        self.bootstrap_owner(client, email="cancel-add-link@example.com", shop_name="Alpha Shop")
        shop_id = self.shop_id_for_email("cancel-add-link@example.com")
        appointment_id = self.seed_service_appointment_for_shop(
            shop_id,
            customer_name="Unlinked Owner",
            customer_phone="",
            customer_email="",
            status="Cancelled",
        )

        page = client.get("/pro/calendar")
        card = page.text.split("Unlinked Owner", 1)[1].split("</article>", 1)[0]

        self.assertIn(">Add Contact Info</summary>", card)
        self.assertIn(f'action="/pro/calendar/{appointment_id}/convert"', card)
        self.assertIn('name="conversion_action" value="add_customer_cancellation_email"', card)
        self.assertIn(f'name="appointment_id" value="{appointment_id}"', card)
        self.assertIn(f'name="appointment_action" value="{pro_module.CUSTOMER_APPOINTMENT_CANCELLATION_EMAIL_ACTION}"', card)
        self.assertIn('name="appointment_token"', card)
        self.assertIn('name="new_customer_name" value="Unlinked Owner"', card)
        self.assertNotIn("Edit Customer", card)
        self.assertNotIn("Send Email", card)

    def test_customer_save_continuation_sends_cancellation_email_once_using_saved_customer_email(self):
        client = self.client()
        self.bootstrap_owner(client, email="cancel-save@example.com", shop_name="Alpha Shop")
        shop_id = self.shop_id_for_email("cancel-save@example.com")
        customer_id, vehicle_id = self.seed_customer_vehicle_for_shop(shop_id, first_name="Saved", email="", phone="")
        appointment_id = self.seed_service_appointment_for_shop(
            shop_id,
            customer_name="Saved Owner",
            customer_phone="",
            customer_email="",
            status="Cancelled",
        )
        self.link_appointment_customer(appointment_id, customer_id, vehicle_id)
        calendar_page = client.get("/pro/calendar")
        card = calendar_page.text.split("Saved Owner", 1)[1].split("</article>", 1)[0]
        edit_url = html.unescape(re.search(r'href="([^"]+)">Add Contact Info</a>', card).group(1))
        edit_page = client.get(edit_url)
        sent_messages = []

        with patch.object(
            pro_module.email_service,
            "send_email",
            side_effect=lambda message, config=None, **kwargs: sent_messages.append(message) or pro_module.email_service.EmailSendResult(success=True, transport="test"),
        ) as send_email:
            response = client.post(
                f"/pro/customers/{customer_id}",
                data={
                    "csrf_token": csrf_from(edit_page.text),
                    "appointment_id": re.search(r'name="appointment_id" value="([^"]+)"', edit_page.text).group(1),
                    "appointment_action": re.search(r'name="appointment_action" value="([^"]+)"', edit_page.text).group(1),
                    "appointment_token": re.search(r'name="appointment_token" value="([^"]+)"', edit_page.text).group(1),
                    "first_name": "Saved",
                    "last_name": "Owner",
                    "phone": "5552223333",
                    "email": "New.Customer@Example.COM",
                    "customer_email": "query-or-form-email@example.com",
                    "notes": "updated",
                },
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/pro/calendar?notice=customer_updated_cancellation_email_sent")
        self.assertEqual(send_email.call_count, 1)
        self.assertEqual(len(sent_messages), 1)
        self.assertEqual(sent_messages[0].recipients, ["new.customer@example.com"])
        saved = self.conn.execute("SELECT email FROM customers WHERE id = ?", (customer_id,)).fetchone()
        self.assertEqual(saved["email"], "New.Customer@Example.COM")
        notice_page = client.get(response.headers["location"])
        self.assertIn("Customer updated and cancellation email sent.", notice_page.text)

    def test_unlinked_add_customer_continuation_creates_links_and_sends_once_using_saved_email(self):
        client = self.client()
        self.bootstrap_owner(client, email="cancel-add-send@example.com", shop_name="Alpha Shop")
        shop_id = self.shop_id_for_email("cancel-add-send@example.com")
        appointment_id = self.seed_service_appointment_for_shop(
            shop_id,
            customer_name="Add Send",
            customer_phone="",
            customer_email="",
            status="Cancelled",
        )
        page = client.get("/pro/calendar")
        card = page.text.split("Add Send", 1)[1].split("</article>", 1)[0]
        sent_messages = []

        with patch.object(
            pro_module.email_service,
            "send_email",
            side_effect=lambda message, config=None, **kwargs: sent_messages.append(message) or pro_module.email_service.EmailSendResult(success=True, transport="test"),
        ) as send_email:
            response = client.post(
                f"/pro/calendar/{appointment_id}/convert",
                data={
                    "csrf_token": csrf_from(page.text),
                    "conversion_action": "add_customer_cancellation_email",
                    "customer_mode": "new",
                    "appointment_id": re.search(r'name="appointment_id" value="([^"]+)"', card).group(1),
                    "appointment_action": re.search(r'name="appointment_action" value="([^"]+)"', card).group(1),
                    "appointment_token": re.search(r'name="appointment_token" value="([^"]+)"', card).group(1),
                    "new_customer_name": "Add Send",
                    "new_customer_phone": "(555) 111-2222",
                    "new_customer_email": "Added.Customer@Example.COM",
                    "customer_email": "do-not-use@example.com",
                },
                follow_redirects=False,
            )

        appointment = self.conn.execute("SELECT customer_id, vehicle_id FROM service_appointments WHERE id = ?", (appointment_id,)).fetchone()
        customer = self.conn.execute("SELECT * FROM customers WHERE id = ?", (appointment["customer_id"],)).fetchone()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/pro/calendar?notice=customer_added_cancellation_email_sent")
        self.assertIsNotNone(appointment["customer_id"])
        self.assertIsNone(appointment["vehicle_id"])
        self.assertEqual(customer["shop_id"], shop_id)
        self.assertEqual(customer["email"], "Added.Customer@Example.COM")
        self.assertEqual(send_email.call_count, 1)
        self.assertEqual(len(sent_messages), 1)
        self.assertEqual(sent_messages[0].recipients, ["added.customer@example.com"])
        notice_page = client.get(response.headers["location"])
        self.assertIn("Customer added and cancellation email sent.", notice_page.text)

    def test_unlinked_add_customer_missing_email_links_without_provider(self):
        client = self.client()
        self.bootstrap_owner(client, email="cancel-add-missing@example.com", shop_name="Alpha Shop")
        shop_id = self.shop_id_for_email("cancel-add-missing@example.com")
        appointment_id = self.seed_service_appointment_for_shop(
            shop_id,
            customer_name="Add Missing",
            customer_phone="",
            customer_email="",
            status="Cancelled",
        )
        page = client.get("/pro/calendar")
        card = page.text.split("Add Missing", 1)[1].split("</article>", 1)[0]

        with patch.object(pro_module.email_service, "send_email") as send_email:
            response = client.post(
                f"/pro/calendar/{appointment_id}/convert",
                data={
                    "csrf_token": csrf_from(page.text),
                    "conversion_action": "add_customer_cancellation_email",
                    "customer_mode": "new",
                    "appointment_id": re.search(r'name="appointment_id" value="([^"]+)"', card).group(1),
                    "appointment_action": re.search(r'name="appointment_action" value="([^"]+)"', card).group(1),
                    "appointment_token": re.search(r'name="appointment_token" value="([^"]+)"', card).group(1),
                    "new_customer_name": "Add Missing",
                    "new_customer_phone": "(555) 111-2222",
                    "new_customer_email": "",
                },
                follow_redirects=False,
            )

        appointment = self.conn.execute("SELECT customer_id FROM service_appointments WHERE id = ?", (appointment_id,)).fetchone()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/pro/calendar?notice=customer_added_cancellation_email_missing")
        self.assertIsNotNone(appointment["customer_id"])
        send_email.assert_not_called()
        notice_page = client.get(response.headers["location"])
        self.assertIn("Customer added. Add an email address before emailing this cancellation.", notice_page.text)

    def test_unlinked_add_customer_delivery_failure_keeps_created_linked_customer(self):
        client = self.client()
        self.bootstrap_owner(client, email="cancel-add-fail@example.com", shop_name="Alpha Shop")
        shop_id = self.shop_id_for_email("cancel-add-fail@example.com")
        appointment_id = self.seed_service_appointment_for_shop(
            shop_id,
            customer_name="Add Fail",
            customer_phone="",
            customer_email="",
            status="Cancelled",
        )
        page = client.get("/pro/calendar")
        card = page.text.split("Add Fail", 1)[1].split("</article>", 1)[0]

        with patch.object(
            pro_module.email_service,
            "send_email",
            return_value=pro_module.email_service.EmailSendResult(success=False, transport="test", error_category="provider_exception", provider_related=True),
        ) as send_email:
            response = client.post(
                f"/pro/calendar/{appointment_id}/convert",
                data={
                    "csrf_token": csrf_from(page.text),
                    "conversion_action": "add_customer_cancellation_email",
                    "customer_mode": "new",
                    "appointment_id": re.search(r'name="appointment_id" value="([^"]+)"', card).group(1),
                    "appointment_action": re.search(r'name="appointment_action" value="([^"]+)"', card).group(1),
                    "appointment_token": re.search(r'name="appointment_token" value="([^"]+)"', card).group(1),
                    "new_customer_name": "Add Fail",
                    "new_customer_phone": "(555) 111-2222",
                    "new_customer_email": "add-fail@example.com",
                },
                follow_redirects=False,
            )

        appointment = self.conn.execute("SELECT customer_id FROM service_appointments WHERE id = ?", (appointment_id,)).fetchone()
        customer = self.conn.execute("SELECT email FROM customers WHERE id = ?", (appointment["customer_id"],)).fetchone()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/pro/calendar?notice=customer_added_cancellation_email_failed")
        self.assertIsNotNone(appointment["customer_id"])
        self.assertEqual(customer["email"], "add-fail@example.com")
        self.assertEqual(send_email.call_count, 1)
        notice_page = client.get(response.headers["location"])
        self.assertIn("Customer added, but the cancellation email could not be sent. Use Send Email to retry.", notice_page.text)

    def test_unlinked_add_customer_invalid_contexts_do_not_create_or_send(self):
        client = self.client()
        self.bootstrap_owner(client, email="cancel-add-guard-alpha@example.com", shop_name="Alpha Shop")
        alpha_shop = self.shop_id_for_email("cancel-add-guard-alpha@example.com")
        cancelled_id = self.seed_service_appointment_for_shop(alpha_shop, customer_name="Alpha Add", status="Cancelled")
        mismatch_id = self.seed_service_appointment_for_shop(alpha_shop, customer_name="Alpha Other", status="Cancelled", requested_time="10:00")
        confirmed_id = self.seed_service_appointment_for_shop(alpha_shop, customer_name="Alpha Confirmed", status="Confirmed", requested_time="11:00")
        page = client.get("/pro/calendar")
        csrf_token = csrf_from(page.text)
        valid_context = pro_module.customer_appointment_continuation_context(
            Request({"type": "http", "session": {pro_module.AUTH_SESSION_CSRF_KEY: csrf_token}}),
            shop_id=alpha_shop,
            customer_id=0,
            appointment_id=cancelled_id,
            action=pro_module.CUSTOMER_APPOINTMENT_CANCELLATION_EMAIL_ACTION,
        )
        confirmed_context = pro_module.customer_appointment_continuation_context(
            Request({"type": "http", "session": {pro_module.AUTH_SESSION_CSRF_KEY: csrf_token}}),
            shop_id=alpha_shop,
            customer_id=0,
            appointment_id=confirmed_id,
            action=pro_module.CUSTOMER_APPOINTMENT_CANCELLATION_EMAIL_ACTION,
        )
        client_two = self.client()
        self.signup(client_two, email="cancel-add-guard-beta@example.com", shop_name="Beta Shop")
        self.verify_user("cancel-add-guard-beta@example.com")
        beta_shop = self.shop_id_for_email("cancel-add-guard-beta@example.com")
        beta_id = self.seed_service_appointment_for_shop(beta_shop, customer_name="Beta Add", status="Cancelled")
        before_count = self.conn.execute("SELECT COUNT(*) AS count FROM customers").fetchone()["count"]

        with patch.object(pro_module.email_service, "send_email") as send_email:
            tampered = client.post(
                f"/pro/calendar/{cancelled_id}/convert",
                data={
                    "csrf_token": csrf_token,
                    "conversion_action": "add_customer_cancellation_email",
                    "customer_mode": "new",
                    "appointment_id": str(cancelled_id),
                    "appointment_action": "send_confirmation_email_after_customer_save",
                    "appointment_token": "bad-token",
                    "new_customer_name": "Tampered",
                    "new_customer_email": "tampered@example.com",
                },
                follow_redirects=False,
            )
            mismatched = client.post(
                f"/pro/calendar/{mismatch_id}/convert",
                data={
                    "csrf_token": csrf_token,
                    "conversion_action": "add_customer_cancellation_email",
                    "customer_mode": "new",
                    **valid_context,
                    "new_customer_name": "Mismatched",
                    "new_customer_email": "mismatched@example.com",
                },
                follow_redirects=False,
            )
            non_cancelled = client.post(
                f"/pro/calendar/{confirmed_id}/convert",
                data={
                    "csrf_token": csrf_token,
                    "conversion_action": "add_customer_cancellation_email",
                    "customer_mode": "new",
                    **confirmed_context,
                    "new_customer_name": "Non Cancelled",
                    "new_customer_email": "noncancelled@example.com",
                },
                follow_redirects=False,
            )
            cross_shop = client.post(
                f"/pro/calendar/{beta_id}/convert",
                data={
                    "csrf_token": csrf_token,
                    "conversion_action": "add_customer_cancellation_email",
                    "customer_mode": "new",
                    **valid_context,
                    "new_customer_name": "Cross Shop",
                    "new_customer_email": "cross@example.com",
                },
                follow_redirects=False,
            )

        after_count = self.conn.execute("SELECT COUNT(*) AS count FROM customers").fetchone()["count"]
        self.assertEqual(tampered.headers["location"], "/pro/calendar?notice=cancellation_email_missing")
        self.assertEqual(mismatched.headers["location"], "/pro/calendar?notice=cancellation_email_missing")
        self.assertEqual(non_cancelled.headers["location"], "/pro/calendar?notice=cancellation_email_missing")
        self.assertEqual(cross_shop.headers["location"], "/pro/calendar?notice=cancellation_email_missing")
        self.assertEqual(after_count, before_count)
        send_email.assert_not_called()

    def test_customer_save_continuation_failure_still_saves_and_redirects_to_calendar_retry_notice(self):
        client = self.client()
        self.bootstrap_owner(client, email="cancel-save-fail@example.com", shop_name="Alpha Shop")
        shop_id = self.shop_id_for_email("cancel-save-fail@example.com")
        customer_id, vehicle_id = self.seed_customer_vehicle_for_shop(shop_id, first_name="Fail", email="", phone="")
        appointment_id = self.seed_service_appointment_for_shop(
            shop_id,
            customer_name="Fail Owner",
            customer_phone="",
            customer_email="",
            status="Cancelled",
        )
        self.link_appointment_customer(appointment_id, customer_id, vehicle_id)
        calendar_page = client.get("/pro/calendar")
        edit_url = html.unescape(re.search(r'href="([^"]+)">Add Contact Info</a>', calendar_page.text).group(1))
        edit_page = client.get(edit_url)

        with patch.object(
            pro_module.email_service,
            "send_email",
            return_value=pro_module.email_service.EmailSendResult(success=False, transport="test", error_category="provider_exception", provider_related=True),
        ) as send_email:
            response = client.post(
                f"/pro/customers/{customer_id}",
                data={
                    "csrf_token": csrf_from(edit_page.text),
                    "appointment_id": re.search(r'name="appointment_id" value="([^"]+)"', edit_page.text).group(1),
                    "appointment_action": re.search(r'name="appointment_action" value="([^"]+)"', edit_page.text).group(1),
                    "appointment_token": re.search(r'name="appointment_token" value="([^"]+)"', edit_page.text).group(1),
                    "first_name": "Fail",
                    "last_name": "Owner",
                    "phone": "5552223333",
                    "email": "fail-new@example.com",
                    "notes": "still saved",
                },
                follow_redirects=False,
            )

        saved = self.conn.execute("SELECT email, notes FROM customers WHERE id = ?", (customer_id,)).fetchone()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/pro/calendar?notice=customer_updated_cancellation_email_failed")
        self.assertEqual(send_email.call_count, 1)
        self.assertEqual(saved["email"], "fail-new@example.com")
        self.assertEqual(saved["notes"], "still saved")
        notice_page = client.get(response.headers["location"])
        self.assertIn(
            "Customer updated, but the cancellation email could not be sent. Use Send Email to retry.",
            notice_page.text,
        )

    def test_ordinary_customer_edit_and_invalid_continuations_do_not_send_appointment_email(self):
        client = self.client()
        self.bootstrap_owner(client, email="cancel-invalid-context@example.com", shop_name="Alpha Shop")
        shop_id = self.shop_id_for_email("cancel-invalid-context@example.com")
        customer_id, vehicle_id = self.seed_customer_vehicle_for_shop(shop_id, first_name="Normal", email="")
        appointment_id = self.seed_service_appointment_for_shop(
            shop_id,
            customer_name="Normal Owner",
            customer_email="",
            status="Cancelled",
        )
        self.link_appointment_customer(appointment_id, customer_id, vehicle_id)
        edit_page = client.get(f"/pro/customers/{customer_id}")

        with patch.object(pro_module.email_service, "send_email") as send_email:
            ordinary = client.post(
                f"/pro/customers/{customer_id}",
                data={
                    "csrf_token": csrf_from(edit_page.text),
                    "first_name": "Normal",
                    "last_name": "Owner",
                    "phone": "5552223333",
                    "email": "ordinary@example.com",
                    "notes": "",
                },
                follow_redirects=False,
            )
            tampered = client.post(
                f"/pro/customers/{customer_id}",
                data={
                    "csrf_token": csrf_from(edit_page.text),
                    "appointment_id": str(appointment_id),
                    "appointment_action": "send_confirmation_email_after_customer_save",
                    "appointment_token": "bad-token",
                    "first_name": "Normal",
                    "last_name": "Owner",
                    "phone": "5552223333",
                    "email": "tampered@example.com",
                    "notes": "",
                },
                follow_redirects=False,
            )

        self.assertEqual(ordinary.headers["location"], f"/pro/customers/{customer_id}")
        self.assertEqual(tampered.headers["location"], f"/pro/customers/{customer_id}")
        send_email.assert_not_called()

    def test_customer_save_continuation_rejects_mismatched_cross_shop_and_non_cancelled_appointments(self):
        client = self.client()
        self.bootstrap_owner(client, email="cancel-guards-alpha@example.com", shop_name="Alpha Shop")
        alpha_shop = self.shop_id_for_email("cancel-guards-alpha@example.com")
        customer_id, vehicle_id = self.seed_customer_vehicle_for_shop(alpha_shop, first_name="Guard", email="")
        other_customer_id, other_vehicle_id = self.seed_customer_vehicle_for_shop(alpha_shop, first_name="Other", email="")
        cancelled_id = self.seed_service_appointment_for_shop(alpha_shop, customer_name="Guard Owner", status="Cancelled")
        self.link_appointment_customer(cancelled_id, customer_id, vehicle_id)
        mismatched_id = self.seed_service_appointment_for_shop(alpha_shop, customer_name="Other Owner", status="Cancelled", requested_time="10:00")
        self.link_appointment_customer(mismatched_id, other_customer_id, other_vehicle_id)
        confirmed_id = self.seed_service_appointment_for_shop(alpha_shop, customer_name="Confirmed Owner", status="Confirmed", requested_time="11:00")
        self.link_appointment_customer(confirmed_id, customer_id, vehicle_id)
        client_two = self.client()
        self.signup(client_two, email="cancel-guards-beta@example.com", shop_name="Beta Shop")
        self.verify_user("cancel-guards-beta@example.com")
        beta_shop = self.shop_id_for_email("cancel-guards-beta@example.com")
        beta_customer_id, beta_vehicle_id = self.seed_customer_vehicle_for_shop(beta_shop, first_name="Beta", email="")
        beta_cancelled_id = self.seed_service_appointment_for_shop(beta_shop, customer_name="Beta Owner", status="Cancelled")
        self.link_appointment_customer(beta_cancelled_id, beta_customer_id, beta_vehicle_id)
        page = client.get("/pro/calendar")
        csrf_token = csrf_from(client.get(f"/pro/customers/{customer_id}").text)

        valid_context = pro_module.customer_appointment_continuation_context(
            Request({"type": "http", "session": {pro_module.AUTH_SESSION_CSRF_KEY: csrf_token}}),
            shop_id=alpha_shop,
            customer_id=customer_id,
            appointment_id=cancelled_id,
            action=pro_module.CUSTOMER_APPOINTMENT_CANCELLATION_EMAIL_ACTION,
        )
        mismatch_context = pro_module.customer_appointment_continuation_context(
            Request({"type": "http", "session": {pro_module.AUTH_SESSION_CSRF_KEY: csrf_token}}),
            shop_id=alpha_shop,
            customer_id=customer_id,
            appointment_id=mismatched_id,
            action=pro_module.CUSTOMER_APPOINTMENT_CANCELLATION_EMAIL_ACTION,
        )
        confirmed_context = pro_module.customer_appointment_continuation_context(
            Request({"type": "http", "session": {pro_module.AUTH_SESSION_CSRF_KEY: csrf_token}}),
            shop_id=alpha_shop,
            customer_id=customer_id,
            appointment_id=confirmed_id,
            action=pro_module.CUSTOMER_APPOINTMENT_CANCELLATION_EMAIL_ACTION,
        )
        cross_shop_context = pro_module.customer_appointment_continuation_context(
            Request({"type": "http", "session": {pro_module.AUTH_SESSION_CSRF_KEY: csrf_token}}),
            shop_id=alpha_shop,
            customer_id=customer_id,
            appointment_id=beta_cancelled_id,
            action=pro_module.CUSTOMER_APPOINTMENT_CANCELLATION_EMAIL_ACTION,
        )

        with patch.object(pro_module.email_service, "send_email") as send_email:
            for context in (mismatch_context, confirmed_context, cross_shop_context):
                response = client.post(
                    f"/pro/customers/{customer_id}",
                    data={
                        "csrf_token": csrf_token,
                        **context,
                        "first_name": "Guard",
                        "last_name": "Owner",
                        "phone": "5552223333",
                        "email": "guard-new@example.com",
                        "notes": "",
                    },
                    follow_redirects=False,
                )
                self.assertEqual(response.headers["location"], f"/pro/customers/{customer_id}")

            different_customer = client.post(
                f"/pro/customers/{other_customer_id}",
                data={
                    "csrf_token": csrf_token,
                    **valid_context,
                    "first_name": "Other",
                    "last_name": "Owner",
                    "phone": "5552223333",
                    "email": "other-new@example.com",
                    "notes": "",
                },
                follow_redirects=False,
            )
            cross_shop_customer = client.post(
                f"/pro/customers/{beta_customer_id}",
                data={
                    "csrf_token": csrf_token,
                    **valid_context,
                    "first_name": "Beta",
                    "last_name": "Owner",
                    "phone": "5552223333",
                    "email": "beta-new@example.com",
                    "notes": "",
                },
                follow_redirects=False,
            )

        self.assertEqual(different_customer.headers["location"], f"/pro/customers/{other_customer_id}")
        self.assertEqual(cross_shop_customer.status_code, 404)
        send_email.assert_not_called()

    def test_cross_shop_appointment_mutations_fail_safely(self):
        client_one = self.client()
        self.bootstrap_owner(client_one, email="alpha-actions@example.com", shop_name="Alpha Shop")
        alpha_shop = self.shop_id_for_email("alpha-actions@example.com")
        requested_id = self.seed_service_appointment_for_shop(
            alpha_shop,
            customer_name="Alpha Pending",
            status="Requested",
        )
        confirmed_id = self.seed_service_appointment_for_shop(
            alpha_shop,
            customer_name="Alpha Confirmed",
            status="Confirmed",
            requested_time="10:00",
        )

        client_two = self.client()
        self.signup(client_two, email="beta-actions@example.com", shop_name="Beta Shop")
        self.verify_user("beta-actions@example.com")
        page_two = client_two.get("/pro/calendar")
        csrf_token = csrf_from(page_two.text)

        cross_confirm = client_two.post(
            f"/pro/calendar/{requested_id}/status",
            data={"csrf_token": csrf_token, "status": "Confirmed"},
            follow_redirects=False,
        )
        cross_decline = client_two.post(
            f"/pro/calendar/{requested_id}/status",
            data={"csrf_token": csrf_token, "status": "Declined"},
            follow_redirects=False,
        )
        cross_reschedule = client_two.post(
            f"/pro/calendar/{confirmed_id}/reschedule",
            data={
                "csrf_token": csrf_token,
                "requested_date": future_weekday(1),
                "requested_time": "11:00",
            },
            follow_redirects=False,
        )
        cross_cancel = client_two.post(
            f"/pro/calendar/{confirmed_id}/cancel",
            data={"csrf_token": csrf_token},
            follow_redirects=False,
        )
        rows = {
            int(row["id"]): row["status"]
            for row in self.conn.execute(
                "SELECT id, status FROM service_appointments WHERE id IN (?, ?)",
                (requested_id, confirmed_id),
            ).fetchall()
        }

        self.assertEqual(page_two.status_code, 200)
        self.assertNotIn("Alpha Pending", page_two.text)
        self.assertEqual(cross_confirm.status_code, 404)
        self.assertEqual(cross_decline.status_code, 404)
        self.assertEqual(cross_reschedule.status_code, 303)
        self.assertIn("error=", cross_reschedule.headers["location"])
        self.assertEqual(cross_cancel.status_code, 303)
        self.assertIn("error=", cross_cancel.headers["location"])
        self.assertEqual(rows[requested_id], "Requested")
        self.assertEqual(rows[confirmed_id], "Confirmed")

    def test_calendar_conversion_rejects_cross_shop_customer_and_vehicle(self):
        client = self.client()
        self.bootstrap_owner(client, email="alpha-convert@example.com", shop_name="Alpha Shop")
        alpha_shop = self.shop_id_for_email("alpha-convert@example.com")
        appointment_id = self.seed_service_appointment_for_shop(
            alpha_shop,
            customer_name="Alpha Conversion",
            status="Confirmed",
        )
        alpha_customer_id, _ = self.seed_customer_vehicle_for_shop(
            alpha_shop,
            first_name="Alpha",
            vehicle_make="Honda",
        )

        client_two = self.client()
        self.signup(client_two, email="beta-convert@example.com", shop_name="Beta Shop")
        self.verify_user("beta-convert@example.com")
        beta_shop = self.shop_id_for_email("beta-convert@example.com")
        beta_customer_id, beta_vehicle_id = self.seed_customer_vehicle_for_shop(
            beta_shop,
            first_name="Beta",
            vehicle_make="Ford",
        )

        page = client.get("/pro/calendar")
        csrf_token = csrf_from(page.text)
        cross_customer = client.post(
            f"/pro/calendar/{appointment_id}/convert",
            data={
                "csrf_token": csrf_token,
                "customer_mode": "existing",
                "customer_id": str(beta_customer_id),
                "vehicle_mode": "existing",
                "vehicle_id": str(beta_vehicle_id),
                "conversion_action": "save",
            },
            follow_redirects=False,
        )
        cross_vehicle = client.post(
            f"/pro/calendar/{appointment_id}/convert",
            data={
                "csrf_token": csrf_token,
                "customer_mode": "existing",
                "customer_id": str(alpha_customer_id),
                "vehicle_mode": "existing",
                "vehicle_id": str(beta_vehicle_id),
                "conversion_action": "save",
            },
            follow_redirects=False,
        )
        appointment = self.conn.execute(
            "SELECT customer_id, vehicle_id FROM service_appointments WHERE id = ?",
            (appointment_id,),
        ).fetchone()

        with self.assertRaises(HTTPException):
            pro_module.load_customer_for_shop(self.conn, beta_customer_id, alpha_shop)
        with self.assertRaises(HTTPException):
            pro_module.load_vehicle_for_shop(self.conn, beta_customer_id, beta_vehicle_id, alpha_shop)

        self.assertEqual(cross_customer.status_code, 303)
        self.assertIn("error=", cross_customer.headers["location"])
        self.assertEqual(cross_vehicle.status_code, 303)
        self.assertIn("error=", cross_vehicle.headers["location"])
        self.assertIsNone(appointment["customer_id"])
        self.assertIsNone(appointment["vehicle_id"])

    def shop_id_for_email(self, email: str) -> int:
        row = self.conn.execute(
            """
            SELECT sp.id
            FROM shop_profile sp
            JOIN users u ON u.id = sp.owner_user_id
            WHERE u.email = ?
            LIMIT 1
            """,
            (email,),
        ).fetchone()
        if not row:
            raise AssertionError(f"shop not found for {email}")
        return int(row["id"])

    def seed_service_appointment_for_shop(
        self,
        shop_id: int,
        *,
        customer_name: str = "Appointment Customer",
        customer_phone: str = "5551112222",
        customer_email: str = "",
        status: str = "Requested",
        requested_date: str | None = None,
        requested_time: str = "09:00",
    ) -> int:
        return pro_module.create_service_appointment(
            self.conn,
            {
                "customer_name": customer_name,
                "customer_phone": customer_phone,
                "customer_email": customer_email,
                "vehicle_label": "2010 Honda Accord",
                "service_name": "Brake Inspection",
                "requested_date": requested_date or future_weekday(0),
                "requested_time": requested_time,
                "status": status,
                "source": "manual",
            },
            shop_id=shop_id,
        )

    def link_appointment_customer(self, appointment_id: int, customer_id: int, vehicle_id: int | None = None) -> None:
        self.conn.execute(
            "UPDATE service_appointments SET customer_id = ?, vehicle_id = ? WHERE id = ?",
            (customer_id, vehicle_id, appointment_id),
        )
        self.conn.commit()

    def seed_customer_vehicle_for_shop(
        self,
        shop_id: int,
        *,
        first_name: str = "Cross",
        vehicle_make: str = "Toyota",
        email: str | None = None,
        phone: str = "5552223333",
    ) -> tuple[int, int]:
        pro_module.ensure_customer_status_schema(self.conn)
        now = "2026-07-24T12:00:00"
        customer_email = f"{first_name.lower()}@example.com" if email is None else email
        customer_id = int(
            self.conn.execute(
                """
                INSERT INTO customers (
                  shop_id, first_name, last_name, phone, email, customer_status, notes, created_at, updated_at
                )
                VALUES (?, ?, 'Owner', ?, ?, 'active', '', ?, ?)
                """,
                (shop_id, first_name, phone, customer_email, now, now),
            ).lastrowid
        )
        vehicle_id = int(
            self.conn.execute(
                """
                INSERT INTO customer_vehicles (shop_id, customer_id, year, make, model, created_at, updated_at)
                VALUES (?, ?, 2014, ?, 'Camry', ?, ?)
                """,
                (shop_id, customer_id, vehicle_make, now, now),
            ).lastrowid
        )
        self.conn.commit()
        return customer_id, vehicle_id

    def seed_finding_for_shop_estimate_stage(self, customer_id: int, vehicle_id: int) -> int:
        pro_module.ensure_findings_records_schema(self.conn)
        now = "2026-07-24T12:00:00"
        return int(
            self.conn.execute(
                """
                INSERT INTO findings_records (
                  customer_id, vehicle_id, request_type, finding, recommendation,
                  before_inspection_photo_paths, severity, status, mileage,
                  finding_date, customer_notes, internal_notes, created_at
                )
                VALUES (?, ?, 'finding', 'Brake pads below spec',
                        'Replace front brake pads and resurface rotors',
                        ?, 'High', 'Open', 120500, '2026-07-24',
                        'Customer heard grinding.', 'Outer pads at 2mm.', ?)
                """,
                (
                    customer_id,
                    vehicle_id,
                    json.dumps(["/static/uploads/findings/brake-before.jpg"]),
                    now,
                ),
            ).lastrowid
        )

    def seed_repair_estimate_document_for_finding(
        self,
        customer_id: int,
        vehicle_id: int,
        finding_id: int,
        *,
        total: float = 700.0,
    ) -> int:
        pro_module.ensure_repair_estimate_documents_schema(self.conn)
        payload = {
            "source": "finding",
            "problem_found": "Brake pads below spec",
            "recommended_repair": "Replace front brake pads and resurface rotors",
            "line_items": [
                {
                    "service_text": "Front brake service",
                    "labor_hours": 2.0,
                    "labor_rate": 150.0,
                    "parts_total": 400.0,
                    "pricing_mode": "custom",
                }
            ],
        }
        estimate_id = int(
            self.conn.execute(
                """
                INSERT INTO repair_estimate_documents (
                  customer_id, vehicle_id, finding_id, estimate_date,
                  customer_name, vehicle_label, related_title, estimate_total,
                  approval_status, pdf_path, payload_json, created_at
                )
                VALUES (?, ?, ?, '2026-07-24', 'Alpha Customer',
                        '2014 Toyota Camry', 'Front brake service', ?,
                        'Prepared estimate', 'test-estimate.pdf', ?, '2026-07-24T12:15:00')
                """,
                (customer_id, vehicle_id, finding_id, total, json.dumps(payload)),
            ).lastrowid
        )
        self.conn.commit()
        return estimate_id

    def test_shop_can_open_saved_finding_and_reopen_linked_estimate(self):
        client = self.client()
        self.bootstrap_owner(client, email="stage1-alpha@example.com", shop_name="Alpha Shop")
        shop_id = self.shop_id_for_email("stage1-alpha@example.com")
        customer_id, vehicle_id = self.seed_customer_vehicle_for_shop(shop_id, first_name="Alpha")
        finding_id = self.seed_finding_for_shop_estimate_stage(customer_id, vehicle_id)
        estimate_id = self.seed_repair_estimate_document_for_finding(customer_id, vehicle_id, finding_id)

        response = client.get(f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/findings/{finding_id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Brake pads below spec", response.text)
        self.assertIn("Replace front brake pads and resurface rotors", response.text)
        self.assertIn("/static/uploads/findings/brake-before.jpg", response.text)
        self.assertIn("Estimate prepared", response.text)
        self.assertIn("$700.00", response.text)
        self.assertIn("View/Edit Repair Estimate", response.text)
        self.assertIn(f"estimate_id={estimate_id}", response.text)
        self.assertIn(f"finding_id={finding_id}", response.text)
        self.assertIn("Open Estimate PDF", response.text)
        self.assertNotIn("Update Customer Decision", response.text)
        self.assertNotIn("Start Repair", response.text)

    def test_shop_can_start_estimate_from_own_saved_finding_and_other_shop_cannot_open_it(self):
        alpha_client = self.client()
        self.bootstrap_owner(alpha_client, email="stage1-alpha-open@example.com", shop_name="Alpha Shop")
        alpha_shop_id = self.shop_id_for_email("stage1-alpha-open@example.com")
        customer_id, vehicle_id = self.seed_customer_vehicle_for_shop(alpha_shop_id, first_name="Own")
        finding_id = self.seed_finding_for_shop_estimate_stage(customer_id, vehicle_id)
        self.conn.commit()

        alpha_response = alpha_client.get(f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/findings/{finding_id}")
        self.assertEqual(alpha_response.status_code, 200)
        self.assertIn("Build Repair Estimate", alpha_response.text)
        self.assertIn("source=finding", alpha_response.text)
        self.assertIn(f"customer_id={customer_id}", alpha_response.text)
        self.assertIn(f"vehicle_id={vehicle_id}", alpha_response.text)
        self.assertIn(f"finding_id={finding_id}", alpha_response.text)
        self.assertIn("/static/uploads/findings/brake-before.jpg", alpha_response.text)

        estimator_response = alpha_client.get(
            f"/estimator?source=finding&customer_id={customer_id}&vehicle_id={vehicle_id}&finding_id={finding_id}"
            "&year=2014&make=Toyota&model=Camry&recommended_repair=Front+brake+service"
        )
        self.assertEqual(estimator_response.status_code, 200)
        self.assertIn("Pro finding workflow", estimator_response.text)
        self.assertIn(f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/findings/{finding_id}", estimator_response.text)
        self.assertIn(f"/pro/customers/{customer_id}/vehicles/{vehicle_id}#recommendations-findings", estimator_response.text)
        self.assertIn("/pro/dashboard", estimator_response.text)
        self.assertNotIn(">Log In<", estimator_response.text)
        self.assertNotIn(">Sign Up<", estimator_response.text)

        save_response = alpha_client.post(
            "/estimate/pdf",
            json={
                "year": 2014,
                "make": "Toyota",
                "model": "Camry",
                "service": "Front brake service",
                "laborHours": 2.0,
                "partsPrice": 400.0,
                "laborRate": 150.0,
                "source": "finding",
                "customerId": str(customer_id),
                "vehicleId": str(vehicle_id),
                "findingId": str(finding_id),
                "problemFound": "Brake pads below spec",
                "recommendedRepair": "Replace front brake pads and resurface rotors",
                "customerAgrees": True,
            },
        )
        self.assertEqual(save_response.status_code, 200)
        saved_estimate = self.conn.execute(
            """
            SELECT finding_id, customer_id, vehicle_id, estimate_total, approval_status
            FROM repair_estimate_documents
            WHERE finding_id = ?
            """,
            (finding_id,),
        ).fetchone()
        self.assertIsNotNone(saved_estimate)
        self.assertEqual(saved_estimate["customer_id"], customer_id)
        self.assertEqual(saved_estimate["vehicle_id"], vehicle_id)
        self.assertEqual(saved_estimate["estimate_total"], 756)
        self.assertEqual(saved_estimate["approval_status"], "Prepared estimate")

        vehicle_response = alpha_client.get(f"/pro/customers/{customer_id}/vehicles/{vehicle_id}")
        self.assertEqual(vehicle_response.status_code, 200)
        self.assertIn("Estimate prepared", vehicle_response.text)
        self.assertIn("$756.00", vehicle_response.text)
        self.assertIn("View/Edit Repair Estimate", vehicle_response.text)
        self.assertIn("Edit Finding", vehicle_response.text)
        self.assertNotIn("Customer Decision / Update Status", vehicle_response.text)
        self.assertNotIn("Customer Decision / Approval Status", vehicle_response.text)
        self.assertNotIn("Create Repair Job", vehicle_response.text)

        beta_client = self.client()
        self.signup(beta_client, email="stage1-beta@example.com", shop_name="Beta Shop")
        self.verify_user("stage1-beta@example.com")
        self.login(beta_client, email="stage1-beta@example.com")
        beta_response = beta_client.get(
            f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/findings/{finding_id}",
            follow_redirects=False,
        )
        self.assertIn(beta_response.status_code, {403, 404})
        cross_save = beta_client.post(
            "/estimate/pdf",
            json={
                "year": 2014,
                "make": "Toyota",
                "model": "Camry",
                "service": "Front brake service",
                "laborHours": 2.0,
                "partsPrice": 400.0,
                "laborRate": 150.0,
                "source": "finding",
                "customerId": str(customer_id),
                "vehicleId": str(vehicle_id),
                "findingId": str(finding_id),
                "problemFound": "Brake pads below spec",
                "recommendedRepair": "Replace front brake pads and resurface rotors",
            },
        )
        self.assertEqual(cross_save.status_code, 404)

    def seed_invoice_estimate_records_for_shop(self, shop_id: int) -> dict[str, int]:
        now = "2026-07-24T12:00:00"
        pro_module.ensure_customer_status_schema(self.conn)
        pro_module.ensure_repair_records_schema(self.conn)
        pro_module.ensure_repair_completion_schema(self.conn)
        pro_module.ensure_invoices_schema(self.conn)
        pro_module.ensure_repair_estimate_documents_schema(self.conn)
        customer_id = int(
            self.conn.execute(
                """
                INSERT INTO customers (
                  shop_id, first_name, last_name, phone, email, customer_status,
                  notes, created_at, updated_at
                )
                VALUES (?, 'Alpha', 'Customer', '555-0101', 'alpha@example.com',
                        'active', '', ?, ?)
                """,
                (shop_id, now, now),
            ).lastrowid
        )
        vehicle_id = int(
            self.conn.execute(
                """
                INSERT INTO customer_vehicles (
                  shop_id, customer_id, year, make, model, mileage, created_at, updated_at
                )
                VALUES (?, ?, 2010, 'Honda', 'Accord', 120000, ?, ?)
                """,
                (shop_id, customer_id, now, now),
            ).lastrowid
        )
        repair_id = int(
            self.conn.execute(
                """
                INSERT INTO repair_records (
                  vehicle_id, customer_id, repair_name, repair_date, mileage,
                  labor_hours, labor_rate, parts_cost, labor_cost, total_cost,
                  workflow_source_type, status, completed_at, notes, created_at
                )
                VALUES (?, ?, 'Brake Pad Replacement', '2026-07-24', 120000,
                        1.0, 120, 80, 120, 200, 'estimate', 'Completed', ?, '', ?)
                """,
                (vehicle_id, customer_id, now, now),
            ).lastrowid
        )
        self.conn.execute(
            """
            INSERT INTO repair_completions (
              repair_record_id, completion_notes, final_inspection_passed,
              final_inspection_notes, completion_date, completion_mileage,
              after_repair_photo_paths, completed_at, created_at, updated_at
            )
            VALUES (?, 'Done', 1, 'Passed', '2026-07-24', 120000, '[]', ?, ?, ?)
            """,
            (repair_id, now, now, now),
        )
        repair = pro_module.load_repair_record(self.conn, customer_id, vehicle_id, repair_id)
        invoice = pro_module.create_invoice_for_repairs(
            self.conn,
            repairs=[repair],
            customer_id=customer_id,
            vehicle_id=vehicle_id,
            now=now,
        )
        storage = pro_module.ensure_storage_directories()
        pdf_path = storage.estimate_pdfs_dir / f"auth-shop-isolation-{shop_id}.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n")
        self.addCleanup(lambda p=pdf_path: p.unlink(missing_ok=True))
        estimate_id = int(
            self.conn.execute(
                """
                INSERT INTO repair_estimate_documents (
                  customer_id, vehicle_id, finding_id, estimate_date, customer_name,
                  vehicle_label, related_title, estimate_total, approval_status,
                  pdf_path, invoice_id, payload_json, created_at
                )
                VALUES (?, ?, NULL, '2026-07-24', 'Alpha Customer',
                        '2010 Honda Accord', 'Brake Pad Replacement', 200,
                        'Prepared', ?, ?, '{}', ?)
                """,
                (customer_id, vehicle_id, str(pdf_path.resolve()), invoice["id"], now),
            ).lastrowid
        )
        self.conn.commit()
        return {
            "customer_id": customer_id,
            "vehicle_id": vehicle_id,
            "repair_id": repair_id,
            "invoice_id": int(invoice["id"]),
            "estimate_id": estimate_id,
        }

    def test_estimate_invoice_customer_vehicle_pdf_routes_are_shop_isolated(self):
        client_one = self.client()
        self.bootstrap_owner(client_one, email="one@example.com", shop_name="Alpha Shop")
        alpha_ids = self.seed_invoice_estimate_records_for_shop(self.shop_id_for_email("one@example.com"))

        client_two = self.client()
        self.signup(client_two, email="two@example.com", shop_name="Beta Shop")
        self.verify_user("two@example.com")
        self.login(client_two, email="two@example.com")

        customer_url = f"/pro/customers/{alpha_ids['customer_id']}"
        vehicle_url = f"/pro/customers/{alpha_ids['customer_id']}/vehicles/{alpha_ids['vehicle_id']}"
        invoice_url = f"{vehicle_url}/invoices/{alpha_ids['invoice_id']}"
        invoice_pdf_url = f"{invoice_url}/pdf"
        estimate_pdf_url = f"{vehicle_url}/estimates/{alpha_ids['estimate_id']}/pdf"

        same_customer = client_one.get(customer_url)
        same_vehicle = client_one.get(vehicle_url)
        same_invoice = client_one.get(invoice_url)
        same_invoice_pdf = client_one.get(invoice_pdf_url)
        same_estimate_pdf = client_one.get(estimate_pdf_url)
        cross_customer = client_two.get(customer_url)
        cross_vehicle = client_two.get(vehicle_url)
        cross_invoice = client_two.get(invoice_url)
        cross_invoice_pdf = client_two.get(invoice_pdf_url)
        cross_estimate_pdf = client_two.get(estimate_pdf_url)

        self.assertEqual(same_customer.status_code, 200)
        self.assertEqual(same_vehicle.status_code, 200)
        self.assertEqual(same_invoice.status_code, 200)
        self.assertIn("Invoice", same_invoice.text)
        self.assertEqual(same_invoice_pdf.status_code, 200)
        self.assertEqual(same_invoice_pdf.headers["content-type"], "application/pdf")
        self.assertTrue(same_invoice_pdf.content.startswith(b"%PDF"))
        self.assertEqual(same_estimate_pdf.status_code, 200)
        self.assertEqual(same_estimate_pdf.headers["content-type"], "application/pdf")
        self.assertTrue(same_estimate_pdf.content.startswith(b"%PDF"))
        self.assertEqual(cross_customer.status_code, 404)
        self.assertEqual(cross_vehicle.status_code, 404)
        self.assertEqual(cross_invoice.status_code, 404)
        self.assertEqual(cross_invoice_pdf.status_code, 404)
        self.assertEqual(cross_estimate_pdf.status_code, 404)

        invoice = pro_module.load_invoice_record(
            self.conn,
            alpha_ids["customer_id"],
            alpha_ids["vehicle_id"],
            alpha_ids["invoice_id"],
            shop_id=self.shop_id_for_email("one@example.com"),
        )
        item = invoice["items"][0]
        edit_url = f"{invoice_url}/edit"
        edit_page = client_one.get(edit_url)
        saved = client_one.post(
            edit_url,
            data={
                f"item_labor_total_{item['invoice_item_id']}": "130",
                f"item_parts_total_{item['invoice_item_id']}": "85",
                f"item_repair_notes_{item['invoice_item_id']}": "Edited customer note.",
                "shop_supplies_fee": "0",
                "tax_total": "0",
                "discount_total": "0",
                "warranty_text": "Edited warranty.",
                "payment_terms": "Due on pickup.",
                "confirm_total_change": "1",
            },
            follow_redirects=False,
        )

        self.assertEqual(edit_page.status_code, 200)
        self.assertEqual(saved.status_code, 303)
        self.assertEqual(saved.headers["location"], invoice_url)
        edited_pdf = client_one.get(invoice_pdf_url)
        self.assertEqual(edited_pdf.status_code, 200)
        self.assertIn(b"Edited warranty.", edited_pdf.content)

    def test_same_shop_invoice_email_sends_pdf_attachment(self):
        client = self.client()
        self.bootstrap_owner(client, email="invoice-sender@example.com", shop_name="Alpha Shop")
        shop_id = self.shop_id_for_email("invoice-sender@example.com")
        ids = self.seed_invoice_estimate_records_for_shop(shop_id)
        self.conn.execute(
            "UPDATE shop_profile SET shop_email = 'service@alpha.example', shop_phone = '5551234567' WHERE id = ?",
            (shop_id,),
        )
        self.conn.commit()
        invoice_url = f"/pro/customers/{ids['customer_id']}/vehicles/{ids['vehicle_id']}/invoices/{ids['invoice_id']}"
        page = client.get(invoice_url)
        sent_messages = []

        def fake_send(message, config=None, *, logger=None, resend_client=None):
            sent_messages.append((message, config))
            return pro_module.email_service.EmailSendResult(success=True, transport="test", provider_message_id="email_123")

        with patch.object(pro_module, "build_invoice_pdf_bytes", return_value=b"%PDF-1.4 invoice email bytes") as build_pdf, \
             patch.object(pro_module.email_service, "send_email", side_effect=fake_send):
            response = client.post(
                f"{invoice_url}/email",
                data={"csrf_token": csrf_from(page.text)},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 303)
        self.assertIn("invoice_email=sent", response.headers["location"])
        self.assertEqual(len(sent_messages), 1)
        message = sent_messages[0][0]
        self.assertEqual(message.recipients, ["alpha@example.com"])
        self.assertIn("Invoice", message.subject)
        self.assertIn("Alpha Shop", message.subject)
        self.assertIn("attached as a PDF", message.text_body)
        self.assertIn("attached as a PDF", message.html_body)
        self.assertIn("2010 Honda Accord", message.text_body)
        self.assertEqual(message.reply_to, "service@alpha.example")
        self.assertEqual(len(message.attachments), 1)
        self.assertEqual(message.attachments[0].filename.startswith("TorqueMech-Invoice-"), True)
        self.assertEqual(message.attachments[0].content_type, "application/pdf")
        self.assertEqual(message.attachments[0].content, b"%PDF-1.4 invoice email bytes")
        build_pdf.assert_called_once()

    def test_invoice_email_omits_invalid_shop_reply_to(self):
        client = self.client()
        self.bootstrap_owner(client, email="invalid-reply@example.com", shop_name="Alpha Shop")
        shop_id = self.shop_id_for_email("invalid-reply@example.com")
        ids = self.seed_invoice_estimate_records_for_shop(shop_id)
        self.conn.execute("UPDATE shop_profile SET shop_email = 'not-an-email' WHERE id = ?", (shop_id,))
        self.conn.commit()
        invoice_url = f"/pro/customers/{ids['customer_id']}/vehicles/{ids['vehicle_id']}/invoices/{ids['invoice_id']}"
        page = client.get(invoice_url)
        sent_messages = []

        with patch.object(pro_module, "build_invoice_pdf_bytes", return_value=b"%PDF invoice"), \
             patch.object(
                 pro_module.email_service,
                 "send_email",
                 side_effect=lambda message, config=None, **kwargs: sent_messages.append(message) or pro_module.email_service.EmailSendResult(success=True, transport="test"),
             ):
            response = client.post(
                f"{invoice_url}/email",
                data={"csrf_token": csrf_from(page.text)},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 303)
        self.assertIsNone(sent_messages[0].reply_to)

    def test_invoice_email_missing_or_invalid_customer_email_fails_without_provider(self):
        client = self.client()
        self.bootstrap_owner(client, email="missing-email@example.com", shop_name="Alpha Shop")
        shop_id = self.shop_id_for_email("missing-email@example.com")
        ids = self.seed_invoice_estimate_records_for_shop(shop_id)
        invoice_url = f"/pro/customers/{ids['customer_id']}/vehicles/{ids['vehicle_id']}/invoices/{ids['invoice_id']}"
        page = client.get(invoice_url)
        self.conn.execute("UPDATE customers SET email = '' WHERE id = ?", (ids["customer_id"],))
        self.conn.commit()
        with patch.object(pro_module.email_service, "send_email") as send_email:
            missing = client.post(
                f"{invoice_url}/email",
                data={"csrf_token": csrf_from(page.text)},
                follow_redirects=False,
            )
        self.conn.execute("UPDATE customers SET email = 'not-an-email' WHERE id = ?", (ids["customer_id"],))
        self.conn.commit()
        page = client.get(invoice_url)
        with patch.object(pro_module.email_service, "send_email") as invalid_send_email:
            invalid = client.post(
                f"{invoice_url}/email",
                data={"csrf_token": csrf_from(page.text)},
                follow_redirects=False,
            )

        self.assertEqual(missing.status_code, 303)
        self.assertIn("invoice_email=missing_customer_email", missing.headers["location"])
        send_email.assert_not_called()
        self.assertEqual(invalid.status_code, 303)
        self.assertIn("invoice_email=error", invalid.headers["location"])
        invalid_send_email.assert_not_called()

    def test_invoice_email_requires_valid_csrf_before_provider(self):
        client = self.client()
        self.bootstrap_owner(client, email="csrf-invoice@example.com", shop_name="Alpha Shop")
        ids = self.seed_invoice_estimate_records_for_shop(self.shop_id_for_email("csrf-invoice@example.com"))
        invoice_url = f"/pro/customers/{ids['customer_id']}/vehicles/{ids['vehicle_id']}/invoices/{ids['invoice_id']}/email"
        with patch.object(pro_module.email_service, "send_email") as send_email:
            missing = client.post(invoice_url, data={}, follow_redirects=False)
            invalid = client.post(invoice_url, data={"csrf_token": "bad-token"}, follow_redirects=False)

        self.assertEqual(missing.status_code, 403)
        self.assertEqual(invalid.status_code, 403)
        send_email.assert_not_called()

    def test_cross_shop_invoice_email_is_rejected_without_provider(self):
        client_one = self.client()
        self.bootstrap_owner(client_one, email="invoice-alpha@example.com", shop_name="Alpha Shop")
        alpha_ids = self.seed_invoice_estimate_records_for_shop(self.shop_id_for_email("invoice-alpha@example.com"))
        client_two = self.client()
        self.signup(client_two, email="invoice-beta@example.com", shop_name="Beta Shop")
        self.verify_user("invoice-beta@example.com")
        self.login(client_two, email="invoice-beta@example.com")
        beta_customer_id, beta_vehicle_id = self.seed_customer_vehicle_for_shop(self.shop_id_for_email("invoice-beta@example.com"))
        beta_page = client_two.get("/pro/calendar")
        alpha_url = f"/pro/customers/{alpha_ids['customer_id']}/vehicles/{alpha_ids['vehicle_id']}/invoices/{alpha_ids['invoice_id']}/email"
        mixed_url = f"/pro/customers/{beta_customer_id}/vehicles/{beta_vehicle_id}/invoices/{alpha_ids['invoice_id']}/email"

        with patch.object(pro_module.email_service, "send_email") as send_email:
            cross = client_two.post(alpha_url, data={"csrf_token": csrf_from(beta_page.text)}, follow_redirects=False)
            mixed = client_two.post(mixed_url, data={"csrf_token": csrf_from(beta_page.text)}, follow_redirects=False)

        self.assertEqual(cross.status_code, 404)
        self.assertEqual(mixed.status_code, 404)
        send_email.assert_not_called()

    def test_invoice_email_missing_provider_configuration_and_provider_failure_are_safe(self):
        client = self.client()
        self.bootstrap_owner(client, email="provider-failure@example.com", shop_name="Alpha Shop")
        ids = self.seed_invoice_estimate_records_for_shop(self.shop_id_for_email("provider-failure@example.com"))
        invoice_url = f"/pro/customers/{ids['customer_id']}/vehicles/{ids['vehicle_id']}/invoices/{ids['invoice_id']}"
        page = client.get(invoice_url)

        with patch.object(pro_module, "build_invoice_pdf_bytes", return_value=b"%PDF invoice"), \
             patch.object(
                 pro_module,
                 "invoice_email_service_config",
                 return_value=pro_module.email_service.EmailServiceConfig(transport="smtp", smtp_server="", smtp_pass="", from_address="sender@example.com"),
             ):
            missing_config = client.post(
                f"{invoice_url}/email",
                data={"csrf_token": csrf_from(page.text)},
                follow_redirects=False,
            )

        page = client.get(invoice_url)
        with patch.object(pro_module, "build_invoice_pdf_bytes", return_value=b"%PDF invoice"), \
             patch.object(
                 pro_module.email_service,
                 "send_email",
                 return_value=pro_module.email_service.EmailSendResult(success=False, transport="test", error_category="provider_exception", provider_related=True),
             ):
            provider_failure = client.post(
                f"{invoice_url}/email",
                data={"csrf_token": csrf_from(page.text)},
                follow_redirects=False,
            )

        self.assertEqual(missing_config.status_code, 303)
        self.assertIn("invoice_email=error", missing_config.headers["location"])
        self.assertEqual(provider_failure.status_code, 303)
        self.assertIn("invoice_email=error", provider_failure.headers["location"])

    def test_invoice_email_pdf_generation_failure_is_safe(self):
        client = self.client()
        self.bootstrap_owner(client, email="pdf-failure@example.com", shop_name="Alpha Shop")
        ids = self.seed_invoice_estimate_records_for_shop(self.shop_id_for_email("pdf-failure@example.com"))
        invoice_url = f"/pro/customers/{ids['customer_id']}/vehicles/{ids['vehicle_id']}/invoices/{ids['invoice_id']}"
        page = client.get(invoice_url)

        with patch.object(pro_module, "build_invoice_pdf_bytes", side_effect=RuntimeError("pdf failed")), \
             patch.object(pro_module.email_service, "send_email") as send_email:
            response = client.post(
                f"{invoice_url}/email",
                data={"csrf_token": csrf_from(page.text)},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 303)
        self.assertIn("invoice_email=error", response.headers["location"])
        send_email.assert_not_called()

    def test_invoice_detail_email_button_requires_customer_email(self):
        client = self.client()
        self.bootstrap_owner(client, email="button-state@example.com", shop_name="Alpha Shop")
        ids = self.seed_invoice_estimate_records_for_shop(self.shop_id_for_email("button-state@example.com"))
        invoice_url = f"/pro/customers/{ids['customer_id']}/vehicles/{ids['vehicle_id']}/invoices/{ids['invoice_id']}"
        with_email = client.get(invoice_url)
        self.conn.execute("UPDATE customers SET email = '' WHERE id = ?", (ids["customer_id"],))
        self.conn.commit()
        without_email = client.get(invoice_url)

        self.assertIn("Email Invoice", with_email.text)
        self.assertNotIn("Email Invoice", without_email.text)
        self.assertIn("Add a customer email address before emailing this invoice.", without_email.text)

    def test_same_shop_estimate_email_sends_saved_pdf_attachment(self):
        client = self.client()
        self.bootstrap_owner(client, email="estimate-sender@example.com", shop_name="Alpha Shop")
        shop_id = self.shop_id_for_email("estimate-sender@example.com")
        ids = self.seed_invoice_estimate_records_for_shop(shop_id)
        self.conn.execute(
            "UPDATE shop_profile SET shop_email = 'Service@Alpha.Example', shop_phone = '5551234567' WHERE id = ?",
            (shop_id,),
        )
        self.conn.commit()
        vehicle_url = f"/pro/customers/{ids['customer_id']}/vehicles/{ids['vehicle_id']}"
        page = client.get(vehicle_url)
        pdf_path = Path(
            self.conn.execute(
                "SELECT pdf_path FROM repair_estimate_documents WHERE id = ?",
                (ids["estimate_id"],),
            ).fetchone()["pdf_path"]
        )
        saved_pdf_bytes = pdf_path.read_bytes()
        sent_messages = []

        with patch.object(
            pro_module.email_service,
            "send_email",
            side_effect=lambda message, config=None, **kwargs: sent_messages.append((message, config, kwargs)) or pro_module.email_service.EmailSendResult(success=True, transport="test"),
        ):
            response = client.post(
                f"{vehicle_url}/estimates/{ids['estimate_id']}/email",
                data={"csrf_token": csrf_from(page.text)},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 303)
        self.assertIn("estimate_email=sent", response.headers["location"])
        self.assertIn("#vehicle-timeline", response.headers["location"])
        self.assertEqual(len(sent_messages), 1)
        message = sent_messages[0][0]
        self.assertEqual(message.recipients, ["alpha@example.com"])
        self.assertEqual(message.subject, f"Estimate {ids['estimate_id']} from Alpha Shop")
        self.assertIn(f"estimate {ids['estimate_id']}", message.text_body)
        self.assertIn("Alpha Customer", message.text_body)
        self.assertIn("2010 Honda Accord", message.text_body)
        self.assertIn("attached as a PDF", message.text_body)
        self.assertIn("attached as a PDF", message.html_body)
        self.assertEqual(message.reply_to, "service@alpha.example")
        self.assertEqual(len(message.attachments), 1)
        self.assertEqual(message.attachments[0].filename, f"TorqueMech-Estimate-{ids['estimate_id']}.pdf")
        self.assertEqual(message.attachments[0].content_type, "application/pdf")
        self.assertEqual(message.attachments[0].content, saved_pdf_bytes)

    def test_estimate_email_omits_invalid_shop_reply_to(self):
        client = self.client()
        self.bootstrap_owner(client, email="estimate-invalid-reply@example.com", shop_name="Alpha Shop")
        shop_id = self.shop_id_for_email("estimate-invalid-reply@example.com")
        ids = self.seed_invoice_estimate_records_for_shop(shop_id)
        self.conn.execute("UPDATE shop_profile SET shop_email = 'not-an-email' WHERE id = ?", (shop_id,))
        self.conn.commit()
        vehicle_url = f"/pro/customers/{ids['customer_id']}/vehicles/{ids['vehicle_id']}"
        page = client.get(vehicle_url)
        sent_messages = []

        with patch.object(
            pro_module.email_service,
            "send_email",
            side_effect=lambda message, config=None, **kwargs: sent_messages.append(message) or pro_module.email_service.EmailSendResult(success=True, transport="test"),
        ):
            response = client.post(
                f"{vehicle_url}/estimates/{ids['estimate_id']}/email",
                data={"csrf_token": csrf_from(page.text)},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 303)
        self.assertIsNone(sent_messages[0].reply_to)

    def test_estimate_email_missing_or_invalid_customer_email_fails_without_provider(self):
        client = self.client()
        self.bootstrap_owner(client, email="estimate-missing-email@example.com", shop_name="Alpha Shop")
        ids = self.seed_invoice_estimate_records_for_shop(self.shop_id_for_email("estimate-missing-email@example.com"))
        vehicle_url = f"/pro/customers/{ids['customer_id']}/vehicles/{ids['vehicle_id']}"
        page = client.get(vehicle_url)
        self.conn.execute("UPDATE customers SET email = '' WHERE id = ?", (ids["customer_id"],))
        self.conn.commit()
        with patch.object(pro_module.email_service, "send_email") as send_email:
            missing = client.post(
                f"{vehicle_url}/estimates/{ids['estimate_id']}/email",
                data={"csrf_token": csrf_from(page.text)},
                follow_redirects=False,
            )
        self.conn.execute("UPDATE customers SET email = 'not-an-email' WHERE id = ?", (ids["customer_id"],))
        self.conn.commit()
        page = client.get(vehicle_url)
        with patch.object(pro_module.email_service, "send_email") as invalid_send_email:
            invalid = client.post(
                f"{vehicle_url}/estimates/{ids['estimate_id']}/email",
                data={"csrf_token": csrf_from(page.text)},
                follow_redirects=False,
            )

        self.assertEqual(missing.status_code, 303)
        self.assertIn("estimate_email=missing_customer_email", missing.headers["location"])
        send_email.assert_not_called()
        self.assertEqual(invalid.status_code, 303)
        self.assertIn("estimate_email=error", invalid.headers["location"])
        invalid_send_email.assert_not_called()

    def test_estimate_email_requires_valid_csrf_before_provider(self):
        client = self.client()
        self.bootstrap_owner(client, email="csrf-estimate@example.com", shop_name="Alpha Shop")
        ids = self.seed_invoice_estimate_records_for_shop(self.shop_id_for_email("csrf-estimate@example.com"))
        estimate_url = f"/pro/customers/{ids['customer_id']}/vehicles/{ids['vehicle_id']}/estimates/{ids['estimate_id']}/email"
        with patch.object(pro_module.email_service, "send_email") as send_email:
            missing = client.post(estimate_url, data={}, follow_redirects=False)
            invalid = client.post(estimate_url, data={"csrf_token": "bad-token"}, follow_redirects=False)

        self.assertEqual(missing.status_code, 403)
        self.assertEqual(invalid.status_code, 403)
        send_email.assert_not_called()

    def test_cross_shop_estimate_email_is_rejected_without_provider(self):
        client_one = self.client()
        self.bootstrap_owner(client_one, email="estimate-alpha@example.com", shop_name="Alpha Shop")
        alpha_ids = self.seed_invoice_estimate_records_for_shop(self.shop_id_for_email("estimate-alpha@example.com"))
        client_two = self.client()
        self.signup(client_two, email="estimate-beta@example.com", shop_name="Beta Shop")
        self.verify_user("estimate-beta@example.com")
        self.login(client_two, email="estimate-beta@example.com")
        beta_customer_id, beta_vehicle_id = self.seed_customer_vehicle_for_shop(self.shop_id_for_email("estimate-beta@example.com"))
        beta_page = client_two.get("/pro/calendar")
        alpha_url = f"/pro/customers/{alpha_ids['customer_id']}/vehicles/{alpha_ids['vehicle_id']}/estimates/{alpha_ids['estimate_id']}/email"
        mixed_url = f"/pro/customers/{beta_customer_id}/vehicles/{beta_vehicle_id}/estimates/{alpha_ids['estimate_id']}/email"

        with patch.object(pro_module.email_service, "send_email") as send_email:
            cross = client_two.post(alpha_url, data={"csrf_token": csrf_from(beta_page.text)}, follow_redirects=False)
            mixed = client_two.post(mixed_url, data={"csrf_token": csrf_from(beta_page.text)}, follow_redirects=False)

        self.assertEqual(cross.status_code, 404)
        self.assertEqual(mixed.status_code, 404)
        send_email.assert_not_called()

    def test_estimate_email_pdf_record_and_file_failures_are_safe(self):
        client = self.client()
        self.bootstrap_owner(client, email="estimate-pdf-failures@example.com", shop_name="Alpha Shop")
        ids = self.seed_invoice_estimate_records_for_shop(self.shop_id_for_email("estimate-pdf-failures@example.com"))
        vehicle_url = f"/pro/customers/{ids['customer_id']}/vehicles/{ids['vehicle_id']}"
        page = client.get(vehicle_url)
        with patch.object(pro_module.email_service, "send_email") as send_email:
            missing_record = client.post(
                f"{vehicle_url}/estimates/99999/email",
                data={"csrf_token": csrf_from(page.text)},
                follow_redirects=False,
            )
        self.assertEqual(missing_record.status_code, 404)
        send_email.assert_not_called()

        pdf_path = Path(
            self.conn.execute(
                "SELECT pdf_path FROM repair_estimate_documents WHERE id = ?",
                (ids["estimate_id"],),
            ).fetchone()["pdf_path"]
        )
        pdf_path.unlink()
        with patch.object(pro_module.email_service, "send_email") as missing_file_send:
            missing_file = client.post(
                f"{vehicle_url}/estimates/{ids['estimate_id']}/email",
                data={"csrf_token": csrf_from(page.text)},
                follow_redirects=False,
            )
        self.assertEqual(missing_file.status_code, 303)
        self.assertIn("estimate_email=error", missing_file.headers["location"])
        missing_file_send.assert_not_called()

    def test_estimate_email_unsafe_path_and_read_error_are_safe(self):
        client = self.client()
        self.bootstrap_owner(client, email="estimate-path-failures@example.com", shop_name="Alpha Shop")
        ids = self.seed_invoice_estimate_records_for_shop(self.shop_id_for_email("estimate-path-failures@example.com"))
        vehicle_url = f"/pro/customers/{ids['customer_id']}/vehicles/{ids['vehicle_id']}"
        page = client.get(vehicle_url)
        outside_pdf = (Path(main.BASE_DIR) / "tmp" / f"{self._testMethodName}-outside.pdf").resolve()
        outside_pdf.parent.mkdir(parents=True, exist_ok=True)
        outside_pdf.write_bytes(b"%PDF outside")
        self.addCleanup(lambda: outside_pdf.unlink(missing_ok=True))
        self.conn.execute(
            "UPDATE repair_estimate_documents SET pdf_path = ? WHERE id = ?",
            (str(outside_pdf), ids["estimate_id"]),
        )
        self.conn.commit()
        with patch.object(pro_module.email_service, "send_email") as unsafe_send:
            unsafe_path = client.post(
                f"{vehicle_url}/estimates/{ids['estimate_id']}/email",
                data={"csrf_token": csrf_from(page.text)},
                follow_redirects=False,
            )
        self.assertEqual(unsafe_path.status_code, 303)
        self.assertIn("estimate_email=error", unsafe_path.headers["location"])
        unsafe_send.assert_not_called()

        storage = pro_module.ensure_storage_directories()
        replacement_pdf = storage.estimate_pdfs_dir / f"{self._testMethodName}-read-error.pdf"
        replacement_pdf.write_bytes(b"%PDF read error")
        self.addCleanup(lambda: replacement_pdf.unlink(missing_ok=True))
        self.conn.execute(
            "UPDATE repair_estimate_documents SET pdf_path = ? WHERE id = ?",
            (str(replacement_pdf.resolve()), ids["estimate_id"]),
        )
        self.conn.commit()
        with patch.object(Path, "read_bytes", side_effect=OSError("permission denied")), \
             patch.object(pro_module.email_service, "send_email") as read_error_send:
            read_error = client.post(
                f"{vehicle_url}/estimates/{ids['estimate_id']}/email",
                data={"csrf_token": csrf_from(page.text)},
                follow_redirects=False,
            )
        self.assertEqual(read_error.status_code, 303)
        self.assertIn("estimate_email=error", read_error.headers["location"])
        read_error_send.assert_not_called()

    def test_estimate_email_provider_and_attachment_failures_are_safe(self):
        client = self.client()
        self.bootstrap_owner(client, email="estimate-provider-failure@example.com", shop_name="Alpha Shop")
        ids = self.seed_invoice_estimate_records_for_shop(self.shop_id_for_email("estimate-provider-failure@example.com"))
        vehicle_url = f"/pro/customers/{ids['customer_id']}/vehicles/{ids['vehicle_id']}"
        page = client.get(vehicle_url)

        with patch.object(
            pro_module,
            "invoice_email_service_config",
            return_value=pro_module.email_service.EmailServiceConfig(transport="smtp", smtp_server="", smtp_pass="", from_address="sender@example.com"),
        ):
            missing_config = client.post(
                f"{vehicle_url}/estimates/{ids['estimate_id']}/email",
                data={"csrf_token": csrf_from(page.text)},
                follow_redirects=False,
            )
        self.assertEqual(missing_config.status_code, 303)
        self.assertIn("estimate_email=error", missing_config.headers["location"])

        with patch.object(
            pro_module.email_service,
            "send_email",
            return_value=pro_module.email_service.EmailSendResult(success=False, transport="test", error_category="provider_exception", provider_related=True),
        ):
            provider_failure = client.post(
                f"{vehicle_url}/estimates/{ids['estimate_id']}/email",
                data={"csrf_token": csrf_from(page.text)},
                follow_redirects=False,
            )
        self.assertEqual(provider_failure.status_code, 303)
        self.assertIn("estimate_email=error", provider_failure.headers["location"])

        pdf_path = Path(
            self.conn.execute(
                "SELECT pdf_path FROM repair_estimate_documents WHERE id = ?",
                (ids["estimate_id"],),
            ).fetchone()["pdf_path"]
        )
        pdf_path.write_bytes(b"")
        attachment_failure = client.post(
            f"{vehicle_url}/estimates/{ids['estimate_id']}/email",
            data={"csrf_token": csrf_from(page.text)},
            follow_redirects=False,
        )
        self.assertEqual(attachment_failure.status_code, 303)
        self.assertIn("estimate_email=error", attachment_failure.headers["location"])

    def test_estimate_email_button_requires_customer_email(self):
        client = self.client()
        self.bootstrap_owner(client, email="estimate-button-state@example.com", shop_name="Alpha Shop")
        ids = self.seed_invoice_estimate_records_for_shop(self.shop_id_for_email("estimate-button-state@example.com"))
        vehicle_url = f"/pro/customers/{ids['customer_id']}/vehicles/{ids['vehicle_id']}"
        with_email = client.get(vehicle_url)
        self.conn.execute("UPDATE customers SET email = '' WHERE id = ?", (ids["customer_id"],))
        self.conn.commit()
        without_email = client.get(vehicle_url)

        self.assertIn("Email Estimate", with_email.text)
        self.assertNotIn("Email Estimate", without_email.text)
        self.assertIn("Add a customer email address before emailing this estimate.", without_email.text)

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
            patch.object(db, "DB_PATH", str(self.primary_db)),
            patch.object(db, "LOCAL_FALLBACK_DB_PATH", str(self.fallback_db)),
            patch.object(db, "LOCAL_DB_MARKER_PATH", self.marker_path),
            patch.object(db, "USE_LOCAL_SQLITE_COMPAT", True),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        for db_path in (self.primary_db, self.fallback_db):
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                pro_module.ensure_auth_schema(conn)
                pro_module.ensure_shop_profile_schema(conn)
                pro_module.ensure_shop_subscription_schema(conn)
            finally:
                conn.close()

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

        self.assertEqual(verify_response.status_code, 200)
        self.assertIn("Your email has been verified", verify_response.text)
        self.assertIn('href="/pro/shop-settings"', verify_response.text)
        self.assertIsNotNone(user_after["email_verified_at"])
        self.assertIsNone(user_after["verification_token_hash"])
        self.assertIsNone(user_after["verification_token_expires_at"])


if __name__ == "__main__":
    unittest.main()
