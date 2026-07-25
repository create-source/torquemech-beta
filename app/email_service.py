from __future__ import annotations

import json
import logging
import os
import smtplib
import re
import hashlib
import base64
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage as SmtpEmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import Any

try:
    import resend
except ImportError:  # pragma: no cover - covered through injected None in tests
    resend = None


TRANSPORT_ENV = "TORQUEMECH_EMAIL_TRANSPORT"
DEV_OUTBOX_ENV = "TORQUEMECH_DEV_EMAIL_OUTBOX"
RESEND_API_KEY_ENV = "RESEND_API_KEY"
MAX_ATTACHMENT_BYTES_ENV = "TORQUEMECH_EMAIL_MAX_ATTACHMENT_BYTES"
DEFAULT_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
SUPPORTED_TRANSPORTS = {"local", "test", "smtp", "resend"}
REDACTED = "[redacted]"
MIME_TYPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*$")


@dataclass(frozen=True)
class EmailAttachment:
    filename: str
    content_type: str
    content: bytes
    content_disposition: str = "attachment"


@dataclass(frozen=True)
class EmailMessage:
    recipients: list[str]
    subject: str
    text_body: str
    html_body: str | None = None
    reply_to: str | None = None
    attachments: list[EmailAttachment] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EmailSendResult:
    success: bool
    transport: str
    provider_message_id: str = ""
    error_category: str = ""
    safe_error_message: str = ""
    configuration_related: bool = False
    provider_related: bool = False
    unexpected: bool = False


@dataclass(frozen=True)
class EmailConfigurationValidation:
    ok: bool
    transport: str
    missing_variables: list[str] = field(default_factory=list)
    error_category: str = ""
    safe_error_message: str = ""


@dataclass(frozen=True)
class EmailServiceConfig:
    transport: str = "local"
    smtp_server: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""
    resend_api_key: str = ""
    dev_outbox_path: Path | None = None
    from_address: str = ""
    from_display_name: str = "TorqueMech"
    envelope_sender: str = ""
    reply_to_address: str = ""
    local_default_outbox_path: Path | None = None
    max_attachment_bytes: int = DEFAULT_MAX_ATTACHMENT_BYTES


def normalize_transport(value: str | None) -> str:
    return str(value or "local").strip().lower() or "local"


def config_from_env(*, default_outbox_path: Path | None = None) -> EmailServiceConfig:
    configured_outbox = (os.getenv(DEV_OUTBOX_ENV) or "").strip()
    feedback_email = (os.getenv("FEEDBACK_EMAIL") or "").strip()
    max_attachment_bytes = _max_attachment_bytes_from_env(os.getenv(MAX_ATTACHMENT_BYTES_ENV))
    return EmailServiceConfig(
        transport=normalize_transport(os.getenv(TRANSPORT_ENV)),
        smtp_server=(os.getenv("SMTP_SERVER") or "").strip(),
        smtp_port=int(os.getenv("SMTP_PORT", "587") or 587),
        smtp_user=(os.getenv("SMTP_USER") or "").strip(),
        smtp_pass=os.getenv("SMTP_PASS") or "",
        resend_api_key=(os.getenv(RESEND_API_KEY_ENV) or "").strip(),
        dev_outbox_path=Path(configured_outbox) if configured_outbox else None,
        from_address=feedback_email,
        envelope_sender=feedback_email,
        reply_to_address=feedback_email,
        local_default_outbox_path=default_outbox_path,
        max_attachment_bytes=max_attachment_bytes,
    )


def _max_attachment_bytes_from_env(value: str | None) -> int:
    try:
        parsed = int(str(value or "").strip())
    except (TypeError, ValueError):
        return DEFAULT_MAX_ATTACHMENT_BYTES
    return parsed if parsed > 0 else DEFAULT_MAX_ATTACHMENT_BYTES


