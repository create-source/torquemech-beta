import json
import logging
import unittest
import base64
from pathlib import Path
from unittest.mock import patch

from app import email_service


class EmailServiceTests(unittest.TestCase):
    def config(self, **overrides):
        values = {
            "transport": "local",
            "smtp_server": "smtp.example.test",
            "smtp_port": 2525,
            "smtp_user": "smtp-user",
            "smtp_pass": "smtp-secret",
            "resend_api_key": "re_secret_test_key",
            "from_address": "no-reply@updates.torquemech.com",
            "from_display_name": "TorqueMech",
            "envelope_sender": "mailer@updates.torquemech.com",
            "reply_to_address": "support@torquemech.com",
        }
        values.update(overrides)
        return email_service.EmailServiceConfig(**values)

    def message(self):
        return email_service.EmailMessage(
            recipients=["customer@example.com"],
            subject="Service update",
            text_body="Plain body with token safe-token-123",
            html_body="<p>HTML body with token safe-token-123</p>",
            reply_to="reply@example.com",
        )

    def pdf_attachment(self, filename="estimate.pdf", content=b"%PDF-1.4 test pdf bytes"):
        return email_service.EmailAttachment(
            filename=filename,
            content_type="application/pdf",
            content=content,
        )

    def message_with_attachments(self, *attachments):
        message = self.message()
        return email_service.EmailMessage(
            recipients=message.recipients,
            subject=message.subject,
            text_body=message.text_body,
            html_body=message.html_body,
            reply_to=message.reply_to,
            attachments=list(attachments),
        )

    def capture_smtp_send(self, message):
        calls = []

        class FakeSMTP:
            def __init__(self, server, port, timeout=None):
                calls.append(("connect", server, port, timeout))

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                pass

            def starttls(self):
                calls.append(("starttls",))

            def login(self, username, password):
                calls.append(("login", username, password))

            def send_message(self, smtp_message, from_addr=None, to_addrs=None):
                calls.append(("send", smtp_message, from_addr, to_addrs))
                return {}

        with patch.object(email_service.smtplib, "SMTP", FakeSMTP):
            result = email_service.send_email(message, self.config(transport="smtp"))

        sent = next(call for call in calls if call[0] == "send")
        return result, sent[1]

    def test_local_transport_writes_jsonl_and_structured_success(self):
        scratch_root = Path(__file__).resolve().parents[1] / "tmp" / "test_email_service"
        scratch_root.mkdir(parents=True, exist_ok=True)
        outbox = scratch_root / "local_transport.jsonl"
        outbox.unlink(missing_ok=True)
        self.addCleanup(lambda: outbox.unlink(missing_ok=True))
        result = email_service.send_email(
            email_service.EmailMessage(
                recipients=["user@example.com"],
                subject="Verify",
                text_body="Body",
                metadata={"token": "local-token", "verification_url": "http://localhost/verify-email?token=local-token"},
            ),
            self.config(transport="local", dev_outbox_path=outbox),
        )

        messages = [json.loads(line) for line in outbox.read_text(encoding="utf-8").splitlines()]

        self.assertTrue(result.success)
        self.assertEqual(result.transport, "local")
        self.assertEqual(messages[0]["transport"], "local")
        self.assertEqual(messages[0]["to"], "user@example.com")
        self.assertEqual(messages[0]["token"], "local-token")

    def test_local_transport_records_attachment_metadata_without_raw_bytes(self):
        scratch_root = Path(__file__).resolve().parents[1] / "tmp" / "test_email_service"
        scratch_root.mkdir(parents=True, exist_ok=True)
        outbox = scratch_root / "local_attachment.jsonl"
        outbox.unlink(missing_ok=True)
        self.addCleanup(lambda: outbox.unlink(missing_ok=True))
        attachment = self.pdf_attachment(content=b"%PDF-1.4 private pdf bytes")
        result = email_service.send_email(
            self.message_with_attachments(attachment),
            self.config(transport="local", dev_outbox_path=outbox),
        )

        raw_outbox = outbox.read_text(encoding="utf-8")
        messages = [json.loads(line) for line in raw_outbox.splitlines()]

        self.assertTrue(result.success)
        self.assertEqual(messages[0]["attachments"][0]["filename"], "estimate.pdf")
        self.assertEqual(messages[0]["attachments"][0]["content_type"], "application/pdf")
        self.assertEqual(messages[0]["attachments"][0]["byte_size"], len(attachment.content))
        self.assertEqual(messages[0]["attachments"][0]["content_disposition"], "attachment")
        self.assertEqual(len(messages[0]["attachments"][0]["sha256"]), 64)
        self.assertNotIn("%PDF-1.4 private pdf bytes", raw_outbox)
        self.assertNotIn(base64.b64encode(attachment.content).decode("ascii"), raw_outbox)

    def test_smtp_sends_text_and_html_multipart(self):
        calls = []

        class FakeSMTP:
            def __init__(self, server, port, timeout=None):
                calls.append(("connect", server, port, timeout))

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                pass

            def starttls(self):
                calls.append(("starttls",))

            def login(self, username, password):
                calls.append(("login", username, password))

            def send_message(self, message, from_addr=None, to_addrs=None):
                calls.append(("send", message, from_addr, to_addrs))
                return {}

        with patch.object(email_service.smtplib, "SMTP", FakeSMTP):
            result = email_service.send_email(self.message(), self.config(transport="smtp"))

        send = next(call for call in calls if call[0] == "send")
        sent_message = send[1]
        body_parts = [part for part in sent_message.walk() if part.get_content_type() in {"text/plain", "text/html"}]

        self.assertTrue(result.success)
        self.assertEqual(send[2], "mailer@updates.torquemech.com")
        self.assertEqual(send[3], ["customer@example.com"])
        self.assertEqual(sent_message["From"], "TorqueMech <no-reply@updates.torquemech.com>")
        self.assertEqual(sent_message["Reply-To"], "reply@example.com")
        self.assertEqual({part.get_content_type() for part in body_parts}, {"text/plain", "text/html"})

    def test_smtp_sends_one_pdf_attachment(self):
        attachment = self.pdf_attachment(content=b"%PDF-1.4 attachment-one")
        result, sent_message = self.capture_smtp_send(self.message_with_attachments(attachment))
        attachments = [part for part in sent_message.walk() if part.get_content_disposition() == "attachment"]

        self.assertTrue(result.success)
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_filename(), "estimate.pdf")
        self.assertEqual(attachments[0].get_content_type(), "application/pdf")
        self.assertEqual(attachments[0].get_payload(decode=True), attachment.content)

    def test_smtp_sends_multiple_attachments(self):
        first = self.pdf_attachment("estimate.pdf", b"%PDF-1.4 estimate")
        second = self.pdf_attachment("invoice.pdf", b"%PDF-1.4 invoice")
        result, sent_message = self.capture_smtp_send(self.message_with_attachments(first, second))
        attachments = [part for part in sent_message.walk() if part.get_content_disposition() == "attachment"]

        self.assertTrue(result.success)
        self.assertEqual([part.get_filename() for part in attachments], ["estimate.pdf", "invoice.pdf"])
        self.assertEqual([part.get_payload(decode=True) for part in attachments], [first.content, second.content])

    def test_smtp_preserves_plain_text_and_html_with_attachments(self):
        result, sent_message = self.capture_smtp_send(self.message_with_attachments(self.pdf_attachment()))
        body_parts = [part for part in sent_message.walk() if part.get_content_type() in {"text/plain", "text/html"}]
        attachments = [part for part in sent_message.walk() if part.get_content_disposition() == "attachment"]

        self.assertTrue(result.success)
        self.assertEqual(sent_message.get_content_type(), "multipart/mixed")
        self.assertEqual({part.get_content_type() for part in body_parts}, {"text/plain", "text/html"})
        self.assertEqual(len(attachments), 1)

    def test_resend_sends_text_and_html_payload(self):
        calls = []

        class FakeEmails:
            @staticmethod
            def send(payload):
                calls.append(payload)
                return {"id": "email_123"}

        class FakeResend:
            api_key = ""
            Emails = FakeEmails

        result = email_service.send_email(
            self.message(),
            self.config(transport="resend", from_address="TorqueMech <verify@updates.torquemech.com>", from_display_name=""),
            resend_client=FakeResend,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.provider_message_id, "email_123")
        self.assertEqual(FakeResend.api_key, "re_secret_test_key")
        self.assertEqual(calls[0]["from"], "TorqueMech <verify@updates.torquemech.com>")
        self.assertEqual(calls[0]["text"], "Plain body with token safe-token-123")
        self.assertEqual(calls[0]["html"], "<p>HTML body with token safe-token-123</p>")

    def test_resend_sends_one_attachment_payload(self):
        calls = []

        class FakeEmails:
            @staticmethod
            def send(payload):
                calls.append(payload)
                return {"id": "email_123"}

        class FakeResend:
            api_key = ""
            Emails = FakeEmails

        attachment = self.pdf_attachment(content=b"%PDF-1.4 resend pdf")
        result = email_service.send_email(
            self.message_with_attachments(attachment),
            self.config(transport="resend", from_address="sender@example.com"),
            resend_client=FakeResend,
        )

        self.assertTrue(result.success)
        self.assertEqual(calls[0]["attachments"], [
            {
                "filename": "estimate.pdf",
                "content": list(attachment.content),
                "content_type": "application/pdf",
            }
        ])

    def test_resend_supports_multiple_attachments(self):
        calls = []

        class FakeEmails:
            @staticmethod
            def send(payload):
                calls.append(payload)
                return {"id": "email_123"}

        class FakeResend:
            api_key = ""
            Emails = FakeEmails

        first = self.pdf_attachment("estimate.pdf", b"%PDF estimate")
        second = self.pdf_attachment("invoice.pdf", b"%PDF invoice")
        result = email_service.send_email(
            self.message_with_attachments(first, second),
            self.config(transport="resend", from_address="sender@example.com"),
            resend_client=FakeResend,
        )

        self.assertTrue(result.success)
        self.assertEqual([item["filename"] for item in calls[0]["attachments"]], ["estimate.pdf", "invoice.pdf"])
        self.assertEqual([item["content"] for item in calls[0]["attachments"]], [list(first.content), list(second.content)])

    def test_missing_resend_api_key_returns_configuration_result(self):
        result = email_service.send_email(
            self.message(),
            self.config(transport="resend", resend_api_key="", from_address="sender@example.com"),
            resend_client=object(),
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, "missing_configuration")
        self.assertTrue(result.configuration_related)
        self.assertIn("RESEND_API_KEY", result.safe_error_message)
        self.assertNotIn("re_secret", result.safe_error_message)

    def test_missing_smtp_configuration_returns_configuration_result(self):
        result = email_service.send_email(self.message(), self.config(transport="smtp", smtp_pass=""))

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, "missing_configuration")
        self.assertTrue(result.configuration_related)
        self.assertIn("SMTP_PASS", result.safe_error_message)
        self.assertNotIn("smtp-secret", result.safe_error_message)

    def test_invalid_transport_name_fails_safely(self):
        result = email_service.send_email(self.message(), self.config(transport="bogus"))

        self.assertFalse(result.success)
        self.assertEqual(result.transport, "bogus")
        self.assertEqual(result.error_category, "invalid_transport")
        self.assertTrue(result.configuration_related)

    def test_provider_exception_result_and_logs_are_safe(self):
        logger = logging.getLogger("test.email_service.safe")

        class FailingEmails:
            @staticmethod
            def send(payload):
                raise RuntimeError("provider unavailable token=safe-token-123 api_key=re_secret_test_key")

        class FakeResend:
            api_key = ""
            Emails = FailingEmails

        with self.assertLogs(logger, level="INFO") as captured:
            result = email_service.send_email(
                self.message(),
                self.config(transport="resend", from_address="sender@example.com"),
                logger=logger,
                resend_client=FakeResend,
            )

        log_output = "\n".join(captured.output)
        self.assertFalse(result.success)
        self.assertEqual(result.error_category, "provider_exception")
        self.assertTrue(result.provider_related)
        self.assertIn("RuntimeError: provider unavailable", result.safe_error_message)
        self.assertNotIn("re_secret_test_key", result.safe_error_message)
        self.assertNotIn("safe-token-123", result.safe_error_message)
        self.assertNotIn("re_secret_test_key", log_output)
        self.assertNotIn("safe-token-123", log_output)

    def test_provider_exception_redacts_attachment_content_from_logs(self):
        logger = logging.getLogger("test.email_service.attachments.safe")
        attachment = self.pdf_attachment(content=b"%PDF-1.4 secret attachment bytes")

        class FailingEmails:
            @staticmethod
            def send(payload):
                raise RuntimeError(f"provider echoed payload {payload}")

        class FakeResend:
            api_key = ""
            Emails = FailingEmails

        with self.assertLogs(logger, level="INFO") as captured:
            result = email_service.send_email(
                self.message_with_attachments(attachment),
                self.config(transport="resend", from_address="sender@example.com"),
                logger=logger,
                resend_client=FakeResend,
            )

        log_output = "\n".join(captured.output)
        encoded = base64.b64encode(attachment.content).decode("ascii")
        self.assertFalse(result.success)
        self.assertEqual(result.error_category, "provider_exception")
        self.assertNotIn("%PDF-1.4 secret attachment bytes", result.safe_error_message)
        self.assertNotIn(encoded, result.safe_error_message)
        self.assertNotIn(str(list(attachment.content)), result.safe_error_message)
        self.assertNotIn("%PDF-1.4 secret attachment bytes", log_output)
        self.assertNotIn(encoded, log_output)
        self.assertNotIn(str(list(attachment.content)), log_output)

    def test_invalid_blank_filename_is_rejected_before_provider(self):
        calls = []

        class FakeEmails:
            @staticmethod
            def send(payload):
                calls.append(payload)
                return {"id": "email_123"}

        class FakeResend:
            api_key = ""
            Emails = FakeEmails

        result = email_service.send_email(
            self.message_with_attachments(self.pdf_attachment(filename=" ")),
            self.config(transport="resend", from_address="sender@example.com"),
            resend_client=FakeResend,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, "invalid_attachment")
        self.assertEqual(calls, [])

    def test_crlf_filename_injection_is_rejected(self):
        result = email_service.send_email(
            self.message_with_attachments(self.pdf_attachment(filename="estimate.pdf\r\nBcc: attacker@example.com")),
            self.config(transport="smtp"),
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, "invalid_attachment")

    def test_non_bytes_attachment_content_is_rejected(self):
        result = email_service.send_email(
            self.message_with_attachments(
                email_service.EmailAttachment(
                    filename="estimate.pdf",
                    content_type="application/pdf",
                    content="not-bytes",
                )
            ),
            self.config(transport="smtp"),
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, "invalid_attachment")
        self.assertIn("content must be bytes", result.safe_error_message)

    def test_empty_attachment_content_is_rejected(self):
        result = email_service.send_email(
            self.message_with_attachments(self.pdf_attachment(content=b"")),
            self.config(transport="smtp"),
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, "invalid_attachment")
        self.assertIn("content is empty", result.safe_error_message)

    def test_oversized_attachment_is_rejected_before_provider(self):
        calls = []

        class FakeEmails:
            @staticmethod
            def send(payload):
                calls.append(payload)
                return {"id": "email_123"}

        class FakeResend:
            api_key = ""
            Emails = FakeEmails

        result = email_service.send_email(
            self.message_with_attachments(self.pdf_attachment(content=b"123456")),
            self.config(transport="resend", from_address="sender@example.com", max_attachment_bytes=5),
            resend_client=FakeResend,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, "attachment_too_large")
        self.assertEqual(calls, [])

    def test_invalid_mime_type_is_rejected(self):
        result = email_service.send_email(
            self.message_with_attachments(
                email_service.EmailAttachment(
                    filename="estimate.pdf",
                    content_type="not a mime",
                    content=b"%PDF-1.4",
                )
            ),
            self.config(transport="smtp"),
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, "invalid_attachment")

    def test_configuration_validation_reports_variable_names_only(self):
        validation = email_service.validate_email_configuration(
            self.config(transport="smtp", smtp_server="", smtp_pass="")
        )

        self.assertFalse(validation.ok)
        self.assertEqual(validation.transport, "smtp")
        self.assertEqual(validation.error_category, "missing_configuration")
        self.assertIn("SMTP_SERVER", validation.missing_variables)
        self.assertIn("SMTP_PASS", validation.missing_variables)
        self.assertNotIn("smtp-secret", validation.safe_error_message)


if __name__ == "__main__":
    unittest.main()
