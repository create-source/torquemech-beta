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


def verification_urls_from_text(text: str) -> list[str]:
    return re.findall(r"https?://[^\s\"'<>]+/verify-email\?token=[^\s\"'<>]+", text)


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

    def latest_reset_token(self):
        messages = [message for message in self.outbox_messages() if message.get("reset_url")]
        if not messages:
            raise AssertionError("password reset outbox is empty")
        parsed = urlparse(messages[-1]["reset_url"])
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
        smtp_body = message.get_payload(decode=True).decode(message.get_content_charset() or "utf-8")
        self.assertIn("/verify-email?token=", smtp_body)
        self.assertNotEqual(message["From"], "smtp-user")
        self.assertIn("VERIFICATION_EMAIL_TRANSPORT_SELECTED transport=smtp", log_output)
        self.assertIn("host=smtp.example.test port=2525", log_output)
        self.assertIn("sender=mailer@updates.torquemech.com", log_output)
        self.assertIn("recipient=user@example.com", log_output)
        self.assertIn("VERIFICATION_EMAIL_DELIVERY_ENTERED transport=smtp", log_output)
        self.assertIn("VERIFICATION_EMAIL_SMTP_CONNECTING", log_output)
        self.assertIn("VERIFICATION_EMAIL_SMTP_CONNECTED", log_output)
        self.assertIn("VERIFICATION_EMAIL_SMTP_STARTTLS_START", log_output)
        self.assertIn("VERIFICATION_EMAIL_SMTP_STARTTLS_OK", log_output)
        self.assertIn("VERIFICATION_EMAIL_SMTP_AUTH_START", log_output)
        self.assertIn("VERIFICATION_EMAIL_SMTP_AUTH_OK", log_output)
        self.assertIn("VERIFICATION_EMAIL_SMTP_SEND_START", log_output)
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

        self.assertEqual(verify_response.status_code, 200)
        self.assertIn("Your email has been verified", verify_response.text)
        self.assertIn('href="/pro/shop-settings"', verify_response.text)
        self.assertIsNotNone(user_after["email_verified_at"])
        self.assertIsNone(user_after["verification_token_hash"])
        self.assertIsNone(user_after["verification_token_expires_at"])


if __name__ == "__main__":
    unittest.main()