def validate_email_configuration(config: EmailServiceConfig) -> EmailConfigurationValidation:
    transport = normalize_transport(config.transport)
    if transport not in SUPPORTED_TRANSPORTS:
        return EmailConfigurationValidation(
            ok=False,
            transport=transport,
            error_category="invalid_transport",
            safe_error_message=f"Unsupported email transport: {transport}",
        )
    if transport in {"local", "test"}:
        return EmailConfigurationValidation(ok=True, transport="local")
    missing: list[str] = []
    if transport == "smtp":
        if not config.smtp_server:
            missing.append("SMTP_SERVER")
        if not config.smtp_port:
            missing.append("SMTP_PORT")
        if not config.smtp_user:
            missing.append("SMTP_USER")
        if not config.smtp_pass:
            missing.append("SMTP_PASS")
        if not config.envelope_sender:
            missing.append("FEEDBACK_EMAIL")
    if transport == "resend":
        if not config.from_address:
            missing.append("FEEDBACK_EMAIL")
        if not config.resend_api_key:
            missing.append(RESEND_API_KEY_ENV)
    if missing:
        return EmailConfigurationValidation(
            ok=False,
            transport=transport,
            missing_variables=missing,
            error_category="missing_configuration",
            safe_error_message="Missing email configuration: " + ", ".join(missing),
        )
    return EmailConfigurationValidation(ok=True, transport=transport)


def send_email(
    message: EmailMessage,
    config: EmailServiceConfig | None = None,
    *,
    logger: logging.Logger | None = None,
    resend_client: Any = None,
) -> EmailSendResult:
    config = config or config_from_env()
    logger = logger or logging.getLogger("uvicorn.error")
    transport = normalize_transport(config.transport)
    attachment_result = validate_attachments(message.attachments, config.max_attachment_bytes)
    if not attachment_result.success:
        logger.error(
            "EMAIL_ATTACHMENT_INVALID transport=%s category=%s message=%s",
            transport,
            attachment_result.error_category,
            attachment_result.safe_error_message,
        )
        return EmailSendResult(
            success=False,
            transport=transport,
            error_category=attachment_result.error_category,
            safe_error_message=attachment_result.safe_error_message,
            configuration_related=attachment_result.error_category == "configuration_error",
        )
    if transport in {"local", "test"}:
        return _send_local(message, config, logger)
    if transport == "smtp":
        return _send_smtp(message, config, logger)
    if transport == "resend":
        return _send_resend(message, config, logger, resend_client=resend_client if resend_client is not None else resend)
    logger.error("EMAIL_TRANSPORT_UNSUPPORTED transport=%s", transport)
    return EmailSendResult(
        success=False,
        transport=transport,
        error_category="invalid_transport",
        safe_error_message=f"Unsupported email transport: {transport}",
        configuration_related=True,
    )


def validate_attachments(attachments: list[EmailAttachment], max_attachment_bytes: int = DEFAULT_MAX_ATTACHMENT_BYTES) -> EmailSendResult:
    max_bytes = max_attachment_bytes if max_attachment_bytes > 0 else DEFAULT_MAX_ATTACHMENT_BYTES
    for index, attachment in enumerate(attachments or [], start=1):
        if _has_header_injection(getattr(attachment, "filename", "")):
            return EmailSendResult(
                success=False,
                transport="",
                error_category="invalid_attachment",
                safe_error_message=f"Attachment {index} has an invalid filename.",
            )
        filename = sanitized_attachment_filename(getattr(attachment, "filename", ""))
        if not filename:
            return EmailSendResult(
                success=False,
                transport="",
                error_category="invalid_attachment",
                safe_error_message=f"Attachment {index} must have a filename.",
            )
        content = getattr(attachment, "content", None)
        if not isinstance(content, bytes):
            return EmailSendResult(
                success=False,
                transport="",
                error_category="invalid_attachment",
                safe_error_message=f"Attachment {index} content must be bytes.",
            )
        if not content:
            return EmailSendResult(
                success=False,
                transport="",
                error_category="invalid_attachment",
                safe_error_message=f"Attachment {index} content is empty.",
            )
        if len(content) > max_bytes:
            return EmailSendResult(
                success=False,
                transport="",
                error_category="attachment_too_large",
                safe_error_message=f"Attachment {index} exceeds the configured size limit.",
            )
        if not _valid_mime_type(getattr(attachment, "content_type", "")):
            return EmailSendResult(
                success=False,
                transport="",
                error_category="invalid_attachment",
                safe_error_message=f"Attachment {index} has an invalid content type.",
            )
        disposition = str(getattr(attachment, "content_disposition", "attachment") or "attachment").strip().lower()
        if disposition not in {"attachment", "inline"}:
            return EmailSendResult(
                success=False,
                transport="",
                error_category="invalid_attachment",
                safe_error_message=f"Attachment {index} has an invalid content disposition.",
            )
    return EmailSendResult(success=True, transport="")


def sanitized_attachment_filename(value: Any) -> str:
    name = str(value or "").strip().replace("\\", "/").split("/")[-1].strip()
    return name


def _has_header_injection(value: Any) -> bool:
    return bool(re.search(r"[\r\n]", str(value or "")))


def _valid_mime_type(value: Any) -> bool:
    return bool(MIME_TYPE_RE.fullmatch(str(value or "").strip()))


def attachment_metadata(attachment: EmailAttachment) -> dict[str, Any]:
    content = attachment.content
    return {
        "filename": sanitized_attachment_filename(attachment.filename),
        "content_type": str(attachment.content_type or "").strip().lower(),
        "byte_size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "content_disposition": str(attachment.content_disposition or "attachment").strip().lower(),
    }


def _send_local(message: EmailMessage, config: EmailServiceConfig, logger: logging.Logger) -> EmailSendResult:
    outbox_path = config.dev_outbox_path or config.local_default_outbox_path
    if outbox_path is None:
        return EmailSendResult(
            success=False,
            transport="local",
            error_category="missing_configuration",
            safe_error_message=f"Missing email configuration: {DEV_OUTBOX_ENV}",
            configuration_related=True,
        )
    try:
        outbox_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "created_at": datetime.utcnow().isoformat(),
            "transport": "local",
            "to": message.recipients[0] if len(message.recipients) == 1 else message.recipients,
            "subject": message.subject,
            "body": message.text_body,
        }
        if message.attachments:
            entry["attachments"] = [attachment_metadata(attachment) for attachment in message.attachments]
        entry.update(message.metadata)
        with outbox_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
        logger.info("EMAIL_LOCAL_ACCEPTED recipient_count=%s outbox=%s", len(message.recipients), outbox_path)
        return EmailSendResult(success=True, transport="local")
    except Exception as exc:
        safe_message = _safe_exception_message(exc)
        logger.error("EMAIL_LOCAL_EXCEPTION recipient_count=%s error=%s", len(message.recipients), safe_message)
        return EmailSendResult(
            success=False,
            transport="local",
            error_category="unexpected",
            safe_error_message=safe_message,
            unexpected=True,
        )


def _send_smtp(message: EmailMessage, config: EmailServiceConfig, logger: logging.Logger) -> EmailSendResult:
    validation = validate_email_configuration(config)
    if not validation.ok:
        logger.error("EMAIL_SMTP_NOT_CONFIGURED missing=%s", ",".join(validation.missing_variables))
        return EmailSendResult(
            success=False,
            transport="smtp",
            error_category=validation.error_category,
            safe_error_message=validation.safe_error_message,
            configuration_related=True,
        )
    smtp_message = _build_smtp_message(message, config)
    try:
        logger.info("EMAIL_SMTP_CONNECTING host=%s port=%s sender=%s recipient_count=%s", config.smtp_server, config.smtp_port, config.from_address, len(message.recipients))
        with smtplib.SMTP(config.smtp_server, config.smtp_port, timeout=10) as server:
            logger.info("EMAIL_SMTP_CONNECTED host=%s port=%s sender=%s recipient_count=%s", config.smtp_server, config.smtp_port, config.from_address, len(message.recipients))
            logger.info("EMAIL_SMTP_STARTTLS_START host=%s port=%s sender=%s recipient_count=%s", config.smtp_server, config.smtp_port, config.from_address, len(message.recipients))
            server.starttls()
            logger.info("EMAIL_SMTP_STARTTLS_OK host=%s port=%s sender=%s recipient_count=%s", config.smtp_server, config.smtp_port, config.from_address, len(message.recipients))
            logger.info("EMAIL_SMTP_AUTH_START host=%s port=%s sender=%s recipient_count=%s", config.smtp_server, config.smtp_port, config.from_address, len(message.recipients))
            server.login(config.smtp_user, config.smtp_pass)
            logger.info("EMAIL_SMTP_AUTH_OK host=%s port=%s sender=%s recipient_count=%s", config.smtp_server, config.smtp_port, config.from_address, len(message.recipients))
            logger.info("EMAIL_SMTP_SEND_START host=%s port=%s sender=%s recipient_count=%s", config.smtp_server, config.smtp_port, config.from_address, len(message.recipients))
            refused = server.send_message(smtp_message, from_addr=config.envelope_sender or config.from_address, to_addrs=message.recipients)
        if refused:
            logger.error("EMAIL_SMTP_REFUSED host=%s port=%s sender=%s recipient_count=%s refused_count=%s", config.smtp_server, config.smtp_port, config.from_address, len(message.recipients), len(refused))
            return EmailSendResult(
                success=False,
                transport="smtp",
                error_category="provider_refused",
                safe_error_message="SMTP provider refused one or more recipients.",
                provider_related=True,
            )
        logger.info("EMAIL_SMTP_ACCEPTED host=%s port=%s sender=%s recipient_count=%s", config.smtp_server, config.smtp_port, config.from_address, len(message.recipients))
        return EmailSendResult(success=True, transport="smtp")
    except Exception as exc:
        safe_message = _safe_exception_message(exc, config.smtp_user, config.smtp_pass, *_attachment_redaction_values(message.attachments))
        logger.error("EMAIL_SMTP_EXCEPTION host=%s port=%s sender=%s recipient_count=%s error=%s", config.smtp_server, config.smtp_port, config.from_address, len(message.recipients), safe_message)
        return EmailSendResult(
            success=False,
            transport="smtp",
            error_category="provider_exception",
            safe_error_message=safe_message,
            provider_related=True,
        )


def _send_resend(message: EmailMessage, config: EmailServiceConfig, logger: logging.Logger, *, resend_client: Any) -> EmailSendResult:
    validation = validate_email_configuration(config)
    if not validation.ok:
        logger.error("EMAIL_RESEND_NOT_CONFIGURED missing=%s", ",".join(validation.missing_variables))
        return EmailSendResult(
            success=False,
            transport="resend",
            error_category=validation.error_category,
            safe_error_message=validation.safe_error_message,
            configuration_related=True,
        )
    if resend_client is None:
        logger.error("EMAIL_RESEND_NOT_CONFIGURED missing=resend package")
        return EmailSendResult(
            success=False,
            transport="resend",
            error_category="missing_configuration",
            safe_error_message="Missing email configuration: resend package",
            configuration_related=True,
        )
    payload = {
        "from": _formatted_sender(config),
        "to": message.recipients,
        "subject": message.subject,
        "text": message.text_body,
    }
    if message.html_body:
        payload["html"] = message.html_body
    if message.attachments:
        payload["attachments"] = [_resend_attachment_payload(attachment) for attachment in message.attachments]
    reply_to = message.reply_to or config.reply_to_address
    if reply_to:
        payload["reply_to"] = reply_to
    try:
        resend_client.api_key = config.resend_api_key
        response = resend_client.Emails.send(payload)
        provider_message_id = _provider_message_id(response)
        logger.info("EMAIL_RESEND_ACCEPTED sender=%s recipient_count=%s resend_email_id=%s", config.from_address, len(message.recipients), provider_message_id)
        return EmailSendResult(success=True, transport="resend", provider_message_id=provider_message_id)
    except Exception as exc:
        safe_message = _safe_exception_message(exc, config.resend_api_key, *_attachment_redaction_values(message.attachments))
        logger.error("EMAIL_RESEND_EXCEPTION sender=%s recipient_count=%s error=%s", config.from_address, len(message.recipients), safe_message)
        return EmailSendResult(
            success=False,
            transport="resend",
            error_category="provider_exception",
            safe_error_message=safe_message,
            provider_related=True,
        )


def _build_smtp_message(message: EmailMessage, config: EmailServiceConfig) -> SmtpEmailMessage:
    smtp_message = SmtpEmailMessage()
    smtp_message["Subject"] = message.subject
    smtp_message["From"] = _formatted_sender(config)
    smtp_message["To"] = ", ".join(message.recipients)
    smtp_message["Sender"] = config.envelope_sender or config.from_address
    reply_to = message.reply_to or config.reply_to_address
    if reply_to:
        smtp_message["Reply-To"] = reply_to
    smtp_message.set_content(message.text_body or "")
    if message.html_body:
        smtp_message.add_alternative(message.html_body, subtype="html")
    for attachment in message.attachments:
        content_type = str(attachment.content_type or "").strip().lower()
        maintype, subtype = content_type.split("/", 1)
        smtp_message.add_attachment(
            attachment.content,
            maintype=maintype,
            subtype=subtype,
            filename=sanitized_attachment_filename(attachment.filename),
            disposition=str(attachment.content_disposition or "attachment").strip().lower(),
        )
    return smtp_message


def _resend_attachment_payload(attachment: EmailAttachment) -> dict[str, Any]:
    return {
        "filename": sanitized_attachment_filename(attachment.filename),
        "content": list(attachment.content),
        "content_type": str(attachment.content_type or "").strip().lower(),
    }


def _formatted_sender(config: EmailServiceConfig) -> str:
    if config.from_display_name and config.from_address and "<" not in config.from_address:
        return formataddr((config.from_display_name, config.from_address))
    return config.from_address


def _provider_message_id(response: Any) -> str:
    if isinstance(response, dict):
        return str(response.get("id") or "")
    return str(getattr(response, "id", "") or "")


def _safe_exception_message(exc: Exception, *secrets: str) -> str:
    message = f"{exc.__class__.__name__}: {exc}"
    for secret in secrets:
        if secret:
            message = message.replace(str(secret), REDACTED)
    message = re.sub(r"(['\"]content['\"]\s*:\s*)\[[^\]]{20,}\]", rf"\1{REDACTED}", message)
    message = re.sub(r"(['\"]content['\"]\s*:\s*)['\"][A-Za-z0-9+/=]{20,}['\"]", rf"\1{REDACTED}", message)
    message = re.sub(r"([?&](?:token|api_key|key|password)=)[^\\s&]+", rf"\1{REDACTED}", message, flags=re.IGNORECASE)
    message = re.sub(r"\b(token|api_key|key|password)=\S+", rf"\1={REDACTED}", message, flags=re.IGNORECASE)
    message = re.sub(r"\b(re_[A-Za-z0-9_\\-]{8,})\b", REDACTED, message)
    return message[:500]


def _attachment_redaction_values(attachments: list[EmailAttachment]) -> list[str]:
    values: list[str] = []
    for attachment in attachments or []:
        content = attachment.content if isinstance(attachment.content, bytes) else b""
        if not content or len(content) > 4096:
            continue
        decoded = content.decode("utf-8", errors="ignore")
        if decoded:
            values.append(decoded)
        values.append(base64.b64encode(content).decode("ascii"))
        values.append(str(list(content)))
    return values
