from __future__ import annotations

import base64
import hashlib
import hmac
import html
import io
import json
import math
import re
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote, urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import qrcode
from fastapi import (
    FastAPI,
    Request,
    Body,
    HTTPException,
    Response,
)

from routers.knowledge import router as knowledge_router
from routers.pro import (
    AUTH_SESSION_USER_KEY,
    AUTH_SESSION_BOOTSTRAP_KEY,
    bootstrap_existing_shop_to_user,
    create_or_ensure_shop_subscription,
    create_shop_profile_for_user,
    csrf_token,
    current_shop_context,
    current_user,
    ensure_auth_schema,
    ensure_shop_profile_schema,
    hash_password,
    load_user_by_email,
    login_session,
    logout_session,
    normalize_email,
    public_router as booking_router,
    record_estimate_pdf_document,
    repair_workspace_parts_sources,
    router as pro_router,
    safe_next_url,
    shop_can_write,
    load_shop_subscription,
    shop_subscription_access_context,
    validate_csrf,
    verification_token_hash,
    verify_password,
    user_email_verified,
)
from app.billing import build_billing_display
from app import email_service

from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
)

from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    PlainTextResponse,
)

from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel, Field
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

import re
import os

import logging

import json
from datetime import datetime

import uuid
import contextvars
from fastapi import Request
from fastapi.responses import JSONResponse

import smtplib
from email.mime.text import MIMEText

try:
    import resend
except ImportError:
    resend = None

from dotenv import load_dotenv
import os

from fastapi.templating import Jinja2Templates


import sqlite3
import json


from pathlib import Path
from fastapi.responses import HTMLResponse

from app.data.labor_profiles import build_labor_breakdown, get_service_labor_profile
from app.storage import configured_storage_paths, resolve_storage_child

from db import connect_app_db, using_postgres
from repair_paths import REPAIR_PATHS

from pathlib import Path
import json
from fastapi import HTTPException

DEFAULT_LABOR_RANGES = {
    "maintenance": (0.5, 1.5),
    "brakes": (1.0, 2.0),
    "engine": (1.5, 3.5),
    "cooling": (1.0, 2.5),
    "electrical": (1.0, 2.5),
    "suspension": (1.5, 3.0),
    "exhaust": (1.0, 2.0),
    "fuel": (1.0, 2.5),
    "transmission": (2.5, 5.0),
    "ac_heating": (1.5, 3.0),
    "default": (1.0, 2.0)
}

def slugify_service_name(name: str) -> str:
    name = (name or "").strip().lower()
    name = re.sub(r"[^a-z0-9\s-]", "", name)
    name = re.sub(r"\s+", "-", name)
    name = re.sub(r"-+", "-", name)
    return name.strip("-")

load_dotenv()

SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
FEEDBACK_EMAIL = os.getenv("FEEDBACK_EMAIL")
RESEND_API_KEY_ENV = "RESEND_API_KEY"
VERIFICATION_EMAIL_RESEND_COOLDOWN_SECONDS = 60
VERIFICATION_EMAIL_SUBJECT = "Verify your TorqueMech account"
PASSWORD_RESET_EMAIL_SUBJECT = "Reset your TorqueMech password"
PASSWORD_RESET_CONFIRMATION_MESSAGE = "If an account exists for this email, we’ve sent password reset instructions."
PASSWORD_RESET_REQUEST_COOLDOWN_SECONDS = 60
PASSWORD_RESET_TOKEN_EXPIRY_MINUTES = 30
EMAIL_CHANGE_DUPLICATE_MESSAGE = "This email address is unavailable. If it's one of your existing accounts, sign in to that account or use Forgot Password to regain access."

def service_slug_exists(service_slug: str) -> bool:
    catalog = load_services_catalog()
    raw = catalog["raw"]

    for category in raw["categories"]:
        for service in category.get("services", []):
            slug = slugify_service_name(service.get("name", ""))
            if slug == service_slug:
                return True

    return False

# ===============================
# Basic Error Logging (Beta-safe)
# ===============================
logging.basicConfig(
    filename="torquemech.log",
    level=logging.ERROR,
    format="%(asctime)s %(levelname)s %(message)s",
)
pro_gate_logger = logging.getLogger("torquemech.pro_gate")
pro_gate_logger.setLevel(logging.WARNING)
uvicorn_error_logger = logging.getLogger("uvicorn.error")
verification_email_logger = uvicorn_error_logger
verification_email_logger.setLevel(logging.INFO)

# ===============================
# Request ID (adds X-Request-ID)
# ===============================
_request_id_ctx = contextvars.ContextVar("request_id", default="-")

class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_ctx.get()
        return True

# Add request_id to ALL existing handlers (including your file handler)
for _h in logging.getLogger().handlers:
    _h.addFilter(RequestIdFilter())

# Update log format to include request_id (best effort)
# NOTE: basicConfig already set handler/format; this helps if you later add handlers.

# ============================================================
# App Setup
# ============================================================

SESSION_COOKIE_NAME = "tm_session"
BOOTSTRAP_TOKEN_ENV = "TORQUEMECH_BOOTSTRAP_TOKEN"


def load_server_session_data(session_id: str) -> dict[str, Any]:
    if not session_id:
        return {}
    try:
        conn = app_db_conn(row_factory=True)
        try:
            ensure_auth_schema(conn)
            row = conn.execute(
                "SELECT data_json FROM auth_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if not row:
                return {}
            loaded = json.loads(row["data_json"] or "{}")
            return loaded if isinstance(loaded, dict) else {}
        finally:
            conn.close()
    except Exception:
        logging.exception("SESSION_LOAD_FAILED")
        return {}


class SQLiteSessionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        session_id = request.cookies.get(SESSION_COOKIE_NAME, "")
        session_data = load_server_session_data(session_id)
        if session_id and not session_data:
            session_id = ""
        request.scope["session"] = session_data
        response = await call_next(request)
        try:
            updated_session = dict(request.scope.get("session") or {})
            rotate_session = bool(request.scope.pop("rotate_session_id", False))
            conn = app_db_conn(row_factory=True)
            try:
                ensure_auth_schema(conn)
                if updated_session:
                    old_session_id = session_id
                    if rotate_session:
                        session_id = ""
                    if not session_id:
                        session_id = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii").rstrip("=")
                    now = datetime.utcnow().isoformat()
                    conn.execute(
                        """
                        INSERT INTO auth_sessions (session_id, data_json, created_at, updated_at)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(session_id) DO UPDATE SET
                          data_json = excluded.data_json,
                          updated_at = excluded.updated_at
                        """,
                        (session_id, json.dumps(updated_session), now, now),
                    )
                    if rotate_session and old_session_id and old_session_id != session_id:
                        conn.execute("DELETE FROM auth_sessions WHERE session_id = ?", (old_session_id,))
                    conn.commit()
                    response.set_cookie(
                        SESSION_COOKIE_NAME,
                        session_id,
                        httponly=True,
                        secure=request.url.scheme == "https",
                        samesite="lax",
                    )
                elif session_id:
                    conn.execute("DELETE FROM auth_sessions WHERE session_id = ?", (session_id,))
                    conn.commit()
                    response.delete_cookie(SESSION_COOKIE_NAME)
            finally:
                conn.close()
        except Exception:
            logging.exception("SESSION_SAVE_FAILED")
        return response


app = FastAPI()
app.add_middleware(SQLiteSessionMiddleware)

DEFAULT_SHOP_TIMEZONE = "America/Los_Angeles"
try:
    SHOP_ZONEINFO = ZoneInfo(DEFAULT_SHOP_TIMEZONE)
except ZoneInfoNotFoundError:
    SHOP_ZONEINFO = timezone(timedelta(hours=-7), DEFAULT_SHOP_TIMEZONE)


def local_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(SHOP_ZONEINFO)


def local_today_iso() -> str:
    return local_now().date().isoformat()

from starlette.middleware.base import BaseHTTPMiddleware

class CanonicalHostMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        host = request.headers.get("host", "")
        if host.startswith("www."):
            url = request.url.replace(netloc=host.replace("www.", ""))
            return RedirectResponse(str(url), status_code=301)
        return await call_next(request)

app.add_middleware(CanonicalHostMiddleware)

PRO_ACCESS_COOKIE = "tm_pro_access"
PRO_QA_ACCESS_COOKIE = "tm_pro_qa_access"
PRO_PRIVATE_MESSAGE = "TorqueMech Pro is in private development."


def pro_enabled() -> bool:
    return (os.getenv("PRO_ENABLED") or "").strip().lower() == "true"


def pro_access_code() -> str:
    return (os.getenv("PRO_ACCESS_CODE") or "").strip()


def pro_qa_key() -> str:
    return (os.getenv("PRO_QA_KEY") or "").strip()


def is_local_pro_request(request: Request) -> bool:
    host = (request.headers.get("host") or "").split(":", 1)[0].strip().lower()
    client_host = (request.client.host if request.client else "").strip().lower()
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    return host in local_hosts or client_host in {"127.0.0.1", "::1"}


def pro_access_signature(code: str) -> str:
    return hmac.new(code.encode("utf-8"), b"torquemech-pro-access", hashlib.sha256).hexdigest()


def pro_qa_access_signature(key: str) -> str:
    return hmac.new(key.encode("utf-8"), b"torquemech-pro-qa-access", hashlib.sha256).hexdigest()


def has_valid_pro_access_cookie(request: Request, code: str) -> bool:
    cookie_value = request.cookies.get(PRO_ACCESS_COOKIE, "")
    expected = pro_access_signature(code)
    return bool(cookie_value) and hmac.compare_digest(cookie_value, expected)


def has_valid_pro_qa_cookie(request: Request, key: str) -> bool:
    cookie_value = request.cookies.get(PRO_QA_ACCESS_COOKIE, "")
    expected = pro_qa_access_signature(key)
    return bool(cookie_value) and hmac.compare_digest(cookie_value, expected)


def log_pro_qa_gate(
    request: Request,
    *,
    pro_qa_key_present: bool,
    qa_key_param_present: bool,
    qa_key_matched: bool,
    qa_cookie_valid: bool,
    access_allowed: bool,
) -> None:
    message = (
        "PRO_QA_GATE path=%s pro_qa_key_present=%s qa_key_param_present=%s "
        "qa_key_matched=%s qa_cookie_valid=%s access_allowed=%s"
    )
    args = (
        request.url.path,
        pro_qa_key_present,
        qa_key_param_present,
        qa_key_matched,
        qa_cookie_valid,
        access_allowed,
    )
    pro_gate_logger.warning(message, *args)
    uvicorn_error_logger.warning(message, *args)


def pro_request_access_state(request: Request) -> dict[str, Any]:
    qa_key = pro_qa_key()
    submitted_qa_key = (request.query_params.get("qa_key") or "").strip()
    qa_key_present = bool(qa_key)
    qa_param_present = bool(submitted_qa_key)
    qa_cookie_valid = has_valid_pro_qa_cookie(request, qa_key) if qa_key_present else False
    qa_key_matched = (
        qa_key_present
        and qa_param_present
        and hmac.compare_digest(submitted_qa_key, qa_key)
    )
    code = pro_access_code()
    legacy_cookie_valid = bool(code) and has_valid_pro_access_cookie(request, code)
    enabled_without_access_code = not code and pro_enabled()
    access_allowed = (
        is_local_pro_request(request)
        or qa_cookie_valid
        or qa_key_matched
        or legacy_cookie_valid
        or enabled_without_access_code
    )
    return {
        "access_allowed": access_allowed,
        "qa_key": qa_key,
        "qa_key_present": qa_key_present,
        "qa_param_present": qa_param_present,
        "qa_cookie_valid": qa_cookie_valid,
        "qa_key_matched": qa_key_matched,
        "legacy_access_code": code,
        "legacy_cookie_valid": legacy_cookie_valid,
    }


def pro_private_response(error: str = "") -> HTMLResponse:
    error_html = f"<p class=\"tm-pro-gate-error\">{error}</p>" if error else ""
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TorqueMech Pro Private Development</title>
  <style>
    body {{ margin:0; font-family: Arial, sans-serif; background:#f8fafc; color:#0f172a; }}
    main {{ min-height:100vh; display:grid; place-items:center; padding:24px; }}
    section {{ width:min(100%, 440px); border:1px solid #dbe3ef; border-radius:8px; background:#fff; padding:24px; box-shadow:0 18px 40px rgba(15,23,42,.08); }}
    h1 {{ margin:0; font-size:1.45rem; line-height:1.2; }}
    p {{ margin:10px 0 0; color:#475569; line-height:1.5; }}
    label {{ display:block; margin-top:18px; color:#334155; font-weight:700; }}
    input {{ box-sizing:border-box; width:100%; margin-top:7px; border:1px solid #cbd5e1; border-radius:8px; padding:11px 12px; font-size:1rem; }}
    button {{ margin-top:14px; border:0; border-radius:8px; background:#0f766e; color:#fff; padding:11px 14px; font-weight:800; cursor:pointer; }}
    .tm-pro-gate-error {{ color:#b91c1c; font-weight:700; }}
  </style>
</head>
<body>
  <main>
    <section>
      <h1>{PRO_PRIVATE_MESSAGE}</h1>
      <p>TorqueMech Pro is currently in private development.</p>
      {error_html}
      <form method="post">
        <label for="pro_access_code">Access code</label>
        <input id="pro_access_code" name="pro_access_code" type="password" autocomplete="current-password">
        <button type="submit">Continue</button>
      </form>
    </section>
  </main>
</body>
</html>""",
        status_code=403,
    )


def pro_blocked_response() -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TorqueMech Pro Private Development</title>
  <style>
    body {{ margin:0; font-family: Arial, sans-serif; background:#f8fafc; color:#0f172a; }}
    main {{ min-height:100vh; display:grid; place-items:center; padding:24px; }}
    section {{ width:min(100%, 440px); border:1px solid #dbe3ef; border-radius:8px; background:#fff; padding:24px; box-shadow:0 18px 40px rgba(15,23,42,.08); }}
    h1 {{ margin:0; font-size:1.45rem; line-height:1.2; }}
    p {{ margin:10px 0 0; color:#475569; line-height:1.5; }}
  </style>
</head>
<body>
  <main>
    <section>
      <h1>{PRO_PRIVATE_MESSAGE}</h1>
      <p>TorqueMech Pro is currently in private development.</p>
    </section>
  </main>
</body>
</html>""",
        status_code=403,
    )

# --- Paths ---
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
SERVICES_CATALOG_PATH = BASE_DIR / "services_catalog.json"
SERVICE_EDUCATION_PATH = BASE_DIR / "data" / "service_education.json"

STATE_DIR = Path("/data") if Path("/data").exists() else BASE_DIR / ".localstate"
STATE_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = str((STATE_DIR / "app.db").resolve())
LOCAL_FALLBACK_DB_PATH = str((STATE_DIR / "dev_runtime_app.db").resolve())
USE_LOCAL_SQLITE_COMPAT = not Path("/data").exists()
LOCAL_DB_MARKER_PATH = STATE_DIR / "active_app_db_path.txt"

DATA_DIR = Path(__file__).resolve().parent / "data"
REPAIR_GUIDE_TORQUE_SPECS_PATH = DATA_DIR / "torque_specs" / "brakes.json"

_repair_guide_torque_specs_cache: Optional[Dict[str, Any]] = None
_repair_guide_torque_specs_mtime: Optional[float] = None
SHARED_ESTIMATE_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}$"
)

def load_json_file(*parts: str) -> dict:
    file_path = DATA_DIR.joinpath(*parts)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Content not found")

    try:
        with file_path.open("r", encoding="utf-8-sig") as f:
            return json.load(f)
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Invalid JSON content")


def load_repair_guide_torque_specs() -> Dict[str, Any]:
    global _repair_guide_torque_specs_cache, _repair_guide_torque_specs_mtime

    if not REPAIR_GUIDE_TORQUE_SPECS_PATH.exists():
        return {}

    mtime = REPAIR_GUIDE_TORQUE_SPECS_PATH.stat().st_mtime
    if (
        _repair_guide_torque_specs_cache is not None
        and _repair_guide_torque_specs_mtime == mtime
    ):
        return _repair_guide_torque_specs_cache

    try:
        data = json.loads(REPAIR_GUIDE_TORQUE_SPECS_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        data = {}

    if not isinstance(data, dict):
        data = {}

    _repair_guide_torque_specs_cache = data
    _repair_guide_torque_specs_mtime = mtime
    return data


def mark_local_fallback_db_active() -> None:
    if not USE_LOCAL_SQLITE_COMPAT:
        return
    try:
        LOCAL_DB_MARKER_PATH.write_text(LOCAL_FALLBACK_DB_PATH, encoding="utf-8")
    except OSError:
        logging.exception("LOCAL_DB_MARKER_WRITE_FAILED")


def active_app_db_path() -> str:
    if USE_LOCAL_SQLITE_COMPAT:
        try:
            marked_path = LOCAL_DB_MARKER_PATH.read_text(encoding="utf-8").strip()
        except OSError:
            marked_path = ""
        if marked_path == LOCAL_FALLBACK_DB_PATH:
            return LOCAL_FALLBACK_DB_PATH
    return DB_PATH


def app_db_conn(*, row_factory: bool = False) -> sqlite3.Connection:
    return connect_app_db(row_factory=row_factory)


DEFAULT_SHOP_PROFILE: Dict[str, Any] = {
    "shop_name": "",
    "phone": "",
    "email": "",
    "address": "",
    "website": "",
    "scheduling_link": "",
    "logo_url": "",
    "labor_rate_default": 90.0,
    "tax_rate_default": 0.0,
    "warranty_note": "",
    "quote_expiration_days": 30,
    "custom_footer_note": "",
}


def init_shop_profile_db() -> None:
    conn = app_db_conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shop_profile (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              shop_name TEXT,
              phone TEXT,
              email TEXT,
              address TEXT,
              website TEXT,
              scheduling_link TEXT,
              logo_url TEXT,
              labor_rate_default REAL,
              tax_rate_default REAL,
              warranty_note TEXT,
              quote_expiration_days INTEGER,
              custom_footer_note TEXT,
              updated_at TEXT NOT NULL
            )
            """
        )
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(shop_profile)").fetchall()
        }
        if "scheduling_link" not in columns:
            conn.execute("ALTER TABLE shop_profile ADD COLUMN scheduling_link TEXT")
        conn.commit()
    finally:
        conn.close()


def init_pro_crm_schema_db() -> None:
    """Create dormant Pro CRM tables without exposing CRM behavior in Beta."""
    def add_column_if_missing(table_name: str, column_name: str, column_sql: str) -> None:
        columns = {
            row[1]
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name not in columns:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")

    conn = app_db_conn()
    try:
        # Pro groundwork: shared-schema CRM tables for future shop/tenant isolation.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS customers (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              shop_id INTEGER,
              first_name TEXT,
              last_name TEXT,
              phone TEXT,
              email TEXT,
              customer_status TEXT NOT NULL DEFAULT 'active',
              notes TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        add_column_if_missing("customers", "customer_status", "customer_status TEXT NOT NULL DEFAULT 'active'")
        conn.execute(
            """
            UPDATE customers
            SET customer_status = 'active'
            WHERE customer_status IS NULL OR TRIM(customer_status) = ''
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_customers_shop_id ON customers (shop_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_customers_phone ON customers (phone)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_customers_email ON customers (email)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_customers_status ON customers (customer_status)")

        # Pro groundwork: customer vehicle records remain tenant-ready via shop_id.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS customer_vehicles (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              customer_id INTEGER NOT NULL,
              shop_id INTEGER,
              year INTEGER,
              make TEXT,
              model TEXT,
              engine TEXT,
              vin TEXT,
              license_plate TEXT,
              mileage INTEGER,
              notes TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY (customer_id) REFERENCES customers(id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_customer_vehicles_customer_id ON customer_vehicles (customer_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_customer_vehicles_shop_id ON customer_vehicles (shop_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_customer_vehicles_vin ON customer_vehicles (vin)")

        # Pro groundwork: historical service records for future CRM modules only.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS service_history (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              customer_id INTEGER NOT NULL,
              vehicle_id INTEGER NOT NULL,
              shop_id INTEGER,
              service_title TEXT,
              service_notes TEXT,
              mileage_at_service INTEGER,
              service_date TEXT NOT NULL,
              labor_amount REAL,
              parts_amount REAL,
              estimate_total REAL,
              actual_total REAL,
              status TEXT NOT NULL CHECK (status IN ('estimate', 'approved', 'completed', 'declined')),
              customer_authorized_at TEXT,
              customer_authorized_by TEXT,
              authorization_notes TEXT,
              discrepancy_notes TEXT,
              created_at TEXT,
              updated_at TEXT,
              FOREIGN KEY (customer_id) REFERENCES customers(id),
              FOREIGN KEY (vehicle_id) REFERENCES customer_vehicles(id)
            )
            """
        )
        # Pro groundwork: safe additive migration for 14.0A databases already initialized.
        add_column_if_missing("service_history", "customer_authorized_at", "customer_authorized_at TEXT")
        add_column_if_missing("service_history", "customer_authorized_by", "customer_authorized_by TEXT")
        add_column_if_missing("service_history", "authorization_notes", "authorization_notes TEXT")
        add_column_if_missing("service_history", "discrepancy_notes", "discrepancy_notes TEXT")
        add_column_if_missing("service_history", "labor_amount", "labor_amount REAL")
        add_column_if_missing("service_history", "parts_amount", "parts_amount REAL")
        add_column_if_missing("service_history", "actual_total", "actual_total REAL")
        add_column_if_missing("service_history", "created_at", "created_at TEXT")
        add_column_if_missing("service_history", "updated_at", "updated_at TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_service_history_customer_id ON service_history (customer_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_service_history_vehicle_id ON service_history (vehicle_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_service_history_shop_id ON service_history (shop_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_service_history_service_date ON service_history (service_date)")

        # Pro permanent vehicle ledger. Rows are appended from maintenance and repair sources.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS service_history_records (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              customer_id INTEGER NOT NULL,
              vehicle_id INTEGER NOT NULL,
              source_type TEXT NOT NULL,
              source_record_id INTEGER NOT NULL,
              service_name TEXT,
              service_date TEXT,
              mileage INTEGER,
              labor_hours REAL,
              labor_rate REAL,
              parts_cost REAL,
              labor_cost REAL,
              total_cost REAL,
              notes TEXT,
              created_at TEXT NOT NULL,
              FOREIGN KEY (customer_id) REFERENCES customers(id),
              FOREIGN KEY (vehicle_id) REFERENCES customer_vehicles(id),
              UNIQUE (source_type, source_record_id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_service_history_records_customer_id ON service_history_records (customer_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_service_history_records_vehicle_id ON service_history_records (vehicle_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_service_history_records_vehicle_mileage_date ON service_history_records (vehicle_id, mileage, service_date)")

        # Pro groundwork: maintenance reminders for future CRM modules only.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS maintenance_reminders (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              customer_id INTEGER NOT NULL,
              vehicle_id INTEGER NOT NULL,
              shop_id INTEGER,
              service_type TEXT,
              due_date TEXT,
              due_mileage INTEGER,
              reminder_status TEXT NOT NULL CHECK (reminder_status IN ('pending', 'notified', 'completed', 'dismissed')),
              last_notified_at TEXT,
              notes TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY (customer_id) REFERENCES customers(id),
              FOREIGN KEY (vehicle_id) REFERENCES customer_vehicles(id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_maintenance_reminders_customer_id ON maintenance_reminders (customer_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_maintenance_reminders_vehicle_id ON maintenance_reminders (vehicle_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_maintenance_reminders_shop_id ON maintenance_reminders (shop_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_maintenance_reminders_due_date ON maintenance_reminders (due_date)")

        # Pro maintenance reminder action tracking only; no external sending.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS maintenance_reminder_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              customer_id INTEGER NOT NULL,
              vehicle_id INTEGER NOT NULL,
              maintenance_record_id INTEGER NOT NULL,
              service_type TEXT,
              status TEXT NOT NULL CHECK (status IN ('drafted', 'copied', 'marked_sent', 'snoozed', 'customer_replied', 'completed')),
              method TEXT NOT NULL CHECK (method IN ('manual', 'sms', 'email', 'phone', 'other')),
              message TEXT,
              created_at TEXT NOT NULL,
              sent_at TEXT,
              snoozed_until TEXT,
              notes TEXT,
              FOREIGN KEY (customer_id) REFERENCES customers(id),
              FOREIGN KEY (vehicle_id) REFERENCES customer_vehicles(id),
              FOREIGN KEY (maintenance_record_id) REFERENCES maintenance_records(id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_maintenance_reminder_events_customer_id ON maintenance_reminder_events (customer_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_maintenance_reminder_events_vehicle_id ON maintenance_reminder_events (vehicle_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_maintenance_reminder_events_record_id ON maintenance_reminder_events (maintenance_record_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_maintenance_reminder_events_status ON maintenance_reminder_events (status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_maintenance_reminder_events_snoozed_until ON maintenance_reminder_events (snoozed_until)")

        # Pro groundwork: performed maintenance records for future follow-up workflows.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS maintenance_records (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              customer_id INTEGER NOT NULL,
              vehicle_id INTEGER NOT NULL,
              shop_id INTEGER,
              service_type TEXT,
              date_performed TEXT,
              mileage_performed INTEGER,
              interval_miles INTEGER,
              interval_months INTEGER,
              due_mileage INTEGER,
              due_date TEXT,
              notes TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY (customer_id) REFERENCES customers(id),
              FOREIGN KEY (vehicle_id) REFERENCES customer_vehicles(id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_maintenance_records_customer_id ON maintenance_records (customer_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_maintenance_records_vehicle_id ON maintenance_records (vehicle_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_maintenance_records_shop_id ON maintenance_records (shop_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_maintenance_records_date_performed ON maintenance_records (date_performed)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_maintenance_records_vehicle_date ON maintenance_records (vehicle_id, date_performed)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_maintenance_records_vehicle_mileage_date ON maintenance_records (vehicle_id, mileage_performed, date_performed)")
        add_column_if_missing("maintenance_records", "due_mileage", "due_mileage INTEGER")
        add_column_if_missing("maintenance_records", "due_date", "due_date TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_maintenance_records_due_mileage ON maintenance_records (due_mileage)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_maintenance_records_due_date ON maintenance_records (due_date)")

        # Pro groundwork: completed repair records remain separate from maintenance.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS repair_records (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              vehicle_id INTEGER NOT NULL,
              customer_id INTEGER NOT NULL,
              repair_name TEXT,
              repair_date TEXT,
              mileage INTEGER,
              labor_hours REAL,
              parts_cost REAL,
              labor_cost REAL,
              total_cost REAL,
              track_as_maintenance INTEGER NOT NULL DEFAULT 0,
              workflow_source_type TEXT,
              workflow_source_id INTEGER,
              parts_search_term TEXT,
              pricing_mode TEXT,
              flat_rate_price REAL,
              approved_estimate_total REAL,
              status TEXT NOT NULL DEFAULT 'Open',
              completed_at TEXT,
              notes TEXT,
              created_at TEXT NOT NULL,
              FOREIGN KEY (vehicle_id) REFERENCES customer_vehicles(id),
              FOREIGN KEY (customer_id) REFERENCES customers(id)
            )
            """
        )
        add_column_if_missing("repair_records", "track_as_maintenance", "track_as_maintenance INTEGER NOT NULL DEFAULT 0")
        add_column_if_missing("repair_records", "workflow_source_type", "workflow_source_type TEXT")
        add_column_if_missing("repair_records", "workflow_source_id", "workflow_source_id INTEGER")
        add_column_if_missing("repair_records", "parts_search_term", "parts_search_term TEXT")
        add_column_if_missing("repair_records", "pricing_mode", "pricing_mode TEXT")
        add_column_if_missing("repair_records", "flat_rate_price", "flat_rate_price REAL")
        add_column_if_missing("repair_records", "approved_estimate_total", "approved_estimate_total REAL")
        add_column_if_missing("repair_records", "labor_rate", "labor_rate REAL")
        add_column_if_missing("repair_records", "status", "status TEXT NOT NULL DEFAULT 'Open'")
        add_column_if_missing("repair_records", "completed_at", "completed_at TEXT")
        conn.execute(
            """
            UPDATE repair_records
            SET status = 'Open'
            WHERE status IS NULL OR TRIM(status) = ''
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_repair_records_customer_id ON repair_records (customer_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_repair_records_vehicle_id ON repair_records (vehicle_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_repair_records_vehicle_date_mileage ON repair_records (vehicle_id, repair_date, mileage)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS repair_job_parts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              repair_record_id INTEGER NOT NULL,
              part_name TEXT NOT NULL,
              qty REAL NOT NULL DEFAULT 1,
              vendor TEXT,
              part_number TEXT,
              unit_cost REAL NOT NULL DEFAULT 0,
              subtotal REAL NOT NULL DEFAULT 0,
              status TEXT NOT NULL DEFAULT 'Needed',
              notes TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY (repair_record_id) REFERENCES repair_records(id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_repair_job_parts_repair_record_id ON repair_job_parts (repair_record_id)")
        conn.execute("DROP INDEX IF EXISTS idx_repair_records_workflow_source")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_repair_records_workflow_source
            ON repair_records (workflow_source_type, workflow_source_id)
            WHERE workflow_source_type IS NOT NULL
              AND TRIM(workflow_source_type) != ''
              AND workflow_source_id IS NOT NULL
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS repair_checklist_items (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              repair_record_id INTEGER NOT NULL,
              task_name TEXT NOT NULL,
              task_order INTEGER NOT NULL DEFAULT 0,
              completed INTEGER NOT NULL DEFAULT 0,
              completed_at TEXT,
              notes TEXT,
              created_at TEXT NOT NULL,
              FOREIGN KEY (repair_record_id) REFERENCES repair_records(id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_repair_checklist_items_repair_record_id "
            "ON repair_checklist_items (repair_record_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_repair_checklist_items_completed_at "
            "ON repair_checklist_items (completed_at)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS repair_completions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              repair_record_id INTEGER NOT NULL UNIQUE,
              torque_verified INTEGER NOT NULL DEFAULT 0,
              fluids_verified INTEGER NOT NULL DEFAULT 0,
              leaks_checked INTEGER NOT NULL DEFAULT 0,
              codes_cleared INTEGER NOT NULL DEFAULT 0,
              road_test_completed INTEGER NOT NULL DEFAULT 0,
              customer_concern_resolved INTEGER NOT NULL DEFAULT 0,
              completion_date TEXT,
              completion_mileage INTEGER,
              technician_notes TEXT,
              completion_notes TEXT,
              final_inspection_passed INTEGER NOT NULL DEFAULT 0,
              final_inspection_notes TEXT,
              after_repair_photo_paths TEXT,
              override_reason TEXT,
              completed_at TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY (repair_record_id) REFERENCES repair_records(id)
            )
            """
        )
        add_column_if_missing("repair_completions", "completion_date", "completion_date TEXT")
        add_column_if_missing("repair_completions", "completion_mileage", "completion_mileage INTEGER")
        add_column_if_missing("repair_completions", "technician_notes", "technician_notes TEXT")
        add_column_if_missing("repair_completions", "torque_verified", "torque_verified INTEGER NOT NULL DEFAULT 0")
        add_column_if_missing("repair_completions", "fluids_verified", "fluids_verified INTEGER NOT NULL DEFAULT 0")
        add_column_if_missing("repair_completions", "leaks_checked", "leaks_checked INTEGER NOT NULL DEFAULT 0")
        add_column_if_missing("repair_completions", "codes_cleared", "codes_cleared INTEGER NOT NULL DEFAULT 0")
        add_column_if_missing("repair_completions", "road_test_completed", "road_test_completed INTEGER NOT NULL DEFAULT 0")
        add_column_if_missing("repair_completions", "customer_concern_resolved", "customer_concern_resolved INTEGER NOT NULL DEFAULT 0")
        add_column_if_missing("repair_completions", "final_inspection_passed", "final_inspection_passed INTEGER NOT NULL DEFAULT 0")
        add_column_if_missing("repair_completions", "final_inspection_notes", "final_inspection_notes TEXT")
        add_column_if_missing("repair_completions", "after_repair_photo_paths", "after_repair_photo_paths TEXT")
        add_column_if_missing("repair_completions", "override_reason", "override_reason TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_repair_completions_repair_record_id "
            "ON repair_completions (repair_record_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_repair_completions_completed_at "
            "ON repair_completions (completed_at)"
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS invoices (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              invoice_number TEXT NOT NULL UNIQUE,
              repair_record_id INTEGER NOT NULL UNIQUE,
              customer_id INTEGER NOT NULL,
              vehicle_id INTEGER NOT NULL,
              labor_total REAL NOT NULL DEFAULT 0,
              parts_total REAL NOT NULL DEFAULT 0,
              grand_total REAL NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              FOREIGN KEY (repair_record_id) REFERENCES repair_records(id),
              FOREIGN KEY (customer_id) REFERENCES customers(id),
              FOREIGN KEY (vehicle_id) REFERENCES customer_vehicles(id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_invoices_customer_id ON invoices (customer_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_invoices_vehicle_id ON invoices (vehicle_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_invoices_created_at ON invoices (created_at)")

        # Pro groundwork: mechanic-authored inspection findings and recommendations.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS findings_records (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              vehicle_id INTEGER NOT NULL,
              customer_id INTEGER NOT NULL,
              finding TEXT,
              recommendation TEXT,
              customer_notes TEXT,
              internal_notes TEXT,
              request_type TEXT NOT NULL DEFAULT 'finding',
              labor_description TEXT,
              labor_hours REAL,
              labor_rate REAL,
              labor_amount REAL,
              labor_reason TEXT,
              severity TEXT NOT NULL CHECK (severity IN ('Low', 'Medium', 'High', 'Critical')),
              status TEXT NOT NULL CHECK (status IN ('Open', 'Approved', 'Declined', 'Deferred', 'Completed')),
              repair_work_status TEXT,
              repair_work_updated_at TEXT,
              linked_repair_record_id INTEGER,
              repair_record_created_at TEXT,
              mileage INTEGER,
              finding_date TEXT,
              created_at TEXT NOT NULL,
              FOREIGN KEY (vehicle_id) REFERENCES customer_vehicles(id),
              FOREIGN KEY (customer_id) REFERENCES customers(id)
            )
            """
        )
        add_column_if_missing("findings_records", "customer_notes", "customer_notes TEXT")
        add_column_if_missing("findings_records", "internal_notes", "internal_notes TEXT")
        add_column_if_missing("findings_records", "request_type", "request_type TEXT NOT NULL DEFAULT 'finding'")
        add_column_if_missing("findings_records", "labor_description", "labor_description TEXT")
        add_column_if_missing("findings_records", "labor_hours", "labor_hours REAL")
        add_column_if_missing("findings_records", "labor_rate", "labor_rate REAL")
        add_column_if_missing("findings_records", "labor_amount", "labor_amount REAL")
        add_column_if_missing("findings_records", "labor_reason", "labor_reason TEXT")
        add_column_if_missing("findings_records", "repair_work_status", "repair_work_status TEXT")
        add_column_if_missing("findings_records", "repair_work_updated_at", "repair_work_updated_at TEXT")
        add_column_if_missing("findings_records", "linked_repair_record_id", "linked_repair_record_id INTEGER")
        add_column_if_missing("findings_records", "repair_record_created_at", "repair_record_created_at TEXT")
        conn.execute(
            """
            UPDATE findings_records
            SET request_type = 'finding'
            WHERE request_type IS NULL OR TRIM(request_type) = ''
            """
        )
        conn.execute(
            """
            UPDATE findings_records
            SET repair_work_status = CASE WHEN status = 'Completed' THEN 'completed' ELSE 'ready' END,
                repair_work_updated_at = COALESCE(NULLIF(repair_work_updated_at, ''), created_at)
            WHERE status IN ('Approved', 'Completed')
              AND (repair_work_status IS NULL OR TRIM(repair_work_status) = '')
            """
        )
        repair_table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'repair_records'"
        ).fetchone()
        if repair_table_exists:
            conn.execute(
                """
                UPDATE findings_records
                SET linked_repair_record_id = (
                    SELECT rr.id
                    FROM repair_records rr
                    WHERE rr.workflow_source_type = 'finding'
                      AND rr.workflow_source_id = findings_records.id
                    LIMIT 1
                )
                WHERE linked_repair_record_id IS NULL
                  AND EXISTS (
                    SELECT 1
                    FROM repair_records rr
                    WHERE rr.workflow_source_type = 'finding'
                      AND rr.workflow_source_id = findings_records.id
                  )
                """
            )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_findings_records_customer_id ON findings_records (customer_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_findings_records_vehicle_id ON findings_records (vehicle_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_findings_records_vehicle_mileage_date ON findings_records (vehicle_id, mileage, finding_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_findings_records_status ON findings_records (status)")

        # Pro audit trail: append-only decision history for repair findings.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS finding_history_records (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              finding_id INTEGER NOT NULL,
              previous_status TEXT,
              new_status TEXT NOT NULL,
              event_type TEXT NOT NULL,
              actor_name TEXT,
              notes TEXT,
              metadata_json TEXT,
              created_at TEXT NOT NULL,
              FOREIGN KEY (finding_id) REFERENCES findings_records(id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_finding_history_records_finding_id ON finding_history_records (finding_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_finding_history_records_created_at ON finding_history_records (created_at)")

        # Pro approval groundwork: lightweight customer decision logs for findings.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS customer_decision_logs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              finding_id INTEGER NOT NULL,
              decision_status TEXT NOT NULL,
              customer_name TEXT,
              source TEXT NOT NULL,
              approval_method TEXT,
              advisor_name TEXT,
              signature_path TEXT,
              approval_pdf_path TEXT,
              estimate_revision_id INTEGER,
              notes TEXT,
              metadata_json TEXT,
              created_at TEXT NOT NULL,
              FOREIGN KEY (finding_id) REFERENCES findings_records(id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_customer_decision_logs_finding_id ON customer_decision_logs (finding_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_customer_decision_logs_created_at ON customer_decision_logs (created_at)")

        # Pro groundwork: additional findings and customer decision records.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS discrepancy_approvals (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              customer_id INTEGER NOT NULL,
              vehicle_id INTEGER NOT NULL,
              service_history_id INTEGER,
              shop_id INTEGER,
              request_type TEXT NOT NULL DEFAULT 'finding',
              finding_title TEXT,
              finding_description TEXT,
              recommended_repair TEXT,
              estimated_cost REAL,
              labor_hours REAL,
              labor_rate REAL,
              labor_amount REAL,
              labor_reason TEXT,
              part_description TEXT,
              part_name TEXT,
              part_number TEXT,
              quantity REAL,
              unit_cost REAL,
              parts_amount REAL,
              parts_total REAL,
              customer_decision TEXT NOT NULL CHECK (customer_decision IN ('pending', 'approved', 'declined', 'deferred')),
              repair_work_status TEXT,
              repair_work_updated_at TEXT,
              linked_repair_record_id INTEGER,
              repair_record_created_at TEXT,
              decision_notes TEXT,
              decision_recorded_at TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY (customer_id) REFERENCES customers(id),
              FOREIGN KEY (vehicle_id) REFERENCES customer_vehicles(id),
              FOREIGN KEY (service_history_id) REFERENCES service_history(id)
            )
            """
        )
        add_column_if_missing("discrepancy_approvals", "request_type", "request_type TEXT NOT NULL DEFAULT 'finding'")
        add_column_if_missing("discrepancy_approvals", "labor_hours", "labor_hours REAL")
        add_column_if_missing("discrepancy_approvals", "labor_rate", "labor_rate REAL")
        add_column_if_missing("discrepancy_approvals", "labor_amount", "labor_amount REAL")
        add_column_if_missing("discrepancy_approvals", "labor_reason", "labor_reason TEXT")
        add_column_if_missing("discrepancy_approvals", "part_description", "part_description TEXT")
        add_column_if_missing("discrepancy_approvals", "part_name", "part_name TEXT")
        add_column_if_missing("discrepancy_approvals", "part_number", "part_number TEXT")
        add_column_if_missing("discrepancy_approvals", "quantity", "quantity REAL")
        add_column_if_missing("discrepancy_approvals", "unit_cost", "unit_cost REAL")
        add_column_if_missing("discrepancy_approvals", "parts_amount", "parts_amount REAL")
        add_column_if_missing("discrepancy_approvals", "parts_total", "parts_total REAL")
        add_column_if_missing("discrepancy_approvals", "repair_work_status", "repair_work_status TEXT")
        add_column_if_missing("discrepancy_approvals", "repair_work_updated_at", "repair_work_updated_at TEXT")
        add_column_if_missing("discrepancy_approvals", "linked_repair_record_id", "linked_repair_record_id INTEGER")
        add_column_if_missing("discrepancy_approvals", "repair_record_created_at", "repair_record_created_at TEXT")
        conn.execute(
            """
            UPDATE discrepancy_approvals
            SET request_type = 'finding'
            WHERE request_type IS NULL OR TRIM(request_type) = '' OR request_type = 'general'
            """
        )
        conn.execute(
            """
            UPDATE discrepancy_approvals
            SET part_name = COALESCE(NULLIF(part_name, ''), part_description),
                parts_total = COALESCE(parts_total, parts_amount)
            WHERE request_type = 'parts'
            """
        )
        conn.execute(
            """
            UPDATE discrepancy_approvals
            SET repair_work_status = 'ready',
                repair_work_updated_at = COALESCE(NULLIF(repair_work_updated_at, ''), decision_recorded_at, updated_at, created_at)
            WHERE customer_decision = 'approved'
              AND (repair_work_status IS NULL OR TRIM(repair_work_status) = '')
            """
        )
        repair_table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'repair_records'"
        ).fetchone()
        if repair_table_exists:
            conn.execute(
                """
                UPDATE discrepancy_approvals
                SET linked_repair_record_id = (
                    SELECT rr.id
                    FROM repair_records rr
                    WHERE rr.workflow_source_type = 'approval'
                      AND rr.workflow_source_id = discrepancy_approvals.id
                    LIMIT 1
                )
                WHERE linked_repair_record_id IS NULL
                  AND EXISTS (
                    SELECT 1
                    FROM repair_records rr
                    WHERE rr.workflow_source_type = 'approval'
                      AND rr.workflow_source_id = discrepancy_approvals.id
                  )
                """
            )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_discrepancy_approvals_customer_id ON discrepancy_approvals (customer_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_discrepancy_approvals_vehicle_id ON discrepancy_approvals (vehicle_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_discrepancy_approvals_service_history_id ON discrepancy_approvals (service_history_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_discrepancy_approvals_decision ON discrepancy_approvals (customer_decision)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_discrepancy_approvals_created_at ON discrepancy_approvals (created_at)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS discrepancy_approval_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              approval_id INTEGER NOT NULL,
              customer_id INTEGER NOT NULL,
              vehicle_id INTEGER NOT NULL,
              event_type TEXT NOT NULL,
              event_label TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY (approval_id) REFERENCES discrepancy_approvals(id),
              FOREIGN KEY (customer_id) REFERENCES customers(id),
              FOREIGN KEY (vehicle_id) REFERENCES customer_vehicles(id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_discrepancy_approval_events_vehicle ON discrepancy_approval_events (vehicle_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_discrepancy_approval_events_approval ON discrepancy_approval_events (approval_id)")

        # Visual Reference Library: vehicle/service-specific reference media, specs, and OEM numbers.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS visual_reference_records (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              vehicle_identifier TEXT NOT NULL,
              service_type TEXT NOT NULL,
              title TEXT,
              quick_reference TEXT,
              created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS visual_reference_images (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              visual_reference_id INTEGER NOT NULL,
              image_type TEXT NOT NULL CHECK (
                image_type IN (
                  'component_location',
                  'exploded_view',
                  'belt_routing',
                  'connector_view',
                  'reference_image'
                )
              ),
              image_path TEXT NOT NULL,
              caption TEXT,
              FOREIGN KEY (visual_reference_id) REFERENCES visual_reference_records(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS visual_reference_specs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              visual_reference_id INTEGER NOT NULL,
              spec_name TEXT NOT NULL,
              spec_value TEXT NOT NULL,
              spec_unit TEXT,
              FOREIGN KEY (visual_reference_id) REFERENCES visual_reference_records(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS visual_reference_oem_parts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              visual_reference_id INTEGER NOT NULL,
              part_name TEXT NOT NULL,
              oem_part_number TEXT NOT NULL,
              future_parts_intelligence_id INTEGER,
              FOREIGN KEY (visual_reference_id) REFERENCES visual_reference_records(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS visual_reference_hotspots (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              visual_reference_id INTEGER NOT NULL,
              label TEXT NOT NULL,
              hotspot_type TEXT NOT NULL,
              x_percent REAL NOT NULL,
              y_percent REAL NOT NULL,
              title TEXT NOT NULL,
              description TEXT,
              torque_spec TEXT,
              fastener_size TEXT,
              tool_size TEXT,
              oem_part_number TEXT,
              related_part_name TEXT,
              parts_intelligence_id INTEGER,
              sort_order INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              FOREIGN KEY (visual_reference_id) REFERENCES visual_reference_records(id)
            )
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_visual_reference_records_vehicle_service
            ON visual_reference_records (vehicle_identifier, service_type)
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_visual_reference_records_vehicle ON visual_reference_records (vehicle_identifier)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_visual_reference_records_service ON visual_reference_records (service_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_visual_reference_images_reference ON visual_reference_images (visual_reference_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_visual_reference_specs_reference ON visual_reference_specs (visual_reference_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_visual_reference_oem_parts_reference ON visual_reference_oem_parts (visual_reference_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_visual_reference_hotspots_reference ON visual_reference_hotspots (visual_reference_id, sort_order)")

        conn.commit()
    finally:
        conn.close()


def normalize_shop_profile(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    profile = dict(DEFAULT_SHOP_PROFILE)
    if raw:
        for key in profile:
            value = raw.get(key)
            if value is not None:
                profile[key] = value

    for key in ("shop_name", "phone", "email", "address", "website", "scheduling_link", "logo_url", "warranty_note", "custom_footer_note"):
        profile[key] = str(profile.get(key) or "").strip()

    try:
        profile["labor_rate_default"] = max(0.0, float(profile.get("labor_rate_default") or 0.0))
    except (TypeError, ValueError):
        profile["labor_rate_default"] = DEFAULT_SHOP_PROFILE["labor_rate_default"]

    try:
        profile["tax_rate_default"] = max(0.0, float(profile.get("tax_rate_default") or 0.0))
    except (TypeError, ValueError):
        profile["tax_rate_default"] = DEFAULT_SHOP_PROFILE["tax_rate_default"]

    try:
        profile["quote_expiration_days"] = max(0, int(profile.get("quote_expiration_days") or 0))
    except (TypeError, ValueError):
        profile["quote_expiration_days"] = DEFAULT_SHOP_PROFILE["quote_expiration_days"]

    return profile


def load_shop_profile() -> Dict[str, Any]:
    try:
        init_shop_profile_db()
        conn = app_db_conn(row_factory=True)
        try:
            row = conn.execute("SELECT * FROM shop_profile WHERE id = 1").fetchone()
            return normalize_shop_profile(dict(row) if row else None)
        finally:
            conn.close()
    except Exception:
        logging.exception("SHOP_PROFILE_LOAD_FAILED")
        return normalize_shop_profile(None)


def save_shop_profile(profile_data: Dict[str, Any]) -> Dict[str, Any]:
    profile = normalize_shop_profile(profile_data)
    init_shop_profile_db()
    conn = app_db_conn()
    try:
        conn.execute(
            """
            INSERT INTO shop_profile (
              id, shop_name, phone, email, address, website, scheduling_link, logo_url,
              labor_rate_default, tax_rate_default, warranty_note,
              quote_expiration_days, custom_footer_note, updated_at
            )
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              shop_name = excluded.shop_name,
              phone = excluded.phone,
              email = excluded.email,
              address = excluded.address,
              website = excluded.website,
              scheduling_link = excluded.scheduling_link,
              logo_url = excluded.logo_url,
              labor_rate_default = excluded.labor_rate_default,
              tax_rate_default = excluded.tax_rate_default,
              warranty_note = excluded.warranty_note,
              quote_expiration_days = excluded.quote_expiration_days,
              custom_footer_note = excluded.custom_footer_note,
              updated_at = excluded.updated_at
            """,
            (
                profile["shop_name"],
                profile["phone"],
                profile["email"],
                profile["address"],
                profile["website"],
                profile["scheduling_link"],
                profile["logo_url"],
                profile["labor_rate_default"],
                profile["tax_rate_default"],
                profile["warranty_note"],
                profile["quote_expiration_days"],
                profile["custom_footer_note"],
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return profile


def normalize_repair_guide_slug(value: str) -> str:
    return (value or "").strip().lower().replace("_", "-")


def normalize_torque_lookup_key(value: str) -> str:
    return " ".join(str(value or "").strip().upper().split())


def normalize_repair_guide_torque_specs_map(raw_specs: Any) -> Dict[str, str]:
    if not isinstance(raw_specs, dict):
        return {}

    normalized_specs: Dict[str, str] = {}
    for label, value in raw_specs.items():
        label_text = str(label or "").strip()
        value_text = normalize_torque_spec_value(value)
        if label_text and value_text:
            normalized_specs[label_text] = value_text
    return normalized_specs


def parse_torque_lookup_year(value: str | int) -> Optional[int]:
    try:
        return int(str(value or "").strip())
    except (TypeError, ValueError):
        return None


def expand_torque_guide_targets(targets: Any, guide_sets: Dict[str, Any]) -> List[str]:
    expanded: List[str] = []

    if isinstance(targets, str):
        if targets in guide_sets and isinstance(guide_sets[targets], list):
            for item in guide_sets[targets]:
                slug = normalize_repair_guide_slug(item)
                if slug:
                    expanded.append(slug)
        else:
            slug = normalize_repair_guide_slug(targets)
            if slug:
                expanded.append(slug)
    elif isinstance(targets, list):
        for item in targets:
            expanded.extend(expand_torque_guide_targets(item, guide_sets))

    return expanded


def torque_entry_matches_year(entry: Dict[str, Any], year_value: int) -> bool:
    years = entry.get("years")
    if isinstance(years, list):
        normalized_years = {
            parsed_year
            for item in years
            if (parsed_year := parse_torque_lookup_year(item)) is not None
        }
        if year_value in normalized_years:
            return True

    year_range = entry.get("year_range")
    if isinstance(year_range, dict):
        start_year = parse_torque_lookup_year(year_range.get("start"))
        end_year = parse_torque_lookup_year(year_range.get("end"))
        if start_year is not None and end_year is not None:
            return start_year <= year_value <= end_year

    return False


def lookup_structured_repair_guide_torque_specs(
    data: Dict[str, Any],
    guide_slug: str,
    year: str | int,
    make: str,
    model: str,
) -> Dict[str, str]:
    entries = data.get("entries")
    if not isinstance(entries, list):
        return {}

    year_value = parse_torque_lookup_year(year)
    make_key = normalize_torque_lookup_key(make)
    model_key = normalize_torque_lookup_key(model)
    if year_value is None or not make_key or not model_key:
        return {}

    guide_sets = data.get("guide_sets")
    if not isinstance(guide_sets, dict):
        guide_sets = {}

    matched_specs: Dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue

        entry_make = normalize_torque_lookup_key(entry.get("make", ""))
        entry_model = normalize_torque_lookup_key(entry.get("model", ""))
        if entry_make != make_key or entry_model != model_key:
            continue

        entry_guides = expand_torque_guide_targets(
            entry.get("guides") or entry.get("guide") or [],
            guide_sets,
        )
        if guide_slug not in entry_guides:
            continue

        if not torque_entry_matches_year(entry, year_value):
            continue

        matched_specs.update(normalize_repair_guide_torque_specs_map(entry.get("specs")))

    return matched_specs


def get_repair_guide_vehicle_torque_specs(
    guide_slug: str,
    year: str | int,
    make: str,
    model: str,
) -> Dict[str, str]:
    data = load_repair_guide_torque_specs()
    slug_key = normalize_repair_guide_slug(guide_slug)
    year_key = str(year or "").strip()
    make_key = normalize_torque_lookup_key(make)
    model_key = normalize_torque_lookup_key(model)

    if not (slug_key and year_key and make_key and model_key):
        return {}

    if isinstance(data.get("entries"), list):
        return lookup_structured_repair_guide_torque_specs(
            data,
            slug_key,
            year_key,
            make_key,
            model_key,
        )

    return normalize_repair_guide_torque_specs_map(
        data.get(slug_key, {})
        .get(year_key, {})
        .get(make_key, {})
        .get(model_key, {})
    )

# --- Templates ---
def static_version(asset_path: str) -> int:
    rel_path = str(asset_path or "").split("?", 1)[0].lstrip("/")
    if rel_path.startswith("static/"):
        rel_path = rel_path[len("static/"):]

    try:
        asset_file = (STATIC_DIR / rel_path).resolve()
        asset_file.relative_to(STATIC_DIR.resolve())
        return int(asset_file.stat().st_mtime)
    except Exception:
        return 0

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["static_version"] = static_version
app.state.templates = templates

# routers
app.include_router(knowledge_router)
app.include_router(booking_router)
app.include_router(pro_router)

# --- Static Mount ---
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(STATIC_DIR / "favicon.ico")

@app.get("/static/visual-references/uploads/{filename:path}", include_in_schema=False)
async def visual_reference_upload_file(filename: str):
    storage = configured_storage_paths()
    upload_path = resolve_storage_child(storage.visual_reference_uploads_dir, filename)
    if not upload_path.exists() or not upload_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(upload_path)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.middleware("http")
async def add_request_id_middleware(request: Request, call_next):
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    token = _request_id_ctx.set(rid)
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response
    finally:
        _request_id_ctx.reset(token)


@app.middleware("http")
async def auth_context_middleware(request: Request, call_next):
    request.state.current_user = None
    request.state.current_shop = {}
    request.state.subscription_access = {}
    try:
        conn = app_db_conn(row_factory=True)
        try:
            user = current_user(conn, request)
            request.state.current_user = user
            if user:
                request.state.current_shop = current_shop_context(conn, request)
                request.state.subscription_access = shop_subscription_access_context(
                    conn,
                    int(request.state.current_shop.get("id") or 0) or None,
                )
        finally:
            conn.close()
    except Exception:
        logging.exception("AUTH_CONTEXT_LOAD_FAILED")
    return await call_next(request)


@app.middleware("http")
async def pro_private_access_middleware(request: Request, call_next):
    path = request.url.path
    if path != "/pro" and not path.startswith("/pro/"):
        return await call_next(request)
    if path == "/pro/billing/webhook":
        return await call_next(request)

    async def continue_if_authenticated():
        try:
            conn = app_db_conn(row_factory=True)
            try:
                ensure_auth_schema(conn)
                user_count = conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"]
                session_data = request.scope.get("session")
                if not isinstance(session_data, dict):
                    session_data = load_server_session_data(request.cookies.get(SESSION_COOKIE_NAME, ""))
                    request.scope["session"] = session_data
                user = current_user(conn, request) if session_data.get(AUTH_SESSION_USER_KEY) else None
            finally:
                conn.close()
            if int(user_count or 0) == 0:
                return await call_next(request)
        except Exception:
            logging.exception("AUTH_BOOTSTRAP_CHECK_FAILED")
            user = None
        if user and user_email_verified(user):
            return await call_next(request)
        if user:
            return RedirectResponse("/check-email", status_code=303)
        return_to = safe_next_url(str(request.url.path) + (f"?{request.url.query}" if request.url.query else ""))
        suffix = f"?{urlencode({'next': return_to})}" if return_to else ""
        return RedirectResponse(f"/login{suffix}", status_code=303)

    access_state = pro_request_access_state(request)
    qa_key = access_state["qa_key"]
    qa_key_present = access_state["qa_key_present"]
    qa_param_present = access_state["qa_param_present"]
    qa_cookie_valid = access_state["qa_cookie_valid"]
    qa_key_matched = access_state["qa_key_matched"]

    if is_local_pro_request(request):
        log_pro_qa_gate(
            request,
            pro_qa_key_present=qa_key_present,
            qa_key_param_present=qa_param_present,
            qa_key_matched=qa_key_matched,
            qa_cookie_valid=qa_cookie_valid,
            access_allowed=True,
        )
        return await continue_if_authenticated()

    if qa_cookie_valid or qa_key_matched:
        log_pro_qa_gate(
            request,
            pro_qa_key_present=qa_key_present,
            qa_key_param_present=qa_param_present,
            qa_key_matched=qa_key_matched,
            qa_cookie_valid=qa_cookie_valid,
            access_allowed=True,
        )
        response = await continue_if_authenticated()
        if qa_key_matched:
            response.set_cookie(
                PRO_QA_ACCESS_COOKIE,
                pro_qa_access_signature(qa_key),
                max_age=60 * 60 * 8,
                httponly=True,
                secure=request.url.scheme == "https",
                samesite="lax",
            )
        return response

    code = access_state["legacy_access_code"]
    if code:
        if access_state["legacy_cookie_valid"]:
            log_pro_qa_gate(
                request,
                pro_qa_key_present=qa_key_present,
                qa_key_param_present=qa_param_present,
                qa_key_matched=qa_key_matched,
                qa_cookie_valid=qa_cookie_valid,
                access_allowed=True,
            )
            return await continue_if_authenticated()
        if request.method.upper() == "POST":
            raw_body = (await request.body()).decode("utf-8", errors="replace")
            parsed = parse_qs(raw_body, keep_blank_values=True)
            submitted_code = (parsed.get("pro_access_code") or [""])[0].strip()
            if hmac.compare_digest(submitted_code, code):
                log_pro_qa_gate(
                    request,
                    pro_qa_key_present=qa_key_present,
                    qa_key_param_present=qa_param_present,
                    qa_key_matched=qa_key_matched,
                    qa_cookie_valid=qa_cookie_valid,
                    access_allowed=True,
                )
                response = RedirectResponse(str(request.url), status_code=303)
                response.set_cookie(
                    PRO_ACCESS_COOKIE,
                    pro_access_signature(code),
                    max_age=60 * 60 * 8,
                    httponly=True,
                    secure=request.url.scheme == "https",
                    samesite="lax",
                )
                return response
            log_pro_qa_gate(
                request,
                pro_qa_key_present=qa_key_present,
                qa_key_param_present=qa_param_present,
                qa_key_matched=qa_key_matched,
                qa_cookie_valid=qa_cookie_valid,
                access_allowed=False,
            )
            return pro_private_response("Invalid access code.")
        log_pro_qa_gate(
            request,
            pro_qa_key_present=qa_key_present,
            qa_key_param_present=qa_param_present,
            qa_key_matched=qa_key_matched,
            qa_cookie_valid=qa_cookie_valid,
            access_allowed=False,
        )
        return pro_private_response()

    if not pro_enabled():
        log_pro_qa_gate(
            request,
            pro_qa_key_present=qa_key_present,
            qa_key_param_present=qa_param_present,
            qa_key_matched=qa_key_matched,
            qa_cookie_valid=qa_cookie_valid,
            access_allowed=False,
        )
        return pro_blocked_response()

    log_pro_qa_gate(
        request,
        pro_qa_key_present=qa_key_present,
        qa_key_param_present=qa_param_present,
        qa_key_matched=qa_key_matched,
        qa_cookie_valid=qa_cookie_valid,
        access_allowed=True,
    )
    return await continue_if_authenticated()


def auth_form_values(form: dict[str, str]) -> dict[str, str]:
    return {
        "first_name": str(form.get("first_name") or "").strip(),
        "last_name": str(form.get("last_name") or "").strip(),
        "email": normalize_email(form.get("email")),
        "shop_name": str(form.get("shop_name") or "").strip(),
    }


def password_rules_error(password: str) -> str:
    if len(str(password or "")) < 8:
        return "Password must be at least 8 characters."
    return ""


def email_format_error(email: str) -> str:
    clean = normalize_email(email)
    if not clean:
        return "Email address is required."
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", clean):
        return "Enter a valid email address."
    return ""


def account_full_name(user: dict[str, Any] | None) -> str:
    first_name = str((user or {}).get("first_name") or "").strip()
    last_name = str((user or {}).get("last_name") or "").strip()
    return " ".join(part for part in (first_name, last_name) if part).strip()


def split_account_full_name(full_name: str) -> tuple[str, str]:
    clean_name = re.sub(r"\s+", " ", str(full_name or "").strip())
    if not clean_name:
        return "", ""
    first_name, sep, last_name = clean_name.partition(" ")
    return first_name, last_name if sep else ""


def account_phone_digits(value: Any) -> str:
    raw = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(raw) == 11 and raw.startswith("1"):
        raw = raw[1:]
    return raw


def format_account_phone(value: Any) -> str:
    digits = account_phone_digits(value)
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return str(value or "").strip()


def validate_account_phone(value: Any) -> tuple[str, str]:
    submitted = str(value or "").strip()
    if not submitted:
        return "", ""
    digits = account_phone_digits(submitted)
    if len(digits) != 10:
        return submitted, "Enter a 10-digit US phone number."
    return format_account_phone(digits), ""


def format_account_created_at(user: dict[str, Any] | None) -> str:
    raw = str((user or {}).get("created_at") or "").strip()
    if not raw:
        return "Not available"
    try:
        created_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return "Not available"
    return f"{created_at.strftime('%B')} {created_at.day}, {created_at.year}"


def format_friendly_datetime(raw: Any) -> str:
    value = str(raw or "").strip()
    if not value:
        return "Not available"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return "Not available"
    return f"{parsed.strftime('%B')} {parsed.day}, {parsed.year} at {parsed.strftime('%I:%M %p').lstrip('0')}"


def account_settings_context(
    request: Request,
    *,
    user: dict[str, Any],
    errors: dict[str, str] | None = None,
    message: str = "",
    back_url: str = "",
    profile_values: dict[str, str] | None = None,
    email_change_values: dict[str, str] | None = None,
) -> dict[str, Any]:
    values = profile_values or {
        "full_name": account_full_name(user),
        "phone": format_account_phone((user or {}).get("phone")),
    }
    pending_email = normalize_email((user or {}).get("pending_email"))
    cooldown_remaining = verification_resend_cooldown_remaining(user)
    current_shop = getattr(request.state, "current_shop", {}) or {}
    shop_id = int(current_shop.get("id") or 0) if isinstance(current_shop, dict) else 0
    subscription = None
    subscription_access = getattr(request.state, "subscription_access", {}) or {}
    if shop_id:
        conn = app_db_conn(row_factory=True)
        try:
            subscription = load_shop_subscription(conn, shop_id)
        finally:
            conn.close()
    if shop_id and not subscription_access:
        conn = app_db_conn(row_factory=True)
        try:
            subscription_access = shop_subscription_access_context(conn, shop_id)
        finally:
            conn.close()
    portal_access_states = {
        "subscribed_active",
        "subscribed_canceling",
        "trial_active",
        "read_only_past_due",
        "read_only_unpaid",
    }
    can_manage_stripe_subscription = bool(
        (subscription or {}).get("stripe_customer_id")
        and subscription_access.get("access_state") in portal_access_states
    )
    billing_display = build_billing_display(
        subscription,
        subscription_access,
        display_tz=SHOP_ZONEINFO,
    )
    return {
        "request": request,
        "csrf_token": csrf_token(request),
        "user": user,
        "errors": errors or {},
        "message": message,
        "back_url": safe_next_url(back_url) or "/pro/dashboard",
        "profile_values": values,
        "email_change_values": email_change_values or {
            "new_email": "",
            "confirm_new_email": "",
        },
        "account_created": format_account_created_at(user),
        "password_changed": format_friendly_datetime((user or {}).get("password_changed_at")),
        "email_verified": user_email_verified(user),
        "pending_email": pending_email,
        "has_pending_email_change": bool(pending_email),
        "verification_cooldown_remaining": cooldown_remaining,
        "verification_resend_available": bool(not user_email_verified(user) and cooldown_remaining <= 0),
        "current_shop": current_shop,
        "billing_subscription": subscription or {},
        "billing_access": subscription_access,
        "billing_display": billing_display,
        "can_manage_stripe_subscription": can_manage_stripe_subscription,
    }


def configured_bootstrap_token() -> str:
    return (os.getenv(BOOTSTRAP_TOKEN_ENV) or "").strip()


def user_count(conn) -> int:
    ensure_auth_schema(conn)
    row = conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()
    return int(row["count"] if row else 0)


def bootstrap_token_is_valid(submitted_token: str) -> bool:
    expected = configured_bootstrap_token()
    submitted = str(submitted_token or "").strip()
    if not expected or not submitted:
        return False
    return hmac.compare_digest(submitted, expected)


def new_verification_token_record() -> tuple[str, str, str]:
    token = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii").rstrip("=")
    expires_at = (datetime.utcnow() + timedelta(hours=24)).isoformat()
    return token, verification_token_hash(token), expires_at


def password_reset_token_hash(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def new_password_reset_token_record() -> tuple[str, str, str]:
    token = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii").rstrip("=")
    expires_at = (datetime.utcnow() + timedelta(minutes=PASSWORD_RESET_TOKEN_EXPIRY_MINUTES)).isoformat()
    return token, password_reset_token_hash(token), expires_at


def ensure_password_reset_schema(conn: sqlite3.Connection) -> None:
    ensure_auth_schema(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          token_hash TEXT NOT NULL UNIQUE,
          expires_at TEXT NOT NULL,
          used_at TEXT,
          created_at TEXT NOT NULL,
          FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_user_id ON password_reset_tokens (user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_hash ON password_reset_tokens (token_hash)")
    conn.commit()


def verification_outbox_path() -> Path:
    configured = (os.getenv("TORQUEMECH_DEV_EMAIL_OUTBOX") or "").strip()
    return Path(configured) if configured else STATE_DIR / "email_outbox.jsonl"


def auth_email_service_config() -> email_service.EmailServiceConfig:
    transport = email_service.normalize_transport(os.getenv("TORQUEMECH_EMAIL_TRANSPORT"))
    feedback_email = str(FEEDBACK_EMAIL or "").strip()
    return email_service.EmailServiceConfig(
        transport=transport,
        smtp_server=str(SMTP_SERVER or "").strip(),
        smtp_port=int(SMTP_PORT or 587),
        smtp_user=str(SMTP_USER or "").strip(),
        smtp_pass=str(SMTP_PASS or ""),
        resend_api_key=(os.getenv(RESEND_API_KEY_ENV) or "").strip(),
        dev_outbox_path=verification_outbox_path(),
        from_address="no-reply@updates.torquemech.com" if transport == "smtp" else feedback_email,
        from_display_name="TorqueMech" if transport == "smtp" else "",
        envelope_sender=feedback_email,
        reply_to_address=feedback_email,
        local_default_outbox_path=STATE_DIR / "email_outbox.jsonl",
    )


def validate_auth_email_configuration() -> email_service.EmailConfigurationValidation:
    return email_service.validate_email_configuration(auth_email_service_config())


def local_email_transport_enabled() -> bool:
    transport = email_service.normalize_transport(os.getenv("TORQUEMECH_EMAIL_TRANSPORT"))
    return transport in {"local", "test"}


def verification_url_for_token(request: Request, token: str) -> str:
    clean_token = str(token or "").strip()
    return f"{str(request.base_url).rstrip('/')}/verify-email?{urlencode({'token': clean_token})}"


def password_reset_base_url(request: Request) -> str:
    host = str(request.url.hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1", "testserver"}:
        return str(request.base_url).rstrip("/")
    return "https://torquemech.com"


def password_reset_url_for_token(request: Request, token: str) -> str:
    clean_token = str(token or "").strip()
    return f"{password_reset_base_url(request)}/reset-password?{urlencode({'token': clean_token})}"


def password_reset_email_text_body(reset_url: str) -> str:
    clean_url = str(reset_url or "").strip()
    return (
        "Reset your TorqueMech password\n\n"
        "We received a request to reset the password for your TorqueMech account. "
        "Use the link below to choose a new password.\n\n"
        f"{clean_url}\n\n"
        "This link expires in 30 minutes. If you did not request this reset, you can ignore this email."
    )


def password_reset_email_body(reset_url: str) -> str:
    clean_url = str(reset_url or "").strip()
    escaped_url = html.escape(clean_url, quote=True)
    return (
        "<!doctype html><html><body>"
        "<h1>Reset your TorqueMech password</h1>"
        "<p>We received a request to reset the password for your TorqueMech account.</p>"
        f'<p><a href="{escaped_url}">Reset Password</a></p>'
        "<p>This link expires in 30 minutes.</p>"
        "<p>If you did not request this reset, you can ignore this email.</p>"
        "</body></html>"
    )


def send_verification_email_local(request: Request, *, email: str, token: str, user_id: int) -> bool:
    verification_email_logger.info(
        "VERIFICATION_EMAIL_DELIVERY_ENTERED transport=local sender=local-outbox recipient=%s user_id=%s",
        email,
        user_id,
    )
    verification_url = verification_url_for_token(request, token)
    message = email_service.EmailMessage(
        recipients=[email],
        subject=VERIFICATION_EMAIL_SUBJECT,
        text_body=verification_email_text_body(verification_url),
        html_body=verification_email_body(verification_url),
        metadata={"verification_url": verification_url, "token": token, "user_id": user_id},
    )
    result = email_service.send_email(message, auth_email_service_config(), logger=verification_email_logger)
    verification_email_logger.info(
        "VERIFICATION_EMAIL_LOCAL_ACCEPTED sender=local-outbox recipient=%s outbox=%s",
        email,
        verification_outbox_path(),
    )
    return result.success


def smtp_email_transport_enabled() -> bool:
    transport = email_service.normalize_transport(os.getenv("TORQUEMECH_EMAIL_TRANSPORT"))
    return transport == "smtp"


def resend_email_transport_enabled() -> bool:
    transport = email_service.normalize_transport(os.getenv("TORQUEMECH_EMAIL_TRANSPORT"))
    return transport == "resend"


def verification_email_text_body(verification_url: str) -> str:
    clean_url = str(verification_url or "").strip()
    return (
        "Verify your TorqueMech account before continuing to your Pro workspace.\n\n"
        f"{clean_url}\n"
    )


def verification_email_body(verification_url: str) -> str:
    clean_url = str(verification_url or "").strip()
    escaped_url = html.escape(clean_url, quote=True)
    return (
        "<!doctype html><html><body>"
        "<p>Verify your TorqueMech account before continuing to your Pro workspace.</p>"
        f'<p><a href="{escaped_url}">Verify your email address</a></p>'
        "</body></html>"
    )


def send_verification_email_smtp(request: Request, *, email: str, token: str) -> bool:
    verification_email_logger.info(
        "VERIFICATION_EMAIL_DELIVERY_ENTERED transport=smtp host=%s port=%s sender=%s recipient=%s",
        SMTP_SERVER,
        SMTP_PORT,
        FEEDBACK_EMAIL,
        email,
    )
    config = auth_email_service_config()
    validation = email_service.validate_email_configuration(config)
    if not validation.ok:
        verification_email_logger.error(
            "VERIFICATION_EMAIL_SMTP_NOT_CONFIGURED missing=%s recipient=%s",
            ",".join(validation.missing_variables),
            email,
        )
        return False

    verification_url = verification_url_for_token(request, token)
    result = email_service.send_email(
        email_service.EmailMessage(
            recipients=[email],
            subject=VERIFICATION_EMAIL_SUBJECT,
            text_body=verification_email_text_body(verification_url),
            html_body=verification_email_body(verification_url),
        ),
        config,
        logger=verification_email_logger,
    )
    if result.error_category == "provider_refused":
        verification_email_logger.error("VERIFICATION_EMAIL_SMTP_REFUSED host=%s port=%s sender=%s recipient=%s", SMTP_SERVER, SMTP_PORT, FEEDBACK_EMAIL, email)
    elif result.provider_related and not result.success:
        verification_email_logger.error("VERIFICATION_EMAIL_SMTP_EXCEPTION host=%s port=%s sender=%s recipient=%s", SMTP_SERVER, SMTP_PORT, FEEDBACK_EMAIL, email)
    elif result.success:
        verification_email_logger.info("VERIFICATION_EMAIL_SMTP_ACCEPTED host=%s port=%s sender=%s recipient=%s", SMTP_SERVER, SMTP_PORT, FEEDBACK_EMAIL, email)
    return result.success


def resend_email_id(response: Any) -> str:
    if isinstance(response, dict):
        return str(response.get("id") or "")
    return str(getattr(response, "id", "") or "")


def send_verification_email_resend(request: Request, *, email: str, token: str) -> bool:
    sender = str(FEEDBACK_EMAIL or "").strip()
    verification_email_logger.info(
        "VERIFICATION_EMAIL_DELIVERY_ENTERED transport=resend sender=%s recipient=%s",
        sender,
        email,
    )
    config = auth_email_service_config()
    validation = email_service.validate_email_configuration(config)
    if not validation.ok:
        missing = "api_key" if RESEND_API_KEY_ENV in validation.missing_variables else ",".join(validation.missing_variables)
        verification_email_logger.error(
            "VERIFICATION_EMAIL_RESEND_NOT_CONFIGURED missing=%s sender=%s recipient=%s",
            missing,
            sender,
            email,
        )
        return False

    verification_url = verification_url_for_token(request, token)
    result = email_service.send_email(
        email_service.EmailMessage(
            recipients=[email],
            subject=VERIFICATION_EMAIL_SUBJECT,
            text_body=verification_email_text_body(verification_url),
            html_body=verification_email_body(verification_url),
        ),
        config,
        logger=verification_email_logger,
        resend_client=resend,
    )
    if result.success:
        verification_email_logger.info("VERIFICATION_EMAIL_RESEND_ACCEPTED sender=%s recipient=%s resend_email_id=%s", sender, email, result.provider_message_id)
    else:
        verification_email_logger.error("VERIFICATION_EMAIL_RESEND_EXCEPTION sender=%s recipient=%s", sender, email)
    return result.success


def send_verification_email(request: Request, *, email: str, token: str, user_id: int) -> bool:
    transport = email_service.normalize_transport(os.getenv("TORQUEMECH_EMAIL_TRANSPORT"))
    sender = FEEDBACK_EMAIL if transport in {"smtp", "resend"} else "local-outbox"
    verification_email_logger.info(
        "VERIFICATION_EMAIL_TRANSPORT_SELECTED transport=%s host=%s port=%s sender=%s recipient=%s user_id=%s",
        transport,
        SMTP_SERVER if transport == "smtp" else ("resend" if transport == "resend" else "local"),
        SMTP_PORT if transport == "smtp" else "",
        sender,
        email,
        user_id,
    )
    if local_email_transport_enabled():
        return send_verification_email_local(request, email=email, token=token, user_id=user_id)
    if smtp_email_transport_enabled():
        return send_verification_email_smtp(request, email=email, token=token)
    if resend_email_transport_enabled():
        return send_verification_email_resend(request, email=email, token=token)
    verification_email_logger.error("VERIFICATION_EMAIL_TRANSPORT_UNSUPPORTED transport=%s", transport)
    return False


def send_password_reset_email_local(request: Request, *, email: str, token: str, user_id: int) -> bool:
    verification_email_logger.info(
        "PASSWORD_RESET_EMAIL_DELIVERY_ENTERED transport=local sender=local-outbox recipient=%s user_id=%s",
        email,
        user_id,
    )
    reset_url = password_reset_url_for_token(request, token)
    result = email_service.send_email(
        email_service.EmailMessage(
            recipients=[email],
            subject=PASSWORD_RESET_EMAIL_SUBJECT,
            text_body=password_reset_email_text_body(reset_url),
            html_body=password_reset_email_body(reset_url),
            metadata={"reset_url": reset_url, "token": token, "user_id": user_id},
        ),
        auth_email_service_config(),
        logger=verification_email_logger,
    )
    verification_email_logger.info(
        "PASSWORD_RESET_EMAIL_LOCAL_ACCEPTED sender=local-outbox recipient=%s outbox=%s",
        email,
        verification_outbox_path(),
    )
    return result.success


def send_password_reset_email_smtp(request: Request, *, email: str, token: str) -> bool:
    verification_email_logger.info(
        "PASSWORD_RESET_EMAIL_DELIVERY_ENTERED transport=smtp host=%s port=%s sender=%s recipient=%s",
        SMTP_SERVER,
        SMTP_PORT,
        FEEDBACK_EMAIL,
        email,
    )
    config = auth_email_service_config()
    validation = email_service.validate_email_configuration(config)
    if not validation.ok:
        verification_email_logger.error(
            "PASSWORD_RESET_EMAIL_SMTP_NOT_CONFIGURED missing=%s recipient=%s",
            ",".join(validation.missing_variables),
            email,
        )
        return False
    reset_url = password_reset_url_for_token(request, token)
    result = email_service.send_email(
        email_service.EmailMessage(
            recipients=[email],
            subject=PASSWORD_RESET_EMAIL_SUBJECT,
            text_body=password_reset_email_text_body(reset_url),
            html_body=password_reset_email_body(reset_url),
        ),
        config,
        logger=verification_email_logger,
    )
    if result.error_category == "provider_refused":
        verification_email_logger.error("PASSWORD_RESET_EMAIL_SMTP_REFUSED host=%s port=%s sender=%s recipient=%s", SMTP_SERVER, SMTP_PORT, FEEDBACK_EMAIL, email)
    elif result.provider_related and not result.success:
        verification_email_logger.error("PASSWORD_RESET_EMAIL_SMTP_EXCEPTION host=%s port=%s sender=%s recipient=%s", SMTP_SERVER, SMTP_PORT, FEEDBACK_EMAIL, email)
    elif result.success:
        verification_email_logger.info("PASSWORD_RESET_EMAIL_SMTP_ACCEPTED host=%s port=%s sender=%s recipient=%s", SMTP_SERVER, SMTP_PORT, FEEDBACK_EMAIL, email)
    return result.success


def send_password_reset_email_resend(request: Request, *, email: str, token: str) -> bool:
    sender = str(FEEDBACK_EMAIL or "").strip()
    verification_email_logger.info(
        "PASSWORD_RESET_EMAIL_DELIVERY_ENTERED transport=resend sender=%s recipient=%s",
        sender,
        email,
    )
    config = auth_email_service_config()
    validation = email_service.validate_email_configuration(config)
    if not validation.ok:
        missing = "api_key" if RESEND_API_KEY_ENV in validation.missing_variables else ",".join(validation.missing_variables)
        verification_email_logger.error(
            "PASSWORD_RESET_EMAIL_RESEND_NOT_CONFIGURED missing=%s sender=%s recipient=%s",
            missing,
            sender,
            email,
        )
        return False
    reset_url = password_reset_url_for_token(request, token)
    result = email_service.send_email(
        email_service.EmailMessage(
            recipients=[email],
            subject=PASSWORD_RESET_EMAIL_SUBJECT,
            text_body=password_reset_email_text_body(reset_url),
            html_body=password_reset_email_body(reset_url),
        ),
        config,
        logger=verification_email_logger,
        resend_client=resend,
    )
    if result.success:
        verification_email_logger.info("PASSWORD_RESET_EMAIL_RESEND_ACCEPTED sender=%s recipient=%s resend_email_id=%s", sender, email, result.provider_message_id)
    else:
        verification_email_logger.error("PASSWORD_RESET_EMAIL_RESEND_EXCEPTION sender=%s recipient=%s", sender, email)
    return result.success


def send_password_reset_email(request: Request, *, email: str, token: str, user_id: int) -> bool:
    transport = email_service.normalize_transport(os.getenv("TORQUEMECH_EMAIL_TRANSPORT"))
    sender = FEEDBACK_EMAIL if transport in {"smtp", "resend"} else "local-outbox"
    verification_email_logger.info(
        "PASSWORD_RESET_EMAIL_TRANSPORT_SELECTED transport=%s sender=%s recipient=%s user_id=%s",
        transport,
        sender,
        email,
        user_id,
    )
    if local_email_transport_enabled():
        return send_password_reset_email_local(request, email=email, token=token, user_id=user_id)
    if smtp_email_transport_enabled():
        return send_password_reset_email_smtp(request, email=email, token=token)
    if resend_email_transport_enabled():
        return send_password_reset_email_resend(request, email=email, token=token)
    verification_email_logger.error("PASSWORD_RESET_EMAIL_TRANSPORT_UNSUPPORTED transport=%s", transport)
    return False


def verification_resend_cooldown_remaining(user: dict[str, Any] | None) -> int:
    last_sent_at = parse_verification_expiry((user or {}).get("verification_email_last_sent_at"))
    if not last_sent_at:
        return 0
    elapsed = (datetime.utcnow() - last_sent_at).total_seconds()
    return max(0, int(math.ceil(VERIFICATION_EMAIL_RESEND_COOLDOWN_SECONDS - elapsed)))


def check_email_context(request: Request, *, status: str = "", message: str = "", user: dict[str, Any] | None = None) -> dict[str, Any]:
    if user is None:
        user = getattr(request.state, "current_user", None)
    cooldown_remaining = verification_resend_cooldown_remaining(user)
    return {
        "request": request,
        "csrf_token": csrf_token(request),
        "status": status,
        "message": message,
        "cooldown_remaining": cooldown_remaining,
        "resend_available": bool(user and not user_email_verified(user) and cooldown_remaining <= 0),
    }


def parse_verification_expiry(raw: Any) -> datetime | None:
    try:
        value = str(raw or "").strip()
        if not value:
            return None
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def find_user_for_verification_token(conn: sqlite3.Connection, token: str) -> dict[str, Any] | None:
    submitted_hash = verification_token_hash(token)
    rows = conn.execute(
        """
        SELECT *
        FROM users
        WHERE verification_token_hash IS NOT NULL
          AND TRIM(verification_token_hash) != ''
          AND email_verified_at IS NULL
        """
    ).fetchall()
    for row in rows:
        user = dict(row)
        stored_hash = str(user.get("verification_token_hash") or "")
        if hmac.compare_digest(stored_hash, submitted_hash):
            expires_at = parse_verification_expiry(user.get("verification_token_expires_at"))
            if not expires_at or expires_at < datetime.utcnow():
                return None
            return user
    return None


def find_user_for_pending_email_token(conn: sqlite3.Connection, token: str) -> dict[str, Any] | None:
    submitted_hash = verification_token_hash(token)
    rows = conn.execute(
        """
        SELECT *
        FROM users
        WHERE pending_email_token_hash IS NOT NULL
          AND TRIM(pending_email_token_hash) != ''
          AND pending_email IS NOT NULL
          AND TRIM(pending_email) != ''
        """
    ).fetchall()
    for row in rows:
        user = dict(row)
        stored_hash = str(user.get("pending_email_token_hash") or "")
        if hmac.compare_digest(stored_hash, submitted_hash):
            expires_at = parse_verification_expiry(user.get("pending_email_token_expires_at"))
            if not expires_at or expires_at < datetime.utcnow():
                return None
            return user
    return None


def pending_email_token_result(conn: sqlite3.Connection, token: str) -> tuple[str, dict[str, Any] | None]:
    submitted_hash = verification_token_hash(token)
    rows = conn.execute(
        """
        SELECT *
        FROM users
        WHERE pending_email_token_hash IS NOT NULL
           OR pending_email_used_token_hash IS NOT NULL
        """
    ).fetchall()
    for row in rows:
        user = dict(row)
        active_hash = str(user.get("pending_email_token_hash") or "")
        used_hash = str(user.get("pending_email_used_token_hash") or "")
        if used_hash and hmac.compare_digest(used_hash, submitted_hash):
            return "used", user
        if active_hash and hmac.compare_digest(active_hash, submitted_hash):
            expires_at = parse_verification_expiry(user.get("pending_email_token_expires_at"))
            if not expires_at or expires_at < datetime.utcnow():
                return "expired", user
            return "valid", user
    return "invalid", None


def delete_auth_sessions_for_user(conn: sqlite3.Connection, user_id: int) -> None:
    rows = conn.execute("SELECT session_id, data_json FROM auth_sessions").fetchall()
    for row in rows:
        try:
            data = json.loads(row["data_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        try:
            session_user_id = int(data.get(AUTH_SESSION_USER_KEY) or 0)
        except (TypeError, ValueError):
            session_user_id = 0
        if session_user_id == int(user_id):
            conn.execute("DELETE FROM auth_sessions WHERE session_id = ?", (row["session_id"],))


def password_reset_request_recent(conn: sqlite3.Connection, user_id: int) -> bool:
    row = conn.execute(
        """
        SELECT created_at
        FROM password_reset_tokens
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    created_at = parse_verification_expiry(row["created_at"] if row else None)
    if not created_at:
        return False
    elapsed = (datetime.utcnow() - created_at).total_seconds()
    return elapsed < PASSWORD_RESET_REQUEST_COOLDOWN_SECONDS


def find_valid_password_reset_token(conn: sqlite3.Connection, token: str) -> dict[str, Any] | None:
    submitted_hash = password_reset_token_hash(token)
    row = conn.execute(
        """
        SELECT prt.*, users.email, users.is_active
        FROM password_reset_tokens prt
        JOIN users ON users.id = prt.user_id
        WHERE prt.token_hash = ?
        LIMIT 1
        """,
        (submitted_hash,),
    ).fetchone()
    if not row:
        return None
    record = dict(row)
    stored_hash = str(record.get("token_hash") or "")
    expires_at = parse_verification_expiry(record.get("expires_at"))
    if (
        not hmac.compare_digest(stored_hash, submitted_hash)
        or record.get("used_at")
        or not expires_at
        or expires_at <= datetime.utcnow()
        or not record.get("is_active")
    ):
        return None
    return record


def local_fallback_verification_conn(token: str) -> tuple[sqlite3.Connection, dict[str, Any]] | None:
    if not USE_LOCAL_SQLITE_COMPAT:
        return None
    fallback_path = Path(LOCAL_FALLBACK_DB_PATH)
    if active_app_db_path() == LOCAL_FALLBACK_DB_PATH or not fallback_path.exists():
        return None
    conn = sqlite3.connect(LOCAL_FALLBACK_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        ensure_auth_schema(conn)
        user = find_user_for_verification_token(conn, token)
        if user:
            mark_local_fallback_db_active()
            return conn, user
    except Exception:
        logging.exception("LOCAL_FALLBACK_VERIFICATION_LOOKUP_FAILED")
    conn.close()
    return None


def has_bootstrap_session(request: Request) -> bool:
    return bool(request.session.get(AUTH_SESSION_BOOTSTRAP_KEY))


def mark_bootstrap_session(request: Request) -> None:
    request.session[AUTH_SESSION_BOOTSTRAP_KEY] = True


def clear_bootstrap_session(request: Request) -> None:
    request.session.pop(AUTH_SESSION_BOOTSTRAP_KEY, None)


@app.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request):
    conn = app_db_conn(row_factory=True)
    try:
        first_signup = user_count(conn) == 0
    finally:
        conn.close()
    clear_bootstrap_session(request)
    return templates.TemplateResponse(
        "signup.html",
        {
            "request": request,
            "csrf_token": csrf_token(request),
            "values": {},
            "errors": {},
            "setup_incomplete": first_signup,
        },
    )


@app.post("/signup", response_class=HTMLResponse)
async def signup_submit(request: Request):
    raw_body = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qs(raw_body, keep_blank_values=True)
    form = {key: values[0].strip() for key, values in parsed.items()}
    values = auth_form_values(form)
    errors: dict[str, str] = {}
    if not validate_csrf(request, form):
        errors["form"] = "Please refresh the page and try again."
    for key, label in (
        ("first_name", "First name"),
        ("last_name", "Last name"),
        ("email", "Email address"),
        ("shop_name", "Shop name"),
    ):
        if not values.get(key):
            errors[key] = f"{label} is required."
    password = form.get("password", "")
    confirm_password = form.get("confirm_password", "")
    password_error = password_rules_error(password)
    if password_error:
        errors["password"] = password_error
    if password != confirm_password:
        errors["confirm_password"] = "Passwords must match."
    if form.get("terms") != "1":
        errors["terms"] = "You must agree before creating an account."

    conn = app_db_conn(row_factory=True)
    try:
        ensure_auth_schema(conn)
        ensure_shop_profile_schema(conn)
        first_signup = user_count(conn) == 0
        if first_signup:
            errors["form"] = "TorqueMech setup is not yet complete."
        if values.get("email") and load_user_by_email(conn, values["email"]):
            errors["email"] = "An account with this email already exists."
        if errors:
            return templates.TemplateResponse(
                "signup.html",
                {
                    "request": request,
                    "csrf_token": csrf_token(request),
                    "values": values,
                    "errors": errors,
                    "setup_incomplete": first_signup,
                },
                status_code=400,
            )
        now = datetime.utcnow().isoformat()
        _verification_token, token_hash, token_expires_at = new_verification_token_record()
        try:
            conn.execute("BEGIN")
            cur = conn.execute(
                """
                INSERT INTO users (
                  email, password_hash, first_name, last_name, is_active,
                  email_verified_at, verification_token_hash, verification_token_expires_at,
                  verification_email_last_sent_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 1, NULL, ?, ?, ?, ?, ?)
                """,
                (
                    values["email"],
                    hash_password(password),
                    values["first_name"],
                    values["last_name"],
                    token_hash,
                    token_expires_at,
                    now,
                    now,
                    now,
                ),
            )
            user_id = int(cur.lastrowid)
            create_shop_profile_for_user(conn, user_id, values["shop_name"])
            conn.commit()
            send_verification_email(request, email=values["email"], token=_verification_token, user_id=user_id)
        except sqlite3.IntegrityError:
            conn.rollback()
            errors["email"] = "An account with this email already exists."
            return templates.TemplateResponse(
                "signup.html",
                {
                    "request": request,
                    "csrf_token": csrf_token(request),
                    "values": values,
                    "errors": errors,
                    "setup_incomplete": first_signup,
                },
                status_code=400,
            )
        login_session(request, user_id)
        clear_bootstrap_session(request)
    finally:
        conn.close()
    return RedirectResponse("/check-email", status_code=303)


@app.get("/admin/bootstrap", response_class=HTMLResponse, include_in_schema=False)
def admin_bootstrap_page(request: Request):
    conn = app_db_conn(row_factory=True)
    try:
        if user_count(conn) > 0:
            raise HTTPException(status_code=404)
    finally:
        conn.close()
    return templates.TemplateResponse(
        "admin_bootstrap.html",
        {
            "request": request,
            "csrf_token": csrf_token(request),
            "values": {},
            "errors": {},
            "bootstrap_configured": bool(configured_bootstrap_token()),
        },
    )


@app.post("/admin/bootstrap", response_class=HTMLResponse, include_in_schema=False)
async def admin_bootstrap_submit(request: Request):
    raw_body = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qs(raw_body, keep_blank_values=True)
    form = {key: values[0].strip() for key, values in parsed.items()}
    values = auth_form_values(form)
    errors: dict[str, str] = {}
    if not validate_csrf(request, form):
        errors["form"] = "Please refresh the page and try again."
    for key, label in (
        ("first_name", "First name"),
        ("last_name", "Last name"),
        ("email", "Email address"),
        ("shop_name", "Shop name"),
    ):
        if not values.get(key):
            errors[key] = f"{label} is required."
    password = form.get("password", "")
    confirm_password = form.get("confirm_password", "")
    password_error = password_rules_error(password)
    if password_error:
        errors["password"] = password_error
    if password != confirm_password:
        errors["confirm_password"] = "Passwords must match."
    if form.get("terms") != "1":
        errors["terms"] = "You must agree before creating an account."
    bootstrap_configured = bool(configured_bootstrap_token())
    if not bootstrap_configured:
        errors["bootstrap_token"] = "Initial account setup is not enabled. Ask the site owner to configure setup access before creating the first account."
    elif not bootstrap_token_is_valid(form.get("bootstrap_token", "")):
        errors["bootstrap_token"] = "Setup token is invalid."

    conn = app_db_conn(row_factory=True)
    try:
        ensure_auth_schema(conn)
        ensure_shop_profile_schema(conn)
        if user_count(conn) > 0:
            raise HTTPException(status_code=404)
        if values.get("email") and load_user_by_email(conn, values["email"]):
            errors["email"] = "An account with this email already exists."
        if errors:
            return templates.TemplateResponse(
                "admin_bootstrap.html",
                {
                    "request": request,
                    "csrf_token": csrf_token(request),
                    "values": values,
                    "errors": errors,
                    "bootstrap_configured": bootstrap_configured,
                },
                status_code=400,
            )
        now = datetime.utcnow().isoformat()
        try:
            conn.execute("BEGIN")
            if user_count(conn) > 0:
                conn.rollback()
                raise HTTPException(status_code=404)
            cur = conn.execute(
                """
                INSERT INTO users (
                  email, password_hash, first_name, last_name, is_active,
                  email_verified_at, verification_token_hash, verification_token_expires_at,
                  created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 1, ?, NULL, NULL, ?, ?)
                """,
                (
                    values["email"],
                    hash_password(password),
                    values["first_name"],
                    values["last_name"],
                    now,
                    now,
                    now,
                ),
            )
            user_id = int(cur.lastrowid)
            shop_id = bootstrap_existing_shop_to_user(conn, user_id, values["shop_name"])
            create_or_ensure_shop_subscription(conn, int(shop_id))
            conn.commit()
        except sqlite3.IntegrityError:
            conn.rollback()
            errors["email"] = "An account with this email already exists."
            return templates.TemplateResponse(
                "admin_bootstrap.html",
                {
                    "request": request,
                    "csrf_token": csrf_token(request),
                    "values": values,
                    "errors": errors,
                    "bootstrap_configured": bootstrap_configured,
                },
                status_code=400,
            )
        login_session(request, user_id)
        clear_bootstrap_session(request)
    finally:
        conn.close()
    return RedirectResponse(
        "/pro/shop-settings?notice=first_setup",
        status_code=303,
    )


@app.get("/verify-email", response_class=HTMLResponse)
def verify_email(request: Request, token: str = ""):
    submitted = str(token or "").strip()
    if not submitted:
        return templates.TemplateResponse(
            "verify_email_error.html",
            {"request": request},
            status_code=400,
        )
    conn = app_db_conn(row_factory=True)
    try:
        ensure_auth_schema(conn)
        user = find_user_for_verification_token(conn, submitted)
        if not user:
            fallback_match = local_fallback_verification_conn(submitted)
            if fallback_match:
                conn.close()
                conn, user = fallback_match
        if not user:
            pending_status, pending_user = pending_email_token_result(conn, submitted)
            if pending_status == "used":
                return templates.TemplateResponse(
                    "verify_email_error.html",
                    {
                        "request": request,
                        "title": "Email address already updated",
                        "message": "This verification link has already been used. Your email address has already been updated.",
                        "action_href": "/account/settings" if current_user(conn, request) else "/login",
                        "action_label": "Go to Account Settings" if current_user(conn, request) else "Log In",
                    },
                    status_code=200,
                )
            if pending_status == "expired":
                return templates.TemplateResponse(
                    "verify_email_error.html",
                    {
                        "request": request,
                        "title": "Verification link expired",
                        "message": "This verification link has expired. Return to Account Settings and request a new verification email.",
                        "action_href": "/account/settings" if current_user(conn, request) else "/login",
                        "action_label": "Go to Account Settings" if current_user(conn, request) else "Log In",
                    },
                    status_code=400,
                )
            if pending_status == "valid" and pending_user:
                pending_email = normalize_email(pending_user.get("pending_email"))
                duplicate_user = load_user_by_email(conn, pending_email) if pending_email else None
                if duplicate_user and int(duplicate_user["id"]) != int(pending_user["id"]):
                    return templates.TemplateResponse(
                        "verify_email_error.html",
                        {"request": request},
                        status_code=400,
                    )
                now = datetime.utcnow().isoformat()
                conn.execute(
                    """
                    UPDATE users
                    SET email = ?,
                        email_verified_at = ?,
                        pending_email = NULL,
                        pending_email_token_hash = NULL,
                        pending_email_token_expires_at = NULL,
                        pending_email_requested_at = NULL,
                        pending_email_last_sent_at = NULL,
                        pending_email_used_token_hash = ?,
                        updated_at = ?
                    WHERE id = ?
                      AND pending_email = ?
                      AND pending_email_token_hash IS NOT NULL
                      AND is_active = 1
                    """,
                    (pending_email, now, submitted_hash := verification_token_hash(submitted), now, int(pending_user["id"]), pending_email),
                )
                conn.commit()
                login_session(request, int(pending_user["id"]))
                return templates.TemplateResponse(
                    "verify_email_success.html",
                    {
                        "request": request,
                        "csrf_token": csrf_token(request),
                        "title": "✓ Email Address Updated",
                        "kicker": "Security & Login",
                        "intro": "Your sign-in email has been successfully changed.",
                        "body": "New sign-in email:",
                        "detail_value": pending_email,
                        "primary_href": "/pro/dashboard",
                        "primary_label": "Continue to Dashboard",
                        "show_sign_in": True,
                        "show_logout": False,
                    },
                )
        if not user:
            return templates.TemplateResponse(
                "verify_email_error.html",
                {"request": request},
                status_code=400,
            )
        now = datetime.utcnow().isoformat()
        conn.execute(
            """
            UPDATE users
            SET email_verified_at = ?,
                verification_token_hash = NULL,
                verification_token_expires_at = NULL,
                updated_at = ?
            WHERE id = ?
              AND email_verified_at IS NULL
              AND verification_token_hash IS NOT NULL
            """,
            (now, now, int(user["id"])),
        )
        shop_row = conn.execute(
            "SELECT id FROM shop_profile WHERE owner_user_id = ? LIMIT 1",
            (int(user["id"]),),
        ).fetchone()
        shop_id = int(shop_row["id"]) if shop_row else None
        if shop_id is None:
            shop_id = create_shop_profile_for_user(conn, int(user["id"]))
        create_or_ensure_shop_subscription(conn, int(shop_id))
        conn.commit()
        login_session(request, int(user["id"]))
    finally:
        conn.close()
    return templates.TemplateResponse(
        "verify_email_success.html",
        {
            "request": request,
            "csrf_token": csrf_token(request),
        },
    )


@app.get("/check-email", response_class=HTMLResponse)
def check_email_page(request: Request):
    conn = app_db_conn(row_factory=True)
    try:
        ensure_auth_schema(conn)
        user = current_user(conn, request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        if user_email_verified(user):
            return RedirectResponse("/pro/dashboard", status_code=303)
        return templates.TemplateResponse("check_email.html", check_email_context(request, user=user))
    finally:
        conn.close()


@app.post("/check-email/resend", response_class=HTMLResponse)
async def resend_verification_email(request: Request):
    raw_body = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qs(raw_body, keep_blank_values=True)
    form = {key: values[0].strip() for key, values in parsed.items()}
    conn = app_db_conn(row_factory=True)
    try:
        ensure_auth_schema(conn)
        user = current_user(conn, request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        if user_email_verified(user):
            return RedirectResponse("/pro/dashboard", status_code=303)
        if not validate_csrf(request, form):
            return templates.TemplateResponse(
                "check_email.html",
                check_email_context(
                    request,
                    status="error",
                    message="Please refresh the page and try again.",
                    user=user,
                ),
                status_code=400,
            )
        cooldown_remaining = verification_resend_cooldown_remaining(user)
        if cooldown_remaining > 0:
            return templates.TemplateResponse(
                "check_email.html",
                check_email_context(
                    request,
                    status="error",
                    message=f"Please wait {cooldown_remaining} seconds before requesting another verification email.",
                    user=user,
                ),
                status_code=429,
            )
        token, token_hash, token_expires_at = new_verification_token_record()
        delivered = send_verification_email(request, email=str(user["email"]), token=token, user_id=int(user["id"]))
        if not delivered:
            return templates.TemplateResponse(
                "check_email.html",
                check_email_context(
                    request,
                    status="error",
                    message="We could not send a verification email right now. Please try again.",
                    user=user,
                ),
                status_code=503,
            )
        now = datetime.utcnow().isoformat()
        conn.execute(
            """
            UPDATE users
            SET verification_token_hash = ?,
                verification_token_expires_at = ?,
                verification_email_last_sent_at = ?,
                updated_at = ?
            WHERE id = ?
              AND email_verified_at IS NULL
              AND is_active = 1
            """,
            (token_hash, token_expires_at, now, now, int(user["id"])),
        )
        conn.commit()
        updated_user = current_user(conn, request) or user
        return templates.TemplateResponse(
            "check_email.html",
            check_email_context(
                request,
                status="success",
                message="A fresh verification email has been sent.",
                user=updated_user,
            ),
        )
    finally:
        conn.close()


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "", reset: str = "", signed_out_all: str = ""):
    message = ""
    if reset == "success":
        message = "Your password has been reset. You can now sign in."
    elif signed_out_all == "1":
        message = "You have been signed out of all devices."
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "csrf_token": csrf_token(request),
            "next": safe_next_url(next),
            "values": {},
            "errors": {},
            "message": message,
        },
    )


@app.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request):
    return templates.TemplateResponse(
        "forgot_password.html",
        {
            "request": request,
            "csrf_token": csrf_token(request),
            "values": {},
            "errors": {},
            "message": "",
        },
    )


@app.post("/forgot-password", response_class=HTMLResponse)
async def forgot_password_submit(request: Request):
    raw_body = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qs(raw_body, keep_blank_values=True)
    form = {key: values[0].strip() for key, values in parsed.items()}
    email = normalize_email(form.get("email"))
    errors: dict[str, str] = {}
    if not validate_csrf(request, form):
        errors["form"] = "Please refresh the page and try again."
    if not email:
        errors["email"] = "Email address is required."
    if errors:
        return templates.TemplateResponse(
            "forgot_password.html",
            {
                "request": request,
                "csrf_token": csrf_token(request),
                "values": {"email": email},
                "errors": errors,
                "message": "",
            },
            status_code=400,
        )

    conn = app_db_conn(row_factory=True)
    try:
        ensure_password_reset_schema(conn)
        user = load_user_by_email(conn, email) if email else None
        if user and user.get("is_active") and not password_reset_request_recent(conn, int(user["id"])):
            token, token_hash, expires_at = new_password_reset_token_record()
            now = datetime.utcnow().isoformat()
            try:
                conn.execute("BEGIN")
                conn.execute(
                    """
                    UPDATE password_reset_tokens
                    SET used_at = ?
                    WHERE user_id = ?
                      AND used_at IS NULL
                    """,
                    (now, int(user["id"])),
                )
                conn.execute(
                    """
                    INSERT INTO password_reset_tokens (user_id, token_hash, expires_at, used_at, created_at)
                    VALUES (?, ?, ?, NULL, ?)
                    """,
                    (int(user["id"]), token_hash, expires_at, now),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            delivered = send_password_reset_email(request, email=str(user["email"]), token=token, user_id=int(user["id"]))
            if not delivered:
                verification_email_logger.error(
                    "PASSWORD_RESET_EMAIL_DELIVERY_FAILED recipient=%s user_id=%s",
                    user["email"],
                    user["id"],
                )
    finally:
        conn.close()

    return templates.TemplateResponse(
        "forgot_password.html",
        {
            "request": request,
            "csrf_token": csrf_token(request),
            "values": {"email": email},
            "errors": {},
            "message": PASSWORD_RESET_CONFIRMATION_MESSAGE,
        },
    )


@app.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(request: Request, token: str = ""):
    submitted = str(token or "").strip()
    conn = app_db_conn(row_factory=True)
    try:
        ensure_password_reset_schema(conn)
        reset_record = find_valid_password_reset_token(conn, submitted) if submitted else None
    finally:
        conn.close()
    if not reset_record:
        return templates.TemplateResponse(
            "reset_password_invalid.html",
            {"request": request},
            status_code=400,
        )
    return templates.TemplateResponse(
        "reset_password.html",
        {
            "request": request,
            "csrf_token": csrf_token(request),
            "token": submitted,
            "errors": {},
        },
    )


@app.post("/reset-password", response_class=HTMLResponse)
async def reset_password_submit(request: Request):
    raw_body = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qs(raw_body, keep_blank_values=True)
    form = {key: values[0].strip() for key, values in parsed.items()}
    submitted = str(form.get("token") or "").strip()
    password = form.get("password", "")
    confirm_password = form.get("confirm_password", "")
    errors: dict[str, str] = {}
    if not validate_csrf(request, form):
        errors["form"] = "Please refresh the page and try again."
    password_error = password_rules_error(password)
    if password_error:
        errors["password"] = password_error
    if password != confirm_password:
        errors["confirm_password"] = "Passwords must match."

    conn = app_db_conn(row_factory=True)
    try:
        ensure_password_reset_schema(conn)
        reset_record = find_valid_password_reset_token(conn, submitted) if submitted else None
        if not reset_record:
            return templates.TemplateResponse(
                "reset_password_invalid.html",
                {"request": request},
                status_code=400,
            )
        if errors:
            return templates.TemplateResponse(
                "reset_password.html",
                {
                    "request": request,
                    "csrf_token": csrf_token(request),
                    "token": submitted,
                    "errors": errors,
                },
                status_code=400,
            )
        now = datetime.utcnow().isoformat()
        conn.execute("BEGIN")
        conn.execute(
            """
            UPDATE users
            SET password_hash = ?,
                password_changed_at = ?,
                updated_at = ?
            WHERE id = ?
              AND is_active = 1
            """,
            (hash_password(password), now, now, int(reset_record["user_id"])),
        )
        conn.execute(
            """
            UPDATE password_reset_tokens
            SET used_at = ?
            WHERE user_id = ?
              AND used_at IS NULL
            """,
            (now, int(reset_record["user_id"])),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return RedirectResponse("/login?reset=success", status_code=303)


@app.get("/account/settings", response_class=HTMLResponse)
def account_settings_page(request: Request, next: str = "", subscription_notice: str = ""):
    conn = app_db_conn(row_factory=True)
    try:
        ensure_auth_schema(conn)
        user = current_user(conn, request)
        if not user:
            return RedirectResponse("/login?next=%2Faccount%2Fsettings", status_code=303)
        request.state.current_user = user
        request.state.current_shop = current_shop_context(conn, request)
        notice_message = ""
        if subscription_notice == "read_only":
            notice_message = str(request.session.pop("subscription_notice", "") or "").strip()
            if not notice_message:
                notice_message = "Your account is in read-only mode. Update billing to make changes."
        return templates.TemplateResponse(
            "account_settings.html",
            account_settings_context(request, user=user, message=notice_message, back_url=next),
        )
    finally:
        conn.close()


@app.post("/account/settings", response_class=HTMLResponse)
async def account_settings_submit(request: Request):
    raw_body = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qs(raw_body, keep_blank_values=True)
    form = {key: values[0].strip() for key, values in parsed.items()}
    errors: dict[str, str] = {}
    action = form.get("action", "change_password")
    back_url = safe_next_url(form.get("back_url")) or "/pro/dashboard"

    conn = app_db_conn(row_factory=True)
    try:
        ensure_auth_schema(conn)
        user = current_user(conn, request)
        if not user:
            return RedirectResponse("/login?next=%2Faccount%2Fsettings", status_code=303)
        request.state.current_user = user
        request.state.current_shop = current_shop_context(conn, request)

        if action == "save_profile":
            full_name = re.sub(r"\s+", " ", form.get("full_name", "").strip())
            phone_value, phone_error = validate_account_phone(form.get("phone", ""))
            profile_values = {"full_name": full_name, "phone": phone_value}
            if not validate_csrf(request, form):
                errors["form"] = "Please refresh the page and try again."
            if phone_error:
                errors["phone"] = phone_error
            if errors:
                return templates.TemplateResponse(
                    "account_settings.html",
                    account_settings_context(
                        request,
                        user=user,
                        errors=errors,
                        back_url=back_url,
                        profile_values=profile_values,
                    ),
                    status_code=400,
                )

            first_name, last_name = split_account_full_name(full_name)
            now = datetime.utcnow().isoformat()
            conn.execute(
                """
                UPDATE users
                SET first_name = ?,
                    last_name = ?,
                    phone = ?,
                    updated_at = ?
                WHERE id = ?
                  AND is_active = 1
                """,
                (first_name, last_name, phone_value, now, int(user["id"])),
            )
            conn.commit()
            updated_user = current_user(conn, request) or user
            request.state.current_user = updated_user
            request.state.current_shop = current_shop_context(conn, request)
            return templates.TemplateResponse(
                "account_settings.html",
                account_settings_context(
                    request,
                    user=updated_user,
                    message="Your profile has been updated.",
                    back_url=back_url,
                ),
            )

        if action == "change_email":
            current_password = form.get("email_current_password", "")
            new_email = normalize_email(form.get("new_email"))
            confirm_new_email = normalize_email(form.get("confirm_new_email"))
            email_change_values = {"new_email": new_email, "confirm_new_email": confirm_new_email}
            if not validate_csrf(request, form):
                errors["form"] = "Please refresh the page and try again."
            if not current_password:
                errors["email_current_password"] = "Current password is required."
            email_error = email_format_error(new_email)
            if email_error:
                errors["new_email"] = email_error
            if new_email != confirm_new_email:
                errors["confirm_new_email"] = "Email addresses must match."
            current_email = normalize_email(user.get("email"))
            if new_email and new_email == current_email:
                errors["new_email"] = "Enter a different email address."
            password_hash = str(user.get("password_hash") or "")
            if current_password and not verify_password(current_password, password_hash):
                errors["email_current_password"] = "Current password is incorrect."
            duplicate_user = load_user_by_email(conn, new_email) if new_email else None
            if duplicate_user and int(duplicate_user["id"]) != int(user["id"]):
                errors["new_email"] = EMAIL_CHANGE_DUPLICATE_MESSAGE
            if errors:
                return templates.TemplateResponse(
                    "account_settings.html",
                    account_settings_context(
                        request,
                        user=user,
                        errors=errors,
                        back_url=back_url,
                        email_change_values=email_change_values,
                    ),
                    status_code=400,
                )
            token, token_hash, token_expires_at = new_verification_token_record()
            delivered = send_verification_email(request, email=new_email, token=token, user_id=int(user["id"]))
            if not delivered:
                return templates.TemplateResponse(
                    "account_settings.html",
                    account_settings_context(
                        request,
                        user=user,
                        errors={"new_email": "We could not send a verification email right now. Please try again."},
                        back_url=back_url,
                        email_change_values=email_change_values,
                    ),
                    status_code=503,
                )
            now = datetime.utcnow().isoformat()
            conn.execute(
                """
                UPDATE users
                SET pending_email = ?,
                    pending_email_token_hash = ?,
                    pending_email_token_expires_at = ?,
                    pending_email_requested_at = ?,
                    pending_email_last_sent_at = ?,
                    updated_at = ?
                WHERE id = ?
                  AND is_active = 1
                """,
                (new_email, token_hash, token_expires_at, now, now, now, int(user["id"])),
            )
            conn.commit()
            updated_user = current_user(conn, request) or user
            request.state.current_user = updated_user
            request.state.current_shop = current_shop_context(conn, request)
            return templates.TemplateResponse(
                "account_settings.html",
                account_settings_context(
                    request,
                    user=updated_user,
                    message="Check your new email address to complete the change.",
                    back_url=back_url,
                ),
            )

        if action == "resend_email_change":
            if not validate_csrf(request, form):
                errors["form"] = "Please refresh the page and try again."
            pending_email = normalize_email(user.get("pending_email"))
            if not pending_email:
                errors["email_change"] = "There is no pending email change to resend."
            if errors:
                return templates.TemplateResponse(
                    "account_settings.html",
                    account_settings_context(request, user=user, errors=errors, back_url=back_url),
                    status_code=400,
                )
            token, token_hash, token_expires_at = new_verification_token_record()
            delivered = send_verification_email(request, email=pending_email, token=token, user_id=int(user["id"]))
            if not delivered:
                return templates.TemplateResponse(
                    "account_settings.html",
                    account_settings_context(
                        request,
                        user=user,
                        errors={"email_change": "We could not send a verification email right now. Please try again."},
                        back_url=back_url,
                    ),
                    status_code=503,
                )
            now = datetime.utcnow().isoformat()
            conn.execute(
                """
                UPDATE users
                SET pending_email_token_hash = ?,
                    pending_email_token_expires_at = ?,
                    pending_email_last_sent_at = ?,
                    updated_at = ?
                WHERE id = ?
                  AND pending_email = ?
                  AND is_active = 1
                """,
                (token_hash, token_expires_at, now, now, int(user["id"]), pending_email),
            )
            conn.commit()
            updated_user = current_user(conn, request) or user
            return templates.TemplateResponse(
                "account_settings.html",
                account_settings_context(
                    request,
                    user=updated_user,
                    message="A fresh change-verification email has been sent.",
                    back_url=back_url,
                ),
            )

        if action == "cancel_email_change":
            if not validate_csrf(request, form):
                errors["form"] = "Please refresh the page and try again."
                return templates.TemplateResponse(
                    "account_settings.html",
                    account_settings_context(request, user=user, errors=errors, back_url=back_url),
                    status_code=400,
                )
            now = datetime.utcnow().isoformat()
            conn.execute(
                """
                UPDATE users
                SET pending_email = NULL,
                    pending_email_token_hash = NULL,
                    pending_email_token_expires_at = NULL,
                    pending_email_requested_at = NULL,
                    pending_email_last_sent_at = NULL,
                    updated_at = ?
                WHERE id = ?
                  AND is_active = 1
                """,
                (now, int(user["id"])),
            )
            conn.commit()
            updated_user = current_user(conn, request) or user
            return templates.TemplateResponse(
                "account_settings.html",
                account_settings_context(
                    request,
                    user=updated_user,
                    message="Pending email change canceled.",
                    back_url=back_url,
                ),
            )

        if action == "sign_out_all":
            current_password = form.get("signout_current_password", "")
            if not validate_csrf(request, form):
                errors["form"] = "Please refresh the page and try again."
            if not current_password:
                errors["signout_current_password"] = "Current password is required."
            elif not verify_password(current_password, str(user.get("password_hash") or "")):
                errors["signout_current_password"] = "Current password is incorrect."
            if errors:
                return templates.TemplateResponse(
                    "account_settings.html",
                    account_settings_context(request, user=user, errors=errors, back_url=back_url),
                    status_code=400,
                )
            now = datetime.utcnow().isoformat()
            conn.execute(
                """
                UPDATE users
                SET session_version = COALESCE(session_version, 0) + 1,
                    updated_at = ?
                WHERE id = ?
                  AND is_active = 1
                """,
                (now, int(user["id"])),
            )
            delete_auth_sessions_for_user(conn, int(user["id"]))
            conn.commit()
            logout_session(request)
            response = RedirectResponse("/login?signed_out_all=1", status_code=303)
            response.delete_cookie(SESSION_COOKIE_NAME)
            return response
    finally:
        conn.close()

    current_password = form.get("current_password", "")
    new_password = form.get("new_password", "")
    confirm_new_password = form.get("confirm_new_password", "")

    if not validate_csrf(request, form):
        errors["form"] = "Please refresh the page and try again."
    if not current_password:
        errors["current_password"] = "Current password is required."
    password_error = password_rules_error(new_password)
    if password_error:
        errors["new_password"] = password_error
    if new_password != confirm_new_password:
        errors["confirm_new_password"] = "Passwords must match."

    conn = app_db_conn(row_factory=True)
    try:
        ensure_auth_schema(conn)
        user = current_user(conn, request)
        if not user:
            return RedirectResponse("/login?next=%2Faccount%2Fsettings", status_code=303)
        request.state.current_user = user
        request.state.current_shop = current_shop_context(conn, request)
        password_hash = str(user.get("password_hash") or "")
        current_password_valid = bool(current_password and verify_password(current_password, password_hash))
        if current_password and not current_password_valid:
            errors["current_password"] = "Current password is incorrect."
        if current_password_valid and new_password and verify_password(new_password, password_hash):
            errors["new_password"] = "New password must be different from your current password."
        if errors:
            return templates.TemplateResponse(
                "account_settings.html",
                account_settings_context(request, user=user, errors=errors, back_url=back_url),
                status_code=400,
            )

        now = datetime.utcnow().isoformat()
        conn.execute(
            """
            UPDATE users
            SET password_hash = ?,
                password_changed_at = ?,
                updated_at = ?
            WHERE id = ?
              AND is_active = 1
            """,
            (hash_password(new_password), now, now, int(user["id"])),
        )
        conn.commit()
        updated_user = current_user(conn, request) or user
        request.state.current_user = updated_user
        request.state.current_shop = current_shop_context(conn, request)
        return templates.TemplateResponse(
            "account_settings.html",
            account_settings_context(
                request,
                user=updated_user,
                message="Your password has been changed.",
                back_url=back_url,
            ),
        )
    finally:
        conn.close()


@app.post("/account/settings/resend-verification", response_class=HTMLResponse)
async def account_settings_resend_verification(request: Request):
    raw_body = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qs(raw_body, keep_blank_values=True)
    form = {key: values[0].strip() for key, values in parsed.items()}
    back_url = safe_next_url(form.get("back_url")) or "/pro/dashboard"
    conn = app_db_conn(row_factory=True)
    try:
        ensure_auth_schema(conn)
        user = current_user(conn, request)
        if not user:
            return RedirectResponse("/login?next=%2Faccount%2Fsettings", status_code=303)
        request.state.current_user = user
        request.state.current_shop = current_shop_context(conn, request)
        if user_email_verified(user):
            return templates.TemplateResponse(
                "account_settings.html",
                account_settings_context(request, user=user, back_url=back_url),
            )
        if not validate_csrf(request, form):
            return templates.TemplateResponse(
                "account_settings.html",
                account_settings_context(
                    request,
                    user=user,
                    errors={"form": "Please refresh the page and try again."},
                    back_url=back_url,
                ),
                status_code=400,
            )
        cooldown_remaining = verification_resend_cooldown_remaining(user)
        if cooldown_remaining > 0:
            return templates.TemplateResponse(
                "account_settings.html",
                account_settings_context(
                    request,
                    user=user,
                    errors={"verification": f"Please wait {cooldown_remaining} seconds before requesting another verification email."},
                    back_url=back_url,
                ),
                status_code=429,
            )
        token, token_hash, token_expires_at = new_verification_token_record()
        delivered = send_verification_email(request, email=str(user["email"]), token=token, user_id=int(user["id"]))
        if not delivered:
            return templates.TemplateResponse(
                "account_settings.html",
                account_settings_context(
                    request,
                    user=user,
                    errors={"verification": "We could not send a verification email right now. Please try again."},
                    back_url=back_url,
                ),
                status_code=503,
            )
        now = datetime.utcnow().isoformat()
        conn.execute(
            """
            UPDATE users
            SET verification_token_hash = ?,
                verification_token_expires_at = ?,
                verification_email_last_sent_at = ?,
                updated_at = ?
            WHERE id = ?
              AND email_verified_at IS NULL
              AND is_active = 1
            """,
            (token_hash, token_expires_at, now, now, int(user["id"])),
        )
        conn.commit()
        updated_user = current_user(conn, request) or user
        request.state.current_user = updated_user
        request.state.current_shop = current_shop_context(conn, request)
        return templates.TemplateResponse(
            "account_settings.html",
            account_settings_context(
                request,
                user=updated_user,
                message="A fresh verification email has been sent.",
                back_url=back_url,
            ),
        )
    finally:
        conn.close()


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request):
    raw_body = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qs(raw_body, keep_blank_values=True)
    form = {key: values[0].strip() for key, values in parsed.items()}
    next_url = safe_next_url(form.get("next"))
    errors: dict[str, str] = {}
    email = normalize_email(form.get("email"))
    password = form.get("password", "")
    if not validate_csrf(request, form):
        errors["form"] = "Please refresh the page and try again."
    if not email:
        errors["email"] = "Email address is required."
    if not password:
        errors["password"] = "Password is required."
    conn = app_db_conn(row_factory=True)
    try:
        user = load_user_by_email(conn, email) if email else None
        if not errors and (not user or not user.get("is_active") or not verify_password(password, user.get("password_hash", ""))):
            errors["form"] = "Email or password is incorrect."
        if errors:
            return templates.TemplateResponse(
                "login.html",
                {
                    "request": request,
                    "csrf_token": csrf_token(request),
                    "next": next_url,
                    "values": {"email": email},
                    "errors": errors,
                    "message": "",
                },
                status_code=400,
            )
        login_session(request, int(user["id"]))
    finally:
        conn.close()
    return RedirectResponse(next_url or "/pro/dashboard", status_code=303)


@app.post("/logout")
async def logout_submit(request: Request):
    raw_body = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qs(raw_body, keep_blank_values=True)
    form = {key: values[0].strip() for key, values in parsed.items()}
    if not validate_csrf(request, form):
        return RedirectResponse("/", status_code=303)
    logout_session(request)
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logging.exception("UNHANDLED_EXCEPTION")
    rid = _request_id_ctx.get()
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal Server Error",
            "request_id": rid,
        },
    )

ADMIN_KEY = os.getenv("TORQUEMECH_ADMIN_KEY", "change-me")

from fastapi import Query

@app.get("/admin/feedback", response_class=HTMLResponse)
def admin_feedback(key: str = Query(None)):
    if key != ADMIN_KEY:
        return HTMLResponse("Unauthorized", status_code=401)

    conn = app_db_conn(row_factory=True)

    rows = conn.execute("""
        SELECT id, created_at, is_read, payload_json
        FROM feedback
        ORDER BY id DESC
    """).fetchall()

    conn.close()

    html = """
    <!doctype html>
    <html>
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>TorqueMech Feedback Admin</title>
      <style>
        body {
          font-family: Arial, sans-serif;
          background: #0b1220;
          color: #e5e7eb;
          margin: 0;
          padding: 24px;
        }
        h1 {
          margin-bottom: 20px;
        }
        .card {
          background: #111827;
          border: 1px solid #243041;
          border-radius: 12px;
          padding: 16px;
          margin-bottom: 16px;
        }
        .meta {
          font-size: 14px;
          color: #9ca3af;
          margin-bottom: 10px;
        }
        pre {
          white-space: pre-wrap;
          word-wrap: break-word;
          background: #0f172a;
          padding: 12px;
          border-radius: 8px;
          overflow-x: auto;
        }
      </style>
    </head>
    <body>
      <h1>TorqueMech Feedback Admin</h1>
    """

    if not rows:
        html += "<p>No feedback found.</p>"
    else:
        for row in rows:
            html += f"""
            <div class="card">
              <div class="meta">
                <strong>ID:</strong> {row['id']} |
                <strong>Created:</strong> {row['created_at']} |
                <strong>Read:</strong> {row['is_read']}
              </div>
              <pre>{row['payload_json']}</pre>
            </div>
            """

    html += """
    </body>
    </html>
    """
    return html

# ===============================
# OBD (SQLite-backed DTC DB)
# ===============================

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

OBD_STATE_DIR = Path("/data") if Path("/data").exists() else STATE_DIR
OBD_STATE_DIR.mkdir(parents=True, exist_ok=True)
OBD_SQLITE_PATH = OBD_STATE_DIR / "obd.sqlite"
OBD_ADMIN_META_PATH = OBD_STATE_DIR / "obd_admin_meta.json"
OBD_SEED_JSON_PATH = BASE_DIR / "data" / "obd_codes.json"

def obd_sqlite_conn(*, row_factory: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(str(OBD_SQLITE_PATH))
    if USE_LOCAL_SQLITE_COMPAT:
        try:
            conn.execute("PRAGMA journal_mode=MEMORY")
            conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.OperationalError as exc:
            logging.warning("Falling back to TRUNCATE journal mode for OBD DB: %s", exc)
            try:
                conn.execute("PRAGMA journal_mode=TRUNCATE")
                conn.execute("PRAGMA synchronous=NORMAL")
            except sqlite3.OperationalError as fallback_exc:
                logging.warning("Skipping local SQLite compatibility PRAGMAs for OBD DB: %s", fallback_exc)
    if row_factory:
        conn.row_factory = sqlite3.Row
    return conn

def init_obd_db() -> None:
    conn = obd_sqlite_conn()
    cur = conn.cursor()

    # Main codes table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS dtc (
            code TEXT PRIMARY KEY,
            system TEXT NOT NULL,
            generic INTEGER NOT NULL,
            title TEXT,
            description TEXT,
            possible_causes TEXT,
            quick_checks TEXT
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_dtc_system ON dtc(system)")

    # Requests table (for Beta → you crowdsource missing codes)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS dtc_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            requested_at TEXT NOT NULL,
            ip TEXT,
            user_agent TEXT
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_dtc_requests_code ON dtc_requests(code)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_dtc_requests_requested_at ON dtc_requests(requested_at)")

    conn.commit()
    conn.close()

def obd_conn() -> sqlite3.Connection:
    return obd_sqlite_conn()

def obd_seed_from_json_if_empty() -> None:
    if not OBD_SEED_JSON_PATH.exists():
        return

    try:
        data = json.loads(OBD_SEED_JSON_PATH.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            return
    except Exception:
        return

    conn = obd_conn()
    cur = conn.cursor()

    for raw_code, item in data.items():
        code = "".join(ch for ch in (raw_code or "").upper() if ch.isalnum())[:7]
        if len(code) < 4:
            continue

        system = code[0] if code else "P"
        generic = 1 if len(code) >= 2 and code[1] == "0" else 0

        title = (item or {}).get("title", "")
        desc = (item or {}).get("description", "")
        causes = (item or {}).get("possible_causes", []) or []
        checks = (item or {}).get("quick_checks", []) or []

        cur.execute(
            """
            INSERT OR REPLACE INTO dtc
              (code, system, generic, title, description, possible_causes, quick_checks)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                code, system, int(generic), title, desc,
                json.dumps(causes, ensure_ascii=False),
                json.dumps(checks, ensure_ascii=False),
            ),
        )

    conn.commit()
    conn.close()


def load_obd_seed_entry(code: str) -> Dict[str, Any] | None:
    norm = "".join(ch for ch in (code or "").upper() if ch.isalnum())[:7]
    if len(norm) < 4 or not OBD_SEED_JSON_PATH.exists():
        return None

    try:
        data = json.loads(OBD_SEED_JSON_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    entry = data.get(norm)
    return entry if isinstance(entry, dict) else None


def normalize_obd_text_list(items: Any) -> List[str]:
    if not isinstance(items, list):
        return []

    normalized: List[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def normalize_obd_difficulty(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    difficulty_map = {
        "easy": "Easy",
        "moderate": "Moderate",
        "advanced": "Advanced",
    }
    return difficulty_map.get(normalized)


def normalize_obd_related_code_list(items: Any) -> List[str]:
    if not isinstance(items, list):
        return []

    normalized: List[str] = []
    seen: set[str] = set()
    for item in items:
        code = "".join(ch for ch in str(item or "").upper() if ch.isalnum())[:7]
        if len(code) < 4 or code in seen:
            continue
        seen.add(code)
        normalized.append(code)
    return normalized


def build_obd_knowledge_sections(code: str) -> Dict[str, Any]:
    norm = "".join(ch for ch in (code or "").upper() if ch.isalnum())[:7]
    seed_entry = load_obd_seed_entry(code) or {}
    lean_causes_by_vehicle = [
        "Toyota Camry: intake manifold gasket leaks, cracked intake boots, and MAF contamination are common lean-code starting points.",
        "Ford F-150: vacuum leaks, PCV hose failures, intake duct leaks, and unmetered air after the MAF are common P0171/P0174 causes.",
        "Chevy Silverado: intake manifold leaks, dirty MAF sensors, and small vacuum leaks are frequent lean-code triggers.",
        "Honda Accord: vacuum leaks, intake tube leaks, and fuel-trim imbalance commonly trigger lean codes before an oxygen sensor is at fault.",
    ]
    lean_diagnostic_steps = [
        "Review short- and long-term fuel trims at idle, 2500 RPM, and steady cruise before replacing sensors.",
        "If rough idle is strongest at idle, inspect vacuum leaks, PCV hoses, intake boots, brake-booster hose, and manifold gasket areas first.",
        "If the lean condition appears mainly under load, inspect fuel pressure, fuel volume, restricted filters, weak pumps, and injector delivery.",
        "If P0171 and P0174 appear together, inspect shared intake leaks, MAF contamination, intake duct leaks, PCV faults, and other unmetered air sources.",
        "If fuel trims are heavily positive at idle but improve with RPM, a vacuum or intake leak is more likely.",
        "If trims get worse at highway speed or under load, fuel delivery or MAF accuracy becomes more likely than a small vacuum leak.",
    ]
    lean_overrides = {
        "P0171": {
            "causes": lean_causes_by_vehicle,
            "diagnostic_steps": lean_diagnostic_steps,
        },
        "P0174": {
            "causes": lean_causes_by_vehicle,
            "diagnostic_steps": lean_diagnostic_steps,
        },
    }
    catalyst_causes_by_vehicle = [
        "Toyota Camry: aging catalytic converters, upstream air/fuel sensor issues, and unresolved fuel-control faults are common catalyst-code starting points.",
        "Ford F-150: exhaust leaks near the manifolds, aging converters, and converter efficiency failures are common P0420/P0430 causes.",
        "Chevy Silverado: misfire damage, rich-running conditions, and converter overheating often trigger catalyst efficiency codes.",
        "Honda Accord: aging converters, upstream or downstream O2 sensor performance issues, and fuel-control problems are frequent catalyst-code causes.",
    ]
    catalyst_diagnostic_steps = [
        "Compare upstream and downstream O2 or air/fuel sensor activity after the engine is fully warm and in closed loop.",
        "If the rear O2 sensor waveform closely follows the front sensor, catalyst oxygen storage is weak and converter efficiency becomes more likely.",
        "If there is recent misfire history, inspect ignition, injector, compression, and fuel faults before replacing the converter.",
        "If there is sulfur smell, excessive converter heat, or glowing converter symptoms, inspect for rich-running, leaking injectors, or fuel-control faults.",
        "If the engine burns oil or consumes coolant, inspect for converter contamination risk before condemning the catalyst alone.",
        "If both catalyst codes appear with fuel-trim, rich, lean, or MAF codes, diagnose the upstream fuel-control issue before calling both converters failed.",
    ]
    catalyst_overrides = {
        "P0420": {
            "causes": catalyst_causes_by_vehicle,
            "diagnostic_steps": catalyst_diagnostic_steps,
        },
        "P0430": {
            "causes": catalyst_causes_by_vehicle,
            "diagnostic_steps": catalyst_diagnostic_steps,
        },
    }
    evap_causes_by_vehicle = [
        "Toyota Camry: loose gas caps, purge valve leakage, cracked EVAP hoses, and canister-side leaks are common EVAP leak starting points.",
        "Ford F-150: vent valve failures, rusted EVAP lines, cracked hoses, and canister-area leaks are frequent P0455/P0456 causes.",
        "Chevy Silverado: charcoal canister vent valve issues, tank vent problems, and rear EVAP hose leaks commonly trigger EVAP leak codes.",
        "Honda Accord: purge solenoid leaks, fuel cap sealing issues, and small EVAP hose cracks often trigger EVAP leak faults.",
    ]
    evap_diagnostic_steps = [
        "Inspect the fuel cap seal and filler neck first, but avoid stopping there if the code returns after cap replacement.",
        "If the code returns after replacing the gas cap, inspect purge valve sealing, vent valve sealing, and EVAP hose connections next.",
        "If fuel smell is strongest near the rear of the vehicle, inspect the charcoal canister, filler neck, tank seals, and rear EVAP lines.",
        "If the vehicle is difficult to refuel or the pump clicks off repeatedly, inspect the vent valve and vent path for restriction.",
        "If repeated EVAP leak codes appear without drivability symptoms, focus on leak testing outside normal engine operation instead of engine-performance parts.",
        "If a smoke test shows a small leak near the tank area, inspect hoses, canister fittings, fuel-pump seal, and vent seals before replacing larger components.",
    ]
    evap_overrides = {
        "P0455": {
            "causes": evap_causes_by_vehicle,
            "diagnostic_steps": evap_diagnostic_steps,
        },
        "P0456": {
            "causes": evap_causes_by_vehicle,
            "diagnostic_steps": evap_diagnostic_steps,
        },
    }
    airflow_causes_by_vehicle = [
        "Toyota Camry: dirty MAF sensors, cracked intake boots, loose air-box seals, and intake leaks after the MAF are common airflow-code starting points.",
        "Ford F-150: cracked intake tubes, contaminated MAF sensors, loose clamps, and unmetered air after the MAF are frequent P0101/P0113 causes.",
        "Chevy Silverado: vacuum leaks, intake duct leaks, and MAF contamination from oiled aftermarket filters are common airflow and IAT fault triggers.",
        "Honda Accord: intake leaks, damaged sensor connectors, and MAF/IAT harness issues often trigger airflow or intake-temperature codes.",
    ]
    airflow_diagnostic_steps = [
        "If P0101 appears with P0171 or P0174, inspect for unmetered air after the MAF before replacing the MAF sensor.",
        "If high idle appears with positive fuel trims, inspect intake boots, vacuum hoses, PCV plumbing, and throttle-body gasket areas for leaks.",
        "If unplugging the MAF improves idle quality, compare MAF readings and connector integrity because the airflow signal may be biased.",
        "If MAF grams per second is unusually low at hot idle, inspect for sensor contamination, airflow restriction, dirty sensing wires, or intake duct problems.",
        "If P0113 appears with cold-start issues, inspect the IAT connector, signal wiring, reference behavior, and sensor voltage before replacing parts.",
        "If the IAT reading is stuck extremely cold, inspect for an open circuit, unplugged sensor, poor terminal fit, or integrated MAF/IAT assembly fault.",
    ]
    airflow_overrides = {
        "P0101": {
            "causes": airflow_causes_by_vehicle,
            "diagnostic_steps": airflow_diagnostic_steps,
        },
        "P0113": {
            "causes": airflow_causes_by_vehicle,
            "diagnostic_steps": airflow_diagnostic_steps,
        },
    }
    downstream_o2_causes_by_vehicle = [
        "Toyota Camry: aging downstream O2 sensors, catalyst efficiency concerns, and exhaust-side wiring wear are common downstream-sensor code starting points.",
        "Ford F-150: wiring damage near exhaust heat, heater circuit failures, and connector issues are frequent P0138/P0141/P0158 causes.",
        "Chevy Silverado: rich-running conditions, downstream sensor contamination, and exhaust-area harness damage are common causes.",
        "Honda Accord: downstream sensor aging, heater circuit faults, and connector or harness issues often trigger these codes.",
    ]
    downstream_o2_diagnostic_steps = [
        "If the rear O2 sensor is stuck high with no major drivability issue, inspect downstream sensor bias, catalyst behavior, and exhaust-side wiring first.",
        "If a heater circuit code is strongest during cold starts, verify heater power, ground, fuse protection, and wiring before replacing the sensor.",
        "If rich-running symptoms appear with high O2 voltage, inspect fuel control, fuel pressure, leaking injectors, and trim data before replacing the sensor.",
        "Inspect for exhaust leaks before the downstream sensor because added oxygen can cause false switching behavior and inaccurate catalyst or sensor readings.",
        "If catalyst codes repeat with downstream O2 faults, confirm converter condition and upstream fuel control before replacing sensors only.",
        "Inspect harness routing near hot exhaust, melted insulation, loose terminals, and connector contamination before condemning the downstream sensor.",
    ]
    downstream_o2_overrides = {
        "P0138": {
            "causes": downstream_o2_causes_by_vehicle,
            "diagnostic_steps": downstream_o2_diagnostic_steps,
        },
        "P0141": {
            "causes": downstream_o2_causes_by_vehicle,
            "diagnostic_steps": downstream_o2_diagnostic_steps,
        },
        "P0158": {
            "causes": downstream_o2_causes_by_vehicle,
            "diagnostic_steps": downstream_o2_diagnostic_steps,
        },
    }
    temperature_causes_by_vehicle = [
        "Toyota Camry: stuck-open thermostats, coolant temperature sensor drift, and low coolant are common temperature-code starting points.",
        "Ford F-150: thermostat failures, coolant temperature connector corrosion, and harness issues are frequent P0128/P0110-adjacent causes.",
        "Chevy Silverado: coolant level issues, thermostat wear, connector problems, and sensor data drift often trigger temperature-related codes.",
        "Honda Accord: aging thermostats, ECT sensor issues, IAT connector faults, and intake-temperature circuit problems are common.",
    ]
    temperature_diagnostic_steps = [
        "If the engine takes too long to reach operating temperature, verify warm-up data because the thermostat is likely stuck open.",
        "If cabin heat is weak at idle, inspect coolant level, coolant flow, air pockets, and thermostat behavior before replacing sensors.",
        "If the temperature gauge is inconsistent, compare ECT sensor readings to ambient temperature, infrared readings, and scan data before replacing parts.",
        "If P0128 returns after thermostat replacement, inspect coolant level, ECT sensor accuracy, connector condition, and thermostat housing sealing.",
        "If P0110 appears with cold-start drivability issues, inspect intake air temperature sensor wiring, connector fit, and signal voltage before replacing components.",
        "If IAT or ECT data is implausible on a cold engine, diagnose the circuit and connector before assuming the sensor is the only fault.",
    ]
    temperature_overrides = {
        "P0128": {
            "causes": temperature_causes_by_vehicle,
            "diagnostic_steps": temperature_diagnostic_steps,
        },
        "P0110": {
            "causes": temperature_causes_by_vehicle,
            "diagnostic_steps": temperature_diagnostic_steps,
        },
    }
    misfire_causes = [
        "Ignition coil breakdown under load or heat soak",
        "Spark plug fouling, worn electrodes, incorrect gap, or oil contamination",
        "Injector leakage causing cold-start misfire or fuel-washed plug tips",
        "Low compression, valve sealing problems, or worn valvetrain components",
        "Small head gasket seep causing overnight coolant intrusion into one cylinder",
        "Vacuum leaks, intake runner leaks, or PCV leaks affecting cylinder balance",
    ]
    misfire_diagnostic_steps = [
        "If the misfire happens only on cold start, inspect for injector leakage, coolant seep, or valve sealing issues before replacing coils only.",
        "If the misfire appears under acceleration or load, inspect ignition coil output, plug gap, plug condition, and coil boots for breakdown.",
        "If the same-cylinder misfire stays after a coil swap, move toward injector testing, compression testing, and leak-down testing.",
        "If coolant loss appears with an overnight rough start, head gasket seep or coolant intrusion suspicion increases.",
        "If the misfire repeats with clean plugs and no obvious ignition fault, compression and leak-down testing become important.",
        "If multiple cylinders misfire with positive fuel trims, inspect vacuum leaks, intake leaks, PCV leaks, and shared fuel delivery.",
    ]
    misfire_overrides = {
        "P0300": {
            "causes": misfire_causes,
            "diagnostic_steps": misfire_diagnostic_steps,
        },
        "P0301": {
            "causes": misfire_causes,
            "diagnostic_steps": misfire_diagnostic_steps,
        },
        "P0302": {
            "causes": misfire_causes,
            "diagnostic_steps": misfire_diagnostic_steps,
        },
        "P0303": {
            "causes": misfire_causes,
            "diagnostic_steps": misfire_diagnostic_steps,
        },
        "P0304": {
            "causes": misfire_causes,
            "diagnostic_steps": misfire_diagnostic_steps,
        },
    }
    low_voltage_causes = [
        "Weak battery that no longer holds proper charge under load",
        "Alternator output failure when headlights, blower motor, AC, or other electrical loads are applied",
        "Loose grounds, corroded battery terminals, or charging-cable voltage drop",
        "Serpentine belt slip, weak tensioner, or pulley issue reducing alternator output",
        "Parasitic drain confusion creating repeated low-voltage or no-start symptoms after parking",
    ]
    low_voltage_diagnostic_steps = [
        "If voltage is mostly low at idle, inspect alternator output, belt condition, belt tension, and pulley behavior first.",
        "If the battery repeatedly dies after replacement, perform a charging-system load test before replacing more parts.",
        "If voltage drops with headlights, blower motor, rear defroster, or AC load, charging-system weakness or cable voltage drop becomes more likely.",
        "If the battery warning light appears with P0562, confirm charging voltage before replacing the battery only.",
        "If no-start symptoms repeat after parking overnight, separate parasitic draw testing from charging failure diagnosis.",
    ]
    low_voltage_overrides = {
        "P0562": {
            "causes": low_voltage_causes,
            "diagnostic_steps": low_voltage_diagnostic_steps,
        },
    }
    timing_reference_causes = [
        "Crankshaft position sensor signal irregularity or dropout",
        "Cam/crank correlation issues affecting the timing reference signal",
        "Damaged reluctor wheel teeth, debris, excessive air gap, or signal interruption",
        "Connector corrosion, loose terminals, or wiring faults near CKP or CMP sensors",
        "Timing chain stretch or mechanical timing movement creating unstable reference timing",
    ]
    timing_reference_diagnostic_steps = [
        "If intermittent no-start or stalling happens during warm restart, inspect crank reference signal stability before replacing unrelated parts.",
        "If the tachometer drops out during cranking, verify CKP signal loss and power or ground integrity at the sensor circuit.",
        "If scan data shows an unstable RPM signal, confirm the signal with circuit and waveform testing before replacing parts.",
        "Use CKP and CMP waveform testing to compare signal shape, dropout, and correlation instead of relying on blind sensor replacement.",
        "If timing-reference faults return after sensor replacement, inspect reluctor condition, sensor air gap, timing chain stretch, and mechanical timing.",
    ]
    timing_reference_overrides = {
        "P0373": {
            "causes": timing_reference_causes,
            "diagnostic_steps": timing_reference_diagnostic_steps,
        },
    }
    overrides = (
        misfire_overrides.get(norm, {})
        or lean_overrides.get(norm, {})
        or catalyst_overrides.get(norm, {})
        or evap_overrides.get(norm, {})
        or airflow_overrides.get(norm, {})
        or downstream_o2_overrides.get(norm, {})
        or temperature_overrides.get(norm, {})
        or low_voltage_overrides.get(norm, {})
        or timing_reference_overrides.get(norm, {})
    )
    return {
        "causes": normalize_obd_text_list(overrides.get("causes") or seed_entry.get("causes")),
        "symptoms": normalize_obd_text_list(seed_entry.get("symptoms")),
        "diagnostic_steps": normalize_obd_text_list(overrides.get("diagnostic_steps") or seed_entry.get("diagnostic_steps")),
        "difficulty": normalize_obd_difficulty(seed_entry.get("difficulty")),
        "related_codes": normalize_obd_related_code_list(seed_entry.get("related_codes")),
    }

from fastapi import Query

OBD_IMPORT_KEY = os.getenv("TORQUEMECH_OBD_IMPORT_KEY", ADMIN_KEY)

# IMPORTANT:
# After updating data/obd_codes.json, redeploy the app and run
# /admin/obd/import?key=YOUR_KEY to sync the live SQLite dtc table.
# The live OBD lookup reads from data/obd.sqlite, not directly from the JSON file.

@app.get("/admin/obd/import")
def admin_import_obd_codes(key: str = Query(...)):
    # simple Beta protection: a secret key in the URL
    if key != OBD_IMPORT_KEY:
        raise HTTPException(status_code=403, detail="Unauthorized")

    if not OBD_SEED_JSON_PATH.exists():
        raise HTTPException(status_code=404, detail="obd_codes.json not found")

    try:
        data = json.loads(OBD_SEED_JSON_PATH.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("obd_codes.json must be an object {code: {...}}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid obd_codes.json: {e}")

    conn = obd_conn()
    cur = conn.cursor()

    count = 0
    for raw_code, item in data.items():
        code = "".join(ch for ch in (raw_code or "").upper() if ch.isalnum())[:7]
        if len(code) < 4:
            continue

        system = code[0] if code else "P"
        generic = 1 if len(code) >= 2 and code[1] == "0" else 0

        title = (item or {}).get("title", "")
        desc = (item or {}).get("description", "")
        causes = (item or {}).get("possible_causes", []) or []
        checks = (item or {}).get("quick_checks", []) or []

        cur.execute(
            """
            INSERT OR REPLACE INTO dtc
              (code, system, generic, title, description, possible_causes, quick_checks)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                code,
                system,
                int(generic),
                title,
                desc,
                json.dumps(causes, ensure_ascii=False),
                json.dumps(checks, ensure_ascii=False),
            ),
        )
        count += 1

    conn.commit()
    conn.close()

    return {"ok": True, "imported": count}

@app.get("/api/obd/lookup")
def obd_lookup(code: str):
    norm = "".join(ch for ch in (code or "").upper() if ch.isalnum())[:7]
    if len(norm) < 4:
        raise HTTPException(status_code=400, detail="Invalid OBD code.")

    conn = obd_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM dtc WHERE code = ?", (norm,))
    row = cur.fetchone()
    conn.close()
    metric_incr("obd_lookup")

    if not row:
        raise HTTPException(status_code=404, detail=f"Code {norm} not found (yet).")

    return {
        "code": row["code"],
        "title": row["title"] or "",
        "description": row["description"] or "",
        "possible_causes": json.loads(row["possible_causes"] or "[]"),
        "quick_checks": json.loads(row["quick_checks"] or "[]"),
        "system": row["system"],
        "generic": bool(row["generic"]),
    }

@app.get("/api/obd/search")
def obd_search(q: str, limit: int = 20):
    norm = "".join(ch for ch in (q or "").upper() if ch.isalnum())[:7]
    if len(norm) < 2:
        return {"q": norm, "results": []}

    limit = max(1, min(int(limit), 50))

    metric_incr("obd_search")

    conn = obd_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT code, title, system, generic
        FROM dtc
        WHERE code LIKE ?
        ORDER BY code
        LIMIT ?
        """,
        (norm + "%", limit),
    )
    rows = cur.fetchall()
    conn.close()

    return {
        "q": norm,
        "results": [
            {"code": r["code"], "title": r["title"] or "", "system": r["system"], "generic": bool(r["generic"])}
            for r in rows
        ],
    }

from datetime import datetime

@app.post("/api/obd/request")
def obd_request(code: str):
    norm = "".join(ch for ch in (code or "").upper() if ch.isalnum())[:7]
    if len(norm) < 4:
        raise HTTPException(status_code=400, detail="Invalid OBD code.")

    conn = obd_conn()
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO dtc_requests (code, requested_at) VALUES (?, ?)",
        (norm, datetime.utcnow().isoformat()),
    )

    conn.commit()
    conn.close()

    return {"ok": True, "code": norm}

@app.get("/admin/obd-requests", response_class=HTMLResponse)
def admin_obd_requests(request: Request, key: str | None = None):
    if key != ADMIN_KEY:
        raise HTTPException(status_code=404, detail="Not Found")
    
    conn = obd_conn()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
          code,
          COUNT(*) AS requests,
          MAX(requested_at) AS last_requested
        FROM dtc_requests
        GROUP BY code
        ORDER BY requests DESC, last_requested DESC
        LIMIT 200
        """
    )
    rows = cur.fetchall()
    conn.close()

    meta = get_admin_meta()
    last_viewed = meta.get("last_viewed")

    conn2 = obd_conn()
    cur2 = conn2.cursor()

    if last_viewed:
        cur2.execute("SELECT COUNT(*) AS n FROM dtc_requests WHERE requested_at > ?", (last_viewed,))
        new_count = int(cur2.fetchone()["n"])
    else:
        cur2.execute("SELECT COUNT(*) AS n FROM dtc_requests")
        new_count = int(cur2.fetchone()["n"])

    conn2.close()

    # rows is a list of sqlite3.Row; convert to plain dicts for Jinja
    items = [
        {
            "code": r["code"],
            "requests": r["requests"],
            "last_requested": r["last_requested"],
        }
        for r in rows
    ]

    # mark admin page as viewed
    set_admin_last_viewed()

    return templates.TemplateResponse(
        "admin_obd_requests.html",
        {
            "request": request,
            "items": items,
            "new_count": new_count,
        },
    )

@app.get("/pro-preview", response_class=HTMLResponse, include_in_schema=False)
def pro_home_preview(request: Request):
    return templates.TemplateResponse(
        "pro_home_preview.html",
        {"request": request},
    )


@app.get("/about", response_class=HTMLResponse)
def about(request: Request):
    return templates.TemplateResponse("about.html", {"request": request})

@app.get("/about", response_class=HTMLResponse)
def about(request: Request):
    return templates.TemplateResponse("about.html", {"request": request})


@app.get("/parts-center", response_class=HTMLResponse)
def parts_center(request: Request):
    metric_incr("page_parts_center")
    return templates.TemplateResponse("parts_center.html", {"request": request})


@app.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
def privacy(request: Request):
    return templates.TemplateResponse("privacy.html", {"request": request})


@app.get("/terms", response_class=HTMLResponse, include_in_schema=False)
def terms(request: Request):
    return templates.TemplateResponse("terms.html", {"request": request})


@app.get("/disclaimer", response_class=HTMLResponse, include_in_schema=False)
def disclaimer(request: Request):
    return templates.TemplateResponse("disclaimer.html", {"request": request})


@app.get("/shop-profile", response_class=HTMLResponse, include_in_schema=False)
def shop_profile_form(request: Request, saved: str = ""):
    # Beta gate: keep Pro profile UI inaccessible until Pro modules launch.
    raise HTTPException(status_code=404, detail="Not found")
    return templates.TemplateResponse(
        "shop_profile.html",
        {
            "request": request,
            "profile": load_shop_profile(),
            "saved": saved == "1",
        },
    )


@app.post("/shop-profile", include_in_schema=False)
async def shop_profile_save(request: Request):
    # Beta gate: keep Pro profile UI inaccessible until Pro modules launch.
    raise HTTPException(status_code=404, detail="Not found")
    raw_body = (await request.body()).decode("utf-8", errors="replace")
    form = parse_qs(raw_body, keep_blank_values=True)

    def form_value(name: str) -> str:
        values = form.get(name) or [""]
        return values[0]

    save_shop_profile(
        {
            "shop_name": form_value("shop_name"),
            "phone": form_value("phone"),
            "email": form_value("email"),
            "address": form_value("address"),
            "website": form_value("website"),
            "scheduling_link": form_value("scheduling_link"),
            "logo_url": form_value("logo_url"),
            "labor_rate_default": form_value("labor_rate_default"),
            "tax_rate_default": form_value("tax_rate_default"),
            "warranty_note": form_value("warranty_note"),
            "quote_expiration_days": form_value("quote_expiration_days"),
            "custom_footer_note": form_value("custom_footer_note"),
        }
    )
    if form_value("next") == "preview":
        return RedirectResponse("/shop-profile/pdf-preview", status_code=303)
    return RedirectResponse("/shop-profile?saved=1", status_code=303)

# ============================================================
# Startup Checks
# ============================================================

@app.on_event("startup")
def startup_checks() -> None:
    required = [
        STATIC_DIR,
        TEMPLATES_DIR,
        TEMPLATES_DIR / "layout.html",
        TEMPLATES_DIR / "home.html",
        TEMPLATES_DIR / "estimator.html",
        TEMPLATES_DIR / "obd.html",
        STATIC_DIR / "style.css",
    ]

    missing = [p for p in required if not p.exists()]
    if missing:
        raise RuntimeError(
            "Missing required files:\n" + "\n".join(str(p) for p in missing)
        )

    if not using_postgres():
        init_db()
        init_metrics_db()
        init_shop_profile_db()
        init_pro_crm_schema_db()
        conn = app_db_conn(row_factory=True)
        try:
            ensure_auth_schema(conn)
            ensure_password_reset_schema(conn)
            ensure_shop_profile_schema(conn)
        finally:
            conn.close()
    init_obd_db()
    obd_seed_from_json_if_empty() 
    _ = load_services_catalog()

REPAIR_GUIDES_PATH = BASE_DIR / "repair_guides_data.json"

def load_repair_guides():
    if not REPAIR_GUIDES_PATH.exists():
        return {}

    try:
        return json.loads(REPAIR_GUIDES_PATH.read_text())
    except Exception:
        return {}


def normalize_repair_guide_list(raw: Any) -> List[str]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []

    normalized: List[str] = []
    for item in raw:
        text = str(item or "").strip()
        if text:
            normalized.append(text)
    return normalized


def infer_mechanic_confidence_guidance(*parts: Any) -> Dict[str, List[str]]:
    text = " ".join(str(part or "") for part in parts).lower()

    if any(term in text for term in ("p0300", "misfire", "spark plug", "ignition coil", "rough idle")):
        return {
            "inspection_priority": [
                "Inspect ignition components first when misfire evidence is present.",
                "Verify fuel trim behavior before replacing parts.",
                "Check for vacuum leaks when misfires are random or lean-related.",
            ],
            "confidence_cues": [
                "Common repair when plug wear or coil failure is confirmed.",
                "Multiple causes possible when misfire counters move between cylinders.",
                "Further diagnostics may be required if fuel trim or compression clues do not match ignition faults.",
            ],
        }

    if any(term in text for term in ("overheat", "cooling", "water pump", "radiator", "thermostat", "coolant")):
        return {
            "inspection_priority": [
                "Verify coolant level and condition first.",
                "Inspect thermostat behavior and circulation evidence together.",
                "Pressure test the cooling system when coolant loss or smell is present.",
            ],
            "confidence_cues": [
                "Inspection recommended before replacement.",
                "Multiple causes possible when temperature behavior changes with vehicle speed.",
                "Access difficulty may vary by engine and drivetrain.",
            ],
        }

    if any(term in text for term in ("no crank", "starter", "battery", "charging", "alternator")):
        return {
            "inspection_priority": [
                "Verify battery voltage and load-test results first.",
                "Inspect cable voltage drop and grounds before replacement.",
                "Confirm starter command or charging output before pricing parts.",
            ],
            "confidence_cues": [
                "Common repair when electrical checks confirm the failed component.",
                "Inspection recommended before replacement.",
                "Further diagnostics may be required for intermittent command or ground faults.",
            ],
        }

    if any(term in text for term in ("brake", "pad", "rotor", "caliper")):
        return {
            "inspection_priority": [
                "Inspect rotor condition and pad thickness first.",
                "Verify inner and outer pad wear pattern.",
                "Check caliper hardware movement before quoting pad-only service.",
            ],
            "confidence_cues": [
                "Common repair when wear measurements support it.",
                "Inspection recommended before replacement.",
                "Multiple causes possible when noise changes with temperature or braking load.",
            ],
        }

    return {
        "inspection_priority": [
            "Confirm the symptom, code, or inspection evidence before replacement.",
            "Check related systems when the failure pattern is not isolated.",
        ],
        "confidence_cues": [
            "Inspection recommended before replacement.",
            "Further diagnostics may be required when evidence is mixed.",
        ],
    }


def normalize_repair_guide_range(raw: Any, min_key: str, max_key: str) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None

    min_value = raw.get(min_key)
    max_value = raw.get(max_key)
    if min_value in (None, "") and max_value in (None, ""):
        return None

    return {
        min_key: min_value,
        max_key: max_value,
    }


def normalize_repair_guide_media_item(media: Any, fallback_alt: str) -> Optional[Dict[str, str]]:
    if isinstance(media, str) and media.strip():
        return {
            "src": media.strip(),
            "caption": "",
            "alt": fallback_alt,
        }

    if isinstance(media, dict):
        src = (
            media.get("src")
            or media.get("url")
            or media.get("path")
            or media.get("image")
        )
        if isinstance(src, str) and src.strip():
            caption = (
                media.get("caption")
                or media.get("caption_text")
                or media.get("text")
                or ""
            )
            alt = media.get("alt") or caption or fallback_alt
            return {
                "src": src.strip(),
                "caption": str(caption).strip(),
                "alt": str(alt).strip(),
            }

    return None


def is_placeholder_torque_value(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return not text or any(
        marker in text
        for marker in (
            "vehicle specific",
            "load vehicle",
            "varies",
            "verify exact",
            "placeholder",
            "tbd",
        )
    )


def normalize_torque_spec_value(value: Any) -> str:
    text = str(value or "").strip()
    return (
        text.replace("NÂ·m", "Nm")
        .replace("N·m", "Nm")
        .replace("lb-ft", "ft-lb")
    )


def normalize_repair_guide_tool_groups(raw_tools: Any) -> Dict[str, List[str]]:
    if isinstance(raw_tools, dict):
        return {
            "basic": normalize_repair_guide_list(
                raw_tools.get("basic") or raw_tools.get("basic_tools")
            ),
            "specialty": normalize_repair_guide_list(
                raw_tools.get("specialty") or raw_tools.get("specialty_tools")
            ),
            "supplies": normalize_repair_guide_list(
                raw_tools.get("supplies")
                or raw_tools.get("shop_supplies")
                or raw_tools.get("fluids")
            ),
        }

    basic_terms = (
        "jack",
        "stand",
        "wrench",
        "socket",
        "ratchet",
        "pliers",
        "screwdriver",
        "impact",
    )
    supply_terms = (
        "cleaner",
        "coolant",
        "fluid",
        "grease",
        "lubricant",
        "sealant",
        "gasket",
        "washer",
        "supplies",
    )
    specialty_terms = (
        "torque wrench",
        "scanner",
        "scan tool",
        "pressure",
        "bleeder",
        "compressor",
        "puller",
        "tester",
        "multimeter",
        "voltmeter",
        "clamp",
        "funnel",
    )

    groups = {"basic": [], "specialty": [], "supplies": []}
    for item in normalize_repair_guide_list(raw_tools):
        text = item.lower()
        if any(term in text for term in supply_terms):
            groups["supplies"].append(item)
        elif any(term in text for term in specialty_terms):
            groups["specialty"].append(item)
        elif any(term in text for term in basic_terms):
            groups["basic"].append(item)
        else:
            groups["basic"].append(item)

    return groups


def normalize_repair_action_items(raw_items: Any) -> List[Dict[str, str]]:
    normalized_items: List[Dict[str, str]] = []
    if not isinstance(raw_items, list):
        return normalized_items

    for item in raw_items:
        if isinstance(item, str):
            title = item.strip()
            if title:
                normalized_items.append({"title": title, "reason": ""})
            continue

        if not isinstance(item, dict):
            continue

        title = str(item.get("title") or item.get("label") or item.get("name") or "").strip()
        reason = str(item.get("reason") or item.get("description") or item.get("why") or "").strip()
        if title:
            normalized_items.append({"title": title, "reason": reason})

    return normalized_items


def normalize_priority_context_items(raw_items: Any) -> List[Dict[str, str]]:
    normalized_items: List[Dict[str, str]] = []
    if not isinstance(raw_items, list):
        return normalized_items

    allowed_priorities = {"Monitor", "Repair Soon", "High Risk", "Verify First"}
    for item in raw_items:
        if isinstance(item, str):
            title = item.strip()
            if title:
                normalized_items.append({"priority": "Verify First", "label": title})
            continue

        if not isinstance(item, dict):
            continue

        priority = str(item.get("priority") or item.get("severity") or item.get("status") or "").strip().title()
        label = str(item.get("label") or item.get("title") or item.get("condition") or "").strip()
        if priority not in allowed_priorities:
            priority = "Verify First"
        if label:
            normalized_items.append({"priority": priority, "label": label})

    return normalized_items


def normalize_inspection_trigger_items(raw_items: Any) -> List[Dict[str, str]]:
    normalized_items: List[Dict[str, str]] = []
    if not isinstance(raw_items, list):
        return normalized_items

    for item in raw_items:
        if isinstance(item, str):
            text = item.strip()
            if text:
                normalized_items.append({"if": text, "check": ""})
            continue

        if not isinstance(item, dict):
            continue

        condition = str(item.get("if") or item.get("condition") or item.get("trigger") or "").strip()
        check = str(item.get("check") or item.get("then") or item.get("action") or "").strip()
        if condition or check:
            normalized_items.append({"if": condition, "check": check})

    return normalized_items


def normalize_repair_text_groups(raw_groups: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_groups, list):
        return []

    groups: List[Dict[str, Any]] = []
    for group in raw_groups:
        if not isinstance(group, dict):
            continue

        title = str(group.get("title") or group.get("label") or group.get("name") or "").strip()
        items = normalize_repair_guide_list(
            group.get("items")
            or group.get("symptoms")
            or group.get("checks")
            or group.get("notes")
        )
        if title and items:
            groups.append({"title": title, "items": items})

    return groups


def normalize_repair_relation_groups(raw_groups: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_groups, list):
        return []

    groups: List[Dict[str, Any]] = []
    for group in raw_groups:
        if not isinstance(group, dict):
            continue

        title = str(group.get("title") or group.get("label") or group.get("name") or "").strip()
        items = normalize_symptom_recommended_repairs(
            group.get("items")
            or group.get("repairs")
            or group.get("related_repairs")
        )
        if title and items:
            groups.append({"title": title, "items": items})

    return groups


def infer_repair_symptom_clusters(guide: Dict[str, Any]) -> List[Dict[str, Any]]:
    symptoms = [item for item in normalize_repair_guide_list(guide.get("symptoms")) if item]
    if not symptoms:
        return []

    text = " ".join(
        str(value or "")
        for value in (
            guide.get("slug"),
            guide.get("title"),
            guide.get("category"),
            guide.get("subcategory"),
            " ".join(symptoms),
        )
    ).lower()

    clusters: List[Dict[str, Any]] = []

    def add(title: str, matches: List[str]) -> None:
        items = [symptom for symptom in symptoms if any(match in symptom.lower() for match in matches)]
        if items:
            clusters.append({"title": title, "items": items[:3]})

    if any(term in text for term in ("brake", "pad", "rotor", "caliper")):
        add("Noise / Wear", ["squeal", "grind", "scrap", "noise", "wear"])
        add("Feel / Control", ["pulsation", "vibration", "pull", "stopping", "pedal"])
    elif any(term in text for term in ("battery", "alternator", "charging", "starter", "no start", "no crank")):
        add("Voltage Clues", ["battery", "voltage", "warning", "light", "dimming", "dropout"])
        add("Starting Clues", ["start", "crank", "dead", "intermittent"])
    elif any(term in text for term in ("coolant", "cooling", "overheat", "thermostat", "radiator", "water pump")):
        add("Temperature Behavior", ["overheat", "temperature", "warm", "heat", "p0128", "gauge"])
        add("Coolant Loss", ["coolant", "leak", "smell", "drip"])
    elif any(term in text for term in ("misfire", "spark plug", "ignition", "coil", "rough idle", "p030")):
        add("Misfire Data", ["misfire", "p030", "check engine", "flashing"])
        add("Driveability", ["rough", "idle", "hesitation", "load", "fuel smell"])

    if not clusters:
        clusters.append({"title": "Primary Clues", "items": symptoms[:3]})
        if len(symptoms) > 3:
            clusters.append({"title": "Secondary Clues", "items": symptoms[3:6]})

    return clusters[:3]


def infer_repair_verify_first_context(guide: Dict[str, Any]) -> List[str]:
    text = " ".join(
        str(value or "")
        for value in (
            guide.get("slug"),
            guide.get("title"),
            guide.get("category"),
            guide.get("subcategory"),
            " ".join(guide.get("symptoms") or []),
            " ".join(guide.get("likely_causes") or []),
        )
    ).lower()

    if any(term in text for term in ("brake", "pad", "rotor", "caliper")):
        return [
            "Measure pads, rotor thickness, and rotor surface before quoting pad-only.",
            "Check caliper slide movement when wear is uneven.",
            "Separate brake pulsation from hub runout or wheel-end play.",
        ]
    if any(term in text for term in ("battery", "alternator", "charging", "starter", "no start", "no crank")):
        return [
            "Load-test the battery before condemning alternator or starter parts.",
            "Voltage-drop main cables and grounds when symptoms are intermittent.",
            "Confirm belt drive condition before quoting charging-system parts.",
        ]
    if any(term in text for term in ("coolant", "cooling", "overheat", "thermostat", "radiator", "water pump")):
        return [
            "Verify coolant level and pressure-test leak evidence first.",
            "Compare scan-tool temperature with hose and fan behavior.",
            "Bleed-air risk should be included before final pricing.",
        ]
    if any(term in text for term in ("misfire", "spark plug", "ignition", "coil", "rough idle", "p030")):
        return [
            "Confirm the cylinder and whether the fault follows the swapped part.",
            "Inspect plug condition before quoting coils or injectors.",
            "Check compression or injector clues when the misfire does not move.",
        ]

    return [
        "Confirm the symptom, code, or inspection evidence before quoting parts.",
        "Check adjacent systems when the evidence is mixed.",
    ]


def infer_repair_diagnostic_overlap_context(guide: Dict[str, Any]) -> List[str]:
    text = " ".join(
        str(value or "")
        for value in (
            guide.get("slug"),
            guide.get("title"),
            guide.get("category"),
            guide.get("subcategory"),
            " ".join(guide.get("symptoms") or []),
            " ".join(guide.get("likely_causes") or []),
        )
    ).lower()

    if any(term in text for term in ("brake", "pad", "rotor", "caliper")):
        return [
            "Pad noise, rotor scoring, seized hardware, and caliper drag can sound similar.",
            "Brake vibration can overlap with rotor runout, hub runout, or wheel bearing play.",
        ]
    if any(term in text for term in ("battery", "alternator", "charging", "starter", "no start", "no crank")):
        return [
            "Weak batteries, poor grounds, belt slip, and alternator faults can all create low-voltage complaints.",
            "No-start complaints may need starting-system and parasitic-draw checks before parts.",
        ]
    if any(term in text for term in ("coolant", "cooling", "overheat", "thermostat", "radiator", "water pump")):
        return [
            "Thermostat, fan, air pocket, radiator, and water pump issues can all show overheating symptoms.",
            "Coolant leaks may appear only after pressure testing or full warm-up.",
        ]
    if any(term in text for term in ("misfire", "spark plug", "ignition", "coil", "rough idle", "p030")):
        return [
            "Ignition, injector, vacuum leak, and compression faults can present as the same misfire code.",
            "Random misfires need fuel-trim and mechanical clues before quoting a single part.",
        ]

    return [
        "Multiple failures may share the same customer symptom.",
        "Inspection protects the estimate when the repair path is not isolated.",
    ]


def build_repair_relation_group(title: str, raw_items: List[Dict[str, Any]], limit: int = 4) -> Dict[str, Any]:
    items: List[Dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        item_title = str(item.get("title") or item.get("label") or item.get("name") or "").strip()
        description = str(item.get("description") or item.get("reason") or "").strip()
        href = str(item.get("cost_guide_href") or item.get("href") or "").strip()
        estimator_href = str(item.get("estimator_href") or item.get("estimator_link") or "").strip()
        if not item_title:
            continue
        key = ((href or estimator_href or item_title).lower(), item_title.lower())
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "title": item_title,
                "description": description,
                "cost_guide_href": href,
                "estimator_href": estimator_href,
            }
        )
        if len(items) >= limit:
            break

    return {"title": title, "items": items}


def score_repair_recommendation(item: Dict[str, Any], guide: Dict[str, Any]) -> int:
    text = " ".join(
        str(value or "")
        for value in (
            item.get("title"),
            item.get("description"),
            item.get("reason"),
            item.get("cost_guide_href"),
            item.get("estimator_href"),
        )
    ).lower()
    guide_text = " ".join(
        str(value or "")
        for value in (
            guide.get("slug"),
            guide.get("title"),
            guide.get("category"),
            guide.get("subcategory"),
            " ".join(guide.get("related_systems") or []),
            " ".join(guide.get("symptoms") or []),
        )
    ).lower()

    score = 0
    if item.get("estimator_href") or item.get("estimator_link"):
        score += 3
    if item.get("cost_guide_href") or item.get("href"):
        score += 2
    if any(term in text for term in ("diagnos", "test", "inspect", "verify", "check", "measure")):
        score += 2
    if any(term in text for term in ("replacement", "service", "belt", "rotor", "battery", "spark plug", "coolant", "caliper")):
        score += 1

    for token in ("brake", "battery", "alternator", "starter", "coolant", "thermostat", "radiator", "water pump", "misfire", "coil", "spark", "evap", "oxygen", "sensor"):
        if token in guide_text and token in text:
            score += 2

    return score


def ranked_repair_recommendations(raw_items: List[Dict[str, Any]], guide: Dict[str, Any], limit: int = 5) -> List[Dict[str, str]]:
    deduped = build_repair_relation_group("Ranked", raw_items, limit=24)["items"]
    return sorted(
        deduped,
        key=lambda item: (-score_repair_recommendation(item, guide), item.get("title", "")),
    )[:limit]


REPAIR_INTELLIGENCE_NETWORK: Dict[str, Dict[str, Any]] = {
    "brake-pad-replacement": {
        "repairs": [
            {"title": "Brake Rotor Replacement", "description": "Check rotor thickness, scoring, and pulsation before pad-only pricing.", "cost_guide_href": "/cost/brake-rotor-replacement", "estimator_href": "/estimator?service=front_brake_pads_and_rotors_replacement"},
            {"title": "Brake Caliper Inspection", "description": "Use when pad wear is uneven, the wheel drags, or slide movement is poor.", "cost_guide_href": "/cost/brake-caliper-replacement", "estimator_href": "/estimator?service=brake_caliper_replacement_each"},
            {"title": "Brake Fluid Inspection", "description": "Check fluid condition when hydraulic age or caliper service affects the quote.", "estimator_href": "/estimator?service=brake_fluid_flush"},
            {"title": "Wheel Bearing Check", "description": "Use when brake vibration overlaps with wheel-end play or hub runout.", "cost_guide_href": "/cost/wheel-bearing-replacement", "estimator_href": "/estimator?service=wheel_bearing_diagnosis"},
        ],
        "symptoms": [
            {"title": "Brake Noise", "href": "/symptoms/brake-noise"},
            {"title": "Vibration While Braking", "href": "/symptoms/vibration-while-braking"},
        ],
        "diagnostics": [
            {"title": "Brake System Repairs", "href": "/repair-systems/brake-system-repairs"},
        ],
    },
    "front-brake-pads": {},
    "rear-brake-pads": {},
    "brake-rotor-replacement": {
        "repairs": [
            {"title": "Brake Pad Replacement", "description": "Pads are usually inspected or replaced when rotors are scored, thin, or heat-spotted.", "cost_guide_href": "/cost/brake-pad-replacement", "estimator_href": "/estimator?service=front_brake_pads_replacement"},
            {"title": "Brake Caliper Inspection", "description": "Check slide pins and piston drag when rotors show heat damage or uneven wear.", "cost_guide_href": "/cost/brake-caliper-replacement", "estimator_href": "/estimator?service=brake_caliper_replacement_each"},
            {"title": "Wheel Bearing Check", "description": "Use when rotor runout and hub play can create the same pedal pulsation.", "cost_guide_href": "/cost/wheel-bearing-replacement", "estimator_href": "/estimator?service=wheel_bearing_diagnosis"},
        ],
        "symptoms": [
            {"title": "Vibration While Braking", "href": "/symptoms/vibration-while-braking"},
            {"title": "Brake Noise", "href": "/symptoms/brake-noise"},
        ],
        "diagnostics": [
            {"title": "Brake System Repairs", "href": "/repair-systems/brake-system-repairs"},
        ],
    },
    "brake-caliper-replacement": {
        "repairs": [
            {"title": "Brake Pad Replacement", "description": "Pads and hardware should be checked when caliper drag caused uneven wear.", "cost_guide_href": "/cost/brake-pad-replacement", "estimator_href": "/estimator?service=front_brake_pads_replacement"},
            {"title": "Brake Rotor Replacement", "description": "Rotor heat damage or scoring may follow a sticking caliper.", "cost_guide_href": "/cost/brake-rotor-replacement", "estimator_href": "/estimator?service=front_brake_pads_and_rotors_replacement"},
            {"title": "Brake Fluid Service", "description": "Use when hydraulic work opens the system or fluid condition is poor.", "estimator_href": "/estimator?service=brake_fluid_flush"},
        ],
        "symptoms": [
            {"title": "Brake Noise", "href": "/symptoms/brake-noise"},
            {"title": "Vibration While Braking", "href": "/symptoms/vibration-while-braking"},
        ],
        "diagnostics": [
            {"title": "Brake System Repairs", "href": "/repair-systems/brake-system-repairs"},
        ],
    },
    "alternator-replacement": {
        "repairs": [
            {"title": "Battery Test", "description": "Confirm battery capacity before condemning charging parts.", "estimator_href": "/estimator?service=battery_test"},
            {"title": "Battery Replacement", "description": "Use when the battery fails load testing or will not recover.", "cost_guide_href": "/cost/battery-replacement", "estimator_href": "/estimator?service=battery_replacement"},
            {"title": "Battery Cable Inspection", "description": "Voltage-drop cables and grounds when charging symptoms are intermittent.", "estimator_href": "/estimator?service=battery_cable_replacement"},
            {"title": "Serpentine Belt Replacement", "description": "Check belt slip, cracking, and tensioner travel with alternator work.", "cost_guide_href": "/cost/serpentine-belt-replacement", "estimator_href": "/estimator?service=serpentine_belt_replacement"},
            {"title": "Charging System Diagnosis", "description": "Use when battery, belt, cable, and alternator evidence is mixed.", "cost_guide_href": "/cost/alternator-replacement", "estimator_href": "/estimator?service=alternator_diagnosis"},
        ],
        "symptoms": [
            {"title": "Battery Light On", "href": "/symptoms/battery-light-on"},
            {"title": "Charging System Warning Light", "href": "/symptoms/charging-system-warning-light"},
            {"title": "Battery Drain", "href": "/symptoms/battery-drain"},
            {"title": "Intermittent No Start", "href": "/symptoms/intermittent-no-start"},
        ],
        "obd": [
            {"code": "P0562", "title": "System voltage low", "href": "/obd/p0562"},
        ],
        "diagnostics": [
            {"title": "Charging & Starting System", "href": "/repair-systems/charging-starting-system"},
        ],
    },
    "battery-replacement": {
        "repairs": [
            {"title": "Alternator Output Test", "description": "Check charging voltage when a new battery may be masking a charge fault.", "cost_guide_href": "/cost/alternator-replacement", "estimator_href": "/estimator?service=alternator_diagnosis"},
            {"title": "Starter Draw Check", "description": "Use when slow crank continues after battery condition is confirmed.", "cost_guide_href": "/cost/starter-replacement", "estimator_href": "/estimator?service=no_crank_diagnosis"},
            {"title": "Battery Cable Inspection", "description": "Corroded terminals and high resistance can mimic a weak battery.", "estimator_href": "/estimator?service=battery_cable_replacement"},
        ],
        "symptoms": [
            {"title": "Battery Drain", "href": "/symptoms/battery-drain"},
            {"title": "Intermittent No Start", "href": "/symptoms/intermittent-no-start"},
            {"title": "No Crank", "href": "/symptoms/no-crank"},
        ],
        "diagnostics": [
            {"title": "Charging & Starting System", "href": "/repair-systems/charging-starting-system"},
        ],
    },
    "starter-replacement": {
        "repairs": [
            {"title": "Battery Test", "description": "Confirm battery state before replacing starter parts.", "cost_guide_href": "/cost/battery-replacement", "estimator_href": "/estimator?service=battery_test"},
            {"title": "Battery Terminal / Cable Inspection", "description": "Voltage-drop cables and terminals when no-crank evidence is mixed.", "estimator_href": "/estimator?service=battery_cable_replacement"},
            {"title": "Starter Relay / Circuit Diagnosis", "description": "Check relay, fuse, neutral safety, and crank command before quoting parts.", "estimator_href": "/estimator?service=no_crank_diagnosis"},
            {"title": "Charging System Check", "description": "Use when repeated low battery state creates starter complaints.", "cost_guide_href": "/cost/alternator-replacement", "estimator_href": "/estimator?service=alternator_diagnosis"},
        ],
        "symptoms": [
            {"title": "No Crank", "href": "/symptoms/no-crank"},
            {"title": "Intermittent No Start", "href": "/symptoms/intermittent-no-start"},
            {"title": "Hard Start After Sitting", "href": "/symptoms/hard-start-after-sitting-overnight"},
        ],
        "diagnostics": [
            {"title": "Charging & Starting System", "href": "/repair-systems/charging-starting-system"},
        ],
    },
    "water-pump-replacement": {
        "repairs": [
            {"title": "Thermostat Replacement", "description": "Compare thermostat behavior when overheating or warm-up patterns overlap.", "cost_guide_href": "/cost/thermostat-replacement", "estimator_href": "/estimator?service=thermostat_replacement"},
            {"title": "Radiator Replacement", "description": "Pressure-test radiator tanks and seams when coolant loss continues.", "cost_guide_href": "/cost/radiator-replacement", "estimator_href": "/estimator?service=radiator_replacement"},
            {"title": "Cooling Fan Diagnosis", "description": "Use when overheating appears at idle or with A/C load.", "estimator_href": "/estimator?service=radiator_fan_diagnosis"},
            {"title": "Coolant Leak Diagnosis", "description": "Pressure-test the system before adding pump or hose repairs.", "estimator_href": "/estimator?service=coolant_leak_diagnosis"},
        ],
        "symptoms": [
            {"title": "Coolant Leaks", "href": "/symptoms/coolant-leaks"},
            {"title": "Overheating At Idle", "href": "/symptoms/overheating-at-idle"},
            {"title": "Vehicle Overheats With AC On", "href": "/symptoms/vehicle-overheats-with-ac-on"},
        ],
        "diagnostics": [
            {"title": "Cooling System Diagnostics", "href": "/repair-systems/cooling-system-diagnostics"},
        ],
    },
    "thermostat-replacement": {
        "repairs": [
            {"title": "Coolant Temperature Sensor Check", "description": "Verify scan data when P0128 or gauge behavior is uncertain.", "cost_guide_href": "/cost/engine-coolant-temperature-sensor-replacement", "estimator_href": "/estimator?service=engine_coolant_temperature_sensor_replacement"},
            {"title": "Water Pump Inspection", "description": "Check circulation when temperature problems continue after thermostat checks.", "cost_guide_href": "/cost/water-pump-replacement", "estimator_href": "/estimator?service=water_pump_replacement"},
            {"title": "Radiator Fan Diagnosis", "description": "Use when overheating happens at idle, low speed, or with A/C load.", "estimator_href": "/estimator?service=radiator_fan_diagnosis"},
        ],
        "symptoms": [
            {"title": "Overheating At Idle", "href": "/symptoms/overheating-at-idle"},
            {"title": "Coolant Leaks", "href": "/symptoms/coolant-leaks"},
        ],
        "obd": [
            {"code": "P0128", "title": "Coolant temperature below thermostat regulating temperature", "href": "/obd/p0128"},
        ],
        "diagnostics": [
            {"title": "Cooling System Diagnostics", "href": "/repair-systems/cooling-system-diagnostics"},
        ],
    },
    "radiator-replacement": {
        "repairs": [
            {"title": "Thermostat Replacement", "description": "Verify thermostat behavior when overheating evidence overlaps.", "cost_guide_href": "/cost/thermostat-replacement", "estimator_href": "/estimator?service=thermostat_replacement"},
            {"title": "Water Pump Inspection", "description": "Check circulation before blaming the radiator for repeat overheating.", "cost_guide_href": "/cost/water-pump-replacement", "estimator_href": "/estimator?service=water_pump_replacement"},
            {"title": "Cooling Fan Diagnosis", "description": "Use when overheating shows up mostly at idle or A/C load.", "estimator_href": "/estimator?service=radiator_fan_diagnosis"},
        ],
        "symptoms": [
            {"title": "Coolant Leaks", "href": "/symptoms/coolant-leaks"},
            {"title": "Overheating At Idle", "href": "/symptoms/overheating-at-idle"},
        ],
        "diagnostics": [
            {"title": "Cooling System Diagnostics", "href": "/repair-systems/cooling-system-diagnostics"},
        ],
    },
    "radiator-fan-replacement": {
        "repairs": [
            {"title": "Thermostat Inspection", "description": "Compare fan command with thermostat and temperature behavior.", "cost_guide_href": "/cost/thermostat-replacement", "estimator_href": "/estimator?service=thermostat_replacement"},
            {"title": "Coolant Temperature Sensor Check", "description": "Verify sensor data before replacing fan parts.", "cost_guide_href": "/cost/engine-coolant-temperature-sensor-replacement", "estimator_href": "/estimator?service=engine_coolant_temperature_sensor_replacement"},
        ],
        "symptoms": [
            {"title": "Vehicle Overheats With AC On", "href": "/symptoms/vehicle-overheats-with-ac-on"},
            {"title": "Overheating At Idle", "href": "/symptoms/overheating-at-idle"},
        ],
        "diagnostics": [
            {"title": "Cooling System Diagnostics", "href": "/repair-systems/cooling-system-diagnostics"},
        ],
    },
    "ignition-coil-replacement": {
        "repairs": [
            {"title": "Spark Plug Replacement", "description": "Inspect plug gap, wear, and fouling before replacing coils.", "cost_guide_href": "/cost/spark-plug-replacement", "estimator_href": "/estimator?service=spark_plug_replacement_4_cyl"},
            {"title": "Fuel Injector Diagnosis", "description": "Use when the misfire does not follow coil or plug evidence.", "cost_guide_href": "/cost/fuel-injector-replacement", "estimator_href": "/estimator?service=fuel_system_diagnostic"},
            {"title": "Misfire Diagnosis", "description": "Compare ignition, fuel, air, and compression before quoting parts.", "estimator_href": "/estimator?service=misfire_diagnosis"},
        ],
        "symptoms": [
            {"title": "Engine Misfire At Idle", "href": "/symptoms/engine-misfire-at-idle"},
            {"title": "Check Engine Light Flashing", "href": "/symptoms/check-engine-light-flashing"},
            {"title": "Cold Start Misfire", "href": "/symptoms/cold-start-misfire"},
        ],
        "obd": [
            {"code": "P0300", "title": "Random or multiple cylinder misfire", "href": "/obd/p0300"},
            {"code": "P0301", "title": "Cylinder 1 misfire", "href": "/obd/p0301"},
        ],
        "diagnostics": [
            {"title": "Engine Performance & Misfire Diagnostics", "href": "/repair-systems/engine-performance-misfire-diagnostics"},
            {"title": "Cylinder Misfire Blueprint", "href": "/repair-guides/how-to-diagnose-a-cylinder-misfire"},
        ],
    },
    "spark-plug-replacement": {},
    "oxygen-sensor-replacement": {
        "repairs": [
            {"title": "Catalytic Converter Check", "description": "Compare upstream/downstream data before replacing downstream sensors.", "cost_guide_href": "/cost/catalytic-converter-replacement", "estimator_href": "/estimator?service=catalyst_efficiency_diagnosis"},
            {"title": "Exhaust Leak Inspection", "description": "Leaks can distort O2 readings and catalyst monitor results.", "estimator_href": "/estimator?service=exhaust_leak_repair"},
            {"title": "Fuel Trim Diagnosis", "description": "Use when rich or lean data is biasing O2 sensor behavior.", "estimator_href": "/estimator?service=fuel_trim_diagnosis"},
        ],
        "symptoms": [
            {"title": "Poor Fuel Economy", "href": "/symptoms/poor-fuel-economy"},
            {"title": "Fuel Smell From Exhaust", "href": "/symptoms/fuel-smell-from-exhaust"},
        ],
        "obd": [
            {"code": "P0130", "title": "O2 sensor circuit bank 1 sensor 1", "href": "/obd/p0130"},
            {"code": "P0135", "title": "O2 sensor heater circuit bank 1 sensor 1", "href": "/obd/p0135"},
            {"code": "P0420", "title": "Catalyst efficiency below threshold bank 1", "href": "/obd/p0420"},
            {"code": "P0430", "title": "Catalyst efficiency below threshold bank 2", "href": "/obd/p0430"},
        ],
        "diagnostics": [
            {"title": "Emissions & EVAP Diagnostics", "href": "/repair-systems/emissions-evap-diagnostics"},
        ],
    },
    "catalytic-converter-replacement": {
        "repairs": [
            {"title": "Oxygen Sensor Inspection", "description": "Compare sensor switching before condemning the converter.", "cost_guide_href": "/cost/oxygen-sensor-replacement", "estimator_href": "/estimator?service=o2_sensor_diagnosis"},
            {"title": "Exhaust Leak Inspection", "description": "Leaks ahead of the catalyst can create false efficiency faults.", "estimator_href": "/estimator?service=exhaust_leak_repair"},
            {"title": "Misfire / Fuel Trim Diagnosis", "description": "Correct upstream faults before installing a converter.", "estimator_href": "/estimator?service=fuel_trim_diagnosis"},
        ],
        "symptoms": [
            {"title": "Loss Of Power While Driving", "href": "/symptoms/loss-of-power-while-driving"},
            {"title": "Fuel Smell From Exhaust", "href": "/symptoms/fuel-smell-from-exhaust"},
            {"title": "Poor Fuel Economy", "href": "/symptoms/poor-fuel-economy"},
        ],
        "obd": [
            {"code": "P0420", "title": "Catalyst efficiency below threshold bank 1", "href": "/obd/p0420"},
            {"code": "P0430", "title": "Catalyst efficiency below threshold bank 2", "href": "/obd/p0430"},
        ],
        "diagnostics": [
            {"title": "Emissions & EVAP Diagnostics", "href": "/repair-systems/emissions-evap-diagnostics"},
        ],
    },
    "evap-purge-valve-replacement": {
        "repairs": [
            {"title": "EVAP Smoke Test", "description": "Smoke test before replacing purge parts when leak evidence is not isolated.", "estimator_href": "/estimator?service=evap_leak_test_smoke_test"},
            {"title": "EVAP Vent Valve Check", "description": "Vent-side faults can overlap with purge and leak codes.", "cost_guide_href": "/cost/evap-vent-valve-replacement", "estimator_href": "/estimator?service=evap_vent_valve_replacement"},
        ],
        "symptoms": [
            {"title": "Fuel Smell From Exhaust", "href": "/symptoms/fuel-smell-from-exhaust"},
            {"title": "Hard Start After Sitting", "href": "/symptoms/hard-start-after-sitting-overnight"},
        ],
        "obd": [
            {"code": "P0440", "title": "EVAP system fault", "href": "/obd/p0440"},
            {"code": "P0442", "title": "Small EVAP leak", "href": "/obd/p0442"},
            {"code": "P0455", "title": "Large EVAP leak", "href": "/obd/p0455"},
            {"code": "P0456", "title": "Very small EVAP leak", "href": "/obd/p0456"},
        ],
        "diagnostics": [
            {"title": "Emissions & EVAP Diagnostics", "href": "/repair-systems/emissions-evap-diagnostics"},
        ],
    },
    "fuel-pump-replacement": {
        "repairs": [
            {"title": "Fuel Pressure Test", "description": "Confirm pressure and volume before replacing the pump.", "estimator_href": "/estimator?service=fuel_pressure_test"},
            {"title": "Fuel Injector Diagnosis", "description": "Use when low power or misfire evidence remains after pressure checks.", "cost_guide_href": "/cost/fuel-injector-replacement", "estimator_href": "/estimator?service=fuel_system_diagnostic"},
        ],
        "symptoms": [
            {"title": "Hard Start After Sitting", "href": "/symptoms/hard-start-after-sitting-overnight"},
            {"title": "Engine Stalls At Idle", "href": "/symptoms/engine-stalls-at-idle"},
            {"title": "Loss Of Power While Driving", "href": "/symptoms/loss-of-power-while-driving"},
        ],
        "diagnostics": [
            {"title": "Engine Performance & Misfire Diagnostics", "href": "/repair-systems/engine-performance-misfire-diagnostics"},
        ],
    },
}

REPAIR_INTELLIGENCE_NETWORK["front-brake-pads"] = REPAIR_INTELLIGENCE_NETWORK["brake-pad-replacement"]
REPAIR_INTELLIGENCE_NETWORK["rear-brake-pads"] = REPAIR_INTELLIGENCE_NETWORK["brake-pad-replacement"]
REPAIR_INTELLIGENCE_NETWORK["spark-plug-replacement"] = REPAIR_INTELLIGENCE_NETWORK["ignition-coil-replacement"]


def merge_network_repair_items(*groups: List[Dict[str, str]]) -> List[Dict[str, str]]:
    merged: List[Dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for group in groups:
        for item in group or []:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            href = str(item.get("cost_guide_href") or item.get("href") or item.get("estimator_href") or "").strip()
            key = (title.lower(), href)
            if not title or key in seen:
                continue
            seen.add(key)
            merged.append(dict(item))
    return merged


def merge_network_link_items(*groups: List[Dict[str, str]], key_name: str = "href") -> List[Dict[str, str]]:
    merged: List[Dict[str, str]] = []
    seen: set[str] = set()
    for group in groups:
        for item in group or []:
            if not isinstance(item, dict):
                continue
            href = str(item.get(key_name) or "").strip()
            title = str(item.get("title") or item.get("code") or "").strip()
            if not href or not title or href in seen:
                continue
            seen.add(href)
            merged.append(dict(item))
    return merged


def apply_repair_intelligence_network(guide: Dict[str, Any]) -> Dict[str, Any]:
    slug = str(guide.get("slug") or "").strip().replace("_", "-")
    network = REPAIR_INTELLIGENCE_NETWORK.get(slug)
    if not network:
        return guide

    guide = dict(guide)
    network_repairs = normalize_symptom_recommended_repairs(network.get("repairs"))
    guide["recommended_repairs"] = merge_network_repair_items(
        network_repairs,
        guide.get("recommended_repairs") or [],
    )
    guide["bundled_repair_suggestions"] = merge_network_repair_items(
        network_repairs,
        normalize_symptom_recommended_repairs(guide.get("bundled_repair_suggestions")),
    )
    guide["related_symptoms"] = merge_network_link_items(
        normalize_related_link_items(network.get("symptoms")),
        guide.get("related_symptoms") or [],
    )
    guide["related_obd_codes"] = merge_network_link_items(
        normalize_symptom_obd_codes(network.get("obd")),
        guide.get("related_obd_codes") or [],
    )
    guide["related_diagnostic_links"] = merge_network_link_items(
        normalize_related_link_items(network.get("diagnostics")),
        normalize_related_link_items(guide.get("related_diagnostic_links")),
    )
    return guide


def infer_symptom_confidence_groups(guide: Dict[str, Any]) -> List[Dict[str, Any]]:
    symptoms = normalize_repair_guide_list(guide.get("symptoms"))
    if not symptoms:
        return []

    text = " ".join(
        str(value or "")
        for value in (
            guide.get("slug"),
            guide.get("title"),
            guide.get("category"),
            guide.get("subcategory"),
        )
    ).lower()

    strong_terms: List[str] = []
    if any(term in text for term in ("brake", "pad", "rotor", "caliper")):
        strong_terms = ["grind", "squeal", "scrap", "pad", "brake warning", "uneven pad"]
    elif any(term in text for term in ("battery", "alternator", "charging", "starter", "no start", "no crank")):
        strong_terms = ["battery", "voltage", "warning light", "charging", "no start", "no crank"]
    elif any(term in text for term in ("coolant", "cooling", "overheat", "thermostat", "radiator", "water pump")):
        strong_terms = ["p0128", "overheat", "coolant", "temperature", "warm-up", "leak"]
    elif any(term in text for term in ("misfire", "spark plug", "ignition", "coil", "rough idle", "p030")):
        strong_terms = ["p030", "misfire", "flashing", "rough idle", "hesitation"]

    strong = [symptom for symptom in symptoms if any(term in symptom.lower() for term in strong_terms)]
    possible = [symptom for symptom in symptoms if symptom not in strong]
    if not strong:
        strong = symptoms[:2]
        possible = symptoms[2:]

    groups: List[Dict[str, Any]] = []
    if strong:
        groups.append({"label": "Strong Match", "items": strong[:3]})
    if possible:
        groups.append({"label": "Possible Match", "items": possible[:3]})
    return groups


def build_recommendation_priority_groups(guide: Dict[str, Any]) -> List[Dict[str, Any]]:
    verify_items: List[Dict[str, str]] = []
    for item in guide.get("verify_first_context") or []:
        verify_items.append({"title": str(item or "").strip(), "description": "Confirm before quoting.", "cost_guide_href": "", "estimator_href": ""})
    for item in guide.get("related_inspections") or []:
        if isinstance(item, dict):
            verify_items.append(item)

    bundled_items = ranked_repair_recommendations(
        list(guide.get("bundled_repair_suggestions") or [])
        + list(guide.get("recommended_repairs") or []),
        guide,
        limit=5,
    )
    situational_items = ranked_repair_recommendations(
        list(guide.get("workflow_next_steps") or [])
        + list(guide.get("recommended_while_replacing") or []),
        guide,
        limit=5,
    )

    seen_titles: set[str] = set()

    def unique_items(items: List[Dict[str, str]], limit: int = 4) -> List[Dict[str, str]]:
        unique: List[Dict[str, str]] = []
        for item in items:
            title = str(item.get("title") or "").strip()
            key = title.lower()
            if not title or key in seen_titles:
                continue
            seen_titles.add(key)
            unique.append(item)
            if len(unique) >= limit:
                break
        return unique

    groups = [
        {"title": "Verify First", "tone": "verify", "items": unique_items(build_repair_relation_group("Verify First", verify_items, limit=6)["items"])},
        {"title": "Commonly Bundled", "tone": "bundle", "items": unique_items(bundled_items)},
        {"title": "Situational", "tone": "situational", "items": unique_items(situational_items)},
    ]
    return [group for group in groups if group["items"]]


def infer_mechanic_reasoning_notes(guide: Dict[str, Any]) -> List[str]:
    verify_first = normalize_repair_guide_list(guide.get("verify_first_context"))
    overlap = normalize_repair_guide_list(guide.get("diagnostic_overlap_context"))
    notes: List[str] = []
    if verify_first:
        notes.append(verify_first[0])
    if overlap:
        notes.append(overlap[0])
    if guide.get("bundled_repair_suggestions"):
        notes.append("Add bundled work only when inspection supports it.")
    notes.append("Keep the quote tied to confirmed evidence.")

    deduped: List[str] = []
    for note in notes:
        if note and note not in deduped:
            deduped.append(note)
    return deduped[:3]


def build_repair_guide_intelligence_expansion(guide: Dict[str, Any]) -> Dict[str, Any]:
    explicit_groups = normalize_repair_relation_groups(guide.get("related_repair_groups"))
    inspected = build_repair_relation_group(
        "Frequently inspected together",
        list(guide.get("recommended_while_replacing") or [])
        + list(guide.get("related_inspections") or []),
    )
    replaced = build_repair_relation_group(
        "Frequently replaced together",
        list(guide.get("recommended_repairs") or []),
    )
    follow_up = build_repair_relation_group(
        "Common follow-up repairs",
        list(guide.get("workflow_next_steps") or [])
        + list(guide.get("recommended_repairs") or []),
    )
    inferred_groups = [group for group in (inspected, replaced, follow_up) if group["items"]]

    bundled = build_repair_relation_group(
        "Commonly bundled repair suggestions",
        list(guide.get("recommended_repairs") or [])
        + list(guide.get("related_inspections") or []),
        limit=5,
    )

    bundled_suggestions = normalize_symptom_recommended_repairs(guide.get("bundled_repair_suggestions")) or ranked_repair_recommendations(bundled["items"], guide, limit=5)
    expanded = {
        "related_repair_groups": explicit_groups or inferred_groups,
        "symptom_clusters": normalize_repair_text_groups(guide.get("symptom_clusters")) or infer_repair_symptom_clusters(guide),
        "verify_first_context": normalize_repair_guide_list(guide.get("verify_first_context") or guide.get("verify_first")) or infer_repair_verify_first_context(guide),
        "diagnostic_overlap_context": normalize_repair_guide_list(guide.get("diagnostic_overlap_context") or guide.get("diagnostic_overlap")) or infer_repair_diagnostic_overlap_context(guide),
        "bundled_repair_suggestions": bundled_suggestions,
    }
    guide_for_priority = dict(guide)
    guide_for_priority.update(expanded)
    expanded["symptom_confidence_groups"] = infer_symptom_confidence_groups(guide_for_priority)
    expanded["recommendation_priority_groups"] = build_recommendation_priority_groups(guide_for_priority)
    expanded["mechanic_reasoning_notes"] = infer_mechanic_reasoning_notes(guide_for_priority)
    return expanded


def infer_repair_precision_defaults(guide: Dict[str, Any]) -> Dict[str, Any]:
    text = " ".join(
        str(value or "")
        for value in (
            guide.get("slug"),
            guide.get("title"),
            guide.get("category"),
            guide.get("subcategory"),
            " ".join(guide.get("related_systems") or []),
            " ".join(guide.get("repair_overview") or []),
        )
    ).lower()

    tools = {"basic": [], "specialty": [], "supplies": []}
    recommended: List[Dict[str, str]] = []
    verification: List[str] = []
    priority_context: List[Dict[str, str]] = []
    failure_signs: List[str] = []
    inspection_triggers: List[Dict[str, str]] = []

    def add_many(target: List[str], values: List[str]) -> None:
        for value in values:
            if value and value not in target:
                target.append(value)

    if any(term in text for term in ("water pump", "cooling", "coolant", "thermostat", "radiator")):
        add_many(tools["basic"], ["Socket set", "Wrenches", "Drain pan"])
        add_many(tools["specialty"], ["Cooling system pressure tester", "Spill-free funnel or vacuum fill tool", "Torque wrench"])
        add_many(tools["supplies"], ["Correct coolant", "Gasket or sealant as specified", "Shop towels"])
        recommended = [
            {"title": "Coolant service", "reason": "Cooling system is already drained/open."},
            {"title": "Belt inspection / replacement", "reason": "Belt is often removed or exposed during pump access."},
            {"title": "Thermostat inspection", "reason": "Overheating concerns often overlap with thermostat behavior."},
            {"title": "Radiator hose inspection", "reason": "Hoses should be checked while the cooling system is open."},
        ]
        verification = ["Refill and bleed cooling system", "Pressure-test for leaks", "Confirm operating temperature", "Verify radiator fan operation", "Road test and recheck coolant level"]
        priority_context = [
            {"priority": "High Risk", "label": "Active leak, pulley wobble, or bearing noise"},
            {"priority": "Repair Soon", "label": "Coolant age, contamination, or hose deterioration"},
            {"priority": "Monitor", "label": "Minor seep with no overheating after verification"},
            {"priority": "Verify First", "label": "Mixed leak evidence or repeat overheating"},
        ]
        failure_signs = [
            "Coolant crust near weep hole",
            "Pulley wobble or bearing noise",
            "Overheating at idle or low speed",
            "Coolant smell after shutdown",
            "Visible drip after pressure test",
        ]
        inspection_triggers = [
            {"if": "Belt is coolant-soaked", "check": "Inspect/replace belt."},
            {"if": "Overheating continues after repair", "check": "Verify thermostat and radiator fan operation."},
            {"if": "Coolant is rusty or contaminated", "check": "Recommend coolant service or flush inspection."},
            {"if": "Pressure test still fails", "check": "Inspect hoses, radiator, cap, and gasket surfaces."},
        ]
    elif any(term in text for term in ("brake", "pad", "rotor", "caliper")):
        add_many(tools["basic"], ["Floor jack", "Jack stands", "Lug wrench", "Socket set"])
        add_many(tools["specialty"], ["Torque wrench", "Brake piston compressor", "Brake bleeder when hydraulics are opened"])
        add_many(tools["supplies"], ["Brake cleaner", "Brake lubricant", "Brake fluid if bleeding"])
        recommended = [
            {"title": "Rotor inspection", "reason": "Rotor face and thickness are exposed during pad access."},
            {"title": "Caliper slide inspection", "reason": "Slide condition affects pad wear and repeat comebacks."},
            {"title": "Brake fluid inspection", "reason": "Hydraulic condition can explain poor pedal feel or caliper issues."},
            {"title": "Hardware inspection", "reason": "Clips and abutments are already accessible."},
        ]
        verification = ["Torque wheel fasteners", "Pump brake pedal before moving", "Confirm pedal feel", "Check for drag or leaks", "Road test and recheck noise/vibration"]
        priority_context = [
            {"priority": "High Risk", "label": "Grinding, metal contact, fluid leak, or severe pull"},
            {"priority": "Repair Soon", "label": "Low pad thickness or uneven wear"},
            {"priority": "Monitor", "label": "Light noise with pads/rotors still in spec"},
            {"priority": "Verify First", "label": "Vibration, ABS, or hub symptoms overlap"},
        ]
        failure_signs = [
            "Inner pad worn faster than outer pad",
            "Rotor scoring, heat spots, or heavy rust lip",
            "Slide pins dry, seized, or torn boots",
            "Caliper drag after pedal release",
            "Brake fluid leak or low reservoir",
        ]
        inspection_triggers = [
            {"if": "Pad wear is uneven", "check": "Inspect slide pins, caliper piston, and hose restriction."},
            {"if": "Pulsation is present", "check": "Measure rotor condition and check wheel-end runout."},
            {"if": "Fluid is dark or low", "check": "Inspect hydraulic leaks and fluid condition."},
            {"if": "Wheel drags after braking", "check": "Verify caliper, hose, and hardware movement."},
        ]
    elif any(term in text for term in ("alternator", "charging", "battery")):
        add_many(tools["basic"], ["Socket set", "Wrenches", "Belt tool when required"])
        add_many(tools["specialty"], ["Digital multimeter", "Battery tester", "Torque wrench"])
        add_many(tools["supplies"], ["Battery terminal cleaner", "Dielectric grease as appropriate"])
        recommended = [
            {"title": "Battery test", "reason": "Battery condition can mimic or mask charging failure."},
            {"title": "Belt / tensioner inspection", "reason": "Belt drive is already exposed during alternator access."},
            {"title": "Charging cable inspection", "reason": "High resistance can cause repeat low-voltage complaints."},
            {"title": "Ground inspection", "reason": "Ground faults can imitate alternator output problems."},
        ]
        verification = ["Confirm charging voltage", "Load-test battery if needed", "Check belt tracking", "Clear low-voltage codes", "Road test and recheck charging output"]
        priority_context = [
            {"priority": "High Risk", "label": "No charge, warning light, or repeated stall/low voltage"},
            {"priority": "Repair Soon", "label": "Weak output under load or noisy bearing"},
            {"priority": "Monitor", "label": "Intermittent complaint with normal verified output"},
            {"priority": "Verify First", "label": "Weak battery or parasitic draw suspected"},
        ]
        failure_signs = [
            "Low charging voltage under load",
            "Battery light stays on",
            "Bearing whine or pulley noise",
            "Belt slip, glaze, or tensioner flutter",
            "Hot or corroded charge cable connection",
        ]
        inspection_triggers = [
            {"if": "Battery fails load test", "check": "Address battery before condemning alternator."},
            {"if": "Belt is glazed or loose", "check": "Inspect belt, tensioner, and pulley alignment."},
            {"if": "Voltage drop is high", "check": "Inspect charge cable, grounds, and main fuse links."},
            {"if": "Low-voltage codes return", "check": "Recheck charging output and power/ground paths."},
        ]
    elif any(term in text for term in ("spark plug", "ignition", "misfire")):
        add_many(tools["basic"], ["Socket set", "Extensions", "Ignition coil puller when required"])
        add_many(tools["specialty"], ["Spark plug socket", "Gap gauge when applicable", "Torque wrench"])
        add_many(tools["supplies"], ["Dielectric grease as appropriate", "Compressed air for plug wells"])
        recommended = [
            {"title": "Ignition coil boot inspection", "reason": "Boots are removed during plug access."},
            {"title": "Plug well inspection", "reason": "Oil or coolant intrusion can damage new plugs/boots."},
            {"title": "Misfire code review", "reason": "Prevents replacing plugs when the fault is fuel or compression."},
            {"title": "Intake gasket inspection", "reason": "Access overlap applies when intake removal is required."},
        ]
        verification = ["Verify plug type and gap", "Torque plugs to spec when available", "Confirm coil connectors are seated", "Check misfire counters", "Road test and recheck idle quality"]
        priority_context = [
            {"priority": "High Risk", "label": "Flashing MIL or active misfire under load"},
            {"priority": "Repair Soon", "label": "Worn plugs, hard start, or recurring misfire counts"},
            {"priority": "Monitor", "label": "Mileage-based service with no drivability concern"},
            {"priority": "Verify First", "label": "Misfire stays after coil/plug swap"},
        ]
        failure_signs = [
            "Wide gap or worn electrode",
            "Oil or coolant fouling",
            "Carbon tracking on boot or plug",
            "Plug well oil intrusion",
            "Misfire counter follows cylinder evidence",
        ]
        inspection_triggers = [
            {"if": "Oil is in plug wells", "check": "Inspect valve cover gasket and coil boots."},
            {"if": "Misfire stays on same cylinder", "check": "Check injector, compression, and vacuum leak paths."},
            {"if": "Plug is fuel-soaked", "check": "Verify spark and injector control."},
            {"if": "Intake must be removed", "check": "Inspect intake gasket and access-related hoses."},
        ]
    else:
        add_many(tools["basic"], ["Basic hand tools", "Socket set", "Wrenches"])
        add_many(tools["specialty"], ["Torque wrench", "Scan tool when diagnosis is involved"])
        add_many(tools["supplies"], ["Shop towels", "Cleaner or fluid required by the repair"])
        recommended = [
            {"title": "Inspect nearby wear items", "reason": "Access is already available."},
            {"title": "Check fasteners and mounting surfaces", "reason": "Reduces repeat teardown risk."},
            {"title": "Review related symptoms", "reason": "Confirms the repair path before adding work."},
        ]
        verification = ["Confirm repair concern is resolved", "Check for leaks, noise, or warning lights", "Road test when appropriate", "Recheck fluid level or fastener security if applicable"]
        priority_context = [
            {"priority": "Verify First", "label": "Evidence is mixed or incomplete"},
            {"priority": "Repair Soon", "label": "Confirmed wear or leakage"},
            {"priority": "Monitor", "label": "Minor concern with no confirmed failure"},
        ]
        failure_signs = ["Confirmed leak, noise, play, or fault data", "Repeat symptom after basic checks"]
        inspection_triggers = [
            {"if": "Evidence is mixed", "check": "Verify the system before adding parts."},
            {"if": "Access exposes related wear", "check": "Inspect related fasteners, mounts, and seals."},
        ]

    return {
        "tool_groups": tools,
        "recommended_while_replacing": recommended,
        "post_repair_verification": verification,
        "priority_context": priority_context,
        "failure_signs": failure_signs,
        "inspection_triggers": inspection_triggers,
    }


def normalize_repair_guide(raw_guide: Any, *, slug: str = "") -> Dict[str, Any]:
    guide = dict(raw_guide) if isinstance(raw_guide, dict) else {}
    normalized: Dict[str, Any] = dict(guide)

    guide_slug = str(guide.get("slug") or slug or "").strip()
    guide_title = str(guide.get("title") or guide_slug.replace("-", " ").replace("_", " ").title() or "Repair Guide").strip()

    normalized["slug"] = guide_slug
    normalized["title"] = guide_title
    normalized["summary"] = str(guide.get("summary") or "").strip()
    normalized["category"] = str(guide.get("category") or "Other").strip() or "Other"
    normalized["subcategory"] = str(guide.get("subcategory") or "").strip()
    normalized["vehicle_optional"] = bool(guide.get("vehicle_optional", True))

    try:
        normalized["sort_order"] = int(guide.get("sort_order", 999))
    except (TypeError, ValueError):
        normalized["sort_order"] = 999

    normalized["symptoms"] = normalize_repair_guide_list(guide.get("symptoms"))
    normalized["diagnostic_logic"] = normalize_repair_guide_list(guide.get("diagnostic_logic"))
    normalized["likely_causes"] = normalize_repair_guide_list(guide.get("likely_causes"))
    normalized["testing_approach"] = normalize_repair_guide_list(guide.get("testing_approach"))
    normalized["common_mistakes"] = normalize_repair_guide_list(guide.get("common_mistakes"))
    normalized["repair_overview"] = normalize_repair_guide_list(guide.get("repair_overview"))
    precision_defaults = infer_repair_precision_defaults(normalized)
    normalized["tool_groups"] = normalize_repair_guide_tool_groups(
        guide.get("tool_groups") or guide.get("tools_required") or guide.get("tools")
    )
    for group_key, fallback_items in precision_defaults["tool_groups"].items():
        if not normalized["tool_groups"].get(group_key):
            normalized["tool_groups"][group_key] = fallback_items
    normalized["tools_required"] = (
        normalized["tool_groups"]["basic"]
        + normalized["tool_groups"]["specialty"]
        + normalized["tool_groups"]["supplies"]
    )
    normalized["repair_steps"] = normalize_repair_guide_list(
        guide.get("repair_steps") or guide.get("steps")
    )
    normalized["pro_tips"] = normalize_repair_guide_list(guide.get("pro_tips"))
    normalized["warnings"] = normalize_repair_guide_list(
        guide.get("warnings") or guide.get("watchouts")
    )
    normalized["inspect_first"] = normalize_repair_guide_list(
        guide.get("inspect_first") or guide.get("what_mechanics_inspect_first")
    )
    confidence_defaults = infer_mechanic_confidence_guidance(guide_slug, guide_title, normalized["category"])
    normalized["inspection_priority"] = (
        normalize_repair_guide_list(guide.get("inspection_priority"))
        or confidence_defaults["inspection_priority"]
    )
    normalized["confidence_cues"] = (
        normalize_repair_guide_list(guide.get("confidence_cues") or guide.get("workflow_cues"))
        or confidence_defaults["confidence_cues"]
    )
    normalized["estimate_guidance"] = normalize_repair_guide_list(guide.get("estimate_guidance"))
    normalized["related_systems"] = normalize_repair_guide_list(guide.get("related_systems"))
    normalized["bolt_sizes"] = normalize_repair_guide_list(guide.get("bolt_sizes"))
    normalized["coming_next"] = normalize_repair_guide_list(guide.get("coming_next"))
    normalized["related_obd_codes"] = normalize_symptom_obd_codes(guide.get("related_obd_codes"))
    normalized["recommended_repairs"] = normalize_symptom_recommended_repairs(guide.get("recommended_repairs"))
    normalized["recommended_while_replacing"] = (
        normalize_repair_action_items(guide.get("recommended_while_replacing"))
        or precision_defaults["recommended_while_replacing"]
    )
    normalized["post_repair_verification"] = (
        normalize_repair_guide_list(guide.get("post_repair_verification") or guide.get("verification_checks"))
        or precision_defaults["post_repair_verification"]
    )
    normalized["priority_context"] = (
        normalize_priority_context_items(guide.get("priority_context") or guide.get("severity_context"))
        or precision_defaults["priority_context"]
    )
    normalized["failure_signs"] = (
        normalize_repair_guide_list(guide.get("failure_signs") or guide.get("common_failure_signs"))
        or precision_defaults["failure_signs"]
    )
    normalized["inspection_triggers"] = (
        normalize_inspection_trigger_items(guide.get("inspection_triggers") or guide.get("if_present_check"))
        or precision_defaults["inspection_triggers"]
    )
    normalized["related_symptoms"] = normalize_related_link_items(guide.get("related_symptoms"))

    difficulty = str(guide.get("difficulty") or "").strip().title()
    normalized["difficulty"] = difficulty if difficulty in {"Easy", "Moderate", "Advanced"} else ""

    normalized["labor_range"] = normalize_repair_guide_range(
        guide.get("labor_range"), "min_hours", "max_hours"
    )
    normalized["labor_cost"] = normalize_repair_guide_range(
        guide.get("labor_cost"), "min", "max"
    )
    normalized["parts_cost"] = normalize_repair_guide_range(
        guide.get("parts_cost"), "min", "max"
    )
    normalized["total_cost"] = normalize_repair_guide_range(
        guide.get("total_cost"), "min", "max"
    )

    normalized["diagram"] = normalize_repair_guide_media_item(
        guide.get("diagram"),
        f"{guide_title} diagram",
    )
    if normalized["diagram"] and not normalized["diagram"]["caption"]:
        legacy_caption = str(guide.get("diagram_caption") or "").strip()
        if legacy_caption:
            normalized["diagram"]["caption"] = legacy_caption

    normalized_step_images: List[Dict[str, Any]] = []
    for media in guide.get("step_images", []):
        if not isinstance(media, dict):
            continue

        step_number = (
            media.get("step")
            or media.get("step_number")
            or media.get("repair_step")
            or media.get("index")
        )
        try:
            step_number = int(step_number)
        except (TypeError, ValueError):
            continue

        normalized_image = normalize_repair_guide_media_item(
            media,
            f"{guide_title} step {step_number}",
        )
        if not normalized_image:
            continue

        normalized_step_images.append(
            {
                "step": step_number,
                "src": normalized_image["src"],
                "caption": normalized_image["caption"],
                "alt": normalized_image["alt"],
            }
        )

    normalized["step_images"] = sorted(normalized_step_images, key=lambda item: item["step"])

    normalized_specs: List[Dict[str, str]] = []
    torque_spec_labels: List[str] = []
    for spec in guide.get("torque_specs", []):
        if not isinstance(spec, dict):
            continue

        label = str(spec.get("label") or spec.get("part") or "").strip()
        value = normalize_torque_spec_value(spec.get("value") or spec.get("spec") or "")
        if label and label not in torque_spec_labels:
            torque_spec_labels.append(label)
        if label and value and not is_placeholder_torque_value(value):
            normalized_specs.append({"label": label, "value": value})
    normalized["torque_specs"] = normalized_specs
    normalized["verified_torque_specs"] = normalized_specs
    normalized["torque_spec_labels"] = torque_spec_labels

    diagnostic_context = guide.get("diagnostic_context")
    if isinstance(diagnostic_context, dict):
        intro = str(diagnostic_context.get("intro") or "").strip()
        common_symptoms_link = str(diagnostic_context.get("common_symptoms_link") or "").strip()
        diagnostic_tools_link = str(diagnostic_context.get("diagnostic_tools_link") or "").strip()
        normalized["diagnostic_context"] = (
            {
                "intro": intro,
                "common_symptoms_link": common_symptoms_link,
                "diagnostic_tools_link": diagnostic_tools_link,
            }
            if intro or common_symptoms_link or diagnostic_tools_link
            else None
        )
    else:
        normalized["diagnostic_context"] = None

    estimate = guide.get("estimate")
    if isinstance(estimate, dict):
        cta_label = str(estimate.get("cta_label") or "").strip()
        service_code = str(estimate.get("service_code") or "").strip()
        service_name = str(estimate.get("service_name") or "").strip()
        estimator_link = str(estimate.get("estimator_link") or "/estimator").strip() or "/estimator"
        category_code = str(estimate.get("category_code") or "").strip()
        normalized["estimate"] = (
            {
                "cta_label": cta_label or "Estimate This Repair",
                "service_code": service_code,
                "service_name": service_name,
                "estimator_link": estimator_link,
                "category_code": category_code,
            }
            if service_code or cta_label or service_name
            else None
        )
    else:
        normalized["estimate"] = None

    return normalized


def load_normalized_repair_guides_map() -> Dict[str, Dict[str, Any]]:
    guides_dir = DATA_DIR / "repair_guides"
    guides: Dict[str, Dict[str, Any]] = {}

    for file in guides_dir.glob("*.json"):
        slug = file.stem.replace("_", "-")
        try:
            raw = json.loads(file.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        guides[slug] = normalize_repair_guide(raw, slug=slug)

    return guides


def build_estimator_service_href(service_code: str, estimator_link: str = "/estimator") -> str:
    base = str(estimator_link or "/estimator").strip() or "/estimator"
    code = str(service_code or "").strip()
    if not code:
        return base

    separator = "&" if "?" in base else "?"
    return f"{base}{separator}service={quote(code)}"


def build_vehicle_context_from_request(request: Request) -> Dict[str, str]:
    params = request.query_params
    year = str(params.get("year") or "").strip()[:4]
    make = str(params.get("make") or "").strip()[:40]
    model = str(params.get("model") or "").strip()[:60]
    display_model = str(params.get("displayModel") or params.get("display_model") or "").strip()[:60]

    context = {
        "year": year,
        "make": make,
        "model": model,
        "displayModel": display_model or model,
    }
    context["has_context"] = bool(year or make or model or display_model)
    context["label"] = " ".join(
        item for item in [year, make, context["displayModel"] or model] if item
    )
    return context


def append_vehicle_context_to_href(href: str, vehicle_context: Dict[str, str]) -> str:
    base = str(href or "").strip()
    if not base or not vehicle_context or not vehicle_context.get("has_context"):
        return base

    params = []
    for key in ("year", "make", "model", "displayModel"):
        value = str(vehicle_context.get(key) or "").strip()
        if value:
            params.append((key, value))

    if not params:
        return base

    separator = "&" if "?" in base else "?"
    return f"{base}{separator}{urlencode(params)}"


def build_workflow_context(
    request: Request,
    *,
    vehicle_context: Optional[Dict[str, str]] = None,
    obd_code: str = "",
    service_code: str = "",
    source: str = "",
) -> Dict[str, str]:
    params = request.query_params
    vehicle = vehicle_context or build_vehicle_context_from_request(request)
    service = str(params.get("service") or service_code or "").strip()[:80]
    obd = str(params.get("obd") or obd_code or "").strip().upper()[:7]
    workflow_source = str(params.get("source") or source or "").strip()[:40]

    return_params: List[Tuple[str, str]] = []
    for key in ("year", "make", "model", "displayModel"):
        value = str(vehicle.get(key) or "").strip()
        if value:
            return_params.append((key, value))
    if service:
        return_params.append(("service", service))
    if obd:
        return_params.append(("obd", obd))
    if workflow_source:
        return_params.append(("source", workflow_source))

    return_href = f"/estimator?{urlencode(return_params)}" if return_params else "/estimator"
    return_label = vehicle.get("label") or "current estimate"

    return {
        "return_href": return_href,
        "has_context": bool(return_params or workflow_source),
        "label": return_label,
        "service": service,
        "obd": obd,
        "source": workflow_source,
    }


def append_workflow_context_to_href(
    href: str,
    workflow_context: Dict[str, str],
    *,
    related_service: str = "",
) -> str:
    base = str(href or "").strip()
    if not base:
        return base

    params: List[Tuple[str, str]] = []
    return_href = str(workflow_context.get("return_href") or "").strip()
    if return_href and return_href != "/estimator":
        context_query = return_href.split("?", 1)[1] if "?" in return_href else ""
        params.extend((key, values[-1]) for key, values in parse_qs(context_query).items() if values)

    service = str(related_service or workflow_context.get("service") or "").strip()
    if service and "service=" not in base:
        params.append(("service", service))
    if workflow_context.get("obd") and "obd=" not in base:
        params.append(("obd", str(workflow_context.get("obd"))))
    if workflow_context.get("source") and "source=" not in base:
        params.append(("source", str(workflow_context.get("source"))))

    seen = set()
    deduped: List[Tuple[str, str]] = []
    for key, value in params:
        if not value or key in seen or f"{key}=" in base:
            continue
        seen.add(key)
        deduped.append((key, value))

    if not deduped:
        return base

    separator = "&" if "?" in base else "?"
    return f"{base}{separator}{urlencode(deduped)}"


def apply_workflow_context_to_repair_items(
    items: Any,
    workflow_context: Dict[str, str],
) -> List[Dict[str, Any]]:
    contextual_items: List[Dict[str, Any]] = []
    if not isinstance(items, list):
        return contextual_items

    for item in items:
        if not isinstance(item, dict):
            continue
        linked_item = dict(item)
        service_hint = ""
        estimator_href = str(linked_item.get("estimator_href") or linked_item.get("estimator_link") or "").strip()
        if "service=" in estimator_href:
            parsed = parse_qs(estimator_href.split("?", 1)[1] if "?" in estimator_href else "")
            service_hint = (parsed.get("service") or [""])[-1]

        for key in ("href", "cost_guide_href", "repair_guide_link"):
            linked_href = str(linked_item.get(key) or "").strip()
            if (
                linked_href.startswith("/repair-guides/")
                or linked_href.startswith("/obd/")
                or linked_href.startswith("/symptoms/")
                or linked_href.startswith("/repair-systems/")
            ):
                linked_item[key] = append_workflow_context_to_href(
                    linked_href,
                    workflow_context,
                    related_service=service_hint,
                )

        for key in ("estimator_href", "estimator_link"):
            linked_href = str(linked_item.get(key) or "").strip()
            if linked_href.startswith("/estimator"):
                linked_item[key] = append_workflow_context_to_href(
                    linked_href,
                    workflow_context,
                    related_service=service_hint,
                )

        contextual_items.append(linked_item)

    return contextual_items


def extract_repair_guide_slug(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    if "/repair-guides/" in text:
        text = text.split("/repair-guides/", 1)[1]

    return text.strip("/").replace("_", "-")


def dedupe_link_items(items: List[Dict[str, Any]], key: str = "href") -> List[Dict[str, Any]]:
    seen: set[str] = set()
    deduped: List[Dict[str, Any]] = []

    for item in items:
        value = str(item.get(key) or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        deduped.append(item)

    return deduped


def build_related_repair_guides(raw_items: Any, repair_guides: Dict[str, Dict[str, Any]]) -> List[Dict[str, str]]:
    if not isinstance(raw_items, list):
        return []

    related_guides: List[Dict[str, str]] = []
    for item in raw_items:
        if isinstance(item, dict):
            slug = (
                extract_repair_guide_slug(item.get("repair_guide_link"))
                or extract_repair_guide_slug(item.get("repair_guide_slug"))
                or extract_repair_guide_slug(item.get("href"))
            )
        else:
            slug = extract_repair_guide_slug(item)

        guide = repair_guides.get(slug)
        if not guide:
            continue

        estimate = guide.get("estimate") or {}
        service_code = str(estimate.get("service_code") or "").strip()
        estimator_href = build_estimator_service_href(
            service_code,
            estimate.get("estimator_link") or "/estimator",
        ) if service_code else ""

        related_guides.append(
            {
                "title": str(guide.get("title") or slug.replace("-", " ").title()).strip(),
                "href": f"/repair-guides/{slug}",
                "estimator_href": estimator_href,
            }
        )

    return dedupe_link_items(related_guides)


def build_estimator_links(raw_estimator_link: Any, related_guides: List[Dict[str, str]]) -> List[Dict[str, str]]:
    estimator_links: List[Dict[str, str]] = []

    for guide in related_guides:
        href = str(guide.get("estimator_href") or "").strip()
        if href:
            estimator_links.append(
                {
                    "label": f"Estimate {guide.get('title', 'Repair')}",
                    "href": href,
                }
            )

    explicit = str(raw_estimator_link or "").strip()
    if explicit and explicit != "/":
        estimator_links.append({"label": "Open Estimator", "href": explicit})

    if not estimator_links:
        estimator_links.append({"label": "Open Estimator", "href": "/estimator"})

    return dedupe_link_items(estimator_links)


def normalize_symptom_obd_codes(raw_items: Any) -> List[Dict[str, str]]:
    if not isinstance(raw_items, list):
        return []

    items: List[Dict[str, str]] = []
    for item in raw_items:
        if isinstance(item, dict):
            code = "".join(ch for ch in str(item.get("code") or "").upper() if ch.isalnum())[:7]
            title = str(item.get("title") or "").strip()
            href = str(item.get("href") or "").strip()
        else:
            code = "".join(ch for ch in str(item or "").upper() if ch.isalnum())[:7]
            title = ""
            href = ""

        if not code:
            continue

        if not href:
            href = f"/obd/{code.lower()}"

        items.append(
            {
                "code": code,
                "title": title or code,
                "href": href,
            }
        )

    return dedupe_link_items(items)


def normalize_symptom_recommended_repairs(raw_items: Any) -> List[Dict[str, str]]:
    if not isinstance(raw_items, list):
        return []

    repairs: List[Dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for item in raw_items:
        if not isinstance(item, dict):
            continue

        title = str(item.get("title") or item.get("name") or "").strip()
        description = str(item.get("description") or "").strip()
        cost_guide_href = str(item.get("cost_guide_href") or item.get("href") or "").strip()
        estimator_href = str(item.get("estimator_href") or item.get("estimator_link") or "").strip()

        if not title or not (cost_guide_href or estimator_href):
            continue

        dedupe_key = (cost_guide_href or estimator_href, title.lower())
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        repairs.append(
            {
                "title": title,
                "description": description,
                "cost_guide_href": cost_guide_href,
                "estimator_href": estimator_href,
            }
        )

    return repairs


def normalize_related_link_items(raw_items: Any) -> List[Dict[str, str]]:
    if not isinstance(raw_items, list):
        return []

    links: List[Dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for item in raw_items:
        if not isinstance(item, dict):
            continue

        title = str(item.get("title") or item.get("name") or "").strip()
        description = str(item.get("description") or "").strip()
        href = str(item.get("href") or item.get("link") or item.get("symptom_href") or "").strip()
        estimator_href = str(item.get("estimator_href") or item.get("estimator_link") or "").strip()

        if not title or not href:
            continue

        dedupe_key = (href, title.lower())
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        links.append(
            {
                "title": title,
                "description": description,
                "href": href,
                "estimator_href": estimator_href,
            }
        )

    return links


def normalize_diagnostic_path_sections(raw_items: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_items, list):
        return []

    sections: List[Dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue

        title = str(item.get("title") or item.get("name") or "").strip()
        if not title:
            continue

        sections.append(
            {
                "title": title,
                "summary": str(item.get("summary") or item.get("description") or "").strip(),
                "checks": normalize_repair_guide_list(item.get("checks") or item.get("quick_checks")),
                "repairs": normalize_symptom_recommended_repairs(item.get("repairs") or item.get("related_repairs")),
                "confidence_cues": normalize_repair_guide_list(item.get("confidence_cues")),
            }
        )

    return sections


def build_obd_diagnostic_path(code: str) -> Dict[str, Any]:
    normalized = str(code or "").upper().strip()
    paths = {
        "P0300": {
            "title": "Misfire Diagnostic Path",
            "summary": "Start by separating ignition, fuel, airflow, and mechanical causes before estimating parts.",
            "systems": [
                "Spark plug condition",
                "Ignition coil output",
                "Vacuum leak or intake leak",
                "Fuel delivery and injector behavior",
            ],
            "blueprints": [
                {"title": "Spark Plug Blueprint", "href": "/repair-guides/spark-plug-replacement"},
                {"title": "Ignition Coil Checks", "href": "/repair-guides/how-to-test-an-ignition-coil"},
                {"title": "Vacuum Leak Inspection", "href": "/repair-guides/how-to-diagnose-a-vacuum-leak"},
                {"title": "Fuel Trim Diagnostics", "href": "/repair-guides/how-to-diagnose-lean-condition-p0171-p0174"},
            ],
            "estimator_href": "/estimator?obd=P0300",
        },
        "P0128": {
            "title": "Cooling Temperature Diagnostic Path",
            "summary": "Prove whether the engine is actually running too cool or the temperature signal is misleading.",
            "systems": [
                "Thermostat operation",
                "Coolant level and trapped air",
                "Coolant temperature sensor plausibility",
                "Radiator, water pump, and fan behavior",
            ],
            "blueprints": [
                {"title": "Thermostat Blueprint", "href": "/repair-guides/thermostat-replacement"},
                {"title": "Water Pump Blueprint", "href": "/repair-guides/water-pump-replacement"},
                {"title": "Radiator Blueprint", "href": "/repair-guides/radiator-replacement"},
            ],
            "estimator_href": "/estimator?obd=P0128",
        },
        "P0171": {
            "title": "Lean Condition Diagnostic Path",
            "summary": "Separate vacuum leaks, airflow data, fuel delivery, and exhaust leaks before replacing sensors.",
            "systems": [
                "Vacuum and intake leaks",
                "MAF and airflow data",
                "Fuel pressure and volume",
                "Upstream oxygen sensor feedback",
            ],
            "inspection_priority": [
                "Compare fuel trims at idle, cruise, and under load",
                "Smoke test for vacuum, PCV, and intake leaks after the MAF",
                "Check MAF data and fuel pressure before pricing sensors",
            ],
            "confidence_cues": [
                "Positive trims guide the path",
                "Smoke testing before parts",
                "Fuel pressure matters under load",
            ],
            "estimate_guidance": [
                "Quote smoke testing when trims are strongest at idle.",
                "Use fuel pressure or volume testing before fuel pump replacement.",
                "Price MAF or O2 sensors only when scan data supports the sensor path.",
            ],
            "blueprints": [
                {"title": "Lean Condition Blueprint", "href": "/repair-guides/how-to-diagnose-lean-condition-p0171-p0174"},
                {"title": "Vacuum Leak Inspection", "href": "/repair-guides/how-to-diagnose-a-vacuum-leak"},
                {"title": "Fuel Pump Blueprint", "href": "/repair-guides/fuel-pump-replacement"},
                {"title": "Oxygen Sensor Blueprint", "href": "/repair-guides/oxygen-sensor-replacement"},
            ],
            "symptom_links": [
                {"title": "Rough Idle", "href": "/symptoms/rough-idle"},
                {"title": "Engine Hesitation", "href": "/symptoms/engine-hesitation-on-acceleration"},
                {"title": "Poor Fuel Economy", "href": "/symptoms/poor-fuel-economy"},
            ],
            "estimator_href": "/estimator?obd=P0171",
        },
        "P0174": {
            "title": "Lean Condition Diagnostic Path",
            "summary": "Separate vacuum leaks, airflow data, fuel delivery, and exhaust leaks before replacing sensors.",
            "systems": [
                "Vacuum and intake leaks",
                "MAF and airflow data",
                "Fuel pressure and volume",
                "Upstream oxygen sensor feedback",
            ],
            "inspection_priority": [
                "Compare Bank 1 and Bank 2 trims before assuming a bank-only fault",
                "Smoke test intake, vacuum, and PCV leak paths after the MAF",
                "Check MAF data and fuel pressure when both banks trend lean",
            ],
            "confidence_cues": [
                "Bank comparison matters",
                "Smoke testing before parts",
                "Shared air or fuel faults are common",
            ],
            "estimate_guidance": [
                "Quote smoke testing when trims point to unmetered air.",
                "Use fuel pressure or volume testing before fuel pump replacement.",
                "Price MAF or O2 sensors only when scan data supports the sensor path.",
            ],
            "blueprints": [
                {"title": "Lean Condition Blueprint", "href": "/repair-guides/how-to-diagnose-lean-condition-p0171-p0174"},
                {"title": "Vacuum Leak Inspection", "href": "/repair-guides/how-to-diagnose-a-vacuum-leak"},
                {"title": "Fuel Pump Blueprint", "href": "/repair-guides/fuel-pump-replacement"},
                {"title": "Oxygen Sensor Blueprint", "href": "/repair-guides/oxygen-sensor-replacement"},
            ],
            "symptom_links": [
                {"title": "Rough Idle", "href": "/symptoms/rough-idle"},
                {"title": "Engine Hesitation", "href": "/symptoms/engine-hesitation-on-acceleration"},
                {"title": "Poor Fuel Economy", "href": "/symptoms/poor-fuel-economy"},
            ],
            "estimator_href": "/estimator?obd=P0174",
        },
        "P0420": {
            "title": "Catalyst Efficiency Diagnostic Path",
            "summary": "Confirm converter efficiency only after checking O2 data, exhaust leaks, misfires, and rich-running causes.",
            "systems": [
                "Catalytic converter efficiency",
                "Upstream and downstream oxygen sensor behavior",
                "Exhaust leak inspection",
                "Misfire or rich-running root cause",
            ],
            "blueprints": [
                {"title": "Catalytic Converter Blueprint", "href": "/repair-guides/catalytic-converter-replacement"},
                {"title": "Oxygen Sensor Blueprint", "href": "/repair-guides/oxygen-sensor-replacement"},
                {"title": "Exhaust Leak Inspection", "href": "/estimator?service=exhaust_leak_repair"},
                {"title": "Ignition Coil Blueprint", "href": "/repair-guides/ignition-coil-replacement"},
            ],
            "inspection_priority": [
                "Inspect exhaust leaks before and near the converter",
                "Compare upstream and downstream O2 sensor patterns",
                "Check misfire, fuel trim, and rich-running evidence before replacing the converter",
            ],
            "confidence_cues": [
                "Converter is downstream of root-cause faults",
                "O2 sensor data must support the repair",
                "Exhaust leaks can imitate efficiency faults",
            ],
            "estimate_guidance": [
                "Inspect exhaust leaks before converter replacement.",
                "Compare upstream and downstream O2 behavior before pricing sensors or converter.",
                "Correct fuel trim, misfire, rich-running, oil, or coolant causes before approving the converter.",
            ],
            "symptom_links": [
                {"title": "Fuel Smell From Exhaust", "href": "/symptoms/fuel-smell-from-exhaust"},
                {"title": "Poor Fuel Economy", "href": "/symptoms/poor-fuel-economy"},
                {"title": "Loss of Power", "href": "/symptoms/loss-of-power-while-driving"},
            ],
            "estimator_href": "/estimator?obd=P0420",
        },
        "P0430": {
            "title": "Catalyst Efficiency Diagnostic Path",
            "summary": "Confirm converter efficiency only after checking O2 data, exhaust leaks, misfires, and rich-running causes.",
            "systems": [
                "Catalytic converter efficiency",
                "Upstream and downstream oxygen sensor behavior",
                "Exhaust leak inspection",
                "Misfire or rich-running root cause",
            ],
            "blueprints": [
                {"title": "Oxygen Sensor Blueprint", "href": "/repair-guides/oxygen-sensor-replacement"},
                {"title": "Ignition Coil Blueprint", "href": "/repair-guides/ignition-coil-replacement"},
                {"title": "Fuel Pump and Fuel Control Checks", "href": "/repair-guides/fuel-pump-replacement"},
            ],
            "inspection_priority": [
                "Inspect exhaust leaks before and near the converter",
                "Compare upstream and downstream O2 sensor patterns",
                "Check misfire, fuel trim, and rich-running evidence before replacing the converter",
            ],
            "confidence_cues": [
                "Converter is downstream of root-cause faults",
                "O2 sensor data must support the repair",
                "Exhaust leaks can imitate efficiency faults",
            ],
            "estimator_href": "/estimator?obd=P0430",
        },
        "P0440": {
            "title": "EVAP Diagnostic Path",
            "summary": "Treat EVAP faults as leak, purge, vent, wiring, and smoke-test questions before replacing valves.",
            "systems": [
                "Purge valve sealing",
                "Vent valve operation",
                "Smoke testing and leak location",
                "Fuel cap, filler neck, hoses, and canister",
            ],
            "blueprints": [
                {"title": "EVAP Purge Valve Blueprint", "href": "/repair-guides/evap-purge-valve-replacement"},
                {"title": "EVAP Vent Valve Estimate Path", "href": "/cost/evap-vent-valve-replacement"},
                {"title": "EVAP Smoke Test", "href": "/estimator?service=evap_system_diagnosis"},
            ],
            "inspection_priority": [
                "Smoke test before replacing leak-related parts",
                "Check purge sealing and vent command response",
                "Inspect cap, filler neck, hoses, and canister for physical leaks",
            ],
            "confidence_cues": [
                "Usually no drivability symptoms",
                "Smoke-test evidence matters",
                "Valve command does not prove valve sealing",
            ],
            "estimator_href": "/estimator?obd=P0440",
        },
        "P0442": {
            "title": "EVAP Small Leak Diagnostic Path",
            "summary": "Use smoke testing and vent/purge checks to locate small leaks before pricing EVAP parts.",
            "systems": [
                "Small EVAP leaks",
                "Fuel cap and filler neck",
                "Purge and vent sealing",
                "Canister and hose routing",
            ],
            "blueprints": [
                {"title": "EVAP Purge Valve Blueprint", "href": "/repair-guides/evap-purge-valve-replacement"},
                {"title": "EVAP Vent Valve Estimate Path", "href": "/cost/evap-vent-valve-replacement"},
                {"title": "EVAP Smoke Test", "href": "/estimator?service=evap_system_diagnosis"},
            ],
            "inspection_priority": [
                "Smoke test the EVAP system",
                "Inspect cap, filler neck, and hose connections",
                "Command purge and vent valves only after leak location is understood",
            ],
            "confidence_cues": [
                "Tiny leaks can be visual-invisible",
                "Smoke testing prevents parts guessing",
                "No drivability symptom is common",
            ],
            "estimate_guidance": [
                "Quote smoke testing before purge or vent valve replacement.",
                "Inspect gas cap seal, filler neck, and small hose connections first.",
                "Price valves only when sealing or command tests support them.",
            ],
            "symptom_links": [
                {"title": "Fuel Smell From Exhaust", "href": "/symptoms/fuel-smell-from-exhaust"},
                {"title": "Hard Start After Sitting", "href": "/symptoms/hard-start-after-sitting-overnight"},
            ],
            "estimator_href": "/estimator?obd=P0442",
        },
        "P0446": {
            "title": "EVAP Vent Diagnostic Path",
            "summary": "Check vent command, blockage, contamination, wiring, and smoke-test results before replacing the vent valve.",
            "systems": [
                "Vent valve command",
                "Canister restriction",
                "Fuel tank pressure behavior",
                "Vent wiring and plumbing",
            ],
            "blueprints": [
                {"title": "EVAP Vent Valve Estimate Path", "href": "/cost/evap-vent-valve-replacement"},
                {"title": "EVAP Purge Valve Estimate Path", "href": "/cost/evap-purge-valve-replacement"},
            ],
            "inspection_priority": [
                "Command the vent valve and verify response",
                "Inspect vent filter, canister, and wiring",
                "Smoke test when leak or restriction evidence is unclear",
            ],
            "confidence_cues": [
                "Vent restriction can mimic valve failure",
                "Wiring and contamination are common",
                "Smoke testing protects the estimate",
            ],
            "estimator_href": "/estimator?obd=P0446",
        },
        "P0455": {
            "title": "EVAP Large Leak Diagnostic Path",
            "summary": "Locate the leak with cap, filler neck, hose, purge, vent, and smoke-test checks before parts replacement.",
            "systems": [
                "Large EVAP leak",
                "Fuel cap and filler neck",
                "Purge and vent sealing",
                "Canister and hose damage",
            ],
            "blueprints": [
                {"title": "EVAP Purge Valve Blueprint", "href": "/repair-guides/evap-purge-valve-replacement"},
                {"title": "EVAP Vent Valve Estimate Path", "href": "/cost/evap-vent-valve-replacement"},
                {"title": "EVAP Smoke Test", "href": "/estimator?service=evap_system_diagnosis"},
            ],
            "inspection_priority": [
                "Inspect cap, filler neck, and obvious hose disconnections",
                "Smoke test the system before replacing valves",
                "Verify purge and vent sealing if smoke does not reveal a physical leak",
            ],
            "confidence_cues": [
                "Large leaks may still be hidden above the tank",
                "Valve sealing and plumbing both matter",
                "No drivability symptom is common",
            ],
            "estimate_guidance": [
                "Inspect gas cap fit, filler neck, and disconnected hoses before parts replacement.",
                "Quote smoke testing if the leak is not obvious.",
                "Verify purge and vent valve sealing before estimating either valve.",
            ],
            "symptom_links": [
                {"title": "Fuel Smell From Exhaust", "href": "/symptoms/fuel-smell-from-exhaust"},
                {"title": "Hard Start After Sitting", "href": "/symptoms/hard-start-after-sitting-overnight"},
            ],
            "estimator_href": "/estimator?obd=P0455",
        },
        "P0456": {
            "title": "EVAP Very Small Leak Diagnostic Path",
            "summary": "Use careful smoke testing and cap/filler/valve checks before approving small-leak EVAP parts.",
            "systems": [
                "Very small EVAP leaks",
                "Fuel cap seal",
                "Purge and vent valve sealing",
                "Canister, tank, and hose fittings",
            ],
            "blueprints": [
                {"title": "EVAP Purge Valve Blueprint", "href": "/repair-guides/evap-purge-valve-replacement"},
                {"title": "EVAP Vent Valve Estimate Path", "href": "/cost/evap-vent-valve-replacement"},
                {"title": "EVAP Smoke Test", "href": "/estimator?service=evap_system_diagnosis"},
            ],
            "inspection_priority": [
                "Smoke test slowly and inspect small fittings",
                "Check cap seal and filler neck rust",
                "Verify purge and vent valves seal when commanded closed",
            ],
            "confidence_cues": [
                "Very small leaks are easy to misdiagnose",
                "Smoke-test evidence matters most",
                "Parts should follow leak location",
            ],
            "estimator_href": "/estimator?obd=P0456",
        },
        "P0138": {
            "title": "Oxygen Sensor High-Voltage Diagnostic Path",
            "summary": "Confirm whether the downstream O2 sensor is biased, the circuit is shorted, or the exhaust stream is truly rich.",
            "systems": [
                "Downstream oxygen sensor signal",
                "Sensor heater and wiring",
                "Rich-running fuel control",
                "Catalyst monitor context",
            ],
            "blueprints": [
                {"title": "Oxygen Sensor Blueprint", "href": "/repair-guides/oxygen-sensor-replacement"},
                {"title": "Ignition Coil Blueprint", "href": "/repair-guides/ignition-coil-replacement"},
            ],
            "inspection_priority": [
                "Verify bank and sensor location before replacing parts",
                "Inspect wiring for short-to-voltage or melted exhaust contact",
                "Check fuel trims and misfire data when the exhaust may actually be rich",
            ],
            "confidence_cues": [
                "Sensor code is not automatically sensor failure",
                "Fuel control can bias O2 readings",
                "Bank and sensor location matter",
            ],
            "estimator_href": "/estimator?obd=P0138",
        },
        "P0158": {
            "title": "Oxygen Sensor High-Voltage Diagnostic Path",
            "summary": "Confirm whether the downstream O2 sensor is biased, the circuit is shorted, or the exhaust stream is truly rich.",
            "systems": [
                "Downstream oxygen sensor signal",
                "Sensor heater and wiring",
                "Rich-running fuel control",
                "Catalyst monitor context",
            ],
            "blueprints": [
                {"title": "Oxygen Sensor Blueprint", "href": "/repair-guides/oxygen-sensor-replacement"},
                {"title": "Ignition Coil Blueprint", "href": "/repair-guides/ignition-coil-replacement"},
            ],
            "inspection_priority": [
                "Verify bank and sensor location before replacing parts",
                "Inspect wiring for short-to-voltage or melted exhaust contact",
                "Check fuel trims and misfire data when the exhaust may actually be rich",
            ],
            "confidence_cues": [
                "Sensor code is not automatically sensor failure",
                "Fuel control can bias O2 readings",
                "Bank and sensor location matter",
            ],
            "estimator_href": "/estimator?obd=P0158",
        },
        "P0562": {
            "title": "Low Voltage Diagnostic Path",
            "summary": "Separate battery capacity, alternator output, belt drive, and cable voltage drop before replacing parts.",
            "systems": [
                "Battery health",
                "Alternator output",
                "Serpentine belt and tensioner",
                "Charging cables, fuses, and grounds",
            ],
            "blueprints": [
                {"title": "Battery Blueprint", "href": "/repair-guides/battery-replacement"},
                {"title": "Alternator Blueprint", "href": "/repair-guides/alternator-replacement"},
                {"title": "Serpentine Belt Blueprint", "href": "/repair-guides/serpentine-belt-replacement"},
            ],
            "estimator_href": "/estimator?obd=P0562",
        },
    }
    if normalized in {"P0301", "P0302", "P0303", "P0304"}:
        cylinder = normalized[-1]
        paths[normalized] = {
            "title": f"Cylinder {cylinder} Misfire Diagnostic Path",
            "summary": f"Treat {normalized} as a focused cylinder {cylinder} fault until spark, fuel, air, and compression evidence points to the repair.",
            "systems": [
                f"Cylinder {cylinder} spark plug condition",
                f"Cylinder {cylinder} ignition coil output",
                "Injector pulse and fuel delivery",
                "Compression, leak-down, and intake sealing",
            ],
            "inspection_priority": [
                "Inspect the spark plug before replacing the coil",
                "Swap coil or plug only when the test can prove whether the misfire moves",
                "Check injector command, fuel delivery, and compression if the fault stays on the same cylinder",
            ],
            "confidence_cues": [
                "Ignition verification before coil replacement",
                "Injector and compression checks if the misfire stays",
                "Flashing check engine light means catalyst risk",
            ],
            "estimate_guidance": [
                "Quote ignition parts after plug or coil testing supports the fault.",
                "Use injector or compression diagnosis when swap testing does not move the misfire.",
                "Check catalyst-risk history before pricing downstream converter work.",
            ],
            "blueprints": [
                {"title": "Ignition Coil Blueprint", "href": "/repair-guides/ignition-coil-replacement"},
                {"title": "Spark Plug Blueprint", "href": "/repair-guides/spark-plug-replacement"},
                {"title": "Ignition Coil Test", "href": "/repair-guides/how-to-test-an-ignition-coil"},
                {"title": "Cylinder Misfire Diagnosis", "href": "/repair-guides/how-to-diagnose-a-cylinder-misfire"},
            ],
            "symptom_links": [
                {"title": "Engine Misfire At Idle", "href": "/symptoms/engine-misfire-at-idle"},
                {"title": "Check Engine Light Flashing", "href": "/symptoms/check-engine-light-flashing"},
                {"title": "Cold Start Misfire", "href": "/symptoms/cold-start-misfire"},
            ],
            "estimator_href": f"/estimator?obd={normalized}",
        }
    path = paths.get(normalized, {})
    if not path:
        return {}

    confidence_defaults = infer_mechanic_confidence_guidance(normalized, path.get("title"), path.get("summary"))
    return {
        **path,
        "inspection_priority": path.get("inspection_priority") or confidence_defaults["inspection_priority"],
        "confidence_cues": path.get("confidence_cues") or confidence_defaults["confidence_cues"],
    }


def normalize_diagnostic_entry(raw_entry: Any, *, file_slug: str, repair_guides: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    raw = dict(raw_entry) if isinstance(raw_entry, dict) else {}

    possible_causes = normalize_repair_guide_list(raw.get("common_causes"))
    if not possible_causes:
        possible_causes = normalize_repair_guide_list(
            [item.get("name") for item in raw.get("likely_repairs", []) if isinstance(item, dict)]
        )

    related_repair_guides = build_related_repair_guides(raw.get("likely_repairs"), repair_guides)
    estimator_links = build_estimator_links(raw.get("estimator_link"), related_repair_guides)

    code = str(raw.get("code") or "").strip()
    title = str(raw.get("title") or code or file_slug.replace("-", " ").replace("_", " ").title()).strip()

    canonical_slug = str(raw.get("slug") or code or file_slug).strip().lower().replace("_", "-")

    confidence_defaults = infer_mechanic_confidence_guidance(code, title, raw.get("summary"))

    return {
        "slug": canonical_slug,
        "code": code,
        "title": title,
        "summary": str(raw.get("summary") or "").strip(),
        "meaning": str(raw.get("meaning") or "").strip(),
        "quick_checks": normalize_repair_guide_list(raw.get("quick_checks")),
        "inspection_priority": (
            normalize_repair_guide_list(raw.get("inspection_priority"))
            or confidence_defaults["inspection_priority"]
        ),
        "confidence_cues": (
            normalize_repair_guide_list(raw.get("confidence_cues") or raw.get("workflow_cues"))
            or confidence_defaults["confidence_cues"]
        ),
        "possible_causes": possible_causes,
        "related_repair_guides": related_repair_guides,
        "estimator_links": estimator_links,
        "diagnostic_tools_link": str(raw.get("diagnostic_tools_link") or "/obd").strip() or "/obd",
        "detail_href": f"/diagnostics/{canonical_slug}",
        "entry_type": "obd_code",
        "entry_label": "OBD Code",
    }


def normalize_symptom_entry(raw_entry: Any, *, file_slug: str, repair_guides: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    raw = dict(raw_entry) if isinstance(raw_entry, dict) else {}

    possible_causes = normalize_repair_guide_list(raw.get("common_causes"))
    if not possible_causes:
        possible_causes = normalize_repair_guide_list(
            [item.get("name") for item in raw.get("likely_causes", []) if isinstance(item, dict)]
        )

    related_repair_guides = build_related_repair_guides(raw.get("likely_causes"), repair_guides)
    estimator_links = build_estimator_links(raw.get("estimator_link"), related_repair_guides)

    title = str(raw.get("title") or file_slug.replace("-", " ").replace("_", " ").title()).strip()
    confidence_defaults = infer_mechanic_confidence_guidance(file_slug, title, raw.get("summary"), raw.get("category"))

    return {
        "slug": file_slug.replace("_", "-"),
        "title": title,
        "summary": str(raw.get("summary") or "").strip(),
        "intro": str(raw.get("intro") or "").strip(),
        "system": str(raw.get("system") or raw.get("category") or "").strip(),
        "common_sounds": normalize_repair_guide_list(raw.get("common_sounds")),
        "quick_checks": normalize_repair_guide_list(raw.get("quick_checks")),
        "inspection_priority": (
            normalize_repair_guide_list(raw.get("inspection_priority"))
            or confidence_defaults["inspection_priority"]
        ),
        "confidence_cues": (
            normalize_repair_guide_list(raw.get("confidence_cues") or raw.get("workflow_cues"))
            or confidence_defaults["confidence_cues"]
        ),
        "possible_causes": possible_causes,
        "diagnostic_paths": normalize_repair_guide_list(raw.get("diagnostic_paths")),
        "diagnostic_path_sections": normalize_diagnostic_path_sections(raw.get("diagnostic_path_sections")),
        "related_obd_codes": normalize_symptom_obd_codes(raw.get("related_obd_codes")),
        "related_symptoms": normalize_related_link_items(raw.get("related_symptoms")),
        "recommended_repairs": normalize_symptom_recommended_repairs(raw.get("recommended_repairs")),
        "related_repair_guides": related_repair_guides,
        "estimator_links": estimator_links,
        "diagnostic_tools_link": str(raw.get("diagnostic_tools_link") or "/diagnostics").strip() or "/diagnostics",
        "detail_href": f"/symptoms/{file_slug.replace('_', '-')}",
        "entry_type": "symptom",
        "entry_label": "Symptom",
    }


def build_vehicle_system_entries(
    symptom_entries: List[Dict[str, Any]],
    repair_guides: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}

    for symptom in symptom_entries:
        system_label = str(symptom.get("system") or "").strip()
        if not system_label:
            continue
        grouped.setdefault(
            system_label,
            {
                "title": system_label,
                "possible_causes": [],
                "related_repair_guides": [],
                "estimator_links": [],
            },
        )
        grouped[system_label]["possible_causes"].extend(symptom.get("possible_causes") or [])
        grouped[system_label]["related_repair_guides"].extend(symptom.get("related_repair_guides") or [])
        grouped[system_label]["estimator_links"].extend(symptom.get("estimator_links") or [])

    for guide in repair_guides.values():
        system_label = str(guide.get("category") or "").strip()
        if not system_label:
            continue
        grouped.setdefault(
            system_label,
            {
                "title": system_label,
                "possible_causes": [],
                "related_repair_guides": [],
                "estimator_links": [],
            },
        )

        guide_href = f"/repair-guides/{guide.get('slug')}"
        estimate = guide.get("estimate") or {}
        service_code = str(estimate.get("service_code") or "").strip()
        estimator_href = build_estimator_service_href(
            service_code,
            estimate.get("estimator_link") or "/estimator",
        ) if service_code else "/estimator"

        grouped[system_label]["related_repair_guides"].append(
            {
                "title": str(guide.get("title") or "").strip(),
                "href": guide_href,
                "estimator_href": estimator_href,
            }
        )
        grouped[system_label]["estimator_links"].append(
            {
                "label": f"Estimate {guide.get('title', 'Repair')}",
                "href": estimator_href,
            }
        )

    entries: List[Dict[str, Any]] = []
    for system_label in sorted(grouped):
        possible_causes = []
        for cause in grouped[system_label]["possible_causes"]:
            text = str(cause or "").strip()
            if text and text not in possible_causes:
                possible_causes.append(text)

        related_repair_guides = dedupe_link_items(grouped[system_label]["related_repair_guides"])
        estimator_links = dedupe_link_items(grouped[system_label]["estimator_links"])

        entries.append(
            {
                "title": system_label,
                "summary": f"Common problems, repair guides, and estimate entry points for {system_label.lower()} issues.",
                "possible_causes": possible_causes[:5],
                "related_repair_guides": related_repair_guides[:4],
                "estimator_links": estimator_links[:4],
                "entry_type": "vehicle_system",
                "entry_label": "Vehicle System",
            }
        )

    return entries


def normalize_system_hub_entry(raw_hub: Any, *, file_slug: str = "") -> Dict[str, Any]:
    hub = dict(raw_hub) if isinstance(raw_hub, dict) else {}
    slug = str(hub.get("slug") or file_slug or "").strip().replace("_", "-")
    title = str(hub.get("title") or slug.replace("-", " ").title() or "Repair System Hub").strip()
    estimate = hub.get("estimate") if isinstance(hub.get("estimate"), dict) else {}
    service_code = str(
        estimate.get("service_code")
        or hub.get("service_code")
        or ""
    ).strip()
    estimator_link = str(
        estimate.get("estimator_link")
        or hub.get("estimator_link")
        or "/estimator"
    ).strip() or "/estimator"

    return {
        "slug": slug,
        "title": title,
        "summary": str(hub.get("summary") or "").strip(),
        "intro": str(hub.get("intro") or "").strip(),
        "common_symptoms": normalize_related_link_items(hub.get("common_symptoms") or hub.get("related_symptoms")),
        "related_symptoms": normalize_related_link_items(hub.get("related_symptoms") or hub.get("common_symptoms")),
        "related_obd_codes": normalize_symptom_obd_codes(hub.get("related_obd_codes")),
        "related_repairs": normalize_symptom_recommended_repairs(hub.get("related_repairs") or hub.get("related_repair_guides")),
        "diagnostic_path_sections": normalize_diagnostic_path_sections(hub.get("diagnostic_path_sections")),
        "inspection_priority": normalize_repair_guide_list(hub.get("inspection_priority")),
        "confidence_cues": normalize_repair_guide_list(hub.get("confidence_cues") or hub.get("workflow_cues")),
        "estimate_guidance": normalize_repair_guide_list(hub.get("estimate_guidance")),
        "related_systems": normalize_related_link_items(hub.get("related_systems")),
        "estimate": {
            "cta_label": str(estimate.get("cta_label") or hub.get("cta_label") or "Continue Estimate").strip(),
            "service_code": service_code,
            "service_name": str(estimate.get("service_name") or title).strip(),
            "estimator_link": estimator_link,
            "href": build_estimator_service_href(service_code, estimator_link),
        },
        "entry_type": "system_hub",
        "entry_label": "Repair System",
        "detail_href": f"/repair-systems/{slug}",
    }


def load_system_hub_entries() -> List[Dict[str, Any]]:
    hubs_dir = DATA_DIR / "system_hubs"
    if not hubs_dir.exists():
        return []

    entries: List[Dict[str, Any]] = []
    for file in sorted(hubs_dir.glob("*.json")):
        try:
            raw = json.loads(file.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        entries.append(normalize_system_hub_entry(raw, file_slug=file.stem))

    return sorted(entries, key=lambda item: item.get("title", "").lower())


def load_system_hub_source(slug: str) -> Tuple[Dict[str, Any], str]:
    hubs_dir = DATA_DIR / "system_hubs"
    file_slug = str(slug or "").strip().lower().replace("-", "_")
    direct_path = hubs_dir / f"{file_slug}.json"
    if direct_path.exists():
        return load_json_file("system_hubs", f"{file_slug}.json"), direct_path.stem

    requested = str(slug or "").strip().lower().replace("_", "-")
    for file in hubs_dir.glob("*.json"):
        try:
            raw = json.loads(file.read_text(encoding="utf-8-sig"))
        except Exception:
            continue

        raw_slug = str(raw.get("slug") or "").strip().lower().replace("_", "-")
        if requested in {file.stem.replace("_", "-").lower(), raw_slug}:
            return raw, file.stem

    raise HTTPException(status_code=404, detail="Content not found")


SYSTEM_HUB_NAV_ITEMS = {
    "brake-system-repairs": {
        "title": "Brake System Repairs",
        "href": "/repair-systems/brake-system-repairs",
        "description": "Pads, rotors, calipers, vibration, noise, and wheel-end checks.",
    },
    "charging-starting-system": {
        "title": "Charging & Starting System",
        "href": "/repair-systems/charging-starting-system",
        "description": "Battery, alternator, starter, no-crank, no-start, and voltage workflows.",
    },
    "cooling-system-diagnostics": {
        "title": "Cooling System Diagnostics",
        "href": "/repair-systems/cooling-system-diagnostics",
        "description": "Overheating, coolant loss, fan, thermostat, radiator, and pump paths.",
    },
    "emissions-evap-diagnostics": {
        "title": "Emissions & EVAP Diagnostics",
        "href": "/repair-systems/emissions-evap-diagnostics",
        "description": "EVAP leaks, purge, vent, fuel smell, emissions, and smoke-test direction.",
    },
    "engine-performance-misfire-diagnostics": {
        "title": "Engine Performance & Misfire Diagnostics",
        "href": "/repair-systems/engine-performance-misfire-diagnostics",
        "description": "Misfire, lean trims, rough idle, hard start, MAF, oxygen-sensor, and drivability checks.",
    },
}


def infer_related_system_hubs(*values: Any, limit: int = 3) -> List[Dict[str, str]]:
    text = " ".join(str(value or "") for value in values).lower()
    lean_signal = any(term in text for term in ("p0171", "p0174", "fuel trim", "lean condition", "lean-code", "lean code", "running lean"))
    picks: List[str] = []

    def add(slug: str) -> None:
        if slug in SYSTEM_HUB_NAV_ITEMS and slug not in picks:
            picks.append(slug)

    if any(term in text for term in ("brake", "pad", "rotor", "caliper", "pulsation", "vibration while braking")):
        add("brake-system-repairs")
        add("cooling-system-diagnostics") if "overheat" in text else None
    if any(term in text for term in ("wheel hub", "wheel bearing", "suspension", "steering", "abs", "wheel speed")):
        add("brake-system-repairs")
    if any(term in text for term in ("coolant", "cooling", "overheat", "thermostat", "radiator", "water pump", "fan", "p0128", "p0117", "p0118")):
        add("cooling-system-diagnostics")
        add("engine-performance-misfire-diagnostics")
        add("charging-starting-system")
    if any(term in text for term in ("misfire", "p030", "rough idle", "spark plug", "ignition", "coil", "maf", "mass air", "oxygen sensor", "hard start", "fuel trim", "drivability")) or lean_signal:
        add("engine-performance-misfire-diagnostics")
        add("emissions-evap-diagnostics")
    if any(term in text for term in ("evap", "emission", "p044", "p045", "purge", "vent valve", "fuel smell", "smoke test", "catalyst", "catalytic", "p0420", "p0430")):
        add("emissions-evap-diagnostics")
        add("engine-performance-misfire-diagnostics")
    if any(term in text for term in ("battery", "alternator", "starter", "charging", "starting", "no start", "no-crank", "no crank", "p056", "p0620")):
        add("charging-starting-system")
        add("cooling-system-diagnostics") if "overheat" in text else None

    return [dict(SYSTEM_HUB_NAV_ITEMS[slug]) for slug in picks[:limit]]


def infer_workflow_next_steps(*values: Any, limit: int = 4) -> List[Dict[str, str]]:
    text = " ".join(str(value or "") for value in values).lower()
    lean_signal = any(term in text for term in ("p0171", "p0174", "fuel trim", "lean condition", "lean-code", "lean code", "running lean"))
    steps: List[Dict[str, str]] = []

    def add(title: str, description: str, *, href: str = "", estimator_href: str = "") -> None:
        if any(item["title"] == title for item in steps):
            return
        steps.append(
            {
                "title": title,
                "description": description,
                "href": href,
                "estimator_href": estimator_href,
            }
        )

    if any(term in text for term in ("p030", "misfire", "spark plug", "ignition coil")):
        add("Inspect ignition coils", "Check coil boots, carbon tracking, and whether the miss follows a swap.", href="/repair-guides/ignition-coil-replacement")
        add("Check spark plugs", "Inspect gap, fouling, wear, oil, coolant, and plug-well condition.", href="/repair-guides/spark-plug-replacement")
        add("Verify injector operation", "Move to injector balance, pulse, or leak-down checks if the misfire stays.", estimator_href="/estimator?service=fuel_system_diagnostic")
        add("Check compression if needed", "Use compression or leak-down testing when spark and fuel checks do not move the fault.", estimator_href="/estimator?service=compression_test")
    if any(term in text for term in ("evap", "p044", "p045", "purge", "vent", "smoke test")):
        add("Smoke test EVAP system", "Use smoke testing when the leak source is not obvious.", estimator_href="/estimator?service=evap_leak_test_smoke_test")
        add("Verify purge sealing", "Check purge command and sealing before replacing the valve.", href="/repair-guides/evap-purge-valve-replacement")
        add("Check vent operation", "Command the vent valve and inspect canister-side blockage or contamination.", estimator_href="/estimator?service=evap_vent_valve_replacement")
    if lean_signal or any(term in text for term in ("fuel trim", "rough idle", "vacuum leak")):
        add("Review fuel trims", "Compare trims at idle, 2500 RPM, and cruise before pricing sensors.", estimator_href="/estimator?service=fuel_trim_diagnosis")
        add("Inspect vacuum leaks", "Check intake boots, PCV hoses, and post-MAF leak paths.", href="/repair-guides/how-to-diagnose-a-vacuum-leak")
        add("Check MAF sensor", "Inspect MAF contamination and airflow data after intake leaks are considered.", estimator_href="/estimator?service=mass_air_flow_sensor_replacement")
        add("Inspect intake tubing", "Look for cracked ducts, loose clamps, and unmetered air after the MAF.", estimator_href="/estimator?service=vacuum_leak_diagnosis_smoke_test")
    if any(term in text for term in ("p030", "misfire", "spark plug", "ignition coil", "rough idle", "hard start")):
        add("Inspect ignition coils", "Check coil boots, carbon tracking, and whether the miss follows a swap.", href="/repair-guides/ignition-coil-replacement")
        add("Check spark plugs", "Inspect gap, fouling, wear, oil, coolant, and plug-well condition.", href="/repair-guides/spark-plug-replacement")
        add("Verify injector operation", "Move to injector balance, pulse, or leak-down checks if the misfire stays.", estimator_href="/estimator?service=fuel_system_diagnostic")
        add("Check compression if needed", "Use compression or leak-down testing when spark and fuel checks do not move the fault.", estimator_href="/estimator?service=compression_test")
    if any(term in text for term in ("battery light", "charging", "alternator", "p056", "p0620", "no crank", "no start")):
        add("Test charging voltage", "Measure alternator output and battery voltage under load.", estimator_href="/estimator?service=alternator_diagnosis")
        add("Inspect serpentine belt", "Check belt condition, tensioner travel, and pulley alignment.", href="/repair-guides/serpentine-belt-replacement")
        add("Verify battery condition", "Charge and load test before blaming the alternator or starter.", href="/repair-guides/battery-replacement")
    if any(term in text for term in ("overheat", "coolant", "cooling", "thermostat", "radiator", "water pump", "p0128")):
        add("Pressure test cooling system", "Confirm external leaks, cap behavior, and pressure loss before parts.", estimator_href="/estimator?service=coolant_leak_diagnosis")
        add("Inspect thermostat", "Compare warm-up, scan temperature, and hose temperature behavior.", href="/repair-guides/thermostat-replacement")
        add("Verify radiator fan operation", "Check fan command, AC-load response, fuses, relays, and airflow.", href="/repair-guides/radiator-fan-replacement")
    if any(term in text for term in ("evap", "p044", "p045", "purge", "vent", "fuel smell", "smoke test")):
        add("Smoke test EVAP system", "Use smoke testing when the leak source is not obvious.", estimator_href="/estimator?service=evap_leak_test_smoke_test")
        add("Verify purge sealing", "Check purge command and sealing before replacing the valve.", href="/repair-guides/evap-purge-valve-replacement")
        add("Check vent operation", "Command the vent valve and inspect canister-side blockage or contamination.", estimator_href="/estimator?service=evap_vent_valve_replacement")
    if any(term in text for term in ("brake", "pad", "rotor", "caliper", "vibration while braking", "wheel hub")):
        add("Measure pads and rotors", "Confirm thickness, scoring, heat spots, and inner/outer wear.", estimator_href="/estimator?service=brake_noise_diagnosis")
        add("Inspect caliper movement", "Check slide pins, piston movement, hose restriction, and drag.", href="/repair-guides/brake-caliper-replacement")
        add("Check hub runout/play", "Use when vibration or ABS evidence overlaps brake complaints.", href="/repair-guides/wheel-hub-assembly-replacement")

    return steps[:limit]


def infer_related_inspections(*values: Any, limit: int = 4) -> List[Dict[str, str]]:
    text = " ".join(str(value or "") for value in values).lower()
    lean_signal = any(term in text for term in ("p0171", "p0174", "fuel trim", "lean condition", "lean-code", "lean code", "running lean"))
    inspections: List[Dict[str, str]] = []

    def add(title: str, description: str, estimator_href: str) -> None:
        if any(item["title"] == title for item in inspections):
            return
        inspections.append(
            {
                "title": title,
                "description": description,
                "estimator_href": estimator_href,
            }
        )

    if any(term in text for term in ("brake", "pad", "rotor", "caliper")):
        add("Brake fluid inspection", "Check fluid condition when hydraulic or caliper work is likely.", "/estimator?service=brake_fluid_flush")
    if any(term in text for term in ("wheel hub", "wheel bearing", "steering", "suspension")) or ("alignment" in text and any(term in text for term in ("tire wear", "vehicle pull", "pulling", "steering", "suspension"))):
        add("Alignment inspection", "Use after steering or suspension work when tire wear or pull is present.", "/estimator?service=wheel_alignment_4_wheel")
    if any(term in text for term in ("battery", "alternator", "charging", "starter", "no start", "no crank")):
        add("Charging voltage verification", "Confirm battery, cable, belt, and alternator evidence before replacement.", "/estimator?service=alternator_diagnosis")
    if any(term in text for term in ("coolant", "cooling", "overheat", "radiator", "water pump", "thermostat")):
        add("Coolant contamination check", "Inspect coolant condition, oil/coolant mixing, and overheating history.", "/estimator?service=cooling_system_pressure_test")
    if lean_signal or any(term in text for term in ("evap", "p044", "p045", "vacuum leak")):
        add("Smoke testing", "Use smoke testing when leak evidence needs confirmation before parts.", "/estimator?service=evap_leak_test_smoke_test")
    if any(term in text for term in ("oxygen sensor", "catalyst", "catalytic", "p0420", "p0430", "exhaust")):
        add("Exhaust leak inspection", "Check leaks before oxygen-sensor or catalyst decisions.", "/estimator?service=exhaust_leak_repair")

    return inspections[:limit]


def build_diagnostics_workflow_clusters() -> List[Dict[str, Any]]:
    return [
        {
            "title": "Misfire, Lean, and Drivability",
            "summary": "Start when rough idle, P0300, P0171/P0174, MAF, or oxygen-sensor evidence overlaps.",
            "links": [
                SYSTEM_HUB_NAV_ITEMS["engine-performance-misfire-diagnostics"],
                SYSTEM_HUB_NAV_ITEMS["emissions-evap-diagnostics"],
                {"title": "Rough Idle", "href": "/symptoms/rough-idle", "description": "Symptom path for idle shake and trim clues."},
            ],
        },
        {
            "title": "EVAP, Fuel Smell, and Emissions",
            "summary": "Use when leak codes, purge/vent behavior, smoke testing, or fuel smell drive the next step.",
            "links": [
                SYSTEM_HUB_NAV_ITEMS["emissions-evap-diagnostics"],
                {"title": "P0442", "href": "/obd/p0442", "description": "Small EVAP leak code path."},
                {"title": "EVAP Purge Valve", "href": "/repair-guides/evap-purge-valve-replacement", "description": "Repair path after purge-side testing."},
            ],
        },
        {
            "title": "Brakes, Vibration, and Wheel-End",
            "summary": "Move between brake noise, vibration, rotor, caliper, and wheel hub checks without over-quoting.",
            "links": [
                SYSTEM_HUB_NAV_ITEMS["brake-system-repairs"],
                {"title": "Vibration While Braking", "href": "/symptoms/vibration-while-braking", "description": "Symptom path for runout and hub overlap."},
                {"title": "Wheel Hub Workflow", "href": "/repair-guides/wheel-hub-assembly-replacement", "description": "Wheel-end checks when brakes and hubs overlap."},
            ],
        },
        {
            "title": "Cooling, Starting, and System Load",
            "summary": "Use when overheating, fan load, weak charging, or no-start context changes the inspection order.",
            "links": [
                SYSTEM_HUB_NAV_ITEMS["cooling-system-diagnostics"],
                SYSTEM_HUB_NAV_ITEMS["charging-starting-system"],
                {"title": "Overheating At Idle", "href": "/symptoms/overheating-at-idle", "description": "Symptom path for fan, airflow, and heat load."},
            ],
        },
    ]


def load_diagnostic_entries(repair_guides: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    diagnostics_dir = DATA_DIR / "diagnostics"
    entries: List[Dict[str, Any]] = []

    for file in diagnostics_dir.glob("*.json"):
        try:
            raw = json.loads(file.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        entries.append(
            normalize_diagnostic_entry(raw, file_slug=file.stem, repair_guides=repair_guides)
        )

    return sorted(entries, key=lambda item: ((item.get("code") or item.get("title") or "").lower()))


def load_symptom_entries(repair_guides: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    symptoms_dir = DATA_DIR / "symptoms"
    entries: List[Dict[str, Any]] = []

    for file in symptoms_dir.glob("*.json"):
        try:
            raw = json.loads(file.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        entries.append(
            normalize_symptom_entry(raw, file_slug=file.stem, repair_guides=repair_guides)
        )

    return sorted(entries, key=lambda item: (item.get("title") or "").lower())


def load_diagnostic_source(slug: str) -> Tuple[Dict[str, Any], str]:
    diagnostics_dir = DATA_DIR / "diagnostics"
    file_slug = slug.replace("-", "_")
    direct_path = diagnostics_dir / f"{file_slug}.json"
    if direct_path.exists():
        return load_json_file("diagnostics", f"{file_slug}.json"), direct_path.stem

    requested = str(slug or "").strip().lower().replace("_", "-")
    for file in diagnostics_dir.glob("*.json"):
        try:
            raw = json.loads(file.read_text(encoding="utf-8-sig"))
        except Exception:
            continue

        raw_slug = str(raw.get("slug") or "").strip().lower().replace("_", "-")
        raw_code = str(raw.get("code") or "").strip().lower()
        if requested in {file.stem.replace("_", "-").lower(), raw_slug, raw_code}:
            return raw, file.stem

    raise HTTPException(status_code=404, detail="Content not found")


def build_platform_sections(current_href: str = "") -> List[Dict[str, str]]:
    sections = [
        {
            "title": "Diagnostics Hub",
            "href": "/diagnostics",
            "summary": "Enter from OBD codes, symptoms, and vehicle systems.",
        },
        {
            "title": "Symptoms",
            "href": "/symptoms",
            "summary": "Search symptom guides and open the closest matching problem path.",
        },
        {
            "title": "Repair Guides",
            "href": "/repair-guides",
            "summary": "Browse mechanic-focused repair procedures by system.",
        },
        {
            "title": "Repair Costs",
            "href": "/repair-costs",
            "summary": "Browse labor ranges and pricing context by service.",
        },
        {
            "title": "Estimator",
            "href": "/estimator",
            "summary": "Build the estimate once the repair path is known.",
        },
    ]

    if not current_href:
        return sections

    return [section for section in sections if section.get("href") != current_href]

def build_quick_find_items() -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    seen_hrefs: set[str] = set()

    def add_item(
        *,
        href: str,
        item_type: str,
        title: str,
        subtitle: str = "",
        code: str = "",
        keywords: str = "",
    ) -> None:
        href = str(href or "").strip()
        if not href or href in seen_hrefs:
            return

        seen_hrefs.add(href)
        items.append(
            {
                "href": href,
                "type": item_type,
                "title": str(title or "").strip(),
                "subtitle": str(subtitle or "").strip(),
                "code": str(code or "").strip().lower(),
                "search": " ".join(
                    filter(
                        None,
                        [
                            str(code or "").strip(),
                            str(title or "").strip(),
                            str(subtitle or "").strip(),
                            str(keywords or "").strip(),
                        ],
                    )
                ),
            }
        )

    if OBD_SEED_JSON_PATH.exists():
        try:
            obd_data = json.loads(OBD_SEED_JSON_PATH.read_text(encoding="utf-8-sig"))
        except Exception:
            obd_data = {}

        if isinstance(obd_data, dict):
            for raw_code, item in sorted(obd_data.items()):
                code = "".join(ch for ch in str(raw_code or "").upper() if ch.isalnum())[:7]
                if len(code) < 4:
                    continue

                title = str((item or {}).get("title") or "").strip()
                description = str((item or {}).get("description") or "").strip()
                if not title:
                    continue

                add_item(
                    href=f"/obd/{code.lower()}",
                    item_type="OBD Code",
                    title=code,
                    subtitle=title,
                    code=code,
                    keywords=description,
                )

    for guide in build_repair_cost_guide_cards():
        href = str(guide.get("href") or "").strip()
        title = str(guide.get("title") or "").strip()
        description = str(guide.get("description") or "").strip()
        slug_keywords = href.removeprefix("/cost/").replace("-", " ")
        add_item(
            href=href,
            item_type="Cost Guide",
            title=title,
            subtitle=description,
            keywords=slug_keywords,
        )

    repair_guides = load_normalized_repair_guides_map()
    for entry in load_symptom_entries(repair_guides):
        add_item(
            href=str(entry.get("detail_href") or f"/symptoms/{entry.get('slug', '')}").strip(),
            item_type="Symptom",
            title=str(entry.get("title") or "").strip(),
            subtitle=str(entry.get("summary") or "").strip(),
            keywords=" ".join(
                filter(
                    None,
                    [
                        str(entry.get("system") or "").strip(),
                        " ".join(entry.get("possible_causes") or []),
                        " ".join(entry.get("common_sounds") or []),
                        " ".join(item.get("code") or "" for item in entry.get("related_obd_codes") or []),
                        " ".join(item.get("title") or "" for item in entry.get("recommended_repairs") or []),
                        str(entry.get("slug") or "").replace("-", " "),
                    ],
                )
            ),
        )

    for slug, guide in sorted(repair_guides.items(), key=lambda item: item[0]):
        add_item(
            href=f"/repair-guides/{slug}",
            item_type="Repair Guide",
            title=str(guide.get("title") or slug.replace("-", " ").title()).strip(),
            subtitle=str(guide.get("summary") or "").strip(),
            keywords=" ".join(
                filter(
                    None,
                    [
                        str(guide.get("category") or "").strip(),
                        str(guide.get("subcategory") or "").strip(),
                        " ".join(guide.get("symptoms") or []),
                        " ".join(guide.get("likely_causes") or []),
                        " ".join(guide.get("testing_approach") or []),
                        " ".join(item.get("code") or "" for item in guide.get("related_obd_codes") or []),
                        slug.replace("-", " "),
                    ],
                )
            ),
        )

    return items
   
# ============================================================
# Template Routes
# ============================================================

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    metric_incr("page_home")
    return templates.TemplateResponse(
        "home.html",
        {"request": request},
    )


@app.get("/quick-find", response_class=HTMLResponse)
def quick_find_page(request: Request):
    return templates.TemplateResponse(
        "quick_find_page.html",
        {
            "request": request,
            "quick_find_items": [],
        },
    )


def load_finding_estimator_nav_context(request: Request) -> Dict[str, str]:
    query = request.query_params
    if str(query.get("source") or "").strip().lower() != "finding":
        return {}

    customer_id = str(query.get("customer_id") or "").strip()
    vehicle_id = str(query.get("vehicle_id") or "").strip()
    finding_id = str(query.get("finding_id") or "").strip()
    if not (customer_id and vehicle_id and finding_id):
        raise HTTPException(status_code=400, detail="Finding estimator links require customer, vehicle, and finding ids.")

    conn = app_db_conn()
    try:
        user = current_user(conn, request)
        if user is None:
            raise HTTPException(status_code=403, detail="Finding estimator links require shop access.")
        shop_context = current_shop_context(conn, request)
        request.state.current_user = user
        request.state.current_shop = shop_context
        try:
            customer_id_int = int(customer_id)
            vehicle_id_int = int(vehicle_id)
            finding_id_int = int(finding_id)
            shop_id_int = int(shop_context.get("id"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid finding estimator link.")

        linked = conn.execute(
            """
            SELECT fr.id
            FROM findings_records fr
            JOIN customer_vehicles cv
              ON cv.id = fr.vehicle_id
             AND cv.customer_id = ?
            JOIN customers c
              ON c.id = cv.customer_id
             AND c.shop_id = ?
            WHERE fr.id = ?
              AND fr.customer_id = ?
              AND fr.vehicle_id = ?
            LIMIT 1
            """,
            (customer_id_int, shop_id_int, finding_id_int, customer_id_int, vehicle_id_int),
        ).fetchone()
        if not linked:
            raise HTTPException(status_code=404, detail="Finding estimator link not found.")
    finally:
        conn.close()

    base = f"/pro/customers/{customer_id}/vehicles/{vehicle_id}"
    return {
        "finding_url": f"{base}/findings/{finding_id}",
        "vehicle_url": f"{base}#recommendations-findings",
        "command_center_url": "/pro/dashboard",
        "handoff_url": (
            f"/pro/estimator/finding-handoff?customer_id={customer_id}"
            f"&vehicle_id={vehicle_id}&finding_id={finding_id}"
        ),
    }


@app.get("/estimator", response_class=HTMLResponse)
def estimator(request: Request):
    metric_incr("page_estimator")
    pro_access_state = pro_request_access_state(request)
    query = request.query_params
    finding_estimator_nav = load_finding_estimator_nav_context(request)
    estimator_parts_sources: List[Dict[str, str]] = []
    estimator_parts_source_title = ""
    if str(query.get("source") or "").strip().lower() == "finding":
        estimator_parts_source_title = estimator_repair_keyword_from_query(query)
        estimator_vehicle = estimator_vehicle_from_query(query)
        estimator_parts_sources = repair_workspace_parts_sources(
            None,
            estimator_vehicle,
            estimator_parts_source_title,
        )
    response = templates.TemplateResponse(
        "estimator.html",
        {
            "request": request,
            "pro_handoff_available": pro_access_state["access_allowed"],
            "estimator_parts_sources": estimator_parts_sources,
            "estimator_parts_source_title": estimator_parts_source_title,
            "finding_estimator_nav": finding_estimator_nav,
            "is_finding_estimator": bool(finding_estimator_nav),
        },
    )
    if pro_access_state["qa_key_matched"]:
        response.set_cookie(
            PRO_QA_ACCESS_COOKIE,
            pro_qa_access_signature(pro_access_state["qa_key"]),
            max_age=60 * 60 * 8,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="lax",
        )
    return response


@app.get("/api/parts-sources")
def estimator_parts_sources_api(request: Request):
    query = request.query_params
    service_keyword = estimator_repair_keyword_from_query(query)
    vehicle = estimator_vehicle_from_query(query)
    sources = repair_workspace_parts_sources(None, vehicle, service_keyword)
    return JSONResponse(
        {
            "service_keyword": service_keyword,
            "sources": sources,
        }
    )

@app.get("/obd", response_class=HTMLResponse)
def obd(request: Request):
    metric_incr("page_obd_lookup")
    obd_code_groups, total_codes = build_obd_index_groups()
    return templates.TemplateResponse(
        "obd_index.html",
        {
            "request": request,
            "obd_code_groups": obd_code_groups,
            "total_codes": total_codes,
            "quick_find_items": build_quick_find_items(),
        },
    )

OBD_RANGE_PAGE_CONFIG = {
    "p00xx": {
        "prefixes": ("P00",),
        "title": "P00xx OBD Codes",
        "group_title": "P00xx Air / Fuel / Sensor Codes",
        "intro": "Browse P00xx OBD trouble codes covering core air metering, fuel, and sensor faults, then open the full TorqueMech code page for causes, checks, and repair direction.",
        "meta_description": "Browse P00xx OBD trouble codes on TorqueMech with plain-English meanings, common causes, quick checks, and related repair guidance.",
    },
    "p01xx": {
        "prefixes": ("P01",),
        "title": "P01xx OBD Codes",
        "group_title": "P01xx Air / Fuel / Sensor Codes",
        "intro": "Browse P01xx OBD trouble codes related to air, fuel, and sensor performance, then open each TorqueMech guide for causes, quick checks, and likely repairs.",
        "meta_description": "Browse P01xx OBD trouble codes on TorqueMech with plain-English meanings, common causes, quick checks, and likely repair direction.",
    },
    "p02xx": {
        "prefixes": ("P02",),
        "title": "P02xx OBD Codes",
        "group_title": "P02xx Fuel / Injector Codes",
        "intro": "Browse P02xx OBD trouble codes focused on fuel delivery, injectors, and mixture-control faults, then open the full TorqueMech code pages for diagnostic direction.",
        "meta_description": "Browse P02xx OBD trouble codes on TorqueMech for fuel and injector faults with plain-English meanings, causes, and repair direction.",
    },
    "p03xx": {
        "prefixes": ("P03",),
        "title": "P03xx OBD Codes",
        "group_title": "P03xx Ignition / Misfire Codes",
        "intro": "Browse P03xx OBD trouble codes for ignition, misfire, crank, and cam faults, then open the full TorqueMech guides for causes, checks, and repair next steps.",
        "meta_description": "Browse P03xx OBD trouble codes on TorqueMech for ignition and misfire issues with code meanings, likely causes, and repair guidance.",
    },
    "p04xx": {
        "prefixes": ("P04",),
        "title": "P04xx OBD Codes",
        "group_title": "P04xx Emissions / EVAP / Catalyst Codes",
        "intro": "Browse P04xx OBD trouble codes related to emissions, EVAP, EGR, and catalyst performance, then open each TorqueMech code guide for diagnostic direction.",
        "meta_description": "Browse P04xx OBD trouble codes on TorqueMech for emissions, EVAP, and catalyst faults with meanings, causes, and repair direction.",
    },
    "p05xx": {
        "prefixes": ("P05",),
        "title": "P05xx OBD Codes",
        "group_title": "P05xx Idle / Speed / Electrical Codes",
        "intro": "Browse P05xx OBD trouble codes covering idle control, speed signals, and charging or electrical faults, then open the full TorqueMech guides for next steps.",
        "meta_description": "Browse P05xx OBD trouble codes on TorqueMech for idle, speed, and electrical faults with code meanings, causes, and repair guidance.",
    },
    "p08xx": {
        "prefixes": ("P08",),
        "title": "P08xx OBD Codes",
        "group_title": "P08xx Transmission / Clutch / Range Codes",
        "intro": "Browse P08xx OBD trouble codes covering clutch inputs, shift-position faults, transfer-case controls, and transmission pressure switch issues, then open each TorqueMech guide for practical next steps.",
        "meta_description": "Browse P08xx OBD trouble codes on TorqueMech for clutch, shift-range, 4WD, and transmission pressure faults with plain-English meaning and diagnostic direction.",
    },
    "p09xx": {
        "prefixes": ("P09",),
        "title": "P09xx OBD Codes",
        "group_title": "P09xx Transmission / Hydraulic / Actuator Codes",
        "intro": "Browse P09xx OBD trouble codes focused on clutch actuators, gear-select circuits, hydraulic pressure signals, and transmission control faults, then open each TorqueMech guide for causes and checks.",
        "meta_description": "Browse P09xx OBD trouble codes on TorqueMech for transmission actuator, hydraulic pressure, and gear-select faults with code meanings, causes, and repair direction.",
    },
}

def build_obd_range_group(range_slug: str) -> Tuple[List[Dict[str, Any]], int, Dict[str, str] | None]:
    config = OBD_RANGE_PAGE_CONFIG.get(str(range_slug or "").lower())
    if not config or not OBD_SEED_JSON_PATH.exists():
        return [], 0, config

    try:
        data = json.loads(OBD_SEED_JSON_PATH.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            return [], 0, config
    except Exception:
        return [], 0, config

    items: List[Dict[str, str]] = []
    prefixes = tuple(config["prefixes"])

    for raw_code, item in data.items():
        code = "".join(ch for ch in str(raw_code or "").upper() if ch.isalnum())[:7]
        if len(code) < 4 or not code.startswith(prefixes):
            continue

        title = str((item or {}).get("title") or (item or {}).get("description") or "").strip()
        if not title:
            continue

        items.append(
            {
                "code": code,
                "title": title,
                "href": f"/obd/{code.lower()}",
            }
        )

    items.sort(key=lambda item: item["code"])

    group = {
        "id": range_slug.lower(),
        "title": config["group_title"],
        "items": items,
    }
    return [group], len(items), config

def render_obd_range_page(request: Request, range_slug: str):
    metric_incr("page_obd_lookup")
    obd_code_groups, total_codes, config = build_obd_range_group(range_slug)
    if not config:
        raise HTTPException(status_code=404, detail="OBD code range not found")

    return templates.TemplateResponse(
        "obd_index.html",
        {
            "request": request,
            "obd_code_groups": obd_code_groups,
            "total_codes": total_codes,
            "page_title": f"{config['title']} | TorqueMech",
            "meta_description": config["meta_description"],
            "intro_title": config["title"],
            "intro_body": config["intro"],
            "is_range_page": True,
            "range_slug": range_slug.lower(),
        },
    )

@app.get("/obd/p00xx", response_class=HTMLResponse)
def obd_p00xx(request: Request):
    return render_obd_range_page(request, "p00xx")

@app.get("/obd/p01xx", response_class=HTMLResponse)
def obd_p01xx(request: Request):
    return render_obd_range_page(request, "p01xx")

@app.get("/obd/p02xx", response_class=HTMLResponse)
def obd_p02xx(request: Request):
    return render_obd_range_page(request, "p02xx")

@app.get("/obd/p03xx", response_class=HTMLResponse)
def obd_p03xx(request: Request):
    return render_obd_range_page(request, "p03xx")

@app.get("/obd/p04xx", response_class=HTMLResponse)
def obd_p04xx(request: Request):
    return render_obd_range_page(request, "p04xx")

@app.get("/obd/p05xx", response_class=HTMLResponse)
def obd_p05xx(request: Request):
    return render_obd_range_page(request, "p05xx")

@app.get("/obd/p08xx", response_class=HTMLResponse)
def obd_p08xx(request: Request):
    return render_obd_range_page(request, "p08xx")

@app.get("/obd/p09xx", response_class=HTMLResponse)
def obd_p09xx(request: Request):
    return render_obd_range_page(request, "p09xx")

def build_featured_obd_codes():
    return [
        {"code": "P0300", "title": "Random/Multiple Cylinder Misfire"},
        {"code": "P0301", "title": "Cylinder 1 Misfire"},
        {"code": "P0302", "title": "Cylinder 2 Misfire"},
        {"code": "P0303", "title": "Cylinder 3 Misfire"},
        {"code": "P0304", "title": "Cylinder 4 Misfire"},
        {"code": "P0171", "title": "System Too Lean (Bank 1)"},
        {"code": "P0174", "title": "System Too Lean (Bank 2)"},
        {"code": "P0420", "title": "Catalyst Efficiency Below Threshold (Bank 1)"},
        {"code": "P0442", "title": "EVAP Small Leak Detected"},
        {"code": "P0455", "title": "EVAP Large Leak Detected"},
    ]

def load_available_obd_titles() -> Dict[str, str]:
    conn = obd_conn()
    cur = conn.cursor()
    cur.execute("SELECT code, title FROM dtc ORDER BY code")
    rows = cur.fetchall()
    conn.close()

    return {
        str(row["code"]).upper(): (str(row["title"] or row["code"]).strip() or str(row["code"]).upper())
        for row in rows
        if row["code"]
    }


def build_fallback_related_code_candidates(code: str, available_titles: Dict[str, str]) -> List[str]:
    prefixes: List[str] = []
    if len(code) >= 4:
        prefixes.append(code[:4])
    if len(code) >= 3:
        prefixes.append(code[:3])

    def code_distance(other_code: str) -> int:
        try:
            return abs(int(other_code[1:]) - int(code[1:]))
        except Exception:
            return 9999

    candidates: List[str] = []
    seen: set[str] = {code}

    for prefix in prefixes:
        matches = [
            other_code
            for other_code in available_titles
            if other_code.startswith(prefix) and other_code not in seen
        ]
        for other_code in sorted(matches, key=lambda value: (code_distance(value), value)):
            if other_code in seen:
                continue
            seen.add(other_code)
            candidates.append(other_code)

    return candidates


def build_related_codes(code: str, preferred_codes: Optional[List[str]] = None):
    code = code.upper().strip()
    available_titles = load_available_obd_titles()
    if code not in available_titles:
        return []

    max_items = 5
    related: List[Dict[str, str]] = []
    seen_codes = {code}

    def add_candidate(candidate_code: str, fallback_label: str = "", prefer_fallback: bool = False) -> None:
        candidate_code = str(candidate_code or "").upper().strip()
        if not candidate_code or candidate_code in seen_codes or candidate_code not in available_titles:
            return
        seen_codes.add(candidate_code)
        related.append(
            {
                "code": candidate_code,
                "href": f"/obd/{candidate_code.lower()}",
                "label": (
                    fallback_label
                    if prefer_fallback and fallback_label
                    else available_titles.get(candidate_code) or fallback_label or candidate_code
                ),
            }
        )

    for candidate_code in preferred_codes or []:
        if len(related) >= max_items:
            break
        add_candidate(candidate_code)

    if len(related) >= max_items:
        return related[:max_items]

    curated_related_map = {
        "P0300": ["P0301", "P0302", "P0303", "P0304", "P0171"],
        "P0301": ["P0300", "P0302", "P0303", "P0304"],
        "P0302": ["P0300", "P0301", "P0303", "P0304"],
        "P0303": ["P0300", "P0301", "P0302", "P0304"],
        "P0304": ["P0300", "P0301", "P0302", "P0303"],
        "P0171": ["P0174", "P0101", "P0113", "P0420", "P0300"],
        "P0174": ["P0171", "P0101", "P0113", "P0430", "P0300"],
        "P0101": ["P0171", "P0174", "P0113", "P0300", "P0102"],
        "P0113": ["P0101", "P0171", "P0174", "P0300", "P0110"],
        "P0128": ["P0117", "P0118", "P0217"],
        "P0446": ["P0442", "P0455", "P0456"],
        "P0442": ["P0455", "P0456", "P0446", "P0441", "P0449"],
        "P0455": ["P0456", "P0442", "P0446", "P0441", "P0449"],
        "P0456": ["P0442", "P0455", "P0446", "P0441", "P0449"],
        "P0507": ["P0505"],
        "P0420": ["P0430", "P0171", "P0300", "P0138"],
        "P0430": ["P0420"],
        "P0562": ["P0563", "P0620"],
        "P0563": ["P0562"],
    }
    curated_related_labels = {
        "P0300": {
            "P0301": "Cylinder 1 misfire isolation when the random pattern narrows to one cylinder",
            "P0302": "Cylinder 2 misfire comparison for coil swap, plug inspection, and injector checks",
            "P0303": "Cylinder 3 misfire path when the fault stays on one cylinder after ignition checks",
            "P0304": "Cylinder 4 misfire comparison for same-cylinder versus random misfire logic",
            "P0171": "Lean fuel-trim diagnosis when unmetered air or weak fuel delivery creates misfires",
        },
        "P0303": {
            "P0300": "Random/multiple misfire diagnosis when more cylinders join the fault",
            "P0301": "Cylinder 1 misfire comparison for single-cylinder testing",
            "P0302": "Cylinder 2 misfire comparison for plug, coil, and injector checks",
            "P0304": "Cylinder 4 misfire comparison when nearby cylinders show similar symptoms",
        },
        "P0420": {
            "P0430": "Bank 2 catalyst efficiency comparison when both converter banks need context",
            "P0171": "Fuel trim diagnosis before converter replacement",
            "P0300": "Misfire diagnosis because repeated misfires can damage the converter",
            "P0138": "Downstream O2 sensor behavior check before calling the converter failed",
        },
        "P0171": {
            "P0174": "Bank 2 lean comparison when both banks suggest shared air or fuel delivery problems",
            "P0101": "MAF plausibility diagnosis before replacing airflow parts",
            "P0113": "Intake temperature and MAF/IAT circuit context for airflow faults",
            "P0420": "Catalyst efficiency risk when lean fuel trim problems stay unresolved",
            "P0300": "Misfire diagnosis when lean running creates stumble under load",
        },
        "P0174": {
            "P0171": "Bank 1 comparison for one-bank versus both-bank lean diagnosis",
            "P0101": "MAF plausibility diagnosis when low airflow data may affect both banks",
            "P0113": "Intake temperature and MAF/IAT circuit context for shared airflow faults",
            "P0430": "Bank 2 catalyst efficiency risk when lean fuel trim problems stay unresolved",
            "P0300": "Misfire diagnosis when lean running creates stumble under load",
        },
        "P0101": {
            "P0171": "Bank 1 lean fuel-trim check before replacing the MAF",
            "P0174": "Bank 2 lean comparison when both banks suggest shared airflow or fuel delivery faults",
            "P0113": "IAT and MAF/IAT assembly context when intake temperature data affects airflow readings",
            "P0300": "Misfire diagnosis when hesitation under load may be lean misfire instead of ignition only",
            "P0102": "Low MAF signal comparison for restricted intake, contamination, or signal dropout",
        },
        "P0113": {
            "P0101": "MAF range and performance check when the IAT is built into the airflow sensor assembly",
            "P0171": "Bank 1 lean trim context when intake or MAF/IAT faults skew airflow data",
            "P0174": "Bank 2 lean trim context when shared airflow problems affect both banks",
            "P0300": "Misfire diagnosis when airflow or lean data creates stumble under load",
            "P0110": "IAT circuit comparison before replacing sensor or MAF/IAT assembly parts",
        },
        "P0562": {
            "P0563": "Charging voltage comparison when the system alternates between low and high voltage behavior",
            "P0620": "Generator control circuit diagnosis when alternator output or command testing is suspect",
        },
        "P0128": {
            "P0117": "Coolant temperature sensor low-signal check before blaming the thermostat",
            "P0118": "Coolant temperature sensor high-signal check when scan data looks false",
            "P0217": "Overheating workflow if temperature problems continue after cooling-system service",
        },
        "P0455": {
            "P0456": "Small-leak comparison when the large EVAP leak becomes intermittent",
            "P0442": "Small EVAP leak workflow when smoke testing finds a smaller seep",
            "P0446": "Vent valve and vent-path diagnosis for sealing or refueling issues",
            "P0441": "Purge valve flow diagnosis when the purge side may be sticking open",
            "P0449": "Vent valve control check when the vent circuit affects sealing",
        },
        "P0442": {
            "P0455": "Large-leak comparison when the EVAP system cannot seal during smoke testing",
            "P0456": "Very small leak comparison for cap, hose, canister, or valve seepage",
            "P0446": "Vent valve and vent-path diagnosis for sealing or refueling issues",
            "P0441": "Purge flow diagnosis when purge valve sealing may overlap the leak",
            "P0449": "Vent valve control check when the vent circuit affects sealing",
        },
        "P0456": {
            "P0442": "Small EVAP leak comparison for cap, hose, valve, and canister seepage",
            "P0455": "Large-leak comparison when the EVAP system cannot seal at all",
            "P0446": "Vent valve and vent-path diagnosis for sealing or refueling issues",
            "P0441": "Purge valve flow diagnosis when purge seepage may be present",
            "P0449": "Vent valve control check when the vent circuit affects sealing",
        },
    }

    if code in curated_related_map:
        for candidate_code in curated_related_map[code]:
            related_label = curated_related_labels.get(code, {}).get(candidate_code, "")
            add_candidate(candidate_code, related_label, bool(related_label))
        return related[:max_items]

    clusters = {
        "maf": [
            ("P0100", "Mass or Volume Air Flow Circuit Malfunction"),
            ("P0101", "Mass or Volume Air Flow Circuit Range/Performance"),
            ("P0102", "Mass or Volume Air Flow Circuit Low Input"),
            ("P0103", "Mass or Volume Air Flow Circuit High Input"),
        ],
        "iat": [
            ("P0110", "Intake Air Temperature Circuit Malfunction"),
            ("P0112", "Intake Air Temperature Circuit Low Input"),
            ("P0113", "Intake Air Temperature Circuit High Input"),
        ],
        "cooling": [
            ("P0115", "Engine Coolant Temperature Circuit Malfunction"),
            ("P0117", "Engine Coolant Temperature Circuit Low Input"),
            ("P0118", "Engine Coolant Temperature Circuit High Input"),
            ("P0128", "Coolant Thermostat Below Regulating Temp"),
        ],
        "misfire": [
            ("P0300", "Random/Multiple Cylinder Misfire"),
            ("P0301", "Cylinder 1 Misfire"),
            ("P0302", "Cylinder 2 Misfire"),
            ("P0303", "Cylinder 3 Misfire"),
            ("P0304", "Cylinder 4 Misfire"),
            ("P0305", "Cylinder 5 Misfire"),
            ("P0306", "Cylinder 6 Misfire"),
            ("P0307", "Cylinder 7 Misfire"),
            ("P0308", "Cylinder 8 Misfire"),
        ],
        "egr": [
            ("P0401", "Exhaust Gas Recirculation Flow Insufficient Detected"),
            ("P0402", "Exhaust Gas Recirculation Flow Excessive Detected"),
            ("P0403", "Exhaust Gas Recirculation Circuit Malfunction"),
        ],
        "fuel_trim": [
            ("P0171", "System Too Lean (Bank 1)"),
            ("P0174", "System Too Lean (Bank 2)"),
            ("P0172", "System Too Rich (Bank 1)"),
            ("P0175", "System Too Rich (Bank 2)"),
        ],
        "air_fuel": [
            ("P0171", "System Too Lean (Bank 1)"),
            ("P0172", "System Too Rich (Bank 1)"),
            ("P2195", "O2/A-F Sensor Signal Stuck Lean (B1S1)"),
            ("P2196", "O2/A-F Sensor Signal Stuck Rich (B1S1)"),
            ("P2197", "O2/A-F Sensor Signal Stuck Lean (B2S1)"),
            ("P2198", "O2/A-F Sensor Signal Stuck Rich (B2S1)"),
        ],
        "catalyst": [
            ("P0420", "Catalyst Efficiency Below Threshold (Bank 1)"),
            ("P0430", "Catalyst Efficiency Below Threshold (Bank 2)"),
            ("P0137", "O2 Sensor Circuit Low Voltage (Bank 1 Sensor 2)"),
            ("P0138", "O2 Sensor Circuit High Voltage (Bank 1 Sensor 2)"),
            ("P0140", "O2 Sensor Circuit No Activity (Bank 1 Sensor 2)"),
        ],
        "idle": [
            ("P0505", "Idle Air Control System Malfunction"),
            ("P0507", "Idle Control System RPM Higher Than Expected"),
        ],
        "transmission": [
            ("P0700", "Transmission Control System Malfunction"),
            ("P0715", "Input/Turbine Speed Sensor Circuit Malfunction"),
            ("P0720", "Output Speed Sensor Circuit Malfunction"),
            ("P0740", "Torque Converter Clutch Circuit Malfunction"),
            ("P0741", "TCC Performance/Stuck Off"),
        ],
        "evap": [
            ("P0440", "EVAP System Malfunction"),
            ("P0442", "EVAP Small Leak Detected"),
            ("P0446", "EVAP Vent Control Circuit"),
            ("P0455", "EVAP Large Leak Detected"),
            ("P0456", "EVAP Very Small Leak Detected"),
        ],
    }

    for group in clusters.values():
        codes = [item_code for item_code, _ in group]
        if code not in codes:
            continue
        for item_code, item_label in group:
            if len(related) >= max_items:
                break
            if item_code == code:
                continue
            add_candidate(item_code, item_label)
        break

    if len(related) < 3:
        for fallback_code in build_fallback_related_code_candidates(code, available_titles):
            if len(related) >= max_items:
                break
            add_candidate(fallback_code)

    return related[:max_items]

def build_common_repairs(code: str):
    code = code.upper().strip()

    repair_map = {
        "P0300": [
            {"label": "Spark plug replacement", "service_query": "spark plug replacement"},
            {"label": "Ignition coil replacement", "service_query": "ignition coil replacement"},
            {"label": "Fuel injector replacement", "service_query": "fuel injector replacement"},
            {"label": "Vacuum leak smoke test", "service_query": "vacuum leak diagnosis"},
            {"label": "Fuel system diagnostic", "service_query": "fuel system diagnostic"},
            {"label": "Compression and leak-down testing", "service_query": "engine diagnostic"},
            {"label": "Mass air flow sensor replacement", "service_query": "mass air flow sensor"},
        ],
        "P0301": [
            {"label": "Spark plug replacement", "service_query": "spark plug replacement"},
            {"label": "Ignition coil replacement", "service_query": "ignition coil replacement"},
            {"label": "Fuel injector replacement", "service_query": "fuel injector replacement"},
            {"label": "Vacuum leak smoke test", "service_query": "vacuum leak diagnosis"},
        ],
        "P0302": [
            {"label": "Spark plug replacement", "service_query": "spark plug replacement"},
            {"label": "Ignition coil replacement", "service_query": "ignition coil replacement"},
            {"label": "Fuel injector replacement", "service_query": "fuel injector replacement"},
            {"label": "Vacuum leak smoke test", "service_query": "vacuum leak diagnosis"},
        ],
        "P0303": [
            {"label": "Spark plug replacement", "service_query": "spark plug replacement"},
            {"label": "Ignition coil replacement", "service_query": "ignition coil replacement"},
            {"label": "Fuel injector replacement", "service_query": "fuel injector replacement"},
            {"label": "Vacuum leak smoke test", "service_query": "vacuum leak diagnosis"},
        ],
        "P0304": [
            {"label": "Spark plug replacement", "service_query": "spark plug replacement"},
            {"label": "Ignition coil replacement", "service_query": "ignition coil replacement"},
            {"label": "Fuel injector replacement", "service_query": "fuel injector replacement"},
            {"label": "Vacuum leak smoke test", "service_query": "vacuum leak diagnosis"},
        ],
        "P0171": [
            {"label": "Vacuum leak smoke test", "service_query": "vacuum leak diagnosis"},
            {"label": "Mass air flow sensor replacement", "service_query": "mass air flow sensor"},
            {"label": "Fuel system diagnostic", "service_query": "fuel system diagnostic"},
            {"label": "PCV system service", "service_query": "pcv system"},
        ],
        "P0174": [
            {"label": "Vacuum leak smoke test", "service_query": "vacuum leak diagnosis"},
            {"label": "Mass air flow sensor replacement", "service_query": "mass air flow sensor"},
            {"label": "Fuel system diagnostic", "service_query": "fuel system diagnostic"},
            {"label": "PCV system service", "service_query": "pcv system"},
        ],
        "P0101": [
            {"label": "Mass air flow sensor replacement", "service_query": "mass air flow sensor"},
            {"label": "Vacuum leak smoke test", "service_query": "vacuum leak diagnosis"},
            {"label": "Intake leak diagnosis", "service_query": "intake leak diagnosis"},
            {"label": "Fuel system diagnostic", "service_query": "fuel system diagnostic"},
            {"label": "Air filter and intake inspection", "service_query": "air filter replacement"},
            {"label": "Throttle body cleaning", "service_query": "throttle body cleaning"},
        ],
        "P0138": [
            {"label": "Downstream oxygen sensor replacement", "service_query": "oxygen sensor replacement downstream"},
            {"label": "Fuel system diagnostic", "service_query": "fuel system diagnostic"},
            {"label": "Exhaust wiring inspection", "service_query": "electrical diagnostic"},
        ],
        "P0113": [
            {"label": "Intake air temperature sensor replacement", "service_query": "intake air temperature sensor replacement"},
            {"label": "Intake air temperature circuit diagnosis", "service_query": "electrical diagnostic"},
            {"label": "Mass air flow and intake sensor assembly inspection", "service_query": "mass air flow sensor"},
            {"label": "Vacuum leak smoke test", "service_query": "vacuum leak diagnosis"},
            {"label": "Air filter and intake inspection", "service_query": "air filter replacement"},
            {"label": "Fuel system diagnostic", "service_query": "fuel system diagnostic"},
        ],
        "P0118": [
            {"label": "Coolant temperature sensor replacement", "service_query": "coolant temperature sensor replacement"},
            {"label": "Coolant temperature circuit diagnosis", "service_query": "electrical diagnostic"},
            {"label": "Cooling system inspection", "service_query": "cooling system diagnostic"},
        ],
        "P0141": [
            {"label": "Downstream oxygen sensor replacement", "service_query": "oxygen sensor replacement downstream"},
            {"label": "Oxygen sensor heater circuit diagnosis", "service_query": "electrical diagnostic"},
            {"label": "Fuse and heater power inspection", "service_query": "electrical diagnostic"},
        ],
        "P0158": [
            {"label": "Downstream oxygen sensor replacement", "service_query": "oxygen sensor replacement downstream"},
            {"label": "Fuel system diagnostic", "service_query": "fuel system diagnostic"},
            {"label": "Oxygen sensor circuit inspection", "service_query": "electrical diagnostic"},
        ],
        "P0128": [
            {"label": "Thermostat replacement", "service_query": "thermostat replacement"},
            {"label": "Coolant temperature sensor replacement", "service_query": "coolant temperature sensor replacement"},
            {"label": "Thermostat housing replacement", "service_query": "thermostat housing replacement"},
            {"label": "Water pump replacement", "service_query": "water pump replacement"},
            {"label": "Cooling system diagnostic", "service_query": "cooling system diagnostic"},
        ],
        "P0401": [
            {"label": "EGR diagnosis", "service_query": "egr diagnosis"},
            {"label": "EGR valve replacement", "service_query": "egr valve replacement"},
            {"label": "Vacuum leak smoke test", "service_query": "vacuum leak diagnosis"},
        ],
        "P0403": [
            {"label": "EGR valve replacement", "service_query": "egr valve replacement"},
        ],
        "P0404": [
            {"label": "EGR valve replacement", "service_query": "egr valve replacement"},
        ],
        "P0405": [
            {"label": "EGR valve replacement", "service_query": "egr valve replacement"},
        ],
        "P0406": [
            {"label": "EGR valve replacement", "service_query": "egr valve replacement"},
        ],
        "P0116": [
            {"label": "Engine coolant temperature sensor replacement", "service_query": "engine coolant temperature sensor replacement"},
        ],
        "P0117": [
            {"label": "Engine coolant temperature sensor replacement", "service_query": "engine coolant temperature sensor replacement"},
        ],
        "P0118": [
            {"label": "Engine coolant temperature sensor replacement", "service_query": "engine coolant temperature sensor replacement"},
        ],
        "P0119": [
            {"label": "Engine coolant temperature sensor replacement", "service_query": "engine coolant temperature sensor replacement"},
        ],
        "P0125": [
            {"label": "Engine coolant temperature sensor replacement", "service_query": "engine coolant temperature sensor replacement"},
        ],
        "P0420": [
            {"label": "Catalyst efficiency diagnosis", "service_query": "catalyst efficiency diagnosis"},
            {"label": "Exhaust leak repair", "service_query": "exhaust leak repair"},
            {"label": "Downstream oxygen sensor replacement", "service_query": "oxygen sensor replacement downstream"},
            {"label": "Catalytic converter replacement", "service_query": "catalytic converter replacement"},
        ],
        "P0430": [
            {"label": "Catalyst efficiency diagnosis", "service_query": "catalyst efficiency diagnosis"},
            {"label": "Exhaust leak repair", "service_query": "exhaust leak repair"},
            {"label": "Downstream oxygen sensor replacement", "service_query": "oxygen sensor replacement downstream"},
            {"label": "Catalytic converter replacement", "service_query": "catalytic converter replacement"},
        ],
        "P0505": [
            {"label": "Throttle body replacement", "service_query": "throttle body replacement"},
        ],
        "P0506": [
            {"label": "Throttle body replacement", "service_query": "throttle body replacement"},
        ],
        "P0507": [
            {"label": "Throttle body cleaning", "service_query": "throttle body cleaning"},
            {"label": "Throttle body service", "service_query": "throttle body service"},
            {"label": "Vacuum leak smoke test", "service_query": "vacuum leak diagnosis"},
            {"label": "Throttle body replacement", "service_query": "throttle body replacement"},
        ],
        "P0562": [
            {"label": "Battery replacement", "service_query": "battery replacement"},
            {"label": "Alternator replacement", "service_query": "alternator replacement"},
            {"label": "Charging system diagnostic", "service_query": "electrical diagnostic"},
            {"label": "Serpentine belt replacement", "service_query": "serpentine belt replacement"},
            {"label": "Starter system diagnostic", "service_query": "starter diagnostic"},
        ],
        "P0563": [
            {"label": "Alternator replacement", "service_query": "alternator replacement"},
            {"label": "Battery replacement", "service_query": "battery replacement"},
            {"label": "Charging system diagnostic", "service_query": "electrical diagnostic"},
        ],
        "P0351": [
            {"label": "Ignition coil replacement", "service_query": "ignition coil replacement"},
            {"label": "Ignition wiring diagnosis", "service_query": "electrical diagnostic"},
            {"label": "Spark plug replacement", "service_query": "spark plug replacement"},
        ],
        "P0700": [
            {"label": "Transmission diagnostic", "service_query": "transmission diagnostic"},
            {"label": "Transmission fluid service", "service_query": "transmission fluid service"},
            {"label": "Solenoid pack replacement", "service_query": "solenoid pack replacement"},
            {"label": "Transmission replacement", "service_query": "transmission replacement"},
        ],
        "P0741": [
            {"label": "Transmission diagnostic", "service_query": "transmission diagnostic"},
            {"label": "Transmission fluid service", "service_query": "transmission fluid service"},
            {"label": "Torque converter replacement", "service_query": "torque converter replacement"},
            {"label": "Solenoid pack replacement", "service_query": "solenoid pack replacement"},
        ],
        "P0442": [
            {"label": "Gas cap replacement", "service_query": "gas cap replacement"},
            {"label": "EVAP leak smoke test", "service_query": "evap leak smoke test"},
            {"label": "EVAP small leak diagnosis", "service_query": "evap small leak diagnosis"},
            {"label": "EVAP purge valve replacement", "service_query": "evap purge valve replacement"},
        ],
        "P0446": [
            {"label": "EVAP vent valve replacement", "service_query": "evap vent valve replacement"},
        ],
        "P0449": [
            {"label": "EVAP vent valve replacement", "service_query": "evap vent valve replacement"},
        ],
        "P0455": [
            {"label": "Gas cap replacement", "service_query": "gas cap replacement"},
            {"label": "EVAP system diagnosis", "service_query": "evap system diagnosis"},
            {"label": "EVAP leak smoke test", "service_query": "evap leak smoke test"},
            {"label": "EVAP purge valve replacement", "service_query": "evap purge valve replacement"},
            {"label": "EVAP vent valve replacement", "service_query": "evap vent valve replacement"},
            {"label": "Charcoal canister and tank-area inspection", "service_query": "evap system diagnosis"},
        ],
        "P0456": [
            {"label": "Gas cap replacement", "service_query": "gas cap replacement"},
            {"label": "EVAP leak smoke test", "service_query": "evap leak smoke test"},
            {"label": "EVAP small leak diagnosis", "service_query": "evap small leak diagnosis"},
            {"label": "EVAP purge valve replacement", "service_query": "evap purge valve replacement"},
            {"label": "EVAP vent valve replacement", "service_query": "evap vent valve replacement"},
            {"label": "Charcoal canister and tank-area inspection", "service_query": "evap system diagnosis"},
        ],
        "P2195": [
            {"label": "Air fuel ratio sensor replacement", "service_query": "air fuel ratio sensor replacement"},
            {"label": "Upstream oxygen sensor replacement", "service_query": "oxygen sensor replacement upstream"},
            {"label": "Vacuum leak smoke test", "service_query": "vacuum leak diagnosis"},
            {"label": "Fuel system diagnostic", "service_query": "fuel system diagnostic"},
        ],
    }

    repair_guidance_map = {
        "P0300": {
            "Spark plug replacement": "Price this after plug fouling, worn electrodes, gap problems, carbon tracking, or plug-swap patterns are confirmed across the affected cylinders.",
            "Ignition coil replacement": "Use this path when misfires get worse under load, coil output is weak, boots show tracking, or a cylinder-specific misfire follows a coil swap.",
            "Fuel injector replacement": "Price this when injector balance, contribution, leakdown, or same-cylinder behavior separates fuel delivery from ignition failure.",
            "Vacuum leak smoke test": "Useful when positive fuel trims, idle quality, or multiple-cylinder misfires point to unmetered air causing lean misfire behavior.",
            "Fuel system diagnostic": "Move here when misfires are load-related or fuel pressure drops under acceleration, separating weak delivery from ignition breakdown.",
            "Compression and leak-down testing": "Use this when misfires repeat after ignition repairs, cold-start coolant seep is suspected, or mechanical sealing needs to be separated from spark and fuel faults.",
            "Mass air flow sensor replacement": "Price only after airflow data, fuel trims, contamination, or P0171-style lean evidence points to the MAF as the shared trigger.",
        },
        "P0301": {
            "Spark plug replacement": "A strong estimate path when the cylinder 1 plug is worn, fouled, oil-soaked, or the misfire follows the plug.",
            "Ignition coil replacement": "Use this after the cylinder 1 misfire follows the coil or coil output testing confirms the fault.",
            "Fuel injector replacement": "Price this when the misfire stays on cylinder 1 after ignition checks and injector testing confirms a fuel fault.",
            "Vacuum leak smoke test": "Useful when cylinder 1 is near an intake leak or fuel trims point to unmetered air.",
        },
        "P0302": {
            "Spark plug replacement": "A strong estimate path when the cylinder 2 plug is worn, fouled, oil-soaked, or the misfire follows the plug.",
            "Ignition coil replacement": "Use this after the cylinder 2 misfire follows the coil or coil output testing confirms the fault.",
            "Fuel injector replacement": "Price this when the misfire stays on cylinder 2 after ignition checks and injector testing confirms a fuel fault.",
            "Vacuum leak smoke test": "Useful when cylinder 2 is near an intake leak or fuel trims point to unmetered air.",
        },
        "P0303": {
            "Spark plug replacement": "A strong estimate path when the cylinder 3 plug is fouled, oil-soaked, incorrectly gapped, carbon tracked, or the cold-start misfire follows the plug.",
            "Ignition coil replacement": "Use this when the cylinder 3 misfire follows the coil after a swap, gets worse under load, or boot tracking and coil-output checks confirm ignition breakdown.",
            "Fuel injector replacement": "Price this when the misfire stays on cylinder 3 after coil and plug swaps, and injector balance or contribution testing separates fuel delivery from ignition.",
            "Vacuum leak smoke test": "Useful when cylinder 3 is near an intake leak or fuel trims point to unmetered air.",
        },
        "P0304": {
            "Spark plug replacement": "A strong estimate path when the cylinder 4 plug is worn, fouled, oil-soaked, or the misfire follows the plug.",
            "Ignition coil replacement": "Use this after the cylinder 4 misfire follows the coil or coil output testing confirms the fault.",
            "Fuel injector replacement": "Price this when the misfire stays on cylinder 4 after ignition checks and injector testing confirms a fuel fault.",
            "Vacuum leak smoke test": "Useful when cylinder 4 is near an intake leak or fuel trims point to unmetered air.",
        },
        "P0171": {
            "Vacuum leak smoke test": "Start here when bank 1 trims are leaner at idle, smoke testing may reveal intake manifold, PCV, hose, or post-MAF air leaks before parts are replaced.",
            "Mass air flow sensor replacement": "Price this only after unmetered air is ruled out and MAF grams-per-second, unplug behavior, contamination, or P0101-style plausibility checks confirm the sensor.",
            "Fuel system diagnostic": "Move here when trims get worse at higher RPM, acceleration, or load and fuel pressure or volume drop points toward weak fuel delivery.",
            "PCV system service": "Use this path when PCV plumbing or crankcase ventilation is pulling in unmetered air and affecting idle trims more than load trims.",
        },
        "P0174": {
            "Vacuum leak smoke test": "Start here when bank 2 trims are leaner at idle, smoke testing may reveal intake manifold, PCV, hose, or post-MAF air leaks before parts are replaced.",
            "Mass air flow sensor replacement": "Price this only after one-bank versus both-bank trims and unmetered air checks are reviewed, then MAF g/s, unplug behavior, contamination, or P0101-style data confirms the sensor.",
            "Fuel system diagnostic": "Move here when trims worsen at higher RPM, acceleration, or load and fuel pressure or volume drop points toward weak fuel delivery.",
            "PCV system service": "Use this path when PCV routing or crankcase ventilation is pulling in unmetered air and affecting idle trims more than load trims.",
        },
        "P0101": {
            "Mass air flow sensor replacement": "Price this only after smoke testing and fuel-trim review rule out unmetered air, PCV leaks, intake restriction, and weak fuel delivery.",
            "Vacuum leak smoke test": "Start here when positive trims are stronger at idle or P0171/P0174 suggest air entering after the MAF.",
            "Intake leak diagnosis": "Use this when cracked boots, loose clamps, PCV leaks, or aftermarket intake fitment can create false lean airflow data.",
            "Fuel system diagnostic": "Move here when trims worsen under acceleration or highway load and weak fuel delivery can mimic a MAF fault.",
            "Air filter and intake inspection": "Use this when a dirty filter, intake restriction, oiled filter, or aftermarket intake may contaminate or skew MAF readings.",
            "Throttle body cleaning": "Use this only when throttle data and carbon buildup support it, not as a substitute for fuel-trim, leak, or pressure checks.",
        },
        "P0113": {
            "Intake air temperature sensor replacement": "Price this only after scan data, connector checks, and voltage testing prove a false IAT signal instead of a shared MAF/IAT or intake issue.",
            "Intake air temperature circuit diagnosis": "Start here when the IAT reading is stuck cold or high-input behavior points to an open circuit, poor terminal fit, or wiring fault.",
            "Mass air flow and intake sensor assembly inspection": "Use this when the IAT is integrated with the MAF and P0101, P0171, or P0174 data suggests a shared airflow problem.",
            "Vacuum leak smoke test": "Use this when lean trims or idle symptoms suggest unmetered air after the MAF before replacing intake temperature parts.",
            "Air filter and intake inspection": "Use this when a dirty filter, intake restriction, oiled aftermarket filter, or intake tube issue may skew MAF/IAT readings.",
            "Fuel system diagnostic": "Move here when trims worsen under load and weak fuel delivery may be mimicking airflow or temperature-sensor symptoms.",
        },
        "P0128": {
            "Thermostat replacement": "The strongest estimate path when slow warm-up, repeat P0128 after clearing, and live temperature data confirm a thermostat stuck open instead of a false reading.",
            "Coolant temperature sensor replacement": "Use this only when scan data, ECT plausibility checks, connector condition, or resistance testing shows false low-temperature reporting.",
            "Thermostat housing replacement": "Price this when the thermostat is integrated into the housing, the housing seal is leaking, or trapped air and sealing concerns repeat after service.",
            "Water pump replacement": "Move here when poor coolant circulation, weak heater output, pump noise, or flow testing points to coolant movement instead of thermostat control.",
            "Cooling system diagnostic": "Start here when overheating, trapped air, low coolant, weak cabin heat at idle, or repeated temperature fluctuation continues after thermostat work.",
        },
        "P0446": {
            "EVAP vent valve replacement": "A direct estimate path when command testing, restriction checks, or contamination points to the vent valve or vent assembly.",
        },
        "P0507": {
            "Throttle body cleaning": "Start here when carbon buildup or a sticking throttle plate is visible and the throttle body still responds correctly.",
            "Throttle body service": "Use this when cleaning, inspection, and relearn are needed before replacement is justified.",
            "Vacuum leak smoke test": "Move here when idle stays high after throttle inspection or fuel trims suggest extra air.",
            "Throttle body replacement": "Price this when throttle data, sticking, or actuator testing confirms the assembly is the fault.",
        },
        "P0420": {
            "Catalyst efficiency diagnosis": "Start here when P0420 returns after clearing so front-versus-rear O2 behavior, catalyst monitor data, and upstream engine faults are checked before pricing a converter.",
            "Exhaust leak repair": "Use this path when leaks ahead of the bank 1 converter or near the sensor can distort oxygen readings and mimic weak catalyst efficiency.",
            "Downstream oxygen sensor replacement": "Price this only when rear O2 testing shows biased, slow, or inaccurate behavior rather than simply replacing sensors because P0420 is present.",
            "Catalytic converter replacement": "Move here when repeated P0420, downstream O2 mirroring upstream O2, sulfur smell, overheating, or restriction points to true converter failure after misfire and fuel-trim causes are corrected.",
        },
        "P0562": {
            "Battery replacement": "Price this when load or conductance testing proves the battery cannot hold charge after charging output, parasitic draw, and cable voltage drop are separated.",
            "Alternator replacement": "Use this path only after charging-system testing confirms low alternator output under electrical load, battery warning light behavior, or alternator control failure.",
            "Charging system diagnostic": "Start here when repeated dead batteries, dim lights, or P0562 could be caused by alternator output, weak battery, poor grounds, corroded terminals, or cable voltage drop.",
            "Serpentine belt replacement": "Use this path when belt slip, pulley noise, weak tensioner action, or charging fluctuation at idle points to accessory-drive loss before major electrical parts are replaced.",
            "Starter system diagnostic": "Move here when the complaint is intermittent no-start or no-crank and voltage drop during start attempts must separate battery, starter, alternator, and ground faults.",
        },
        "P0455": {
            "Gas cap replacement": "Use this only after inspecting cap fit, seal condition, and filler-neck surface; if P0455 returns after a cap, the leak diagnosis should continue.",
            "EVAP system diagnosis": "Start here when the EVAP system will not seal and purge, vent, hose, canister, filler-neck, or tank-area faults all remain possible.",
            "EVAP leak smoke test": "Use smoke testing to confirm the open point before replacing multiple EVAP parts, especially when visual checks do not show a disconnected hose.",
            "EVAP purge valve replacement": "Price this when command or bench testing shows the purge valve is sticking open and pulling the EVAP system out of seal.",
            "EVAP vent valve replacement": "Use this when vent valve sealing or vent-path restriction prevents the system from closing, causes readiness failures, or creates hard refueling.",
            "Charcoal canister and tank-area inspection": "Move here when rear fuel smell, strong odor after fill-up, canister cracking, filler-neck corrosion, or tank-side hose leaks are suspected.",
        },
        "P0456": {
            "Gas cap replacement": "Use this only after inspecting cap seal, cap fit, and filler-neck surface; repeat P0456 after cap replacement needs leak testing rather than another cap.",
            "EVAP leak smoke test": "Use careful smoke testing to confirm the small leak location before replacing purge valves, vent valves, hoses, or canister parts by guesswork.",
            "EVAP small leak diagnosis": "Start here when the code repeats without drivability symptoms and the leak may be a tiny hose, fitting, valve, canister, or filler-neck seep.",
            "EVAP purge valve replacement": "Price this when purge solenoid seepage or a purge valve that will not fully close is confirmed during sealing tests.",
            "EVAP vent valve replacement": "Use this when vent valve sealing failure, vent restriction, readiness issues, or refueling difficulty points to the vent side.",
            "Charcoal canister and tank-area inspection": "Move here when smoke appears near the tank, rear fuel smell is present, or canister, filler-neck, hose, tank seal, and fuel-pump seal checks are needed.",
        },
    }

    repairs = [dict(item) for item in repair_map.get(code, [])]
    guidance = repair_guidance_map.get(code, {})
    for item in repairs:
        description = guidance.get(item.get("label", ""))
        if description:
            item["description"] = description

    return repairs

def build_cost_guide_links(code: str):
    code = code.upper().strip()
    live_cost_guides = {
        item["href"]: item
        for item in build_repair_cost_guide_cards()
        if item.get("href")
    }
    supplemental_live_cost_guides = {
        "/cost/camshaft-position-sensor-replacement": {
            "title": "Camshaft Position Sensor Replacement Cost",
            "href": "/cost/camshaft-position-sensor-replacement",
        },
        "/cost/evap-purge-valve-replacement": {
            "title": "EVAP Purge Valve Replacement Cost",
            "href": "/cost/evap-purge-valve-replacement",
        },
        "/cost/throttle-body-replacement": {
            "title": "Throttle Body Replacement Cost",
            "href": "/cost/throttle-body-replacement",
        },
    }
    live_cost_guides.update(supplemental_live_cost_guides)

    results: List[Dict[str, str]] = []
    seen_hrefs: set[str] = set()

    def add_guide(label: str, href: str, description: str) -> None:
        if not href or href in seen_hrefs or href not in live_cost_guides:
            return
        seen_hrefs.add(href)
        results.append(
            {
                "label": label,
                "href": href,
                "description": description,
            }
        )

    cost_guide_map = {
        "P0300": [
            {
                "label": "Spark Plug Replacement Cost",
                "href": "/cost/spark-plug-replacement",
                "description": "Useful when plug fouling, incorrect gap, carbon tracking, or plug-swap testing confirms the misfire source.",
            },
            {
                "label": "Ignition Coil Replacement Cost",
                "href": "/cost/ignition-coil-replacement",
                "description": "A strong next cost check when load-related misfire, boot tracking, weak coil output, or coil-swap behavior points to ignition breakdown.",
            },
            {
                "label": "Fuel Injector Replacement Cost",
                "href": "/cost/fuel-injector-replacement",
                "description": "Relevant when injector balance, leakdown, command, or cylinder contribution testing separates a fuel fault from ignition failure.",
            },
            {
                "label": "Fuel Pump Replacement Cost",
                "href": "/cost/fuel-pump-replacement",
                "description": "Relevant when fuel pressure drops under load and lean misfire behavior points to weak delivery instead of coils or plugs.",
            },
            {
                "label": "Catalytic Converter Replacement Cost",
                "href": "/cost/catalytic-converter-replacement",
                "description": "Use only after active misfires and fuel-trim faults are corrected, since unresolved P0300 can damage the converter and trigger P0420.",
            },
        ],
        "P0301": [
            {
                "label": "Spark Plug Replacement Cost",
                "href": "/cost/spark-plug-replacement",
                "description": "A strong next cost check when the cylinder-specific misfire tracks back to the plug.",
            },
            {
                "label": "Ignition Coil Replacement Cost",
                "href": "/cost/ignition-coil-replacement",
                "description": "Useful when the cylinder-specific misfire follows the coil or a coil output problem is confirmed.",
            },
            {
                "label": "Fuel Injector Replacement Cost",
                "href": "/cost/fuel-injector-replacement",
                "description": "Relevant when the cylinder-specific misfire stays fixed after ignition checks and injector testing confirms the fault.",
            },
        ],
        "P0302": [
            {
                "label": "Spark Plug Replacement Cost",
                "href": "/cost/spark-plug-replacement",
                "description": "A strong next cost check when the cylinder-specific misfire tracks back to the plug.",
            },
            {
                "label": "Ignition Coil Replacement Cost",
                "href": "/cost/ignition-coil-replacement",
                "description": "Useful when the cylinder-specific misfire follows the coil or a coil output problem is confirmed.",
            },
            {
                "label": "Fuel Injector Replacement Cost",
                "href": "/cost/fuel-injector-replacement",
                "description": "Relevant when the cylinder-specific misfire stays fixed after ignition checks and injector testing confirms the fault.",
            },
        ],
        "P0303": [
            {
                "label": "Spark Plug Replacement Cost",
                "href": "/cost/spark-plug-replacement",
                "description": "A strong next cost check when cylinder 3 shows plug fouling, gap problems, carbon tracking, or cold-start misfire clues that follow the plug.",
            },
            {
                "label": "Ignition Coil Replacement Cost",
                "href": "/cost/ignition-coil-replacement",
                "description": "Useful when the cylinder 3 misfire follows the coil after a swap, worsens under load, or coil boot tracking points to ignition breakdown.",
            },
            {
                "label": "Fuel Injector Replacement Cost",
                "href": "/cost/fuel-injector-replacement",
                "description": "Relevant when the misfire stays on cylinder 3 after coil and plug swaps, and injector balance testing points to fuel delivery instead of ignition.",
            },
        ],
        "P0304": [
            {
                "label": "Spark Plug Replacement Cost",
                "href": "/cost/spark-plug-replacement",
                "description": "A strong next cost check when the cylinder-specific misfire tracks back to the plug.",
            },
            {
                "label": "Ignition Coil Replacement Cost",
                "href": "/cost/ignition-coil-replacement",
                "description": "Useful when the cylinder-specific misfire follows the coil or a coil output problem is confirmed.",
            },
            {
                "label": "Fuel Injector Replacement Cost",
                "href": "/cost/fuel-injector-replacement",
                "description": "Relevant when the cylinder-specific misfire stays fixed after ignition checks and injector testing confirms the fault.",
            },
        ],
        "P0101": [
            {
                "label": "Mass Air Flow Sensor Replacement Cost",
                "href": "/cost/mass-air-flow-sensor-replacement",
                "description": "A strong next cost check after smoke testing, fuel trims, and intake inspection point back to MAF contamination or biased airflow data.",
            },
            {
                "label": "Fuel Pump Replacement Cost",
                "href": "/cost/fuel-pump-replacement",
                "description": "Relevant when trims worsen under load and fuel pressure testing shows weak delivery is mimicking an airflow fault.",
            },
        ],
        "P0113": [
            {
                "label": "Mass Air Flow Sensor Replacement Cost",
                "href": "/cost/mass-air-flow-sensor-replacement",
                "description": "Relevant when the IAT is integrated with the MAF and circuit checks confirm the sensor assembly is skewing airflow data.",
            },
            {
                "label": "Fuel Pump Replacement Cost",
                "href": "/cost/fuel-pump-replacement",
                "description": "Relevant when lean trims under load show weak fuel delivery is imitating airflow or intake-temperature symptoms.",
            },
        ],
        "P0138": [
            {
                "label": "Oxygen Sensor Replacement Cost",
                "href": "/cost/oxygen-sensor-replacement",
                "description": "A strong fit when testing confirms the downstream oxygen sensor is biased high or no longer reporting accurately.",
            },
        ],
        "P0401": [
            {
                "label": "EGR Valve Replacement Cost",
                "href": "/cost/egr-valve-replacement",
                "description": "A conservative next cost check when EGR flow stays low because the valve is sticking, restricted, or no longer responding correctly.",
            },
        ],
        "P0403": [
            {
                "label": "EGR Valve Replacement Cost",
                "href": "/cost/egr-valve-replacement",
                "description": "Relevant when EGR circuit testing points to a failed valve actuator or solenoid after wiring and power checks.",
            },
        ],
        "P0404": [
            {
                "label": "EGR Valve Replacement Cost",
                "href": "/cost/egr-valve-replacement",
                "description": "A strong next cost check when EGR range or position testing confirms the valve is sticking or not tracking command correctly.",
            },
        ],
        "P0405": [
            {
                "label": "EGR Valve Replacement Cost",
                "href": "/cost/egr-valve-replacement",
                "description": "Useful when the EGR position signal fault traces back to the valve assembly or its integrated feedback sensor.",
            },
        ],
        "P0406": [
            {
                "label": "EGR Valve Replacement Cost",
                "href": "/cost/egr-valve-replacement",
                "description": "Useful when the EGR position signal fault traces back to the valve assembly or its integrated feedback sensor.",
            },
        ],
        "P0351": [
            {
                "label": "Ignition Coil Replacement Cost",
                "href": "/cost/ignition-coil-replacement",
                "description": "The strongest cost guide when the coil A circuit fault points to a failed coil after wiring checks.",
            },
        ],
        "P0441": [
            {
                "label": "EVAP Purge Valve Replacement Cost",
                "href": "/cost/evap-purge-valve-replacement",
                "description": "A strong next cost check when EVAP flow faults point to a purge valve that is sticking or not sealing correctly.",
            },
        ],
        "P0442": [
            {
                "label": "EVAP Purge Valve Replacement Cost",
                "href": "/cost/evap-purge-valve-replacement",
                "description": "Useful when smoke testing or EVAP diagnosis points to purge-valve leakage as part of the small-leak fault.",
            },
        ],
        "P0440": [
            {
                "label": "EVAP Purge Valve Replacement Cost",
                "href": "/cost/evap-purge-valve-replacement",
                "description": "A conservative next cost check when general EVAP diagnosis points to a purge valve that is leaking or not controlling flow correctly.",
            },
        ],
        "P0446": [
            {
                "label": "EVAP Vent Valve Replacement Cost",
                "href": "/cost/evap-vent-valve-replacement",
                "description": "A direct cost guide when EVAP vent testing points to a stuck, restricted, or failed vent valve.",
            },
        ],
        "P0449": [
            {
                "label": "EVAP Vent Valve Replacement Cost",
                "href": "/cost/evap-vent-valve-replacement",
                "description": "Useful when the EVAP vent solenoid or valve control fault traces back to the vent valve assembly.",
            },
        ],
        "P0455": [
            {
                "label": "EVAP Purge Valve Replacement Cost",
                "href": "/cost/evap-purge-valve-replacement",
                "description": "Relevant when command or sealing tests confirm the purge valve is stuck open, causing repeat EVAP codes after gas cap checks.",
            },
            {
                "label": "EVAP Vent Valve Replacement Cost",
                "href": "/cost/evap-vent-valve-replacement",
                "description": "Useful when vent valve sealing failure, vent restriction, hard refueling, or EVAP readiness problems point to the vent side.",
            },
        ],
        "P0456": [
            {
                "label": "EVAP Purge Valve Replacement Cost",
                "href": "/cost/evap-purge-valve-replacement",
                "description": "Useful when small-leak smoke testing traces the fault to purge-valve seepage or a valve that will not fully close.",
            },
            {
                "label": "EVAP Vent Valve Replacement Cost",
                "href": "/cost/evap-vent-valve-replacement",
                "description": "Relevant when vent valve seepage, vent restriction, refueling difficulty, or readiness failures keep the EVAP monitor from passing.",
            },
        ],
        "P0505": [
            {
                "label": "Throttle Body Replacement Cost",
                "href": "/cost/throttle-body-replacement",
                "description": "A strong next cost check when idle control faults point to a sticking, worn, or failing throttle body.",
            },
        ],
        "P0506": [
            {
                "label": "Throttle Body Replacement Cost",
                "href": "/cost/throttle-body-replacement",
                "description": "Useful when low-idle diagnosis points to carbon buildup or a throttle body that is not controlling airflow correctly.",
            },
        ],
        "P0507": [
            {
                "label": "Throttle Body Replacement Cost",
                "href": "/cost/throttle-body-replacement",
                "description": "A strong next cost check when idle speed stays high because the throttle body is sticking, worn, or failing electronically.",
            },
        ],
        "P0340": [
            {
                "label": "Camshaft Position Sensor Replacement Cost",
                "href": "/cost/camshaft-position-sensor-replacement",
                "description": "A direct cost guide when testing confirms the camshaft position sensor or its signal is the fault.",
            },
        ],
        "P0341": [
            {
                "label": "Camshaft Position Sensor Replacement Cost",
                "href": "/cost/camshaft-position-sensor-replacement",
                "description": "Useful when timing-signal diagnostics point to a weak or erratic camshaft position sensor.",
            },
        ],
        "P0562": [
            {
                "label": "Battery Replacement Cost",
                "href": "/cost/battery-replacement",
                "description": "Useful when load testing proves the battery is weak after overnight drain, charging output, and cable voltage-drop issues are separated.",
            },
            {
                "label": "Alternator Replacement Cost",
                "href": "/cost/alternator-replacement",
                "description": "A strong next cost check when charging-system testing confirms low alternator output under load, battery warning light behavior, or alternator control failure.",
            },
            {
                "label": "Serpentine Belt Replacement Cost",
                "href": "/cost/serpentine-belt-replacement",
                "description": "Relevant when belt slip, pulley noise, weak tensioner action, or idle charging fluctuation reduces alternator output.",
            },
            {
                "label": "Starter Replacement Cost",
                "href": "/cost/starter-replacement",
                "description": "Useful when no-start complaints require battery-versus-starter-versus-alternator separation and voltage drop during crank testing.",
            },
        ],
        "P0563": [
            {
                "label": "Alternator Replacement Cost",
                "href": "/cost/alternator-replacement",
                "description": "The strongest cost guide when overcharging points back to an alternator or regulator fault.",
            },
            {
                "label": "Battery Replacement Cost",
                "href": "/cost/battery-replacement",
                "description": "Useful when the battery has been damaged by sustained overcharging or fails testing after the charging fault is found.",
            },
        ],
        "P0171": [
            {
                "label": "Mass Air Flow Sensor Replacement Cost",
                "href": "/cost/mass-air-flow-sensor-replacement",
                "description": "A strong next cost check only after unmetered air is ruled out and low MAF g/s readings, unplug behavior, contamination, or P0101-style data confirms airflow reporting.",
            },
            {
                "label": "Fuel Pump Replacement Cost",
                "href": "/cost/fuel-pump-replacement",
                "description": "Relevant when fuel pressure drops under acceleration, trims worsen at higher RPM or load, and weak fuel delivery is separated from intake leaks or MAF error.",
            },
        ],
        "P0174": [
            {
                "label": "Mass Air Flow Sensor Replacement Cost",
                "href": "/cost/mass-air-flow-sensor-replacement",
                "description": "A strong next cost check only after Bank 1 versus Bank 2 trims and unmetered air are reviewed, then low MAF g/s readings, unplug behavior, contamination, or P0101-style data confirms airflow reporting.",
            },
            {
                "label": "Fuel Pump Replacement Cost",
                "href": "/cost/fuel-pump-replacement",
                "description": "Relevant when fuel pressure drops under acceleration, trims worsen at higher RPM or load, and weak fuel delivery is separated from bank-specific intake leaks or MAF error.",
            },
        ],
        "P0128": [
            {
                "label": "Thermostat Replacement Cost",
                "href": "/cost/thermostat-replacement",
                "description": "The strongest cost guide when slow warm-up, repeat P0128 after clearing, and scan data confirm the thermostat is stuck open before replacement.",
            },
            {
                "label": "Engine Coolant Temperature Sensor Replacement Cost",
                "href": "/cost/engine-coolant-temperature-sensor-replacement",
                "description": "Relevant when ECT plausibility checks show false low-temperature readings or sensor data is misleading the warm-up monitor.",
            },
            {
                "label": "Water Pump Replacement Cost",
                "href": "/cost/water-pump-replacement",
                "description": "Useful when weak coolant flow, pump noise, poor circulation, or heater performance points away from a thermostat-only repair.",
            },
            {
                "label": "Radiator Replacement Cost",
                "href": "/cost/radiator-replacement",
                "description": "Relevant when overheating, repeated temperature fluctuation, leaks, or broader cooling-system diagnosis continues after thermostat service.",
            },
        ],
        "P0116": [
            {
                "label": "Engine Coolant Temperature Sensor Replacement Cost",
                "href": "/cost/engine-coolant-temperature-sensor-replacement",
                "description": "Used when coolant temperature readings are inaccurate or inconsistent.",
            },
            {
                "label": "Thermostat Replacement Cost",
                "href": "/cost/thermostat-replacement",
                "description": "Common when the engine warms up too slowly or runs outside normal temperature range.",
            },
        ],
        "P0125": [
            {
                "label": "Thermostat Replacement Cost",
                "href": "/cost/thermostat-replacement",
                "description": "Most common cause when the engine fails to reach normal operating temperature.",
            },
        ],
        "P0420": [
            {
                "label": "Catalytic Converter Replacement Cost",
                "href": "/cost/catalytic-converter-replacement",
                "description": "A direct cost guide when repeated P0420, downstream O2 mirroring upstream O2, sulfur smell, overheating, or restriction confirms the converter after root-cause checks.",
            },
            {
                "label": "Oxygen Sensor Replacement Cost",
                "href": "/cost/oxygen-sensor-replacement",
                "description": "Relevant when front-versus-rear waveform comparison shows the downstream O2 sensor is biased, slow, or inaccurate instead of proving converter failure.",
            },
        ],
        "P0430": [
            {
                "label": "Catalytic Converter Replacement Cost",
                "href": "/cost/catalytic-converter-replacement",
                "description": "A direct cost guide when catalyst-efficiency testing confirms the converter is no longer doing the job.",
            },
            {
                "label": "Oxygen Sensor Replacement Cost",
                "href": "/cost/oxygen-sensor-replacement",
                "description": "Relevant when testing points to a weak downstream O2 sensor instead of a failed converter.",
            },
        ],
        "P2195": [
            {
                "label": "Oxygen Sensor Replacement Cost",
                "href": "/cost/oxygen-sensor-replacement",
                "description": "Useful when diagnosis confirms the upstream air-fuel or oxygen sensor is biased lean.",
            },
        ],

        "P0117": [
            {
                "label": "Engine Coolant Temperature Sensor Replacement Cost",
                "href": "/cost/engine-coolant-temperature-sensor-replacement",
                "description": "Useful when inaccurate coolant temperature readings affect engine performance or warm-up behavior.",
            },
        ],
        "P0118": [
            {
                "label": "Engine Coolant Temperature Sensor Replacement Cost",
                "href": "/cost/engine-coolant-temperature-sensor-replacement",
                "description": "Useful when inaccurate coolant temperature readings affect engine performance or warm-up behavior.",
            },
        ],
        "P0119": [
            {
                "label": "Engine Coolant Temperature Sensor Replacement Cost",
                "href": "/cost/engine-coolant-temperature-sensor-replacement",
                "description": "Useful when inaccurate coolant temperature readings affect engine performance or warm-up behavior.",
            },
        ],
    }

    for item in cost_guide_map.get(code, []):
        add_guide(
            str(item.get("label") or "").strip(),
            str(item.get("href") or "").strip(),
            str(item.get("description") or "").strip(),
        )

    if not results:
        fallback_rules = [
            {
                "matches": lambda current: (
                    current.startswith("P013")
                    or (current.startswith("P014") and current not in {"P0148", "P0149"})
                    or current.startswith("P015")
                    or (current.startswith("P016") and current not in {"P0168", "P0169"})
                    or current in {
                    "P0171",
                    "P0174",
                    "P0420",
                    "P0430",
                    "P2195",
                    "P2196",
                    "P2197",
                    "P2198",
                }
                ),
                "guides": [
                    {
                        "label": "Oxygen Sensor Replacement Cost",
                        "href": "/cost/oxygen-sensor-replacement",
                        "description": "Useful when testing confirms the oxygen or air-fuel sensor is the repair path.",
                    },
                ],
            },
            {
                "matches": lambda current: current.startswith("P035"),
                "guides": [
                    {
                        "label": "Ignition Coil Replacement Cost",
                        "href": "/cost/ignition-coil-replacement",
                        "description": "A strong match when the trouble code points directly to an ignition-coil circuit problem.",
                    },
                ],
            },
            {
                "matches": lambda current: current.startswith("P030") or current in {"P0310", "P0311", "P0312", "P0316"},
                "guides": [
                    {
                        "label": "Spark Plug Replacement Cost",
                        "href": "/cost/spark-plug-replacement",
                        "description": "A relevant next cost check when the confirmed misfire path leads back to worn plugs.",
                    },
                    {
                        "label": "Ignition Coil Replacement Cost",
                        "href": "/cost/ignition-coil-replacement",
                        "description": "Useful when misfire diagnosis shows one or more ignition coils breaking down under load.",
                    },
                ],
            },
            {
                "matches": lambda current: current in {"P0100", "P0101", "P0102", "P0103", "P0104"},
                "guides": [
                    {
                        "label": "Mass Air Flow Sensor Replacement Cost",
                        "href": "/cost/mass-air-flow-sensor-replacement",
                        "description": "A direct cost guide when airflow readings or circuit checks point to a failed MAF sensor.",
                    },
                ],
            },
            {
                "matches": lambda current: current == "P0128",
                "guides": [
                    {
                        "label": "Thermostat Replacement Cost",
                        "href": "/cost/thermostat-replacement",
                        "description": "A strong next cost check when the engine is running colder than expected because the thermostat is stuck open.",
                    },
                    {
                        "label": "Radiator Replacement Cost",
                        "href": "/cost/radiator-replacement",
                        "description": "Relevant when diagnosis finds a cooling-system leak or radiator flow problem behind the temperature issue.",
                    },
                    {
                        "label": "Water Pump Replacement Cost",
                        "href": "/cost/water-pump-replacement",
                        "description": "Useful when overheating or circulation tests point to pump flow or bearing failure.",
                    },
                ],
            },
            {
                "matches": lambda current: current in {"P0117", "P0118"},
                "guides": [
                    {
                        "label": "Thermostat Replacement Cost",
                        "href": "/cost/thermostat-replacement",
                        "description": "Relevant when cooling-system diagnosis is part of the temperature-sensor fault path.",
                    },
                    {
                        "label": "Water Pump Replacement Cost",
                        "href": "/cost/water-pump-replacement",
                        "description": "Useful when broader cooling-system testing points to circulation or pump-related problems.",
                    },
                ],
            },
            {
                "matches": lambda current: current == "P0217",
                "guides": [
                    {
                        "label": "Radiator Replacement Cost",
                        "href": "/cost/radiator-replacement",
                        "description": "Relevant when diagnosis finds a cooling-system leak or radiator flow problem behind the temperature issue.",
                    },
                    {
                        "label": "Water Pump Replacement Cost",
                        "href": "/cost/water-pump-replacement",
                        "description": "Useful when overheating or circulation tests point to pump flow or bearing failure.",
                    },
                ],
            },
            {
                "matches": lambda current: current in {"P0562", "P0563"},
                "guides": [
                    {
                        "label": "Battery Replacement Cost",
                        "href": "/cost/battery-replacement",
                        "description": "Useful when low-voltage testing shows the battery itself is weak or unable to hold charge.",
                    },
                    {
                        "label": "Alternator Replacement Cost",
                        "href": "/cost/alternator-replacement",
                        "description": "A strong cost guide when charging-system testing confirms low output or alternator control failure.",
                    },
                ],
            },
            {
                "matches": lambda current: current == "P0620",
                "guides": [
                    {
                        "label": "Alternator Replacement Cost",
                        "href": "/cost/alternator-replacement",
                        "description": "A strong cost guide when charging-system testing confirms low output or alternator control failure.",
                    },
                ],
            },
            {
                "matches": lambda current: current in {"P0230", "P0231", "P0232", "P0460", "P0461", "P0462", "P0463"},
                "guides": [
                    {
                        "label": "Fuel Pump Replacement Cost",
                        "href": "/cost/fuel-pump-replacement",
                        "description": "Relevant when testing shows the fault is in the fuel pump module or an integrated sender assembly.",
                    },
                ],
            },
        ]

        for rule in fallback_rules:
            if not rule["matches"](code):
                continue
            for item in rule["guides"]:
                add_guide(item["label"], item["href"], item["description"])
            break

    return results[:3]

def build_obd_page_metadata(code: str):
    code = code.upper().strip()

    metadata_map = {
        "P0300": {
            "title": "P0300 Random/Multiple Cylinder Misfire: Causes & Repairs | TorqueMech",
            "description": "P0300 means the engine is misfiring on multiple cylinders. Review common causes, quick checks, likely repairs, and spark plug or coil cost guidance.",
        },
        "P0301": {
            "title": "P0301 Cylinder 1 Misfire: Causes & Repairs | TorqueMech",
            "description": "P0301 means cylinder 1 is misfiring. See the most likely ignition, fuel, air, or compression causes plus repair paths and cost guidance.",
        },
        "P0302": {
            "title": "P0302 Cylinder 2 Misfire: Causes & Repairs | TorqueMech",
            "description": "P0302 means cylinder 2 is misfiring. Review the most common ignition, fuel, air, or compression causes, likely repairs, and cost guidance.",
        },
        "P0303": {
            "title": "P0303 Code: Cylinder 3 Misfire Causes, Symptoms & Repair Cost Guide | TorqueMech",
            "description": "Diagnose P0303 cylinder 3 misfire causes, cold-start symptoms, ignition coil vs injector clues, repair costs, and when compression or head gasket issues should be checked.",
            "schema_title": "P0303 Cylinder 3 Misfire: Start With Plug or Coil | TorqueMech",
            "schema_description": "P0303 means cylinder 3 is misfiring. Start with the plug or coil, then check injector, intake-runner leak, and compression faults with practical repair guidance.",
        },
        "P0304": {
            "title": "P0304 Cylinder 4 Misfire: Causes & Repairs | TorqueMech",
            "description": "P0304 means cylinder 4 is misfiring. Review the most common ignition, fuel, air, or compression causes, likely repairs, and cost guidance.",
        },
        "P0373": {
            "title": "P0373 Code: Timing Reference Signal Causes, Symptoms & Fix Guide | TorqueMech",
            "description": "Diagnose P0373 timing reference signal faults, crankshaft sensor symptoms, warm restart stalls, unstable RPM signals, and when timing chain or reluctor problems should be checked.",
        },
        "P0171": {
            "title": "P0171 System Too Lean Bank 1: Start With Vacuum Leaks | TorqueMech",
            "description": "P0171 means bank 1 is running lean. Start with vacuum leaks after the MAF, then check airflow readings and fuel-delivery issues with mechanic-first repair guidance.",
        },
        "P0174": {
            "title": "P0174 System Too Lean Bank 2: Causes & Repairs | TorqueMech",
            "description": "P0174 means bank 2 is running lean. Review common vacuum leak, airflow, and fuel-delivery causes plus likely repairs and related cost guidance.",
        },
        "P0128": {
            "title": "P0128 Thermostat Below Regulating Temp: Start With Thermostat | TorqueMech",
            "description": "P0128 usually points to a thermostat stuck open. Start with thermostat diagnosis, then check coolant level and temperature-sensor faults with practical repair guidance.",
        },
        "P0420": {
            "title": "P0420 Code: Causes, Symptoms, Catalytic Converter Cost & Fix Guide | TorqueMech",
            "description": "Diagnose P0420 catalyst efficiency issues, common causes, O2 sensor behavior, catalytic converter replacement cost, and when repairs are actually needed before expensive damage gets worse.",
        },
        "P0430": {
            "title": "P0430 Catalyst Efficiency Bank 2: Causes & Repairs | TorqueMech",
            "description": "P0430 means bank 2 catalyst efficiency is below threshold. Review common converter and O2 sensor causes, likely repairs, and related cost guidance.",
        },
        "P0446": {
            "title": "P0446 EVAP Vent Control Circuit: Start With Vent Valve | TorqueMech",
            "description": "P0446 usually points to an EVAP vent valve stuck closed or failing. Start with the vent valve and vent path, then check control-circuit faults with practical repair guidance.",
        },
        "P0507": {
            "title": "P0507 Idle Control RPM High: Start With Throttle Body | TorqueMech",
            "description": "P0507 means idle speed is too high. Start with throttle body carbon or sticking issues, then check relearn and vacuum-leak faults with mechanic-first repair guidance.",
        },
        "P0562": {
            "title": "P0562 Code: System Voltage Low Causes, Battery Symptoms & Alternator Fix Guide | TorqueMech",
            "description": "Diagnose P0562 system voltage low issues, battery warning signs, charging system symptoms, alternator failure clues, and when repairs are actually needed before no-start problems worsen.",
        },
        "P0563": {
            "title": "P0563 System Voltage High: Causes & Repairs | TorqueMech",
            "description": "P0563 means system voltage is too high. Review overcharging causes, alternator or regulator repair paths, and battery-related cost guidance.",
        },
    }

    return metadata_map.get(code)

def build_obd_structured_data(code: str, page_title: str, description: str, url: str):
    code = code.upper().strip()
    page_title = str(page_title or "").strip()
    description = str(description or "").strip()
    url = str(url or "").strip()

    schema_focus_map = {
        "P0303": {
            "page_focus": "Cylinder 3 misfire diagnosis",
            "start_here": "Spark plug or ignition coil on cylinder 3",
        },
        "P0171": {
            "page_focus": "Bank 1 lean condition diagnosis",
            "start_here": "Vacuum leak after the mass air flow sensor",
        },
        "P0128": {
            "page_focus": "Slow warm-up and thermostat diagnosis",
            "start_here": "Thermostat stuck open",
        },
        "P0446": {
            "page_focus": "EVAP vent control circuit diagnosis",
            "start_here": "EVAP vent valve stuck closed or failing",
        },
        "P0507": {
            "page_focus": "High idle and throttle body diagnosis",
            "start_here": "Dirty or sticking throttle body",
        },
    }

    focus = schema_focus_map.get(code)
    if not focus or not page_title or not description or not url:
        return None

    return {
        "@context": "https://schema.org",
        "@type": "TechArticle",
        "headline": page_title,
        "name": page_title,
        "description": description,
        "url": url,
        "articleSection": "OBD Diagnostic Guide",
        "author": {
            "@type": "Organization",
            "name": "TorqueMech",
        },
        "mainEntityOfPage": {
            "@type": "WebPage",
            "@id": url,
        },
        "about": [
            {"@type": "Thing", "name": f"OBD-II code {code}"},
            {"@type": "Thing", "name": focus["page_focus"]},
            {"@type": "Thing", "name": focus["start_here"]},
        ],
    }

def build_obd_content_refinement(code: str):
    code = code.upper().strip()

    refinements = {
        "P0300": {
            "meaning": "P0300 means the ECM has detected misfires across multiple cylinders instead of one isolated cylinder. The next step is separating a random misfire pattern from a cylinder-specific fault, then proving whether ignition, fuel delivery, air leaks, or mechanical sealing is driving the failure.",
            "diagnostic_insight_intro": "P0300 should be diagnosed by pattern first: identify whether the misfire is cold-start, load-related, fuel-trim related, same-cylinder after swaps, or truly random across cylinders.",
            "diagnostic_insight_points": [
                "If the scan data starts pointing to P0301, P0302, P0303, or P0304, isolate that cylinder with coil swaps, plug inspection, and injector checks instead of treating the fault as random.",
                "Cold-start-only misfires that clear quickly raise suspicion for injector leakage, small coolant seep, or valve sealing before random coil replacement.",
                "Misfires under acceleration or load often point toward coil breakdown, plug gap issues, carbon or boot tracking, weak fuel delivery, or plug boots arcing under demand.",
                "Positive fuel trims with multiple misfires push P0171-style lean diagnosis, vacuum leaks, intake leaks, PCV leaks, MAF data, and fuel pressure checks higher on the list.",
                "If misfires repeat after plugs or coils and the same cylinders stay involved, compression and leak-down testing help separate mechanical failure from ignition diagnosis.",
                "A flashing check-engine light means active catalyst-damaging misfire; correct misfire and fuel-trim causes before pricing converter work for P0420.",
            ],
            "symptoms": [
                "Rough idle or shaking at a stop",
                "Hesitation or stumbling under load",
                "Flashing check-engine light during active misfire",
            ],
            "quick_checks": [
                "Check freeze-frame data to see whether misfires happen cold, hot, idle-only, or under load",
                "Inspect spark plugs, plug gap, coil boots, plug wells, and carbon tracking across affected cylinders",
                "Use coil swaps and plug inspection to separate same-cylinder faults from random misfire behavior",
                "Review fuel trims, MAF data, injector balance, and fuel pressure before condemning ignition parts alone",
                "Inspect vacuum leaks, PCV plumbing, intake gaskets, and air duct leaks that affect multiple cylinders",
                "Use compression or leak-down testing if misfires repeat after ignition repairs or cold-start coolant seep is suspected",
                "Check catalyst-risk codes such as P0420 after active misfire and fuel-trim faults are corrected",
            ],
        },
        "P0301": {
            "meaning": "P0301 means cylinder 1 is misfiring often enough for the ECM to flag it. Diagnosis should confirm whether the fault follows the plug or coil, stays with the injector, or points to a mechanical problem in that cylinder.",
            "diagnostic_insight_intro": "P0301 should be diagnosed by proving whether cylinder 1 loses spark, fuel control, compression, or sealing under the condition where the misfire occurs.",
            "diagnostic_insight_points": [
                "If the misfire follows a coil or plug swap, ignition is the likely repair path; if it stays on cylinder 1, injector and mechanical testing move up.",
                "Cold-start-only cylinder 1 misfire can point to injector leakdown, coolant intrusion, or valve sealing that improves as the engine warms.",
                "Misfire under load points toward coil breakdown, plug gap, plug fouling, or boot tracking before compression faults.",
                "Coolant loss with overnight rough start increases suspicion for small head gasket seep into cylinder 1.",
            ],
            "symptoms": [
                "Rough idle or shake tied to one cylinder",
                "Hesitation or light bucking on acceleration",
                "Fuel smell or flashing MIL if the misfire is severe",
            ],
            "quick_checks": [
                "Swap the cylinder 1 coil with another cylinder and see whether the misfire follows",
                "Inspect the cylinder 1 spark plug for wear, gap, oil, fuel fouling, coolant staining, or a washed-clean tip",
                "Check injector connector fit, injector operation, and leakdown or balance if the misfire stays on cylinder 1",
                "Run compression and leak-down testing if ignition and injector checks do not move the fault",
                "Inspect for intake runner or vacuum leaks near cylinder 1 if trims suggest unmetered air",
            ],
        },
        "P0302": {
            "meaning": "P0302 means cylinder 2 is misfiring often enough for the ECM to detect it. The most useful next step is confirming whether the fault tracks with the ignition parts, fuel injector, intake leak, or cylinder condition.",
            "diagnostic_insight_intro": "P0302 should be treated as a focused cylinder 2 fault until swap testing, injector checks, and compression data prove whether the cause is ignition, fuel, air, or mechanical.",
            "diagnostic_insight_points": [
                "Swap-based testing is the fastest way to separate coil or plug faults from cylinder 2 injector or mechanical faults.",
                "Cold-start-only cylinder 2 misfire can point to injector leakage, coolant seep, or valve sealing issues.",
                "Under-load cylinder 2 misfire usually keeps coil breakdown, plug gap, and plug boot tracking high on the list.",
                "If the plug looks clean but the misfire repeats, compression and leak-down testing become more important.",
            ],
            "symptoms": [
                "Rough idle or uneven engine note",
                "Reduced power or stumble on throttle",
                "Fuel smell or flashing MIL during a heavy misfire",
            ],
            "quick_checks": [
                "Swap the cylinder 2 coil with another cylinder and see whether the misfire follows",
                "Inspect the cylinder 2 spark plug for wear, gap, oil, fuel fouling, coolant staining, or a washed-clean tip",
                "Check injector connector fit, injector operation, and leakdown or balance if the misfire stays on cylinder 2",
                "Run compression and leak-down testing if ignition and injector checks do not move the fault",
                "Inspect for intake runner or vacuum leaks near cylinder 2 if trims suggest unmetered air",
            ],
        },
        "P0303": {
            "meaning": "P0303 means cylinder 3 is misfiring often enough for the ECM to flag it. Good diagnosis confirms whether the problem follows the plug or coil, stays with the injector, or points to compression loss, leak-down failure, or a small coolant seep on that cylinder.",
            "diagnostic_insight_intro": "P0303 should be narrowed by condition and movement: whether cylinder 3 misfires cold, under load, or remains fixed after plug and coil swaps determines whether ignition, injector, compression, or head gasket testing comes next.",
            "diagnostic_insight_points": [
                "If the misfire follows a cylinder 3 coil swap, ignition coil replacement becomes a stronger path; if it follows the plug, inspect fouling, gap, carbon tracking, and plug condition first.",
                "Cold-start misfire that improves warm can point to injector leakdown, plug fouling, valve sealing, or small head gasket coolant seep rather than a simple coil fault.",
                "Load-related misfire keeps coil breakdown, plug gap, plug-wire or boot tracking, and spark leakage high on the list.",
                "If the fault stays on cylinder 3 after plug and coil swaps, injector balance, contribution testing, compression testing, and leak-down testing become important.",
                "Repeated same-cylinder misfire after ignition parts replacement should raise suspicion for fuel injector, compression, valve sealing, or coolant intrusion issues.",
            ],
            "symptoms": [
                "Noticeable shake at idle",
                "Hesitation or stumble under load",
                "Reduced fuel economy while the misfire is active",
            ],
            "quick_checks": [
                "Swap the cylinder 3 coil with another cylinder and see whether the misfire follows",
                "Inspect the cylinder 3 spark plug for wear, gap, oil, fuel fouling, coolant staining, or a washed-clean tip",
                "Check for coil boot carbon tracking, spark leakage, and load-related ignition breakdown if the misfire worsens under acceleration",
                "Check injector connector fit, injector operation, and leakdown or balance if the misfire stays on cylinder 3",
                "Run compression and leak-down testing if ignition and injector checks do not move the fault",
                "Inspect for cold-start coolant seep clues such as coolant loss, a washed-clean plug, or overnight rough running",
                "Inspect for intake runner or vacuum leaks near cylinder 3 if trims suggest unmetered air",
            ],
        },
        "P0304": {
            "meaning": "P0304 means cylinder 4 is misfiring often enough for the ECM to detect it. The repair path usually becomes clear once you confirm whether the fault follows ignition parts, stays with fuel delivery, or points to a mechanical issue.",
            "diagnostic_insight_intro": "P0304 usually becomes clear once cylinder 4 is tested by condition, swap movement, injector behavior, and mechanical sealing.",
            "diagnostic_insight_points": [
                "A misfire that moves with the coil or plug usually confirms ignition; a fixed cylinder 4 misfire needs injector, compression, and leak-down checks.",
                "Cold-start-only cylinder 4 misfire can come from injector leakage, coolant seep, or valve sealing that changes as the engine warms.",
                "Misfire under load points toward coil breakdown, plug gap, plug fouling, or boot tracking before replacing unrelated parts.",
                "Coolant loss, a washed-clean plug, or overnight rough start raises suspicion for small head gasket seep.",
            ],
            "symptoms": [
                "Rough idle or steady vibration",
                "Hesitation or weak pull during acceleration",
                "Flashing MIL if the misfire is strong enough to threaten the catalyst",
            ],
            "quick_checks": [
                "Swap the cylinder 4 coil with another cylinder and see whether the misfire follows",
                "Inspect the cylinder 4 spark plug for wear, gap, oil, fuel fouling, coolant staining, or a washed-clean tip",
                "Check injector connector fit, injector operation, and leakdown or balance if the misfire stays on cylinder 4",
                "Run compression and leak-down testing if ignition and injector checks do not move the fault",
                "Inspect for intake runner or vacuum leaks near cylinder 4 if trims suggest unmetered air",
            ],
        },
        "P0171": {
            "meaning": "P0171 means bank 1 is running lean because the ECM is correcting for too much air, too little fuel, or an airflow signal it cannot trust. It is not automatically a bad oxygen sensor or MAF sensor; vacuum leaks, intake leaks after the MAF, PCV leaks, weak fuel delivery, low MAF reporting, and bank 1 intake sealing problems can all set the same code.",
            "diagnostic_insight_intro": "P0171 should be diagnosed from fuel-trim behavior first: find when bank 1 goes lean, then decide whether the fault acts like unmetered air, low fuel delivery, bad airflow reporting, or a lean misfire pattern.",
            "diagnostic_insight_points": [
                "High positive trims at idle that improve with RPM usually point toward a vacuum leak, PCV leak, intake manifold gasket leak, or another unmetered-air source near bank 1.",
                "Smoke testing should come before replacing MAF or oxygen-sensor parts when trims look like an idle-heavy intake, PCV, hose, or manifold leak.",
                "Trims that get worse during cruise, acceleration, or higher RPM point more toward fuel pressure, fuel volume, injector delivery, or MAF under-reporting than a small idle-only leak.",
                "If P0101, P0113, or suspicious MAF data appears with P0171, inspect unmetered air after the MAF, low MAF g/s readings, connector behavior, and unplug response before pricing a sensor.",
                "Lean misfire symptoms or P0300 history under load should separate weak fuel delivery from ignition faults before coils, plugs, or injectors are blamed.",
                "Unresolved lean operation can overheat or damage the catalyst, so P0420 and converter replacement decisions should wait until fuel trims are corrected.",
            ],
            "symptoms": [
                "Rough idle or light surge at idle",
                "Hesitation during light acceleration",
                "Weak cold-start behavior or reduced fuel economy",
            ],
            "quick_checks": [
                "Compare short- and long-term fuel trims at idle, 2500 RPM, and steady cruise to see when bank 1 goes lean",
                "Smoke test the intake and inspect intake boots, PCV plumbing, brake-booster hose, and vacuum lines after the MAF",
                "Inspect the bank 1 intake gasket area and nearby hose connections when trims are idle-heavy or bank-specific",
                "Inspect MAF contamination, low grams-per-second readings, and unplug behavior before replacing oxygen-sensor or airflow parts",
                "Check fuel pressure and fuel volume if trims stay lean under load, worsen at highway speed, or lean misfire symptoms appear",
                "Review P0300 misfire history and P0420 catalyst history before treating the lean code as an isolated sensor fault",
            ],
        },
        "P0174": {
            "meaning": "P0174 means bank 2 is running lean because the ECM is correcting for too much air, too little fuel, or an airflow signal it cannot trust. It is not automatically a bad oxygen sensor or MAF sensor; vacuum leaks, intake leaks after the MAF, PCV leaks, weak fuel delivery, low MAF reporting, and bank-specific intake sealing problems can all set the same code.",
            "diagnostic_insight_intro": "P0174 should be diagnosed from fuel-trim behavior first: compare Bank 1 and Bank 2, find when bank 2 goes lean, then decide whether the fault acts like unmetered air, low fuel delivery, bad airflow reporting, or a lean misfire pattern.",
            "diagnostic_insight_points": [
                "High positive trims at idle that improve with RPM usually point toward a bank 2 vacuum leak, PCV leak, intake manifold gasket leak, or another unmetered-air source.",
                "Compare Bank 1 and Bank 2 trims before replacing parts: one-bank lean behavior points more toward bank-side intake sealing, while both-bank lean behavior points toward shared airflow or fuel delivery.",
                "Smoke testing should come before replacing MAF or oxygen-sensor parts when trims look like an idle-heavy intake, PCV, hose, or manifold leak.",
                "Trims that get worse during cruise, acceleration, or higher RPM point more toward fuel pressure, fuel volume, injector delivery, or MAF under-reporting than a small idle-only leak.",
                "If P0101, P0113, or suspicious MAF data appears with P0174, inspect unmetered air after the MAF, low MAF g/s readings, connector behavior, and unplug response before pricing a sensor.",
                "Lean misfire symptoms or P0300 history under load should separate weak fuel delivery from ignition faults before coils, plugs, or injectors are blamed.",
                "Unresolved lean operation can overheat or damage the catalyst, so P0420/P0430 and converter replacement decisions should wait until fuel trims are corrected.",
            ],
            "symptoms": [
                "Light surge or rough idle",
                "Hesitation during light acceleration",
                "Weak cold-start behavior or reduced fuel economy",
            ],
            "quick_checks": [
                "Compare short- and long-term fuel trims at idle, 2500 RPM, and steady cruise to see when bank 2 goes lean",
                "Smoke test the intake and inspect intake boots, PCV plumbing, brake-booster hose, and vacuum lines after the MAF",
                "Inspect the bank 2 intake gasket area and nearby hose connections when trims are idle-heavy or bank-specific",
                "Inspect MAF contamination, low grams-per-second readings, and unplug behavior before replacing oxygen-sensor or airflow parts",
                "Check fuel pressure and fuel volume if trims stay lean under load, worsen at highway speed, or lean misfire symptoms appear",
                "Review P0300 misfire history and P0420/P0430 catalyst history before treating the lean code as an isolated sensor fault",
            ],
        },
        "P0420": {
            "meaning": "P0420 means catalyst efficiency on bank 1 tested below the expected threshold after the ECM compared upstream and downstream oxygen sensor behavior. It is not automatically a bad catalytic converter; exhaust leaks, biased or slow downstream O2 data, unresolved misfires, rich or lean fuel trim problems, oil burning, coolant contamination, or other upstream faults can set the same code or damage catalyst performance.",
            "diagnostic_insight_intro": "P0420 should be diagnosed by comparing catalyst monitor data, front-versus-rear O2 behavior, and upstream engine health before the bank 1 converter is condemned.",
            "diagnostic_insight_points": [
                "If the downstream O2 sensor on bank 1 mirrors the upstream sensor after warm-up, weak catalyst oxygen storage becomes more likely, especially when P0420 returns after clearing.",
                "A downstream O2 sensor should not be replaced blindly; compare front and rear waveforms, sensor response, exhaust leaks, and catalyst monitor data before calling the sensor or converter failed.",
                "Recent P0300-style misfire history, ignition faults, injector faults, rich-running, lean-running, or P0171 fuel-trim history should be corrected first because those faults can damage the converter or create a false efficiency failure.",
                "Exhaust leaks ahead of or near the bank 1 converter can pull oxygen into the stream and make catalyst data look worse than the converter really is.",
                "Oil burning, coolant consumption, or fuel contamination can poison the catalyst, so converter replacement without fixing the source can lead to repeat failure.",
                "Sulfur smell, converter overheating, or restriction symptoms make true converter failure more likely, but root-cause diagnosis still protects the replacement converter.",
            ],
            "symptoms": [
                "Check engine light with little or no major drivability change",
                "Failed emissions readiness or inspection",
                "Possible sulfur smell or loss of power if the converter is restricted",
            ],
            "quick_checks": [
                "Check for current or history misfire, fuel-trim, rich, lean, MAF, or oxygen-sensor codes before blaming the converter",
                "Inspect for exhaust leaks at the manifold, flex pipe, flange, and pipe joints ahead of the bank 1 converter",
                "Compare upstream and downstream bank 1 O2 or air/fuel sensor behavior on scan data after full warm-up",
                "Review P0171, rich-running, and fuel-trim correction history before pricing converter replacement",
                "Review P0300 and other misfire history because unresolved ignition or injector faults can overheat and damage the catalyst",
                "Check for rich-running signs such as sulfur smell, excessive converter heat, fuel smell, or poor fuel economy",
                "Inspect oil or coolant consumption concerns before replacing a contaminated converter",
            ],
        },
        "P0430": {
            "meaning": "P0430 means catalyst efficiency on bank 2 tested below the expected threshold after the ECM compared upstream and downstream oxygen sensor behavior. It is not automatically a bad catalytic converter; exhaust leaks, biased or slow oxygen sensor data, unresolved misfires, rich/lean operation, or other fueling problems can set the same code or damage catalyst performance.",
            "diagnostic_insight_intro": "P0430 should be diagnosed by comparing catalyst monitor data with upstream engine health before the bank 2 converter is condemned.",
            "diagnostic_insight_points": [
                "If the downstream O2 sensor on bank 2 mirrors the upstream sensor after warm-up, weak catalyst oxygen storage becomes more likely.",
                "Recent misfire, rich-running, lean-running, or fuel-trim history should be corrected first because those faults can damage the converter or create a false efficiency failure.",
                "Exhaust leaks ahead of or near the bank 2 converter can pull oxygen into the stream and make catalyst data look worse than the converter really is.",
                "Oil burning, coolant consumption, or fuel contamination can poison the catalyst, so converter replacement without fixing the source can lead to repeat failure.",
            ],
            "symptoms": [
                "Check engine light with little or no major drivability change",
                "Failed emissions readiness or inspection",
                "Possible sulfur smell or power loss if the converter is restricted",
            ],
            "quick_checks": [
                "Check for current or history misfire, fuel-trim, rich, lean, MAF, or oxygen-sensor codes before blaming the converter",
                "Inspect for exhaust leaks at the manifold, flex pipe, flange, and pipe joints ahead of the bank 2 converter",
                "Compare upstream and downstream bank 2 O2 or air/fuel sensor behavior on scan data after full warm-up",
                "Check for rich-running signs such as sulfur smell, excessive converter heat, fuel smell, or poor fuel economy",
                "Inspect oil or coolant consumption concerns before replacing a contaminated converter",
            ],
        },
        "P0440": {
            "meaning": "P0440 means the EVAP system detected a general malfunction during its self-test. It is not automatically just a gas cap issue; a gas cap sealing problem, purge valve fault, vent valve fault, hose or line leak, canister issue, or wiring and control issue can all set the same code.",
            "diagnostic_insight_intro": "P0440 should be treated as a general EVAP system fault until testing narrows whether the leak or control problem is on the cap, purge, vent, hose, line, or canister side.",
            "diagnostic_insight_points": [
                "A gas cap is a good first check, but purge and vent valve operation should not be skipped.",
                "General EVAP faults often need smoke or system testing because the code does not identify one exact leak point.",
                "If P0442, P0455, or P0446 are also present, use those codes to narrow leak size or vent-control direction.",
            ],
            "symptoms": [
                "Check engine light with little or no major drivability change",
                "Emissions readiness failure",
                "Possible fuel vapor smell in some cases",
            ],
            "quick_checks": [
                "Inspect gas cap seal condition and cap fitment",
                "Inspect EVAP hoses and lines for leaks or disconnections",
                "Inspect purge and vent valve operation",
                "Inspect the canister and nearby plumbing",
                "Smoke test or run an EVAP system test if the problem is not obvious",
            ],
        },
        "P0455": {
            "meaning": "P0455 means the EVAP system detected a large leak during its self-test. A loose or missing gas cap is an easy first check, but a repeat code after cap replacement usually points toward disconnected EVAP hoses, cracked lines, purge or vent valves stuck open or not sealing, canister leaks, filler-neck problems, or tank-side plumbing leaks.",
            "diagnostic_insight_intro": "P0455 should be diagnosed as a large EVAP sealing failure, with testing focused on what prevents the system from closing and holding pressure or vacuum before multiple parts are replaced.",
            "diagnostic_insight_points": [
                "A loose or damaged gas cap is common, but the cap should not end diagnosis if the seal, filler neck, and code return pattern do not confirm it.",
                "If the code returns after a cap replacement, command or bench-test the purge valve and vent valve for sealing before replacing the canister or tank-side parts.",
                "Fuel smell near the rear of the vehicle or a strong odor after fill-up moves attention toward the filler neck, tank seal, charcoal canister, and rear EVAP lines.",
                "Hard refueling or the pump clicking off repeatedly points toward vent restriction, vent valve problems, or tank pressure behavior rather than a simple cap fault.",
                "Large leaks usually need smoke testing or sealed-system testing because normal engine drivability can feel completely unchanged.",
            ],
            "symptoms": [
                "Check engine light with little or no major drivability change",
                "Emissions readiness failure",
                "Fuel vapor smell in some cases",
            ],
            "quick_checks": [
                "Inspect gas cap presence, seal condition, cap fitment, and filler-neck sealing surface",
                "Inspect EVAP hoses and lines for disconnected, split, rusted, or collapsed sections",
                "Verify purge and vent valves seal correctly when commanded closed",
                "Inspect canister, tank-side plumbing, fuel-pump seal, and filler neck for leaks, cracks, or loose connections",
                "Ask about hard refueling, fuel smell after fill-up, and tank pressure symptoms to guide vent-side diagnosis",
                "Smoke test the system if visual checks do not reveal the open point",
            ],
        },
        "P0442": {
            "meaning": "P0442 means the EVAP system detected a small leak during its self-test. A weak gas cap seal is common, but this is not automatically just a bad gas cap; small hose cracks, loose fittings, purge or vent valve sealing issues, canister seepage, or line seepage can set the same code.",
            "diagnostic_insight_intro": "P0442 should be treated as a small EVAP leak until testing shows whether the leak is at the cap, hose, valve, canister, or line.",
            "diagnostic_insight_points": [
                "Small leaks can be harder to find than large leaks and often need smoke testing to confirm the source.",
                "A loose or aging gas cap is a good first check, but valve sealing and line seepage should not be skipped.",
                "If P0455 or P0446 are also present, use those codes to narrow leak size, vent behavior, or valve-control direction.",
            ],
            "symptoms": [
                "Check engine light with little or no major drivability change",
                "Emissions readiness failure",
                "Occasional fuel vapor smell in some cases",
            ],
            "quick_checks": [
                "Inspect gas cap seal condition and cap fitment",
                "Inspect EVAP hoses and fittings for small cracks or loose connections",
                "Verify purge and vent valves are sealing properly",
                "Inspect the canister and nearby EVAP lines for seepage",
                "Smoke test the system if the leak is not obvious",
            ],
        },
        "P0456": {
            "meaning": "P0456 means the EVAP system detected a very small leak during its self-test. A gas cap seal is still a valid first check, but repeat P0456 after cap replacement usually points toward tiny hose or fitting seepage, purge or vent valves not sealing fully, canister seepage, filler-neck corrosion, line seepage, or tank-side plumbing leaks.",
            "diagnostic_insight_intro": "P0456 should be diagnosed as a very small EVAP sealing loss, where careful smoke testing, cap and filler-neck inspection, and valve sealing checks usually matter more than parts guessing.",
            "diagnostic_insight_points": [
                "Very small leaks often come from cap seals, cracked hose ends, canister fittings, filler-neck corrosion, or valve seepage that may not be visible during a quick inspection.",
                "If the code returns after a cap replacement, inspect purge solenoid seepage, vent valve sealing, and tank-area hoses before moving to larger components.",
                "Smoke near the tank area should lead to hose, canister, fuel-pump seal, filler-neck, and vent-seal inspection before condemning the tank or canister assembly.",
                "Repeated small-leak codes with no drivability symptoms are normal for EVAP faults; the leak is often outside the engine's normal air and fuel operation.",
                "Hard refueling, pump shutoff, or fuel smell after filling can point toward vent-side restriction or tank-area sealing issues.",
            ],
            "symptoms": [
                "Check engine light with little or no major drivability change",
                "Emissions readiness failure",
                "Occasional fuel vapor smell in some cases",
            ],
            "quick_checks": [
                "Inspect gas cap seal condition, cap fitment, and filler-neck sealing surface",
                "Inspect EVAP hoses, fittings, and line connections for subtle cracks or seepage",
                "Verify purge and vent valves seal correctly and do not leak when closed",
                "Inspect the canister, tank seals, filler neck, and nearby plumbing for small leaks",
                "Ask about hard refueling, fuel smell after fill-up, and repeated readiness failures",
                "Smoke test the system carefully if the leak is not obvious",
            ],
        },
        "P0128": {
            "meaning": "P0128 means the engine coolant temperature is not reaching the expected operating range quickly enough. A thermostat stuck open is common, but low coolant level, trapped air, inaccurate coolant temperature sensor data, poor coolant flow, cooling fan issues, wiring faults, or sensor faults can also mislead the monitor.",
            "diagnostic_insight_intro": "P0128 should be diagnosed from warm-up behavior, heater performance, coolant level, and ECT plausibility: prove whether the engine is truly running too cool or the temperature data is misleading.",
            "diagnostic_insight_points": [
                "If live coolant temperature rises slowly and stays low during cruise, a stuck-open thermostat stays high on the suspect list, especially when P0128 returns after clearing.",
                "Weak cabin heat at idle should push coolant level, trapped air, heater-core flow, water pump circulation, and thermostat behavior ahead of sensor replacement.",
                "An inconsistent gauge or scan reading should be compared against cold-engine ambient temperature, infrared readings, and ECT sensor plausibility before parts are replaced.",
                "False low-temperature readings from the ECT sensor or connector can mimic thermostat failure, so scan data confirmation matters before pricing parts.",
                "If P0128 returns after thermostat replacement, inspect coolant level, air pockets, ECT sensor accuracy, connector condition, thermostat housing sealing, cooling fan behavior, and coolant flow.",
                "Overheating or repeated temperature fluctuation after thermostat service should move the diagnosis toward broader cooling-system flow, radiator, water pump, or trapped-air checks.",
            ],
            "symptoms": [
                "Poor cabin heat or longer warm-up time",
                "Reduced fuel economy",
                "Check engine light or unstable temperature behavior",
            ],
            "quick_checks": [
                "Verify coolant level, coolant condition, and trapped-air concerns before replacing thermostat or sensor parts",
                "Monitor live ECT data from a cold start through normal operating temperature and confirm sensor plausibility",
                "Compare warm-up behavior to expected thermostat opening behavior and highway temperature stability before replacing the thermostat",
                "Compare ECT scan data with ambient temperature on a cold engine and external temperature checks when readings look inconsistent",
                "Check heater performance at idle and under RPM to separate thermostat behavior from coolant flow or air-pocket problems",
                "Inspect ECT connector, wiring, thermostat housing, cooling fan behavior, and coolant flow if the code returns after thermostat service",
                "Escalate to overheating or cooling-system diagnosis if temperature swings, weak flow, or overheating continues after thermostat replacement",
            ],
        },
        "P0446": {
            "meaning": "P0446 usually points to an EVAP vent control problem. The fault is often a stuck or restricted vent valve, blocked vent path, contamination near the canister, or a control-circuit issue rather than just a loose gas cap.",
            "diagnostic_insight_intro": "P0446 should be treated as an EVAP vent-control fault until testing proves whether the valve, vent path, or circuit is responsible.",
            "diagnostic_insight_points": [
                "A failed vent valve or restricted vent filter can keep the EVAP system from sealing or venting correctly.",
                "Dust, rust, or charcoal contamination near the canister and vent assembly often matters more than the gas cap.",
                "Circuit and connector testing should come earlier if the valve does not respond to commands.",
            ],
            "symptoms": [
                "Check engine light with little or no major drivability change",
                "Possible fueling shutoff or slow fill complaints on some vehicles",
                "Emissions readiness failure",
            ],
            "quick_checks": [
                "Inspect the EVAP vent valve and vent path for restriction",
                "Inspect for dirt, dust, rust, or charcoal contamination near the canister",
                "Inspect vent valve wiring and connector condition",
                "Command the vent valve if possible and verify response",
                "Smoke test or run an EVAP system test if the fault is not obvious",
            ],
        },
        "P0507": {
            "meaning": "P0507 means idle speed is higher than the ECM expects. The fault is often caused by extra air entering the engine or throttle control not returning to the expected idle position, but throttle body faults, wiring issues, vacuum leaks, PCV leaks, intake leaks, or idle relearn problems can all create the same high-idle condition.",
            "diagnostic_insight_intro": "P0507 should be treated as a high-idle airflow and throttle-control problem before replacing the throttle body.",
            "diagnostic_insight_points": [
                "Carbon buildup, throttle-body sticking, or an incomplete idle relearn are common first checks.",
                "If idle stays high after throttle inspection, vacuum, PCV, and intake leaks should move up the list quickly.",
                "Commanded throttle angle, actual throttle position, and idle behavior should be reviewed before parts are replaced.",
            ],
            "symptoms": [
                "High idle or hanging RPM",
                "Rough, unstable, or surging idle after warm-up",
                "Check engine light",
            ],
            "quick_checks": [
                "Inspect the throttle body for carbon buildup or sticking",
                "Verify whether an idle relearn or throttle relearn may be needed",
                "Inspect vacuum and PCV hoses for leaks",
                "Inspect the intake tract for unmetered air leaks",
                "Review idle data and throttle position on scan data before replacing parts",
            ],
        },
        "P0101": {
            "meaning": "P0101 means the mass air flow signal is outside the expected range for the current engine load and RPM. The sensor may be dirty or failing, but unmetered air after the MAF, PCV leaks, dirty air filters, intake restriction, aftermarket intake issues, weak fuel delivery, or wiring faults can also make the airflow reading look implausible.",
            "diagnostic_insight_intro": "P0101 should be diagnosed as an airflow plausibility problem: compare MAF data, fuel trims, intake sealing, and fuel pressure behavior before condemning the sensor.",
            "diagnostic_insight_points": [
                "If P0101 appears with P0171 or P0174, use fuel trims to confirm whether both banks are lean and smoke test for unmetered air before replacing the MAF.",
                "High idle with positive trims that are worse at idle usually points toward an intake, vacuum, PCV, or throttle-body gasket leak rather than a standalone MAF failure.",
                "If trims worsen under acceleration or highway load, check fuel pressure and delivery because a weak pump can mimic an airflow fault.",
                "If hesitation or P0300 appears under load, separate lean misfire, fuel delivery, and airflow data from ignition-only misfire diagnosis.",
                "A low MAF grams-per-second reading can come from contamination, a dirty air filter, intake restriction, collapsed ducting, oiled aftermarket filters, or sensor bias.",
                "If unplugging the MAF improves idle, the airflow signal may be biased, but connector fit, harness condition, intake leaks, and trim data still need checks.",
            ],
            "symptoms": [
                "Hesitation or poor throttle response",
                "Rough idle or surge",
                "Reduced fuel economy",
            ],
            "quick_checks": [
                "Inspect air filter condition, air-box sealing, intake ducting, clamps, PCV plumbing, and aftermarket intake fitment",
                "Review fuel trims at idle, cruise, and load with MAF grams-per-second readings against engine RPM",
                "Inspect and clean the MAF sensor with MAF-safe cleaner if contamination is visible or data is biased low",
                "Smoke test for intake, vacuum, PCV, or post-MAF leaks before replacing the MAF",
                "Check fuel pressure if trims get worse under load or acceleration",
                "Inspect the MAF connector and harness for loose terminals, corrosion, spread pins, or wiring damage",
            ],
        },
        "P0110": {
            "meaning": "P0110 means the intake air temperature circuit is malfunctioning. It is not automatically just a bad IAT sensor; an unplugged sensor, damaged wiring, connector corrosion, an open or shorted circuit, failed sensor, or integrated MAF/IAT assembly issue can set the same code.",
            "diagnostic_insight_intro": "P0110 should be diagnosed as an intake-temperature signal circuit problem, with scan data, connector checks, and voltage behavior leading the repair path.",
            "diagnostic_insight_points": [
                "Cold-start drivability issues with P0110 should move IAT signal voltage, connector fit, and wiring integrity ahead of parts replacement.",
                "The IAT reading should be compared with ambient temperature on a cold engine before deciding whether the signal is biased.",
                "Connector corrosion, loose terminals, wiring damage, opens, or shorts can mimic a failed sensor.",
                "On vehicles with an integrated MAF/IAT assembly, confirming the sensor location changes whether the repair is a sensor, connector, harness, or MAF assembly path.",
            ],
            "symptoms": [
                "Check engine light",
                "Hard starting in some conditions",
                "Rich or lean fuel behavior in some cases",
                "Hesitation or drivability issues on some vehicles",
                "Poor fuel economy",
            ],
            "quick_checks": [
                "Inspect IAT connector fit, terminal tension, corrosion, and lock-tab condition",
                "Inspect wiring for opens, shorts, rub-through, corrosion, or intake-area heat damage",
                "Compare IAT reading to ambient temperature on a cold engine before startup",
                "Verify signal voltage, reference behavior, and resistance behavior if appropriate for the sensor design",
                "Confirm whether the IAT is integrated into the MAF housing before replacing separate parts",
            ],
        },
        "P0113": {
            "meaning": "P0113 means the intake air temperature signal is reading colder than expected because the circuit is open or the signal is stuck high. It is not automatically just a bad IAT sensor; an unplugged sensor, damaged wiring, connector corrosion, open circuit, failed sensor, integrated MAF/IAT assembly issue, or intake setup problem can overlap with the same airflow symptoms.",
            "diagnostic_insight_intro": "P0113 should be diagnosed as an intake-temperature circuit-high fault first, then cross-checked against MAF data, fuel trims, intake sealing, and fuel delivery when lean or hesitation symptoms are present.",
            "diagnostic_insight_points": [
                "A cold-looking IAT reading on a warm intake usually points to an open circuit, unplugged sensor, poor terminal fit, or signal circuit problem.",
                "Cold-start issues with P0113 should push connector, wiring, and sensor-voltage checks ahead of replacing the MAF or IAT assembly.",
                "If P0113 appears with P0101, P0171, or P0174, confirm whether the IAT is integrated into the MAF and smoke test for post-MAF leaks before replacing the assembly.",
                "Positive trims that are worse at idle point toward intake, vacuum, or PCV leaks, while trims that worsen under load move the diagnosis toward fuel pressure and delivery.",
                "Hesitation or P0300 with P0113 should be separated into airflow, lean misfire, fuel delivery, and ignition paths before parts are priced.",
                "Air-filter restriction, oiled aftermarket filters, loose intake tubes, or poor air-box sealing can skew MAF/IAT behavior and should be inspected before sensor replacement.",
            ],
            "symptoms": [
                "Check engine light",
                "Hard cold-start behavior in some cases",
                "Rich-running behavior or poor fuel economy",
                "Hesitation or drivability issues on some vehicles",
            ],
            "quick_checks": [
                "Inspect IAT connector fit, terminal tension, corrosion, and lock-tab condition",
                "Inspect wiring for opens, breaks, corrosion, rub-through, or intake-area damage",
                "Compare IAT reading to ambient temperature on a cold engine before startup",
                "Inspect air filter condition, air-box sealing, intake tube fitment, and aftermarket intake changes",
                "Smoke test for intake, vacuum, PCV, or post-MAF leaks if lean trims are present",
                "Check fuel pressure if trims worsen under load or hesitation feels fuel-starved",
                "Verify signal voltage, reference behavior, and resistance behavior if appropriate for the sensor design",
                "Confirm whether the IAT is integrated into the MAF housing before replacing separate parts",
            ],
        },
        "P0201": {
            "meaning": "P0201 means the injector circuit for cylinder 1 is malfunctioning. The injector may have failed, but damaged wiring, poor connector terminal fit, an open or short in the control circuit, or a PCM driver issue in rarer cases can set the same code.",
            "diagnostic_insight_intro": "P0201 should be diagnosed as a cylinder 1 injector circuit fault before the injector itself is condemned.",
            "diagnostic_insight_points": [
                "A circuit code can come from the connector, harness, power feed, or control side, not just the injector.",
                "If cylinder 1 also misfires, confirm spark and compression so fuel control is not blamed for a different fault.",
                "Resistance, pulse, and terminal-fit checks usually come before injector replacement.",
            ],
            "symptoms": [
                "Rough idle or cylinder 1 misfire",
                "Hesitation or reduced power",
                "Fuel smell or poor fuel economy in some cases",
            ],
            "quick_checks": [
                "Inspect cylinder 1 injector connector fit and terminal condition",
                "Check for harness damage, rub-through, or corrosion near the injector",
                "Verify injector resistance if appropriate for the application",
                "Verify injector power and control pulse with proper test equipment",
                "Confirm cylinder 1 spark and compression before condemning fuel control alone",
            ],
        },
        "P0202": {
            "meaning": "P0202 means the injector circuit for cylinder 2 is malfunctioning. The injector may have failed, but damaged wiring, poor connector terminal fit, an open or short in the control circuit, or a PCM driver issue in rarer cases can set the same code.",
            "diagnostic_insight_intro": "P0202 should be diagnosed as a cylinder 2 injector circuit fault before the injector itself is condemned.",
            "diagnostic_insight_points": [
                "A circuit code can come from the connector, harness, power feed, or control side, not just the injector.",
                "If cylinder 2 also misfires, confirm spark and compression so fuel control is not blamed for a different fault.",
                "Resistance, pulse, and terminal-fit checks usually come before injector replacement.",
            ],
            "symptoms": [
                "Rough idle or cylinder 2 misfire",
                "Hesitation or reduced power",
                "Fuel smell or poor fuel economy in some cases",
            ],
            "quick_checks": [
                "Inspect cylinder 2 injector connector fit and terminal condition",
                "Check for harness damage, rub-through, or corrosion near the injector",
                "Verify injector resistance if appropriate for the application",
                "Verify injector power and control pulse with proper test equipment",
                "Confirm cylinder 2 spark and compression before condemning fuel control alone",
            ],
        },
        "P0203": {
            "meaning": "P0203 means the injector circuit for cylinder 3 is malfunctioning. The injector may have failed, but damaged wiring, poor connector terminal fit, an open or short in the control circuit, or a PCM driver issue in rarer cases can set the same code.",
            "diagnostic_insight_intro": "P0203 should be diagnosed as a cylinder 3 injector circuit fault before the injector itself is condemned.",
            "diagnostic_insight_points": [
                "A circuit code can come from the connector, harness, power feed, or control side, not just the injector.",
                "If cylinder 3 also misfires, confirm spark and compression so fuel control is not blamed for a different fault.",
                "Resistance, pulse, and terminal-fit checks usually come before injector replacement.",
            ],
            "symptoms": [
                "Rough idle or cylinder 3 misfire",
                "Hesitation or reduced power",
                "Fuel smell or poor fuel economy in some cases",
            ],
            "quick_checks": [
                "Inspect cylinder 3 injector connector fit and terminal condition",
                "Check for harness damage, rub-through, or corrosion near the injector",
                "Verify injector resistance if appropriate for the application",
                "Verify injector power and control pulse with proper test equipment",
                "Confirm cylinder 3 spark and compression before condemning fuel control alone",
            ],
        },
        "P0204": {
            "meaning": "P0204 means the injector circuit for cylinder 4 is malfunctioning. The injector may have failed, but damaged wiring, poor connector terminal fit, an open or short in the control circuit, or a PCM driver issue in rarer cases can set the same code.",
            "diagnostic_insight_intro": "P0204 should be diagnosed as a cylinder 4 injector circuit fault before the injector itself is condemned.",
            "diagnostic_insight_points": [
                "A circuit code can come from the connector, harness, power feed, or control side, not just the injector.",
                "If cylinder 4 also misfires, confirm spark and compression so fuel control is not blamed for a different fault.",
                "Resistance, pulse, and terminal-fit checks usually come before injector replacement.",
            ],
            "symptoms": [
                "Rough idle or cylinder 4 misfire",
                "Hesitation or reduced power",
                "Fuel smell or poor fuel economy in some cases",
            ],
            "quick_checks": [
                "Inspect cylinder 4 injector connector fit and terminal condition",
                "Check for harness damage, rub-through, or corrosion near the injector",
                "Verify injector resistance if appropriate for the application",
                "Verify injector power and control pulse with proper test equipment",
                "Confirm cylinder 4 spark and compression before condemning fuel control alone",
            ],
        },
        "P0138": {
            "meaning": "P0138 means bank 1 sensor 2 is staying high or reading richer than expected for too long. It is not automatically just a bad oxygen sensor; a biased or failing downstream O2 sensor, wiring short or high-voltage issue, rich-running condition, exhaust contamination, or connector and harness damage can hold the signal high.",
            "diagnostic_insight_intro": "P0138 should be diagnosed as a bank 1 downstream O2 high-voltage fault by separating true rich exhaust, biased sensor output, and exhaust-side wiring problems.",
            "diagnostic_insight_points": [
                "A rear O2 sensor stuck high with no major drivability issue often points toward downstream sensor bias, catalyst behavior, or wiring rather than a primary fuel-control failure.",
                "Rich-running symptoms with high O2 voltage should move fuel trims, fuel pressure, injector leakage, and upstream sensor data ahead of sensor replacement.",
                "Exhaust leaks before the downstream sensor can distort oxygen content and create misleading switching or catalyst data.",
                "Repeated catalyst codes with downstream O2 issues should trigger converter condition checks before replacing sensors only.",
            ],
            "symptoms": [
                "Check engine light with little or no major drivability change",
                "Emissions readiness failure",
                "Rich smell or poor fuel economy in some cases",
            ],
            "quick_checks": [
                "Compare bank 1 sensor 2 voltage behavior with upstream sensor activity after full warm-up",
                "Inspect downstream O2 wiring near the exhaust for melted insulation, shorts, rub-through, or poor routing",
                "Inspect connector condition, terminal fit, and contamination at bank 1 sensor 2",
                "Check fuel trims, fuel pressure, and rich-running signs before replacing the sensor",
                "Inspect for exhaust leaks ahead of bank 1 sensor 2 and catalyst-related companion codes",
            ],
        },
        "P0141": {
            "meaning": "P0141 means the heater circuit for bank 1 sensor 2 is not operating as expected. It is not automatically just a bad oxygen sensor; a failed downstream O2 heater, blown fuse, damaged wiring, poor connector contact, heater power fault, or ground fault can set the same code.",
            "diagnostic_insight_intro": "P0141 should be diagnosed as a bank 1 sensor 2 heater-circuit fault, with power, ground, fuse, and exhaust-area wiring checks before the sensor is condemned.",
            "diagnostic_insight_points": [
                "A heater circuit fault that appears during cold-start monitor activity points strongly toward heater power, ground, fuse protection, or circuit resistance checks.",
                "Wiring near the exhaust can melt, rub through, or lose terminal tension, creating the same fault as an open heater inside the sensor.",
                "Heater resistance and command checks help separate a failed sensor heater from a power-supply or ground-side fault.",
                "If catalyst or downstream signal codes are also present, verify the heater repair restores sensor readiness before chasing converter efficiency.",
            ],
            "symptoms": [
                "Check engine light with little or no major drivability change",
                "Delayed oxygen-sensor monitor readiness",
                "Emissions test failure in some cases",
            ],
            "quick_checks": [
                "Inspect downstream O2 sensor wiring near the exhaust for heat damage, poor routing, or rubbed insulation",
                "Inspect connector fit, terminal tension, and corrosion at bank 1 sensor 2",
                "Verify heater power, ground, and voltage drop across the heater circuit",
                "Check related fuse protection and shared heater feeds if applicable",
                "Confirm heater resistance or circuit behavior before condemning the sensor alone",
            ],
        },
        "P0158": {
            "meaning": "P0158 means bank 2 sensor 2 is staying high or reading richer than expected for too long. It is not automatically just a bad oxygen sensor; a biased or failing downstream O2 sensor, wiring short or high-voltage issue, rich-running condition, exhaust contamination, or connector and harness damage can hold the signal high.",
            "diagnostic_insight_intro": "P0158 should be diagnosed as a bank 2 downstream O2 high-voltage fault by separating true rich exhaust, biased sensor output, and exhaust-side wiring problems.",
            "diagnostic_insight_points": [
                "A rear O2 sensor stuck high with no major drivability issue often points toward downstream sensor bias, catalyst behavior, or wiring rather than a primary fuel-control failure.",
                "Rich-running symptoms with high O2 voltage should move fuel trims, fuel pressure, injector leakage, and upstream sensor data ahead of sensor replacement.",
                "Exhaust leaks before the downstream sensor can distort oxygen content and create misleading switching or catalyst data.",
                "Repeated catalyst codes with downstream O2 issues should trigger converter condition checks before replacing sensors only.",
            ],
            "symptoms": [
                "Check engine light with little or no major drivability change",
                "Emissions readiness failure",
                "Rich smell or poor fuel economy in some cases",
            ],
            "quick_checks": [
                "Compare bank 2 sensor 2 voltage behavior with upstream sensor activity after full warm-up",
                "Inspect downstream O2 wiring near the exhaust for melted insulation, shorts, rub-through, or poor routing",
                "Inspect connector condition, terminal fit, and contamination at bank 2 sensor 2",
                "Check fuel trims, fuel pressure, and rich-running signs before replacing the sensor",
                "Inspect for exhaust leaks ahead of bank 2 sensor 2 and catalyst-related companion codes",
            ],
        },
        "P0562": {
            "meaning": "P0562 means system voltage dropped below the expected range. The fault is usually found by proving whether the battery cannot hold charge, the alternator cannot maintain output under load, belt-drive slip is reducing output, or resistance in the battery, ground, starter, or charging-cable path is pulling voltage down.",
            "diagnostic_insight_intro": "P0562 should be diagnosed as a low-voltage system fault, not just a battery or alternator code. Charging output, battery health, starter voltage drop, belt drive, grounds, terminals, and parasitic draw behavior all need to be separated before parts are replaced.",
            "diagnostic_insight_points": [
                "Low voltage mainly at idle should move alternator output, belt slip, weak tensioner, idler condition, and pulley noise high on the test list.",
                "A new battery that keeps going dead points toward charging-system load testing, parasitic draw testing, or cable voltage-drop checks before another battery is installed.",
                "Voltage that falls when headlights, blower motor, rear defroster, or AC are switched on usually indicates charging weakness, cable resistance, corroded terminals, or engine ground voltage drop.",
                "A battery warning light with P0562 should be confirmed with charging-voltage and alternator-output data before the repair is treated as battery-only.",
                "Intermittent no-start or no-crank complaints need battery, starter, alternator, and ground voltage-drop testing during start attempts.",
                "Overnight no-start complaints need a separate parasitic-draw test so a drain is not confused with an alternator output failure.",
            ],
            "symptoms": [
                "Slow cranking or intermittent no-start",
                "Dim lights or unstable electrical accessories",
                "Battery warning light or repeated dead battery complaints",
                "Multiple low-voltage communication or module codes",
            ],
            "quick_checks": [
                "Check resting battery voltage and perform a battery load or conductance test before condemning the alternator",
                "Measure charging voltage at idle, raised RPM, and with headlights, blower motor, AC, and rear defroster load applied",
                "Inspect battery terminals, main grounds, engine ground straps, alternator output cable, and charging-cable connections for voltage drop",
                "Inspect serpentine belt condition, tensioner operation, idler noise, pulley slip, and alternator-drive behavior if output is low",
                "Measure voltage drop during crank attempts if the complaint includes intermittent no-start or slow/no-crank behavior",
                "If the battery is dead after sitting, perform parasitic-draw testing separately from charging-system testing",
            ],
        },
        "P0563": {
            "meaning": "P0563 means system voltage is higher than expected, pointing to an overcharging condition. The fault often traces to an alternator or regulator issue, but poor sensing connections or control-circuit problems can also push voltage too high.",
            "symptoms": [
                "Battery warning light or electrical system warning",
                "Bright lights or erratic accessory behavior",
                "Battery smell, heat, or repeated battery failure after overcharging",
            ],
            "quick_checks": [
                "Measure charging voltage at idle and with electrical load applied",
                "Inspect regulator and alternator control wiring for faults or poor sensing",
                "Inspect battery terminals and sensing connections for poor contact",
                "Do not keep driving if charging voltage is far above normal",
            ],
        },
        "P0373": {
            "meaning": "P0373 means the high-resolution timing reference signal B has too few pulses for the ECM to trust engine position. The fault can come from crankshaft or camshaft position signal dropout, a damaged reluctor, wiring or connector issues near the CKP/CMP sensors, or mechanical timing instability.",
            "diagnostic_insight_intro": "P0373 should be diagnosed as an engine-position signal integrity problem. The repair path depends on whether the RPM signal drops out, the CKP/CMP waveforms lose pulses, or mechanical timing and reluctor condition are disturbing the reference signal.",
            "diagnostic_insight_points": [
                "Intermittent no-start or warm restart stalling points strongly toward a crank reference signal that becomes unstable with heat or vibration.",
                "A tachometer that drops out during cranking supports CKP signal loss, but sensor power, ground, connector fit, and harness routing still need to be verified.",
                "Unstable RPM data on the scan tool should be confirmed with waveform testing before a sensor is condemned.",
                "CKP/CMP waveform comparison is stronger than blind sensor replacement because it can reveal missing pulses, poor amplitude, noise, or correlation errors.",
                "A repeat timing-reference fault after sensor replacement should move the diagnosis toward reluctor wheel damage, sensor air gap, timing chain stretch, or mechanical timing movement.",
            ],
            "symptoms": [
                "Intermittent no-start or extended crank",
                "Stalling, especially during warm restart or low-speed operation",
                "Tachometer dropout or unstable RPM signal during cranking",
                "Rough running, misfire-like behavior, or reduced power in some cases",
            ],
            "quick_checks": [
                "Review cranking RPM and scan data for unstable or missing engine-speed signal",
                "Inspect CKP and CMP sensor connectors, terminal tension, corrosion, and harness routing near heat or moving components",
                "Scope CKP and CMP waveforms for missing pulses, poor amplitude, noise, or cam/crank correlation problems",
                "Inspect reluctor wheel condition, sensor air gap, and mounting if waveform data shows signal interruption",
                "Check timing chain stretch or mechanical timing if the fault returns after sensor and wiring checks",
            ],
        },
        "P0117": {
            "meaning": "P0117 means the coolant temperature signal is reading hotter than expected because the signal is low or the circuit is shorted low. It is not automatically just a bad coolant temperature sensor; a shorted sensor circuit, damaged wiring, connector contamination, failed sensor, poor reference or ground behavior, or real overheating and low coolant in some cases can set the same code.",
            "diagnostic_insight_intro": "P0117 should be treated as a coolant-temperature circuit-low fault until testing confirms whether the engine is actually hot or the signal is being pulled low.",
            "diagnostic_insight_points": [
                "A hot-looking ECT reading on a cold engine often points to a shorted circuit, contaminated connector, or failed sensor.",
                "Real overheating and low coolant still need to be ruled out before treating the reading as false.",
                "Connector, wiring, reference, and ground checks usually come before replacing the sensor.",
            ],
            "symptoms": [
                "Check engine light",
                "Hard starting or poor fuel control in some cases",
                "Cooling fan behavior issues",
                "Possible overheating concern or false hot reading",
            ],
            "quick_checks": [
                "Inspect coolant temperature sensor connector fit and terminal condition",
                "Inspect wiring for shorts, rub-through, corrosion, or coolant intrusion",
                "Compare ECT reading to actual engine condition",
                "Verify sensor and reference behavior if appropriate",
                "Confirm coolant level and real engine temperature before blaming the sensor alone",
            ],
        },
        "P0118": {
            "meaning": "P0118 means the coolant temperature signal is reading colder than expected because the circuit is open or the signal is stuck high. It is not automatically just a bad coolant temperature sensor; an unplugged sensor, damaged wiring, connector corrosion, open circuit, failed sensor, or less common PCM and reference-voltage issue can set the same code.",
            "diagnostic_insight_intro": "P0118 should be treated as a coolant-temperature circuit-high fault before the sensor itself is condemned.",
            "diagnostic_insight_points": [
                "A cold-looking ECT reading on a warm engine often points to an open circuit or poor connection.",
                "Connector corrosion, coolant intrusion, or broken wiring can mimic a failed sensor.",
                "Cold-engine scan data should be compared to ambient temperature before parts are replaced.",
            ],
            "symptoms": [
                "Hard cold starts or rich-running behavior",
                "Poor fuel economy",
                "Cooling fan behavior issues on some vehicles",
                "Check engine light",
            ],
            "quick_checks": [
                "Inspect coolant temperature sensor connector fit and terminal condition",
                "Inspect wiring for breaks, corrosion, or coolant intrusion",
                "Compare ECT reading to ambient temperature on a cold engine",
                "Verify reference voltage or sensor resistance if appropriate",
                "Confirm coolant level and basic cooling condition before blaming the sensor alone",
            ],
        },
    }

    return refinements.get(code)

@app.get("/obd/{code}", response_class=HTMLResponse)
async def obd_code_page(request: Request, code: str):

    norm = "".join(ch for ch in (code or "").upper() if ch.isalnum())[:7]
    if len(norm) < 4:
        raise HTTPException(status_code=400, detail="Invalid OBD code.")

    conn = obd_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM dtc WHERE code = ?", (norm,))
    row = cur.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="OBD code not found")

    possible_causes = normalize_obd_text_list(json.loads(row["possible_causes"] or "[]"))
    quick_checks = json.loads(row["quick_checks"] or "[]")
    knowledge_sections = build_obd_knowledge_sections(row["code"])

    related_codes = build_related_codes(row["code"], knowledge_sections["related_codes"])
    common_repairs = build_common_repairs(row["code"])
    cost_guide_links = build_cost_guide_links(row["code"])
    diagnostic_path = build_obd_diagnostic_path(row["code"])
    vehicle_context = build_vehicle_context_from_request(request)
    workflow_context = build_workflow_context(
        request,
        vehicle_context=vehicle_context,
        obd_code=row["code"],
        source="obd",
    )
    related_codes = apply_workflow_context_to_repair_items(
        related_codes,
        workflow_context,
    )
    if diagnostic_path:
        diagnostic_path = dict(diagnostic_path)
        diagnostic_path["blueprints"] = apply_workflow_context_to_repair_items(
            diagnostic_path.get("blueprints") or [],
            workflow_context,
        )
        diagnostic_path["symptom_links"] = apply_workflow_context_to_repair_items(
            diagnostic_path.get("symptom_links") or [],
            workflow_context,
        )
        diagnostic_path["estimator_href"] = append_workflow_context_to_href(
            diagnostic_path.get("estimator_href") or f"/estimator?obd={row['code']}",
            workflow_context,
        )
    diagnostic_summary = build_diagnostic_summary(row["code"])
    page_metadata = build_obd_page_metadata(row["code"])
    content_refinement = build_obd_content_refinement(row["code"])
    page_title = (
        page_metadata["title"]
        if page_metadata and page_metadata.get("title")
        else f'{row["code"]} Code - {row["title"] or ""} | TorqueMech'
    )

    # ✅ THIS IS STEP 2
    repair_path = REPAIR_PATHS.get(row["code"])
    display_description = (
        content_refinement["meaning"]
        if content_refinement and content_refinement.get("meaning")
        else row["description"] or ""
    )
    if content_refinement and content_refinement.get("symptoms"):
        display_symptoms = list(content_refinement["symptoms"])
    elif repair_path and repair_path.get("symptoms"):
        display_symptoms = list(repair_path["symptoms"])
    else:
        display_symptoms = []

    if content_refinement and content_refinement.get("quick_checks"):
        display_quick_checks = list(content_refinement["quick_checks"])
    else:
        display_quick_checks = list(quick_checks)
        if repair_path and repair_path.get("electrical") and repair_path["electrical"].get("items"):
            display_quick_checks.extend(repair_path["electrical"]["items"])

    display_causes = knowledge_sections["causes"] or possible_causes
    display_symptoms = knowledge_sections["symptoms"] or display_symptoms
    display_diagnostic_steps = knowledge_sections["diagnostic_steps"] or normalize_obd_text_list(display_quick_checks)
    display_diagnostic_insight_intro = (
        content_refinement.get("diagnostic_insight_intro", "")
        if content_refinement
        else ""
    )
    display_diagnostic_insight_points = normalize_obd_text_list(
        content_refinement.get("diagnostic_insight_points")
        if content_refinement
        else []
    )
    page_description = (
        page_metadata["description"]
        if page_metadata and page_metadata.get("description")
        else display_description
    )
    schema_page_title = (
        page_metadata["schema_title"]
        if page_metadata and page_metadata.get("schema_title")
        else page_title
    )
    schema_page_description = (
        page_metadata["schema_description"]
        if page_metadata and page_metadata.get("schema_description")
        else page_description
    )
    structured_data = build_obd_structured_data(
        row["code"],
        schema_page_title,
        schema_page_description,
        str(request.url),
    )
    related_system_hubs = apply_workflow_context_to_repair_items(
        infer_related_system_hubs(
            row["code"],
            row["title"],
            display_description,
            " ".join(display_causes or []),
            " ".join(display_symptoms or []),
            " ".join(display_diagnostic_steps or []),
        ),
        workflow_context,
    )
    obd_workflow_signal = [
        row["code"],
        row["title"],
        display_description,
        " ".join(display_causes or []),
        " ".join(display_symptoms or []),
        " ".join(display_diagnostic_steps or []),
    ]
    workflow_next_steps = apply_workflow_context_to_repair_items(
        infer_workflow_next_steps(*obd_workflow_signal),
        workflow_context,
    )
    related_inspections = apply_workflow_context_to_repair_items(
        infer_related_inspections(*obd_workflow_signal),
        workflow_context,
    )

    return templates.TemplateResponse(
        "obd_code_detail.html",
        {
            "request": request,
            "code": row["code"],
            "title": row["title"] or "",
            "description": row["description"] or "",
            "display_description": display_description,
            "display_causes": display_causes,
            "display_symptoms": display_symptoms,
            "display_diagnostic_steps": display_diagnostic_steps,
            "display_diagnostic_insight_intro": display_diagnostic_insight_intro,
            "display_diagnostic_insight_points": display_diagnostic_insight_points,
            "display_difficulty": knowledge_sections["difficulty"],
            "related_codes": related_codes,
            "common_repairs": common_repairs,
            "cost_guide_links": cost_guide_links,
            "diagnostic_path": diagnostic_path,
            "diagnostic_summary": diagnostic_summary,
            "workflow_context": workflow_context,
            "related_system_hubs": related_system_hubs,
            "workflow_next_steps": workflow_next_steps,
            "related_inspections": related_inspections,
            "page_title": page_title,
            "meta_description": page_description,
            "structured_data": structured_data,
            "repair_path": repair_path,
        },
    )

def build_diagnostic_summary(code: str):
    code = code.upper().strip()

    summaries = {
        "P0300": {
            "severity": "Medium",
            "drivability": "Rough idle, hesitation, misfire",
            "cost": "$120 – $600"
        },
        "P0301": {
            "severity": "Medium",
            "drivability": "Engine misfire on cylinder 1",
            "cost": "$120 – $420"
        },
        "P0302": {
            "severity": "Medium",
            "drivability": "Engine misfire on cylinder 2",
            "cost": "$120 – $420"
        },
        "P0303": {
            "severity": "Medium",
            "drivability": "Engine misfire on cylinder 3",
            "cost": "$120 – $420"
        },
        "P0304": {
            "severity": "Medium",
            "drivability": "Engine misfire on cylinder 4",
            "cost": "$120 – $420"
        },
        "P0171": {
            "severity": "Medium",
            "drivability": "Lean running condition, hesitation",
            "cost": "$150 – $800"
        },
        "P0420": {
            "severity": "Low–Medium",
            "drivability": "Usually drives normally but emissions affected",
            "cost": "$200 – $1800"
        },
        "P0442": {
            "severity": "Low",
            "drivability": "No drivability symptoms",
            "cost": "$20 – $300"
        }
    }

    return summaries.get(code)

def build_diagnostic_summary(code: str):
    code = code.upper().strip()

    summaries = {
        "P0101": {
            "severity": "Medium",
            "drivability": "Hesitation, surge, poor throttle response",
            "cost": "$120 - $550+"
        },
        "P0113": {
            "severity": "Low-Medium",
            "drivability": "Hard cold start, rich running, poor fuel economy",
            "cost": "$80 - $350+"
        },
        "P0128": {
            "severity": "Low-Medium",
            "drivability": "Usually mild symptoms, weak heat, poor warm-up",
            "cost": "$120 - $500+"
        },
        "P0300": {
            "severity": "Medium-High",
            "drivability": "Rough idle, hesitation, flashing MIL possible",
            "cost": "$150 - $900+"
        },
        "P0301": {
            "severity": "Medium",
            "drivability": "Single-cylinder misfire, rough idle, reduced power",
            "cost": "$120 - $650+"
        },
        "P0302": {
            "severity": "Medium",
            "drivability": "Single-cylinder misfire, rough idle, reduced power",
            "cost": "$120 - $650+"
        },
        "P0303": {
            "severity": "Medium",
            "drivability": "Single-cylinder misfire, rough idle, reduced power",
            "cost": "$120 - $650+"
        },
        "P0304": {
            "severity": "Medium",
            "drivability": "Single-cylinder misfire, rough idle, reduced power",
            "cost": "$120 - $650+"
        },
        "P0171": {
            "severity": "Medium",
            "drivability": "Lean surge, hesitation, rough idle",
            "cost": "$120 - $900+"
        },
        "P0174": {
            "severity": "Medium",
            "drivability": "Lean surge, hesitation, rough idle",
            "cost": "$120 - $900+"
        },
        "P0401": {
            "severity": "Medium",
            "drivability": "Ping under load, rough idle, emissions failure risk",
            "cost": "$150 - $700+"
        },
        "P0420": {
            "severity": "Low-Medium",
            "drivability": "Usually mild symptoms, emissions failure risk",
            "cost": "$180 - $2200+"
        },
        "P0430": {
            "severity": "Low-Medium",
            "drivability": "Usually mild symptoms, emissions failure risk",
            "cost": "$180 - $2200+"
        },
        "P0507": {
            "severity": "Medium",
            "drivability": "High idle, hanging RPM, rough idle after warm-up",
            "cost": "$100 - $600+"
        },
        "P0700": {
            "severity": "Medium",
            "drivability": "Shift concerns or limp mode depend on companion TCM codes",
            "cost": "$120 - $2500+"
        },
        "P0741": {
            "severity": "Medium-High",
            "drivability": "High cruise RPM, shudder, poor lockup performance",
            "cost": "$180 - $3500+"
        },
        "P0442": {
            "severity": "Low",
            "drivability": "Usually no noticeable symptoms",
            "cost": "$20 - $350+"
        },
        "P0455": {
            "severity": "Low",
            "drivability": "Usually no drivability issue, fuel vapor leak likely",
            "cost": "$20 - $450+"
        },
        "P0456": {
            "severity": "Low",
            "drivability": "Usually no noticeable symptoms",
            "cost": "$20 - $300+"
        },
        "P2195": {
            "severity": "Medium",
            "drivability": "Lean surge, hesitation, poor cold-start fueling",
            "cost": "$120 - $850+"
        }
    }

    return summaries.get(code)

def build_obd_index_groups(query: str = "") -> Tuple[List[Dict[str, Any]], int]:
    if not OBD_SEED_JSON_PATH.exists():
        return [], 0

    try:
        data = json.loads(OBD_SEED_JSON_PATH.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            return [], 0
    except Exception:
        return [], 0

    group_defs = [
        ("p00_p01", "P00xx / P01xx Air, Fuel, Sensors"),
        ("p02", "P02xx Fuel / Injector"),
        ("p03", "P03xx Ignition / Misfire"),
        ("p04", "P04xx Emissions / EVAP / Catalyst"),
        ("p05", "P05xx Idle / Speed / Electrical"),
        ("p06", "P06xx Computer / Output / Communication"),
        ("p07", "P07xx Transmission"),
        ("p1", "P1xxx Manufacturer / Advanced Powertrain"),
        ("c", "Cxxxx Chassis / ABS"),
        ("u", "Uxxxx Network / Communication"),
        ("other", "Other Codes"),
    ]

    def pick_group(code: str) -> str:
        if code.startswith(("P00", "P01")):
            return "p00_p01"
        if code.startswith("P02"):
            return "p02"
        if code.startswith("P03"):
            return "p03"
        if code.startswith("P04"):
            return "p04"
        if code.startswith("P05"):
            return "p05"
        if code.startswith("P06"):
            return "p06"
        if code.startswith("P07"):
            return "p07"
        if code.startswith("P1"):
            return "p1"
        if code.startswith("C"):
            return "c"
        if code.startswith("U"):
            return "u"
        return "other"

    groups_map = {
        group_id: {"id": group_id, "title": title, "items": []}
        for group_id, title in group_defs
    }

    query_norm = str(query or "").strip().lower()
    total_codes = 0

    for raw_code, item in data.items():
        code = "".join(ch for ch in str(raw_code or "").upper() if ch.isalnum())[:7]
        if len(code) < 4:
            continue

        title = str((item or {}).get("title") or (item or {}).get("description") or "").strip()
        if not title:
            continue

        description = str((item or {}).get("description") or "").strip()
        searchable = f"{code} {title} {description}".lower()
        if query_norm and query_norm not in searchable:
            continue

        groups_map[pick_group(code)]["items"].append(
            {
                "code": code,
                "title": title,
                "href": f"/obd/{code.lower()}",
            }
        )
        total_codes += 1

    visible_groups = []
    for group_id, title in group_defs:
        group = groups_map[group_id]
        if not group["items"]:
            continue
        group["items"].sort(key=lambda item: item["code"])
        visible_groups.append(group)

    return visible_groups, total_codes

@app.get("/obd-codes", response_class=HTMLResponse)
async def obd_codes_index(request: Request, q: str = ""):
    obd_code_groups, total_codes = build_obd_index_groups(q)
    return templates.TemplateResponse(
        "obd_codes_index.html",
        {
            "request": request,
            "obd_code_groups": obd_code_groups,
            "total_codes": total_codes,
            "query": str(q or "").strip(),
        },
    )


@app.get("/diagnostics", response_class=HTMLResponse)
async def diagnostics_hub(request: Request):
    repair_guides = load_normalized_repair_guides_map()
    obd_entries = load_diagnostic_entries(repair_guides)
    symptom_entries = load_symptom_entries(repair_guides)
    system_entries = build_vehicle_system_entries(symptom_entries, repair_guides)
    system_hub_entries = load_system_hub_entries()

    return templates.TemplateResponse(
        "diagnostics.html",
        {
            "request": request,
            "obd_entries": obd_entries,
            "symptom_entries": symptom_entries,
            "system_hub_entries": system_hub_entries,
            "system_entries": system_entries,
            "featured_obd_codes": build_featured_obd_codes(),
            "workflow_clusters": build_diagnostics_workflow_clusters(),
            "curated_system_hubs": [
                SYSTEM_HUB_NAV_ITEMS["engine-performance-misfire-diagnostics"],
                SYSTEM_HUB_NAV_ITEMS["emissions-evap-diagnostics"],
                SYSTEM_HUB_NAV_ITEMS["brake-system-repairs"],
                SYSTEM_HUB_NAV_ITEMS["cooling-system-diagnostics"],
                SYSTEM_HUB_NAV_ITEMS["charging-starting-system"],
            ],
            "platform_sections": build_platform_sections("/diagnostics"),
            "page_title": "Diagnostics | TorqueMech",
            "meta_description": "Structured diagnostic entry points for OBD codes, symptoms, and vehicle systems.",
        },
    )

@app.get("/diagnostic-repair", include_in_schema=False)
async def diagnostic_repair_redirect():
    return RedirectResponse("/diagnostics", status_code=301)


@app.get("/symptoms", response_class=HTMLResponse)
async def symptoms_index(request: Request):
    repair_guides = load_normalized_repair_guides_map()
    symptom_entries = load_symptom_entries(repair_guides)
    symptom_pages = []

    for entry in symptom_entries:
        search_terms = " ".join(
            filter(
                None,
                [
                    entry.get("title"),
                    entry.get("summary"),
                    entry.get("system"),
                    " ".join(entry.get("possible_causes") or []),
                    " ".join(entry.get("common_sounds") or []),
                    " ".join(entry.get("diagnostic_paths") or []),
                    " ".join(item.get("code") or "" for item in entry.get("related_obd_codes") or []),
                    " ".join(item.get("title") or "" for item in entry.get("recommended_repairs") or []),
                ],
            )
        )
        symptom_pages.append(
            {
                "slug": entry.get("slug", ""),
                "title": entry.get("title", ""),
                "summary": entry.get("summary", ""),
                "search_terms": search_terms.strip(),
            }
        )

    return templates.TemplateResponse(
        "symptoms_index.html",
        {
            "request": request,
            "symptom_pages": symptom_pages,
            "platform_sections": build_platform_sections("/symptoms"),
            "page_title": "Symptoms | TorqueMech",
            "meta_description": "Search common vehicle symptoms and open the matching TorqueMech symptom guide.",
            "quick_find_items": build_quick_find_items(),
        },
    )

@app.get("/repair-cost/{service_slug}", response_class=HTMLResponse)
def repair_cost_page(
    request: Request,
    service_slug: str,
    year: str = "",
    make: str = "",
    model: str = ""
):

    catalog = load_services_catalog()
    raw = catalog["raw"]

    service_match = None

    for category in raw["categories"]:
        for service in category.get("services", []):
            slug = slugify_service_name(service.get("name", ""))

            if slug == service_slug:
                service_match = service
                break

        if service_match:
            break

    if not service_match:
        raise HTTPException(status_code=404, detail="repair guide not found")

    # Labor calculations
    labor_min = float(service_match.get("labor_hours_min", 0) or 0)
    labor_max = float(service_match.get("labor_hours_max", 0) or 0)

    rate = default_labor_rate()

    labor_low = int(labor_min * rate)
    labor_high = int(labor_max * rate)

    # Load guide data
    guides = load_repair_guides()
    guide = guides.get(service_slug, {})

    return templates.TemplateResponse(
        "repair_cost.html",
        {
            "request": request,
            "service": service_match,
            "labor_min": labor_min,
            "labor_max": labor_max,
            "labor_low": labor_low,
            "labor_high": labor_high,
            "guide": guide,
            "vehicle": {
                "year": year,
                "make": make,
                "model": model
            }
        },
    )

@app.get("/repair-cost", response_class=HTMLResponse)
def repair_cost_index(request: Request):
    metric_incr("page_repair_cost_index")

    catalog = load_services_catalog()
    raw = catalog["raw"]
    category_order = [
        "Brakes",
        "Engine",
        "Cooling",
        "Electrical",
        "Suspension",
        "Maintenance",
    ]
    grouped_repairs: Dict[str, List[Dict[str, Any]]] = {category: [] for category in category_order}
    grouped_repairs["Other"] = []

    for category in raw["categories"]:
        category_name = str(category.get("name", "General Repair")).title()
        for service in category.get("services", []):
            name = service.get("name", "").strip()
            if not name:
                continue

            slug = slugify_service_name(name)

            labor_min = float(service.get("labor_hours_min", 0) or 0)
            labor_max = float(service.get("labor_hours_max", 0) or 0)

            item = {
                "name": name,
                "slug": slug,
                "category": category_name,
                "labor_min": labor_min,
                "labor_max": labor_max,
            }

            if category_name in grouped_repairs:
                grouped_repairs[category_name].append(item)
            else:
                grouped_repairs["Other"].append(item)

    for category_name in grouped_repairs:
        grouped_repairs[category_name].sort(key=lambda item: item["name"].lower())

    cost_groups = [
        {"name": category_name, "repairs": grouped_repairs[category_name]}
        for category_name in category_order
        if grouped_repairs[category_name]
    ]

    if grouped_repairs["Other"]:
        cost_groups.append({"name": "Other", "repairs": grouped_repairs["Other"]})

    return templates.TemplateResponse(
        "repair_cost_index.html",
        {
            "request": request,
            "cost_groups": cost_groups,
            "platform_sections": build_platform_sections("/repair-cost"),
        },
    )

@app.get("/cost/brake-pad-replacement", response_class=HTMLResponse)
def brake_pad_cost(request: Request):
    return templates.TemplateResponse(
        "cost_brake_pad_replacement.html",
        {"request": request},
    )

@app.get("/cost/alternator-replacement", response_class=HTMLResponse)
def alternator_cost(request: Request):
    return templates.TemplateResponse(
        "cost_alternator_replacement.html",
        {"request": request},
    )

@app.get("/cost/radiator-replacement", response_class=HTMLResponse)
def radiator_cost(request: Request):
    return templates.TemplateResponse(
        "cost_radiator_replacement.html",
        {"request": request},
    )

@app.get("/cost/serpentine-belt-replacement", response_class=HTMLResponse)
def serpentine_belt_cost(request: Request):
    return templates.TemplateResponse(
        "cost_serpentine_belt_replacement.html",
        {"request": request},
    )

@app.get("/cost/brake-caliper-replacement", response_class=HTMLResponse)
def brake_caliper_cost(request: Request):
    return templates.TemplateResponse(
        "cost_brake_caliper_replacement.html",
        {"request": request},
    )

@app.get("/cost/ac-compressor-replacement", response_class=HTMLResponse)
def ac_compressor_cost(request: Request):
    return templates.TemplateResponse(
        "cost_ac_compressor_replacement.html",
        {"request": request},
    )

@app.get("/cost/spark-plug-replacement", response_class=HTMLResponse)
def spark_plug_cost(request: Request):
    return templates.TemplateResponse(
        "cost_spark_plug_replacement.html",
        {"request": request},
    )

@app.get("/cost/brake-rotor-replacement", response_class=HTMLResponse)
def brake_rotor_cost(request: Request):
    return templates.TemplateResponse(
        "cost_brake_rotor_replacement.html",
        {"request": request},
    )

@app.get("/cost/starter-replacement", response_class=HTMLResponse)
def starter_cost(request: Request):
    return templates.TemplateResponse(
        "cost_starter_replacement.html",
        {"request": request},
    )

@app.get("/cost/ignition-coil-replacement", response_class=HTMLResponse)
def ignition_coil_cost(request: Request):
    return templates.TemplateResponse(
        "cost_ignition_coil_replacement.html",
        {"request": request},
    )

@app.get("/cost/fuel-injector-replacement", response_class=HTMLResponse)
def fuel_injector_cost(request: Request):
    return templates.TemplateResponse(
        "cost_fuel_injector_replacement.html",
        {"request": request},
    )

@app.get("/cost/thermostat-replacement", response_class=HTMLResponse)
def thermostat_cost(request: Request):
    return templates.TemplateResponse(
        "cost_thermostat_replacement.html",
        {"request": request},
    )

@app.get("/cost/battery-replacement", response_class=HTMLResponse)
def battery_cost(request: Request):
    return templates.TemplateResponse(
        "cost_battery_replacement.html",
        {"request": request},
    )

@app.get("/cost/mass-air-flow-sensor-replacement", response_class=HTMLResponse)
def mass_air_flow_sensor_cost(request: Request):
    return templates.TemplateResponse(
        "cost_mass_air_flow_sensor_replacement.html",
        {"request": request},
    )

@app.get("/cost/catalytic-converter-replacement", response_class=HTMLResponse)
def catalytic_converter_cost(request: Request):
    return templates.TemplateResponse(
        "cost_catalytic_converter_replacement.html",
        {"request": request},
    )

@app.get("/cost/egr-valve-replacement", response_class=HTMLResponse)
def egr_valve_cost(request: Request):
    return templates.TemplateResponse(
        "cost_egr_valve_replacement.html",
        {"request": request},
    )

@app.get("/cost/pcv-valve-replacement", response_class=HTMLResponse)
def pcv_valve_cost(request: Request):
    return templates.TemplateResponse(
        "cost_pcv_valve_replacement.html",
        {"request": request},
    )

@app.get("/cost/intake-manifold-gasket-replacement", response_class=HTMLResponse)
def intake_manifold_gasket_cost(request: Request):
    return templates.TemplateResponse(
        "cost_intake_manifold_gasket_replacement.html",
        {"request": request},
    )

def build_repair_cost_guide_cards():
    return [
        {
            "title": "Brake Pad Replacement Cost",
            "description": "Baseline pricing, labor time, and brake wear symptoms for a common service visit.",
            "href": "/cost/brake-pad-replacement",
        },
        {
            "title": "Alternator Replacement Cost",
            "description": "Charging system cost context for battery warning lights, dim lights, or no-charge complaints.",
            "href": "/cost/alternator-replacement",
        },
        {
            "title": "Radiator Replacement Cost",
            "description": "Cooling-system pricing guidance for radiator leaks, cracked tanks, overheating under load, coolant loss, and pressure-test confirmed replacement.",
            "href": "/cost/radiator-replacement",
        },
        {
            "title": "Serpentine Belt Replacement Cost",
            "description": "Useful when belt noise, cracking, or accessory drive wear points to a simple front-drive repair.",
            "href": "/cost/serpentine-belt-replacement",
        },
        {
            "title": "Brake Caliper Replacement Cost",
            "description": "Pricing context for dragging brakes, uneven pad wear, or sticking caliper issues.",
            "href": "/cost/brake-caliper-replacement",
        },
        {
            "title": "A/C Compressor Replacement Cost",
            "description": "Air conditioning repair range for compressor failure, no-cool complaints, or noisy A/C drive loads.",
            "href": "/cost/ac-compressor-replacement",
        },
        {
            "title": "Spark Plug Replacement Cost",
            "description": "A strong starting point for tune-up pricing, maintenance intervals, and misfire-related repairs.",
            "href": "/cost/spark-plug-replacement",
        },
        {
            "title": "Ignition Coil Replacement Cost",
            "description": "Helpful for misfire diagnosis, rough-running complaints, and coil-related ignition failures.",
            "href": "/cost/ignition-coil-replacement",
        },
        {
            "title": "Fuel Injector Replacement Cost",
            "description": "Fuel-delivery pricing guidance for cylinder-specific misfires, rough-running complaints, and confirmed injector faults.",
            "href": "/cost/fuel-injector-replacement",
        },
        {
            "title": "Brake Rotor Replacement Cost",
            "description": "Brake repair pricing guidance for rotor vibration, pedal pulsation, scoring, heat spots, seized hardware, and pad or caliper overlap.",
            "href": "/cost/brake-rotor-replacement",
        },
        {
            "title": "Starter Replacement Cost",
            "description": "Typical no-crank repair pricing for starter motor faults and related starting complaints.",
            "href": "/cost/starter-replacement",
        },
        {
            "title": "Water Pump Replacement Cost",
            "description": "Cooling system cost guidance when pump leaks, bearing noise, or circulation issues are confirmed.",
            "href": "/cost/water-pump-replacement",
        },
        {
            "title": "Thermostat Replacement Cost",
            "description": "Cooling-system pricing context when the engine runs cold, overheats, or warm-up timing is off.",
            "href": "/cost/thermostat-replacement",
        },
        {
            "title": "Control Arm Replacement Cost",
            "description": "Suspension repair pricing for worn bushings, loose ball joints, and front-end instability.",
            "href": "/cost/control-arm-replacement",
        },
        {
            "title": "Wheel Bearing Replacement Cost",
            "description": "Helpful for humming, growling, or wheel-play complaints tied to hub or bearing wear.",
            "href": "/cost/wheel-bearing-replacement",
        },
        {
            "title": "Sway Bar Link Replacement Cost",
            "description": "Quick cost context for clunking over bumps and basic stabilizer link service.",
            "href": "/cost/sway-bar-link-replacement",
        },
        {
            "title": "Oxygen Sensor Replacement Cost",
            "description": "Useful when diagnosis points to a biased O2 signal, slow response, or emissions-related fault.",
            "href": "/cost/oxygen-sensor-replacement",
        },
        {
            "title": "Mass Air Flow Sensor Replacement Cost",
            "description": "Useful for drivability faults, airflow signal problems, and lean-running issues tied to the MAF sensor.",
            "href": "/cost/mass-air-flow-sensor-replacement",
        },
        {
            "title": "Fuel Pump Replacement Cost",
            "description": "Fuel delivery pricing guidance for hard starts, stalling, low-pressure, or no-start complaints.",
            "href": "/cost/fuel-pump-replacement",
        },
        {
            "title": "Battery Replacement Cost",
            "description": "Quick pricing context for weak batteries, slow cranking, and no-start complaints.",
            "href": "/cost/battery-replacement",
        },
        {
            "title": "Catalytic Converter Replacement Cost",
            "description": "Emissions repair pricing when catalyst-efficiency testing confirms the converter is the failure point.",
            "href": "/cost/catalytic-converter-replacement",
        },
        {
            "title": "Timing Chain Kit Replacement Cost",
            "description": "Placeholder support for timing-chain kit cost guidance when chain stretch, guide wear, startup rattle, or engine timing faults are confirmed.",
            "href": "/repair-cost/timing-chain-kit-replacement",
            "status": "placeholder",
        },
        {
            "title": "Timing Chain Tensioner Replacement Cost",
            "description": "Placeholder support for timing-chain tensioner cost guidance when chain tensioner failure, startup rattle, or timing-chain noise is confirmed.",
            "href": "/repair-cost/timing-chain-tensioner-replacement",
            "status": "placeholder",
        },
        {
            "title": "EGR Valve Replacement Cost",
            "description": "Useful when EGR flow, control, or feedback diagnosis confirms the valve is sticking, restricted, or failing mechanically.",
            "href": "/cost/egr-valve-replacement",
        },
        {
            "title": "EVAP Vent Valve Replacement Cost",
            "description": "Useful when EVAP vent faults, blocked venting, or leak testing point to a stuck, contaminated, or failed vent valve.",
            "href": "/cost/evap-vent-valve-replacement",
        },
        {
            "label": "Engine Coolant Temperature Sensor Replacement Cost",
            "href": "/cost/engine-coolant-temperature-sensor-replacement",
            "description": "Used when temperature readings are inaccurate, causing poor fuel mix or cooling issues."
        }

    ]

@app.get("/repair-costs", response_class=HTMLResponse)
async def repair_costs(request: Request):
    return templates.TemplateResponse(
        "repair_costs.html",
        {
            "request": request,
            "cost_guides": build_repair_cost_guide_cards(),
            "quick_find_items": build_quick_find_items(),
        }
    )

@app.get("/cost/water-pump-replacement", response_class=HTMLResponse)
def water_pump_cost(request: Request):
    return templates.TemplateResponse(
        "cost_water_pump_replacement.html",
        {"request": request},
    )

@app.get("/cost/control-arm-replacement", response_class=HTMLResponse)
def control_arm_cost(request: Request):
    return templates.TemplateResponse(
        "cost_control_arm_replacement.html",
        {"request": request},
    )

@app.get("/cost/wheel-bearing-replacement", response_class=HTMLResponse)
def wheel_bearing_cost(request: Request):
    return templates.TemplateResponse(
        "cost_wheel_bearing_replacement.html",
        {"request": request},
    )

@app.get("/cost/sway-bar-link-replacement", response_class=HTMLResponse)
def sway_bar_link_cost(request: Request):
    return templates.TemplateResponse(
        "cost_sway_bar_link_replacement.html",
        {"request": request},
    )

@app.get("/cost/oxygen-sensor-replacement", response_class=HTMLResponse)
def oxygen_sensor_cost(request: Request):
    return templates.TemplateResponse(
        "cost_oxygen_sensor_replacement.html",
        {"request": request},
    )

@app.get("/cost/fuel-pump-replacement", response_class=HTMLResponse)
def fuel_pump_cost(request: Request):
    return templates.TemplateResponse(
        "cost_fuel_pump_replacement.html",
        {"request": request},
    )

@app.get("/cost/engine-coolant-temperature-sensor-replacement")
def cost_ect_sensor(request: Request):
    return templates.TemplateResponse(
        "cost_engine_coolant_temperature_sensor_replacement.html",
        {"request": request}
    )

@app.get("/repair-guides", response_class=HTMLResponse)
async def repair_guides_index(request: Request):
    guides = load_normalized_repair_guides_map()
    category_order = [
        "Brakes",
        "Engine",
        "Cooling",
        "Suspension",
        "Electrical",
        "Maintenance",
    ]

    grouped_guides = {category: [] for category in category_order}
    grouped_guides["Other"] = []

    for slug, guide in guides.items():
        category = str(guide.get("category") or "Other").title()
        if category == "Steering And Suspension":
            category = "Suspension"
        title = guide.get("title", slug.replace("-", " ").title())
        summary = guide.get("summary", "")
        estimate = guide.get("estimate") or {}
        related_obd_codes = [
            " ".join(
                filter(
                    None,
                    [
                        str(code.get("code") or "").strip(),
                        str(code.get("title") or "").strip(),
                    ],
                )
            )
            for code in guide.get("related_obd_codes") or []
            if isinstance(code, dict)
        ]
        related_symptoms = [
            " ".join(
                filter(
                    None,
                    [
                        str(item.get("title") or "").strip(),
                        str(item.get("description") or "").strip(),
                    ],
                )
            )
            for item in guide.get("related_symptoms") or []
            if isinstance(item, dict)
        ]
        recommended_repairs = [
            " ".join(
                filter(
                    None,
                    [
                        str(item.get("title") or "").strip(),
                        str(item.get("description") or "").strip(),
                    ],
                )
            )
            for item in (
                list(guide.get("recommended_repairs") or [])
                + list(guide.get("recommended_while_replacing") or [])
                + list(guide.get("bundled_repair_suggestions") or [])
            )
            if isinstance(item, dict)
        ]

        item = {
            "slug": slug,
            "title": title,
            "summary": summary,
            "difficulty": guide.get("difficulty", ""),
            "sort_order": guide.get("sort_order", 999),
            "subcategory": guide.get("subcategory", ""),
            "keywords": guide.get("keywords") or [],
            "search_terms": guide.get("search_terms") or [],
            "tags": guide.get("tags") or [],
            "symptoms": guide.get("symptoms") or [],
            "related_systems": guide.get("related_systems") or [],
            "related_symptoms": related_symptoms,
            "related_obd_codes": related_obd_codes,
            "recommended_repairs": recommended_repairs,
            "service_name": estimate.get("service_name") if isinstance(estimate, dict) else "",
            "service_code": estimate.get("service_code") if isinstance(estimate, dict) else "",
        }

        if category in grouped_guides:
            grouped_guides[category].append(item)
        else:
            grouped_guides["Other"].append(item)

    for category in grouped_guides:
        grouped_guides[category].sort(
            key=lambda g: (g.get("sort_order", 999), g["title"].lower())
        )

    visible_groups = [
        {"name": category, "guides": grouped_guides[category]}
        for category in category_order
        if grouped_guides[category]
    ]

    if grouped_guides["Other"]:
        visible_groups.append({"name": "Other", "guides": grouped_guides["Other"]})

    return templates.TemplateResponse(
        "repair_guides_index.html",
        {
            "request": request,
            "guide_groups": visible_groups,
            "platform_sections": build_platform_sections("/repair-guides"),
        },
    )

@app.get("/robots.txt", response_class=PlainTextResponse)
def robots_txt():
    return """User-agent: *
Allow: /

Sitemap: https://torquemech.com/sitemap.xml
"""

@app.get("/knowledge", response_class=HTMLResponse)
def knowledge_hub(request: Request):
    metric_incr("page_knowledge_hub")

    sections = [
        {
            "title": "OBD Code Guides",
            "href": "/obd",
            "summary": "Look up diagnostic trouble codes, causes, quick checks, and related repairs.",
        },
        {
            "title": "Repair Cost Guides",
            "href": "/repair-cost",
            "summary": "Browse repair labor times and estimated labor cost ranges powered by TorqueMech’s service database.",
        },
        {
            "title": "Diagnostic Guides",
            "href": "/symptoms",
            "summary": "Quick diagnostic references for common vehicle problems.",
        },
    ]

    return templates.TemplateResponse(
        "knowledge_hub.html",
        {
            "request": request,
            "sections": sections,
        },
    )
# ---------------------------------
# SITEMAP 
# ---------------------------------

from fastapi.responses import Response

SITEMAP_STATIC_PATHS = [
    "/",
    "/estimator",
    "/diagnostics",
    "/symptoms",
    "/obd",
    "/obd-codes",
    "/repair-guides",
    "/repair-costs",
    "/parts-center",
    "/cost",
    "/about",
    "/privacy",
    "/terms",
    "/disclaimer",
]

SITEMAP_OBD_RANGE_PATHS = [
    "/obd/p00xx",
    "/obd/p01xx",
    "/obd/p02xx",
    "/obd/p03xx",
    "/obd/p04xx",
    "/obd/p05xx",
    "/obd/p08xx",
    "/obd/p09xx",
]

def build_sitemap_obd_detail_paths() -> List[str]:
    if not OBD_SEED_JSON_PATH.exists():
        return []

    try:
        data = json.loads(OBD_SEED_JSON_PATH.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            return []
    except Exception:
        return []

    paths: List[str] = []
    for raw_code in sorted(data):
        code = "".join(ch for ch in str(raw_code or "").upper() if ch.isalnum())[:7]
        if len(code) < 4:
            continue
        paths.append(f"/obd/{code.lower()}")

    return paths


def build_sitemap_symptom_paths() -> List[str]:
    symptoms_dir = DATA_DIR / "symptoms"
    if not symptoms_dir.exists():
        return []

    return [
        f"/symptoms/{file.stem.replace('_', '-')}"
        for file in sorted(symptoms_dir.glob("*.json"))
    ]


def build_sitemap_repair_guide_paths() -> List[str]:
    repair_guides_dir = DATA_DIR / "repair_guides"
    if not repair_guides_dir.exists():
        return []

    return [
        f"/repair-guides/{file.stem.replace('_', '-')}"
        for file in sorted(repair_guides_dir.glob("*.json"))
    ]


def build_sitemap_system_hub_paths() -> List[str]:
    system_hubs_dir = DATA_DIR / "system_hubs"
    if not system_hubs_dir.exists():
        return []

    return [
        f"/repair-systems/{file.stem.replace('_', '-')}"
        for file in sorted(system_hubs_dir.glob("*.json"))
    ]

def latest_lastmod_for_files(files: List[Path]) -> Optional[str]:
    timestamps: List[float] = []

    for file_path in files:
        try:
            if file_path.exists():
                timestamps.append(file_path.stat().st_mtime)
        except OSError:
            continue

    if not timestamps:
        return None

    return datetime.fromtimestamp(max(timestamps)).date().isoformat()

def build_sitemap_lastmods() -> Dict[str, str]:
    lastmods: Dict[str, str] = {}
    shared_cost_sources = [BASE_DIR / "main.py"]
    shared_obd_sources = [
        BASE_DIR / "main.py",
        BASE_DIR / "data" / "obd_codes.json",
        BASE_DIR / "repair_paths.py",
        TEMPLATES_DIR / "obd_code_detail.html",
    ]
    shared_symptom_sources = [BASE_DIR / "main.py", TEMPLATES_DIR / "symptom_page.html"]
    symptom_files = sorted((BASE_DIR / "data" / "symptoms").glob("*.json"))
    shared_repair_guide_sources = [BASE_DIR / "main.py", TEMPLATES_DIR / "repair_guide.html"]
    repair_guide_files = sorted((BASE_DIR / "data" / "repair_guides").glob("*.json"))
    shared_system_hub_sources = [BASE_DIR / "main.py", TEMPLATES_DIR / "repair_system_hub.html"]
    system_hub_files = sorted((BASE_DIR / "data" / "system_hubs").glob("*.json"))

    lastmod_sources: Dict[str, List[Path]] = {
        "/": [BASE_DIR / "main.py", TEMPLATES_DIR / "home.html"],
        "/repair-costs": [BASE_DIR / "main.py", TEMPLATES_DIR / "repair_costs.html"],
        "/obd": [BASE_DIR / "main.py", TEMPLATES_DIR / "obd_index.html", BASE_DIR / "data" / "obd_codes.json"],
        "/obd-codes": [BASE_DIR / "main.py", TEMPLATES_DIR / "obd_codes_index.html", BASE_DIR / "data" / "obd_codes.json"],
        "/symptoms": [BASE_DIR / "main.py", TEMPLATES_DIR / "symptoms_index.html", *symptom_files],
        "/repair-guides": [BASE_DIR / "main.py", TEMPLATES_DIR / "repair_guides_index.html", *repair_guide_files],
        "/diagnostics": [BASE_DIR / "main.py", TEMPLATES_DIR / "diagnostics.html", *system_hub_files],
    }

    shared_obd_index_sources = [
        BASE_DIR / "main.py",
        TEMPLATES_DIR / "obd_index.html",
        BASE_DIR / "data" / "obd_codes.json",
    ]

    for guide in build_repair_cost_guide_cards():
        href = str(guide.get("href") or "").strip()
        if not href.startswith("/cost/"):
            continue
        slug = href.removeprefix("/cost/")
        template_name = f"cost_{slug.replace('-', '_')}.html"
        lastmod_sources[href] = [*shared_cost_sources, TEMPLATES_DIR / template_name]

    for path in build_sitemap_obd_detail_paths():
        lastmod_sources[path] = list(shared_obd_sources)

    for file in symptom_files:
        path = f"/symptoms/{file.stem.replace('_', '-')}"
        lastmod_sources[path] = [*shared_symptom_sources, file]

    for file in repair_guide_files:
        path = f"/repair-guides/{file.stem.replace('_', '-')}"
        lastmod_sources[path] = [*shared_repair_guide_sources, file]

    for file in system_hub_files:
        path = f"/repair-systems/{file.stem.replace('_', '-')}"
        lastmod_sources[path] = [*shared_system_hub_sources, file]

    for path in SITEMAP_OBD_RANGE_PATHS:
        lastmod_sources[path] = list(shared_obd_index_sources)

    for path, source_files in lastmod_sources.items():
        lastmod = latest_lastmod_for_files(source_files)
        if lastmod:
            lastmods[path] = lastmod

    return lastmods

def sitemap_priority_for_path(path: str) -> float:
    normalized = str(path or "").strip()

    if normalized == "/":
        return 1.0

    if normalized == "/obd":
        return 0.9

    if normalized in SITEMAP_OBD_RANGE_PATHS:
        return 0.8

    if normalized.startswith("/cost/"):
        return 0.7

    if normalized.startswith("/obd/"):
        return 0.6

    if normalized.startswith("/symptoms/"):
        return 0.6

    if normalized.startswith("/repair-guides/"):
        return 0.6

    if normalized.startswith("/repair-systems/"):
        return 0.6

    return 0.5

@app.get("/sitemap.xml", response_class=Response)
def sitemap():
    base_url = "https://torquemech.com"
    paths = [
        *SITEMAP_STATIC_PATHS,
        *SITEMAP_OBD_RANGE_PATHS,
        *[item["href"] for item in build_repair_cost_guide_cards() if item.get("href")],
        *build_sitemap_obd_detail_paths(),
        *build_sitemap_symptom_paths(),
        *build_sitemap_repair_guide_paths(),
        *build_sitemap_system_hub_paths(),
    ]
    seen_paths: set[str] = set()
    unique_paths: List[str] = []
    for path in paths:
        if path in seen_paths:
            continue
        seen_paths.add(path)
        unique_paths.append(path)

    lastmods = build_sitemap_lastmods()
    urls = []
    for path in unique_paths:
        parts = [f"<loc>{base_url}{path}</loc>"]
        if path in lastmods:
            parts.append(f"<lastmod>{lastmods[path]}</lastmod>")
        parts.append(f"<priority>{sitemap_priority_for_path(path):.1f}</priority>")
        urls.append(f"<url>{''.join(parts)}</url>")

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{''.join(urls)}
</urlset>
"""

    return Response(content=xml, media_type="application/xml")

# ============================================================
# Utility Routes
# ============================================================

@app.get("/health")
def health():
    return {"ok": True, "service": "torquemech"}

@app.get("/admin/metrics")
def admin_metrics(key: str):
    if key != ADMIN_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

    conn = app_db_conn(row_factory=True)
    cur = conn.cursor()
    cur.execute("SELECT name, value, updated_at FROM metrics ORDER BY name")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    return {"metrics": rows}

@app.get("/.well-known/appspecific/com.chrome.devtools.json")
def devtools_json():
    return Response(status_code=204)


@app.get("/favicon.ico")
def favicon():
    return FileResponse(
        str(STATIC_DIR / "favicon.ico"),
        media_type="image/x-icon",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/favicon-48.png")
def favicon_48():
    return FileResponse(str(STATIC_DIR / "favicon-48.png"), media_type="image/png")


@app.get("/icon-192.png")
def icon_192():
    return FileResponse(str(STATIC_DIR / "icon-192.png"), media_type="image/png")


@app.get("/icon-512.png")
def icon_512():
    return FileResponse(str(STATIC_DIR / "icon-512.png"), media_type="image/png")


# ✅ Clean legal routes
FEEDBACK_URL = "https://docs.google.com/forms/d/e/1FAIpQLScqx74MW1pDdyA-I7GHL1vo5TyS6iaQ3QhJogQtkXvfjiaBrA/viewform?usp=sf_link"

def make_qr_image_reader(url: str) -> ImageReader:
    qr = qrcode.QRCode(box_size=8, border=1)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    bio = io.BytesIO()
    img.save(bio, format="PNG")
    bio.seek(0)
    return ImageReader(bio)

# ===============================
# CONFIG
# ===============================
POPULAR_MAKES: List[str] = [
    "ACURA",
    "AUDI",
    "BMW",
    "BUICK",
    "CADILLAC",
    "CHEVROLET",
    "CHRYSLER",
    "DODGE",
    "FORD",
    "GMC",
    "HONDA",
    "HYUNDAI",
    "INFINITI",
    "JEEP",
    "KIA",
    "LEXUS",
    "MAZDA",
    "MERCEDES-BENZ",
    "MINI",
    "NISSAN",
    "PORSCHE",
    "RAM",
    "SUBARU",
    "TESLA",
    "TOYOTA",
    "VOLKSWAGEN",
    "VOLVO",
]

# ===============================
# NHTSA vPIC
# ===============================
VPIC_BASE = "https://vpic.nhtsa.dot.gov/api/vehicles"
VPIC_TIMEOUT_S = 12.0

_models_cache: Dict[str, Tuple[float, List[str]]] = {}

def _cache_key(make_upper: str, year: Optional[int] = None) -> str:
    return f"{make_upper}::{year or 'all'}"

def _cache_get(make_upper: str, year: Optional[int] = None) -> Optional[List[str]]:
    key = _cache_key(make_upper, year)
    item = _models_cache.get(key)
    if not item:
        return None
    expires, models = item
    if time.time() > expires:
        _models_cache.pop(key, None)
        return None
    return models

def _cache_set(make_upper: str, models: List[str], year: Optional[int] = None) -> None:
    key = _cache_key(make_upper, year)
    _models_cache[key] = (time.time() + MODELS_TTL_SECONDS, models)


def fetch_models_for_make(make: str) -> list[str]:
    data = get_json(f"{BASE}/GetModelsForMake/{make}?format=json")

    allowed_vehicle_types = {
        "PASSENGER CAR",
        "MULTIPURPOSE PASSENGER VEHICLE",
        "TRUCK",
        "BUS",
        "INCOMPLETE VEHICLE"
    }

    models = set()

    for row in data.get("Results", []):
        vehicle_type = (row.get("VehicleTypeName") or "").upper()
        model = normalize_model(row.get("Model_Name", ""))

        if vehicle_type in allowed_vehicle_types and model:
            models.add(model)

        # remove junk manufacturers with only 1 model
    if len(models) < 2:
        return []

    return sorted(models)

# ===============================
# DB
# ===============================

def init_metrics_db() -> None:
    conn = app_db_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS metrics (
            name TEXT PRIMARY KEY,
            value INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def metric_incr(name: str, delta: int = 1) -> None:
    now = datetime.utcnow().isoformat()
    conn = app_db_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS metrics (
            name TEXT PRIMARY KEY,
            value INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )
    """)
    cur.execute("""
        INSERT INTO metrics(name, value, updated_at)
        VALUES(?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            value = value + ?,
            updated_at = ?
    """, (name, delta, now, delta, now))
    conn.commit()
    conn.close()

def db_conn() -> sqlite3.Connection:
    return app_db_conn(row_factory=True)


def init_db() -> None:
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS feedback (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          created_at TEXT NOT NULL,
          is_read INTEGER NOT NULL DEFAULT 0,
          payload_json TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()

def get_admin_meta() -> dict:
    if not OBD_ADMIN_META_PATH.exists():
        return {"last_viewed": None}
    return json.loads(OBD_ADMIN_META_PATH.read_text())

def set_admin_last_viewed() -> None:
    data = {"last_viewed": datetime.utcnow().isoformat()}
    OBD_ADMIN_META_PATH.write_text(json.dumps(data))

# ===============================
# Email Feedback Sender
# ===============================

from datetime import datetime
import html

def send_feedback_email(payload: dict, *, feedback_id: int | None = None, created_at: str | None = None, ip: str = "", user_agent: str = ""):
    """
    Sends an email notification if SMTP env vars are configured.
    Safe for Beta: if SMTP isn't configured, it just returns without crashing.
    """
    try:
        if not (SMTP_SERVER and SMTP_PORT and SMTP_USER and SMTP_PASS and FEEDBACK_EMAIL):
            return

        name = (payload.get("name") or "").strip() or "Anonymous"
        email = (payload.get("email") or "").strip() or "Not provided"
        message = (payload.get("message") or "").strip() or "(empty)"

        created_at = created_at or datetime.utcnow().isoformat()

        subject = f"TorqueMech Feedback #{feedback_id}" if feedback_id else "TorqueMech Feedback Received"

        body = (
            f"TorqueMech Feedback\n\n"
            f"ID: {feedback_id or 'N/A'}\n"
            f"Submitted (UTC): {created_at}\n"
            f"Name: {name}\n"
            f"Email: {email}\n"
            f"IP: {ip}\n\n"
            f"Message:\n"
            f"{message}\n\n"
            f"User-Agent:\n{user_agent}\n"
        )

        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = SMTP_USER
        msg["To"] = FEEDBACK_EMAIL

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)

    except Exception:
        logging.exception("Feedback email failed")

# ===============================
# SERVICES CATALOG CACHE
# ===============================
_services_cache: Optional[Dict[str, Any]] = None
_services_mtime: Optional[float] = None


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Invalid JSON in {path.name}: {e}")
    
def load_services_catalog() -> Dict[str, Any]:
    global _services_cache, _services_mtime

    if not SERVICES_CATALOG_PATH.exists():
        raise HTTPException(status_code=500, detail="Missing services_catalog.json at project root.")

    mtime = SERVICES_CATALOG_PATH.stat().st_mtime
    if _services_cache is not None and _services_mtime == mtime:
        return _services_cache

    data = _read_json(SERVICES_CATALOG_PATH)
    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail="services_catalog.json must be a JSON object.")

    if "categories" not in data or not isinstance(data["categories"], list):
        raise HTTPException(status_code=500, detail="services_catalog.json must include: { categories: [...] }")

    services_lookup = {}

    for category in data.get("categories", []):
        category_key = category.get("key", "default")

        for service in category.get("services", []):
            service_code = service.get("code")
            if service_code in services_lookup:
                logging.error("Duplicate service code in services_catalog.json: %s", service_code)
                raise HTTPException(
                    status_code=500,
                    detail=f"Duplicate service code in services_catalog.json: {service_code}",
                )
            service["category"] = category_key
            services_lookup[service_code] = service

    _services_cache = {
        "raw": data,
        "lookup": services_lookup,
    }
    _services_mtime = mtime
    return _services_cache

def find_service_by_code(service_code: str) -> Optional[Dict[str, Any]]:
    catalog = load_services_catalog()
    code = (service_code or "").strip()
    if not code:
        return None
    return catalog["lookup"].get(code)
   
def default_labor_rate() -> float:
    catalog = load_services_catalog()
    raw = catalog["raw"]
    return float(raw.get("default_labor_rate") or raw.get("labor_rate") or 90)


def zip_multiplier(zip_code: str) -> float:
    z = (zip_code or "").strip()[:5]
    if len(z) == 5 and z.isdigit():
        if z.startswith("9"):
            return 1.10
        if z.startswith(("0", "1")):
            return 1.08
    return 1.00


def year_multiplier(year: int) -> float:
    if year <= 2005:
        return 1.08
    if year >= 2020:
        return 1.05
    return 1.00


def wrap_text(text: str, max_chars: int = 95) -> List[str]:
    words = (text or "").split()
    lines: List[str] = []
    cur: List[str] = []
    cur_len = 0
    for w in words:
        add_len = len(w) + (1 if cur else 0)
        if cur_len + add_len > max_chars:
            lines.append(" ".join(cur))
            cur = [w]
            cur_len = len(w)
        else:
            cur.append(w)
            cur_len += add_len
    if cur:
        lines.append(" ".join(cur))
    return lines


# ===============================
# MODELS
# ===============================
class EstimateRequest(BaseModel):
    year: int = Field(..., ge=1970, le=2035)
    make: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    displayModel: Optional[str] = None

    category: Optional[str] = None
    serviceCode: Optional[str] = None
    service: Optional[str] = None

    laborHours: float = Field(0, ge=0)
    partsPrice: float = Field(0, ge=0)
    laborRate: Optional[float] = Field(None, ge=0)

    notes: Optional[str] = None
    customerName: Optional[str] = None
    customerPhone: Optional[str] = None
    source: Optional[str] = None
    customerId: Optional[str] = None
    vehicleId: Optional[str] = None
    findingId: Optional[str] = None
    problemFound: Optional[str] = None
    recommendedRepair: Optional[str] = None
    sourceContext: Optional[Dict[str, Any]] = None

    customerAgrees: bool = False
    zip: Optional[str] = Field(default="00000", min_length=5, max_length=10)
    signatureDataUrl: Optional[str] = None
    showGeneratedDate: bool = True
    showHourlyRate: bool = False
    showLaborColumn: bool = False
    showPartsColumn: bool = False
    showDetailedLaborBreakdown: bool = False


class EstimateResponse(BaseModel):
    estimate: int
    currency: str = "USD"
    service_name: str
    breakdown: Dict[str, Any]
    labor_breakdown: Optional[Dict[str, Any]] = None


def customer_vehicle_model(model: str, display_model: Optional[str] = None) -> str:
    visible_model = (display_model or "").strip()
    return visible_model or (model or "").strip()


def customer_vehicle_line(year: Any, make: str, model: str, display_model: Optional[str] = None) -> str:
    return f"{year} {make} {customer_vehicle_model(model, display_model)}".strip()


def estimator_service_keyword_from_query(query: Any) -> str:
    for key in ("service_name", "serviceText", "service_text", "selected_service", "selectedService"):
        value = str(query.get(key) or "").strip()
        if value:
            return value
    service_value = str(query.get("service") or query.get("serviceCode") or query.get("service_code") or "").strip()
    if service_value:
        service_meta = find_service_by_code(service_value)
        if service_meta:
            return str(service_meta.get("name") or service_value).strip()
        return service_value.replace("_", " ").strip()
    return ""


def estimator_repair_keyword_from_query(query: Any) -> str:
    return (
        estimator_service_keyword_from_query(query)
        or str(query.get("recommended_repair") or query.get("recommendedRepair") or "").strip()
        or str(query.get("problem_found") or query.get("problemFound") or "").strip()
    )


def estimator_vehicle_from_query(query: Any) -> Dict[str, str]:
    return {
        "year": str(query.get("year") or "").strip(),
        "make": str(query.get("make") or "").strip(),
        "model": str(query.get("displayModel") or query.get("display_model") or query.get("model") or "").strip(),
        "engine": str(query.get("engine") or "").strip(),
    }

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===============================
# ROOT + PWA
# ===============================

@app.get("/manifest.webmanifest")
def manifest() -> FileResponse:
    p = STATIC_DIR / "manifest.webmanifest"
    if not p.exists():
        raise HTTPException(status_code=500, detail="Missing static/manifest.webmanifest")
    return FileResponse(str(p), media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


@app.get("/sw.js")
def service_worker() -> FileResponse:
    p = STATIC_DIR / "sw.js"
    if not p.exists():
        raise HTTPException(status_code=500, detail="Missing static/sw.js")
    return FileResponse(str(p), media_type="application/javascript", headers={"Cache-Control": "no-cache"})

VEHICLE_CATALOG_PATH = BASE_DIR / "data" / "vehicle_catalog.json"
VEHICLE_MODEL_YEAR_OVERRIDES_PATH = BASE_DIR / "data" / "vehicle_model_year_overrides.json"

_vehicle_catalog_cache: Optional[Dict[str, List[str]]] = None
_vehicle_catalog_mtime: Optional[float] = None
_vehicle_model_year_overrides_cache: Optional[Dict[int, Dict[str, List[str]]]] = None
_vehicle_model_year_overrides_mtime: Optional[float] = None

def load_vehicle_catalog() -> Dict[str, List[str]]:
    global _vehicle_catalog_cache, _vehicle_catalog_mtime

    if not VEHICLE_CATALOG_PATH.exists():
        raise HTTPException(status_code=500, detail="Missing data/vehicle_catalog.json")

    mtime = VEHICLE_CATALOG_PATH.stat().st_mtime
    if _vehicle_catalog_cache is not None and _vehicle_catalog_mtime == mtime:
        return _vehicle_catalog_cache

    data = json.loads(VEHICLE_CATALOG_PATH.read_text(encoding="utf-8-sig"))

    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail="vehicle_catalog.json must be an object")

    cleaned: Dict[str, List[str]] = {}

    for make, models in data.items():
        make_key = str(make).strip().upper()
        if not make_key:
            continue

        if not isinstance(models, list):
            continue

        seen = set()
        cleaned_models: List[str] = []

        for model in models:
            model_name = str(model).strip().upper()
            if not model_name or model_name in seen:
                continue
            seen.add(model_name)
            cleaned_models.append(model_name)

        cleaned_models.sort()
        cleaned[make_key] = cleaned_models

    _vehicle_catalog_cache = cleaned
    _vehicle_catalog_mtime = mtime
    return cleaned


def normalize_vehicle_model_key(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def load_vehicle_model_year_overrides() -> Dict[int, Dict[str, List[str]]]:
    global _vehicle_model_year_overrides_cache, _vehicle_model_year_overrides_mtime

    if not VEHICLE_MODEL_YEAR_OVERRIDES_PATH.exists():
        return {}

    mtime = VEHICLE_MODEL_YEAR_OVERRIDES_PATH.stat().st_mtime
    if (
        _vehicle_model_year_overrides_cache is not None
        and _vehicle_model_year_overrides_mtime == mtime
    ):
        return _vehicle_model_year_overrides_cache

    data = json.loads(VEHICLE_MODEL_YEAR_OVERRIDES_PATH.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail="vehicle_model_year_overrides.json must be an object")

    cleaned: Dict[int, Dict[str, List[str]]] = {}
    for year_value, makes in data.items():
        try:
            year_key = int(year_value)
        except (TypeError, ValueError):
            continue

        if not isinstance(makes, dict):
            continue

        cleaned[year_key] = {}
        for make, models in makes.items():
            make_key = str(make or "").strip().upper()
            if not make_key or not isinstance(models, list):
                continue

            seen = set()
            cleaned_models: List[str] = []
            for model in models:
                model_name = str(model or "").strip().upper()
                model_key = normalize_vehicle_model_key(model_name)
                if not model_name or not model_key or model_key in seen:
                    continue
                seen.add(model_key)
                cleaned_models.append(model_name)

            cleaned_models.sort()
            cleaned[year_key][make_key] = cleaned_models

    _vehicle_model_year_overrides_cache = cleaned
    _vehicle_model_year_overrides_mtime = mtime
    return cleaned


def get_models_for_make_year(make: str, year: Optional[int] = None) -> List[str]:
    catalog = load_vehicle_catalog()
    make_key = str(make or "").strip().upper()

    if make_key not in catalog:
        raise HTTPException(status_code=404, detail=f"Make '{make}' not supported")

    if year:
        year_models = load_vehicle_model_year_overrides().get(int(year), {}).get(make_key)
        if year_models:
            return year_models

    return catalog[make_key]


def resolve_valid_vehicle_model(year: int, make: str, model: str) -> Optional[str]:
    model_key = normalize_vehicle_model_key(model)
    if not model_key:
        return None

    allowed_models = get_models_for_make_year(make, year)
    for allowed_model in allowed_models:
        if normalize_vehicle_model_key(allowed_model) == model_key:
            return allowed_model

    return None


def normalize_vin_input(vin: str) -> str:
    return re.sub(r"\s+", "", str(vin or "")).upper()


def resolve_decoded_vehicle_model(year: int, make: str, row: Dict[str, Any]) -> Optional[str]:
    raw_model = str(row.get("Model") or "").strip()
    raw_trim = str(row.get("Trim") or "").strip()
    class_match = re.match(r"^([A-Z])\s*-?\s*CLASS$", raw_model.upper())
    class_trim_candidate = (
        f"{class_match.group(1)}{raw_trim}"
        if class_match and re.fullmatch(r"\d{2,4}", raw_trim)
        else ""
    )
    candidates = [
        row.get("Model"),
        row.get("Series"),
        class_trim_candidate,
        row.get("Trim"),
        row.get("Series2"),
        " ".join(str(part or "").strip() for part in [row.get("Model"), row.get("Series")] if str(part or "").strip()),
        " ".join(str(part or "").strip() for part in [row.get("Model"), row.get("Trim")] if str(part or "").strip()),
    ]
    seen = set()
    for candidate in candidates:
        candidate_key = normalize_vehicle_model_key(str(candidate or ""))
        if not candidate_key or candidate_key in seen:
            continue
        seen.add(candidate_key)
        resolved = resolve_valid_vehicle_model(year, make, str(candidate))
        if resolved:
            return resolved
    return None

# ===============================
# MAKES / MODELS API
# ===============================
import httpx

@app.get("/api/makes")
def get_makes() -> List[str]:
    catalog = load_vehicle_catalog()
    return sorted(catalog.keys())


@app.get("/api/models/{make}")
def get_models(make: str, year: Optional[int] = None) -> List[str]:
    return get_models_for_make_year(make, year)


@app.get("/api/repair-guides/{slug}/torque-specs")
def get_repair_guide_torque_specs_api(
    slug: str,
    year: str = Query(""),
    make: str = Query(""),
    model: str = Query(""),
) -> Dict[str, Dict[str, str]]:
    specs = get_repair_guide_vehicle_torque_specs(slug, year, make, model)
    return {"specs": specs}

# ===============================
# SERVICES API
# ===============================
@app.get("/api/categories")
def get_categories() -> List[Dict[str, str]]:
    catalog = load_services_catalog()
    raw = catalog["raw"]
    return [{"key": c.get("key", ""), "name": c.get("name", "")} for c in raw["categories"]]


@app.get("/api/services/{category_key}")
def get_services(category_key: str) -> List[Dict[str, Any]]:
    catalog = load_services_catalog()
    raw = catalog["raw"]
    ck = (category_key or "").strip()
    for c in raw["categories"]:
        if c.get("key") == ck:
            return c.get("services", [])
    raise HTTPException(status_code=404, detail=f"Category '{category_key}' not found")


@app.get("/api/service/{service_code}")
def get_service(service_code: str):
    catalog = load_services_catalog()
    service = catalog["lookup"].get(service_code)

    if not service:
        raise HTTPException(status_code=404, detail="Service not found")

    # If missing labor data → use category defaults
    if not service.get("labor_hours_min") or not service.get("labor_hours_max"):
        category = service.get("category", "default")
        default_min, default_max = DEFAULT_LABOR_RANGES.get(
            category,
            DEFAULT_LABOR_RANGES["default"]
        )

        service["labor_hours_min"] = default_min
        service["labor_hours_max"] = default_max

    return service


# ===============================
# FEEDBACK API (selection-only)
# ===============================
@app.post("/api/feedback")
def submit_feedback(request: Request, payload: Dict[str, Any] = Body(...)) -> JSONResponse:

    try:
        created_at = datetime.utcnow().isoformat()

        conn = db_conn()
        cur = conn.cursor()

        cur.execute(
            "INSERT INTO feedback (created_at, is_read, payload_json) VALUES (?, 0, ?)",
            (created_at, json.dumps(payload, ensure_ascii=False)),
        )

        conn.commit()
        new_id = cur.lastrowid
        conn.close()

        send_feedback_email(
            payload,
            feedback_id=new_id,
            created_at=created_at,
            ip=request.client.host if request and request.client else "",
            user_agent=request.headers.get("user-agent", "") if request else "",
        )

        return JSONResponse({"ok": True, "id": new_id})

    except Exception as e:
        logging.exception("FEEDBACK_FAILED")
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/vin/{vin}")
async def decode_vin(vin: str):
    vin = normalize_vin_input(vin)
    if len(vin) != 17:
        raise HTTPException(status_code=400, detail="VIN must be 17 characters")

    url = f"https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValuesExtended/{vin}?format=json"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="VIN decoder unavailable")

    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="VIN decoder unavailable")

    data = r.json()
    results = (data or {}).get("Results") or []
    if not results:
        raise HTTPException(status_code=404, detail="VIN not found")

    row = results[0]
    year = row.get("ModelYear")
    make = row.get("Make")
    model = row.get("Model")

    if not (year and make and model):
        # Some VINs return partial data; treat as failure for beta
        raise HTTPException(status_code=404, detail="VIN decoded but missing year/make/model")

    resolved_model = resolve_decoded_vehicle_model(int(year), make, row)
    if not resolved_model:
        raise HTTPException(status_code=404, detail="VIN decoded, but model is not supported by this estimator")

    engine = row.get("DisplacementL") or row.get("EngineModel") or row.get("EngineCylinders")
    trim = row.get("Trim") or row.get("Series") or row.get("Series2")

    return {
        "year": int(year),
        "make": make.title(),
        "model": resolved_model,
        "engine": str(engine).strip() if engine else "",
        "trim": str(trim).strip() if trim else "",
    }
def normalize_service_key(value: str) -> str:
    return (
        (value or "")
        .strip()
        .lower()
        .replace("&", "and")
        .replace("/", "_")
        .replace("-", "_")
        .replace(" ", "_")
    )

# ===============================
# ESTIMATE
# ===============================
@app.post("/estimate", response_model=EstimateResponse)
async def estimate(req: EstimateRequest) -> EstimateResponse:
    metric_incr("estimate_requests")
    make_key = (req.make or "").strip().upper()
    catalog = load_vehicle_catalog()
    if make_key not in catalog:
        raise HTTPException(status_code=400, detail="Invalid make")

    model = (req.model or "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="Model is required")

    resolved_model = resolve_valid_vehicle_model(req.year, req.make, model)
    if not resolved_model:
        raise HTTPException(status_code=400, detail="Invalid model for selected year and make")
    if not req.displayModel or normalize_vehicle_model_key(req.displayModel) == normalize_vehicle_model_key(model):
        req.displayModel = resolved_model
    req.model = resolved_model

    service_name = ""
    service_key = ""
    hours_default = 0.0

    if req.serviceCode:
        s = find_service_by_code(req.serviceCode)
        if not s:
            raise HTTPException(status_code=400, detail="Invalid serviceCode")

        service_name = str(s.get("name", "")).strip()
        service_key = str(s.get("code", "")).strip()

        mn = float(s.get("labor_hours_min", 0))
        mx = float(s.get("labor_hours_max", 0))
        if mx > 0 and mx >= mn:
            hours_default = (mn + mx) / 2.0
    else:
        service_name = (req.service or "").strip()
        service_key = (req.serviceCode or "").strip()

        if not service_name:
            raise HTTPException(status_code=400, detail="Select a service")

    labor_rate = float(req.laborRate) if req.laborRate is not None else default_labor_rate()
    labor_hours = float(req.laborHours) if req.laborHours and req.laborHours > 0 else hours_default

    labor_breakdown = build_labor_breakdown(
        service_key,
        labor_hours,
        display_name=service_name,
        category_key=req.category,
        labor_min=mn if req.serviceCode else 0,
        labor_max=mx if req.serviceCode else 0,
    )

    if labor_breakdown:
        labor_hours = labor_breakdown["labor_hours"]["selected"]
    
    if not labor_breakdown:
        raise HTTPException(status_code=500, detail="Labor breakdown failed to generate")
    
    labor = labor_hours * labor_rate
    parts = float(req.partsPrice)

    z = zip_multiplier(req.zip or "00000")
    y = year_multiplier(req.year)

    subtotal = (labor + parts) * z * y
    final_price = int(round(subtotal))

    return EstimateResponse(
        estimate=final_price,
        service_name=service_name,
        breakdown={
            "labor_hours": labor_hours,
            "labor_rate": labor_rate,
            "labor": labor,
            "parts": parts,
            "zip_multiplier": z,
            "year_multiplier": y,
            "subtotal": subtotal,
            "hours_default": hours_default,
            "service_key": service_key,
        },
        labor_breakdown=labor_breakdown,
    )

# ==============================
# Feedback Email Endpoint
# ==============================

@app.post("/feedback")
async def send_feedback(data: dict):

    try:
        message = data.get("message", "")
        email = data.get("email", "Anonymous")

        body = f"""
TorqueMech Feedback

From: {email}

Message:
{message}
"""

        msg = MIMEText(body)
        msg["Subject"] = "TorqueMech Feedback"
        msg["From"] = SMTP_USER
        msg["To"] = FEEDBACK_EMAIL

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)

        return {"success": True}

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    
import base64, io, re
from PIL import Image, ImageEnhance, ImageOps
from reportlab.lib.utils import ImageReader

def signature_to_dark_imagereader(data_url: str) -> ImageReader:
    """
    Convert signature PNG into cropped black ink on white background for PDF.

    This works for both:
    - dark mode: white strokes on transparent canvas
    - light mode: dark strokes on transparent canvas

    We detect strokes using alpha, crop tightly to the actual signature,
    and redraw them as black on white so ReportLab does not render a box.
    """
    if not data_url:
        return None

    m = re.match(r"data:image\/png;base64,(.*)", data_url)
    b64 = m.group(1) if m else data_url
    raw = base64.b64decode(b64)

    im = Image.open(io.BytesIO(raw)).convert("RGBA")
    alpha = im.getchannel("A")

    bbox = alpha.getbbox()
    if not bbox:
        return None

    # Small padding around signature
    pad = 4
    left, top, right, bottom = bbox
    left = max(0, left - pad)
    top = max(0, top - pad)
    right = min(im.width, right + pad)
    bottom = min(im.height, bottom + pad)

    cropped_alpha = alpha.crop((left, top, right, bottom))

    # White background, tightly cropped
    out = Image.new("RGB", (right - left, bottom - top), (255, 255, 255))

    # Any visible stroke becomes black
    black = Image.new("RGB", out.size, (0, 0, 0))
    stroke_mask = cropped_alpha.point(lambda a: 255 if a > 10 else 0)

    out.paste(black, mask=stroke_mask)

    bio = io.BytesIO()
    out.save(bio, format="PNG")
    bio.seek(0)
    return ImageReader(bio)

def pdf_draw_header(c, w, h, *, title="Repair Estimate", left=50, right=50, top=50, show_generated_date=True):
    """
    Consistent header for BOTH pdf and pdf_multi:
    - left title
    - top-right logo.png
    - generated timestamp under header
    Returns the new cursor y.
    """
    y = h - top

    # Title (left)
    c.setFont("Helvetica-Bold", 21)
    c.setFillColorRGB(0.05, 0.08, 0.13)
    c.drawString(left, y, title)

    # Logo (top-right)
    try:
        logo_path = STATIC_DIR / "logo.png"
        logo = ImageReader(str(logo_path))
        logo_w, logo_h = 132, 30
        x = w - right - logo_w
        y_img = y - logo_h + 10
        c.drawImage(logo, x, y_img, width=logo_w, height=logo_h, mask="auto")
    except Exception:
        # If logo missing, fail gracefully (don’t crash PDF)
        c.setFont("Helvetica-Bold", 15)
        c.setFillColorRGB(0.05, 0.08, 0.13)
        c.drawRightString(w - right, y, "TorqueMech")

    y -= 17
    if show_generated_date:
        c.setFont("Helvetica", 9)
        c.setFillGray(0.38)
        c.drawString(left, y, f"Prepared {local_now().strftime('%Y-%m-%d %H:%M')}")
        y -= 8

    c.setStrokeColorRGB(0.16, 0.39, 0.72)
    c.setLineWidth(1.8)
    c.line(left, y, w - right, y)
    c.setStrokeColorRGB(0.86, 0.91, 0.92)
    c.setLineWidth(0.6)
    c.line(left, y - 3, w - right, y - 3)
    c.setLineWidth(1)
    c.setStrokeGray(0)
    c.setFillGray(0)

    return y - 24

def pdf_start_page(c, w, h, *, title="Repair Estimate", vehicle_line: Optional[str]=None, left=50, right=50, top=50, show_generated_date=True):
    """Start a new PDF page with consistent header (+ optional vehicle line). Returns cursor y."""
    y = pdf_draw_header(c, w, h, title=title, left=left, right=right, top=top, show_generated_date=show_generated_date)
    if vehicle_line:
        c.setFont("Helvetica", 12)
        c.setFillGray(0)
        c.drawString(left, y, vehicle_line)
        y -= 26
    return y

def pdf_ensure_space(
    c, w, h, y, needed,
    *, title="Repair Estimate", vehicle_line=None,
    left=50, right=50,
    continued_label=None,
    draw_columns_fn=None,
    show_generated_date=True,
):
    bottom_margin = 66
    if y - needed < bottom_margin:
        c.showPage()
        y = pdf_start_page(c, w, h, title=title, vehicle_line=vehicle_line, left=left, right=right, show_generated_date=show_generated_date)

        if continued_label:
            c.setFont("Helvetica-Bold", 12)
            c.drawString(left, y, continued_label)
            y -= 16

            if callable(draw_columns_fn):
                y = draw_columns_fn(y)

        return y
    return y



def pdf_draw_signature_block(c, w, y, *, signature_data_url=None, left=50, right=50):
    """
    Consistent signature box + note (same in both PDFs).
    Returns the new cursor y (below the signature note).
    """
    card_h = 136
    disclaimer_gap = 16
    c.setFillColorRGB(0.965, 0.985, 0.98)
    c.roundRect(left, y - card_h, w - left - right, card_h - 1, 7, fill=1, stroke=0)
    c.setStrokeGray(0.82)
    c.roundRect(left, y - card_h, w - left - right, card_h - 1, 7, fill=0, stroke=1)
    c.setStrokeGray(0)
    c.setFillGray(0)

    c.setFont("Helvetica-Bold", 11)
    c.drawString(left + 12, y - 16, "Signed Customer Approval")

    c.setFont("Helvetica", 8.7)
    c.setFillGray(0.38)
    c.drawString(left + 12, y - 29, "Signature confirms the customer reviewed and approved the estimate details above.")
    c.setFillGray(0)

    sig_box_h = 64
    sig_box_w = w - left - right
    sig_x = left + 12
    sig_y = y - 102
    sig_inner_w = sig_box_w - 24

    c.setStrokeGray(0.72)
    c.setLineWidth(0.9)
    c.setFillColorRGB(1, 1, 1)
    c.roundRect(sig_x, sig_y, sig_inner_w, sig_box_h, 5, fill=1, stroke=1)
    c.setStrokeGray(0)
    c.setFillGray(0)

    if signature_data_url:
        try:
            sig_reader = signature_to_dark_imagereader(signature_data_url)
            if sig_reader:
                pad = 6
                c.drawImage(
                    sig_reader,
                    sig_x + pad,
                    sig_y + pad,
                    width=sig_inner_w - pad * 2,
                    height=sig_box_h - pad * 2,
                    preserveAspectRatio=True,
                    mask="auto",
                )
        except Exception:
            c.setFont("Helvetica-Oblique", 9)
            c.setFillGray(0.5)
            c.drawString(sig_x + 8, sig_y + sig_box_h - 14, "Signature could not be rendered")
            c.setFillGray(0)

    c.setFont("Helvetica-Oblique", 9)
    c.setFillGray(0.4)
    c.drawString(left + 12, y - card_h - disclaimer_gap, "Estimate approval only. No payment is collected or recorded on this PDF.")
    c.setFillGray(0)

    return y - card_h - disclaimer_gap - 18


def pdf_signature_block_height() -> int:
    return 136 + 16 + 18


def pdf_draw_footer(c, w):
    """
    Consistent footer for BOTH PDFs.
    """
    c.setFont("Helvetica", 8.5)
    c.setFillGray(0.48)
    c.drawCentredString(w / 2, 40, "Prepared for customer review")
    c.drawCentredString(w / 2, 28, "Generated with TorqueMech")
    c.setFillGray(0)


PDF_MODE_FREE = "free"
PDF_MODE_PRO = "pro"


def pdf_draw_wrapped_lines(c, text: str, x: float, y: float, *, max_chars: int = 86, leading: int = 12, limit: int = 6) -> float:
    c.setFillGray(0)
    for line in wrap_text(text or "", max_chars=max_chars)[:limit]:
        c.drawString(x, y, line)
        y -= leading
    return y


def pdf_format_money(value: Any, default: str = "$0.00") -> str:
    if isinstance(value, str):
        return value.strip() or default
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return default


def pdf_draw_pro_logo_placeholder(c, profile: Dict[str, Any], x: float, y: float, *, width: float = 112, height: float = 42) -> None:
    logo_url = str(profile.get("logo_url") or "").strip()
    if not logo_url:
        shop_name = str(profile.get("shop_name") or "").strip() or "Shop Estimate"
        c.setFont("Helvetica-Bold", 21)
        c.setFillColorRGB(1, 1, 1)
        c.drawString(x, y + 18, shop_name[:34])
        c.setFillGray(0)
        return

    c.setStrokeColorRGB(0.68, 0.77, 0.86)
    c.setFillColorRGB(0.93, 0.96, 0.99)
    c.roundRect(x, y, width, height, 6, fill=1, stroke=1)
    c.setFillColorRGB(0.08, 0.15, 0.24)
    c.setFont("Helvetica-Bold", 8)
    c.drawCentredString(x + width / 2, y + 24, "Logo configured")
    c.setFont("Helvetica", 7)
    c.drawCentredString(x + width / 2, y + 12, "Preview placeholder")
    c.setFillGray(0)
    c.setStrokeGray(0)


def pdf_draw_pro_header(c, w: float, h: float, profile: Dict[str, Any]) -> float:
    left = 48
    right = 48
    top = h - 34

    c.setFillColorRGB(0.04, 0.12, 0.20)
    c.rect(0, h - 94, w, 94, fill=1, stroke=0)
    c.setFillColorRGB(0.08, 0.52, 0.50)
    c.rect(0, h - 96, w, 2, fill=1, stroke=0)

    pdf_draw_pro_logo_placeholder(c, profile, left, h - 74, width=264, height=44)

    shop_name = str(profile.get("shop_name") or "").strip() or "Shop Profile"
    contact_lines = [
        str(profile.get("phone") or "").strip(),
        str(profile.get("email") or "").strip(),
        str(profile.get("address") or "").strip().replace("\r", " ").replace("\n", ", "),
        str(profile.get("website") or "").strip(),
    ]
    contact_lines = [line for line in contact_lines if line]
    contact_x = w - right

    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 7.5)
    c.drawRightString(contact_x, top - 2, "SHOP CONTACT")
    c.setFont("Helvetica", 8.2)
    contact_y = top - 15
    if contact_lines:
        for line in contact_lines[:4]:
            c.drawRightString(contact_x, contact_y, line[:56])
            contact_y -= 10.5
    else:
        c.drawRightString(contact_x, contact_y, shop_name[:42])

    c.setFillGray(0)
    y = h - 120
    c.setFont("Helvetica-Bold", 21)
    c.setFillColorRGB(0.05, 0.09, 0.16)
    c.drawString(left, y, "Service Estimate")
    c.setFont("Helvetica", 9)
    c.setFillColorRGB(0.36, 0.42, 0.50)
    c.drawRightString(w - right, y + 6, f"Date: {local_today_iso()}")
    c.setFillGray(0)
    return y - 24


def pdf_draw_pro_footer(c, w: float, profile: Dict[str, Any]) -> None:
    footer_note = str(profile.get("custom_footer_note") or "").strip()
    c.setStrokeColorRGB(0.86, 0.89, 0.93)
    c.line(48, 58, w - 48, 58)
    c.setStrokeGray(0)
    c.setFont("Helvetica", 8)
    c.setFillColorRGB(0.28, 0.34, 0.42)
    if footer_note:
        c.drawCentredString(w / 2, 43, footer_note[:130])
        c.setFont("Helvetica-Bold", 8)
        c.drawCentredString(w / 2, 30, "Generated by TorqueMech Pro")
    else:
        c.setFont("Helvetica-Bold", 8)
        c.drawCentredString(w / 2, 36, "Generated by TorqueMech Pro")
    c.setFillGray(0)


def build_pro_pdf_bytes(profile: Optional[Dict[str, Any]] = None, estimate_data: Optional[Dict[str, Any]] = None, *, pdf_mode: str = PDF_MODE_PRO) -> bytes:
    if pdf_mode != PDF_MODE_PRO:
        raise ValueError("Pro PDF builder only supports pdf_mode='pro'.")

    profile = normalize_shop_profile(profile or load_shop_profile())
    estimate_data = estimate_data or {}
    vehicle = str(estimate_data.get("vehicle") or "Sample Vehicle")
    customer_name = str(estimate_data.get("customer_name") or "Customer Review Copy")
    services = estimate_data.get("services") or [
        {
            "name": "Alternator Replacement",
            "labor_hours": "2.0",
            "parts": "$285.00",
            "labor_cost": "$180.00",
            "total": "$465.00",
        }
    ]
    subtotal = str(estimate_data.get("subtotal") or estimate_data.get("estimated_total") or "$465.00")
    tax = str(estimate_data.get("tax") or "$0.00")
    estimated_total = str(estimate_data.get("estimated_total") or subtotal)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    w, h = letter
    c.setTitle("TorqueMech Pro Estimate Preview")

    left = 48
    right = 48
    y = pdf_draw_pro_header(c, w, h, profile)

    c.setFillColorRGB(0.965, 0.985, 0.985)
    c.roundRect(left, y - 78, w - left - right, 78, 6, fill=1, stroke=0)
    c.setFillGray(0)
    c.setFont("Helvetica-Bold", 9.5)
    c.setFillColorRGB(0.26, 0.34, 0.43)
    c.drawString(left + 16, y - 19, "ESTIMATE SUMMARY")
    c.setFillGray(0)
    c.setFont("Helvetica", 10.5)
    c.drawString(left + 16, y - 41, f"Customer: {customer_name}")
    c.drawString(left + 16, y - 60, f"Vehicle: {vehicle}")
    c.setFont("Helvetica-Bold", 19)
    c.setFillColorRGB(0.04, 0.12, 0.20)
    c.drawRightString(w - right - 16, y - 30, estimated_total)
    c.setFont("Helvetica", 8)
    c.setFillColorRGB(0.38, 0.44, 0.52)
    c.drawRightString(w - right - 16, y - 46, "Total Estimate")
    c.drawRightString(w - right - 16, y - 61, local_today_iso())
    c.setFillGray(0)
    c.setStrokeGray(0)
    y -= 104

    c.setFont("Helvetica-Bold", 12)
    c.drawString(left, y, "Service Line Items")
    y -= 18
    c.setStrokeColorRGB(0.86, 0.89, 0.93)
    c.line(left, y, w - right, y)
    c.setStrokeGray(0)
    y -= 16

    x_hours = 320
    x_parts = 400
    x_labor = 480
    x_total = w - right

    c.setFont("Helvetica-Bold", 8.5)
    c.setFillColorRGB(0.36, 0.43, 0.52)
    c.drawString(left, y, "Service")
    c.drawRightString(x_hours, y, "Hours")
    c.drawRightString(x_parts, y, "Parts")
    c.drawRightString(x_labor, y, "Labor")
    c.drawRightString(x_total, y, "Total")
    c.setFillGray(0)
    y -= 10
    c.setStrokeColorRGB(0.82, 0.86, 0.91)
    c.line(left, y, x_total, y)
    c.setStrokeGray(0)
    y -= 15

    for service in services[:8]:
        c.setFont("Helvetica-Bold", 10)
        c.setFillColorRGB(0.05, 0.09, 0.16)
        c.drawString(left, y, str(service.get("name") or "Service")[:48])
        c.setFont("Helvetica", 10)
        c.setFillColorRGB(0.16, 0.22, 0.30)
        c.drawRightString(x_hours, y, str(service.get("labor_hours") or service.get("hours") or "0.0"))
        c.drawRightString(x_parts, y, pdf_format_money(service.get("parts") or service.get("parts_cost")))
        c.drawRightString(x_labor, y, pdf_format_money(service.get("labor_cost") or service.get("labor")))
        c.setFont("Helvetica-Bold", 10)
        c.drawRightString(x_total, y, pdf_format_money(service.get("total")))
        c.setFillGray(0)
        y -= 13
        c.setStrokeColorRGB(0.91, 0.93, 0.96)
        c.line(left, y, x_total, y)
        c.setStrokeGray(0)
        y -= 12

    y -= 16
    totals_w = 228
    totals_x = w - right - totals_w
    c.setFillColorRGB(0.93, 0.975, 0.97)
    c.setStrokeColorRGB(0.52, 0.72, 0.72)
    c.roundRect(totals_x, y - 94, totals_w, 94, 6, fill=1, stroke=1)
    c.setFillGray(0)
    c.setFont("Helvetica", 9)
    c.drawString(totals_x + 18, y - 22, "Subtotal")
    c.drawRightString(w - right - 18, y - 22, subtotal)
    c.drawString(totals_x + 18, y - 42, "Tax")
    c.drawRightString(w - right - 18, y - 42, tax)
    c.setStrokeColorRGB(0.72, 0.82, 0.82)
    c.line(totals_x + 18, y - 57, w - right - 18, y - 57)
    c.setStrokeGray(0)
    c.setFont("Helvetica-Bold", 14)
    c.setFillColorRGB(0.03, 0.11, 0.18)
    c.drawString(totals_x + 18, y - 78, "Total Estimate")
    c.drawRightString(w - right - 18, y - 78, estimated_total)
    c.setFillGray(0)
    y -= 122

    c.setFillColorRGB(0.985, 0.99, 1.0)
    c.setStrokeColorRGB(0.84, 0.89, 0.94)
    c.roundRect(left, y - 86, w - left - right, 86, 6, fill=1, stroke=1)
    c.setFillGray(0)
    c.setFont("Helvetica-Bold", 11.5)
    c.drawString(left + 18, y - 22, "Warranty & Terms")
    terms_y = y - 42
    c.setFont("Helvetica", 9.2)

    warranty_note = str(profile.get("warranty_note") or "").strip()
    quote_days = int(profile.get("quote_expiration_days") or 0)
    terms = []
    if warranty_note:
        terms.append(warranty_note)
    if quote_days:
        terms.append(f"Quote valid for {quote_days} days unless parts pricing, labor rate, or diagnostic findings change.")
    if not terms:
        terms.append("Final repair cost may vary after inspection, diagnostic confirmation, parts selection, and vehicle condition review.")

    for term in terms[:2]:
        terms_y = pdf_draw_wrapped_lines(c, term, left + 18, terms_y, max_chars=92, leading=12, limit=2)

    y -= 122

    c.setFont("Helvetica-Bold", 11)
    c.drawCentredString(w / 2, y, "Customer Approval")
    y -= 17
    c.setFont("Helvetica", 9)
    c.drawCentredString(w / 2, y, "By signing, you approve the above repair estimate pending final inspection.")
    y -= 28
    c.setStrokeColorRGB(0.48, 0.58, 0.68)
    c.setLineWidth(1.2)
    c.line(left, y, left + 220, y)
    c.line(left + 280, y, w - right, y)
    c.setLineWidth(1)
    c.setStrokeGray(0)
    c.setFont("Helvetica", 8)
    c.setFillColorRGB(0.38, 0.44, 0.52)
    c.drawString(left, y - 12, "Customer Signature")
    c.drawString(left + 280, y - 12, "Date")
    c.setFillGray(0)

    pdf_draw_pro_footer(c, w, profile)
    c.save()
    buf.seek(0)
    return buf.getvalue()


@app.get("/shop-profile/pdf-preview", include_in_schema=False)
def shop_profile_pdf_preview() -> Response:
    # Beta gate: keep Pro PDF preview inaccessible until Pro modules launch.
    raise HTTPException(status_code=404, detail="Not found")
    profile = load_shop_profile()
    content = build_pro_pdf_bytes(
        profile,
        {
            "vehicle": "2018 Toyota Camry",
            "customer_name": "Sample Customer",
            "estimated_total": "$465.00",
            "subtotal": "$465.00",
            "tax": "$0.00",
            "services": [
                {
                    "name": "Alternator Replacement",
                    "labor_hours": "2.0",
                    "parts": "$285.00",
                    "labor_cost": "$180.00",
                    "total": "$465.00",
                },
            ],
        },
        pdf_mode=PDF_MODE_PRO,
    )
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline; filename=torquemech_pro_preview.pdf"},
    )

# ===============================
# PDF
# ===============================
@app.post("/estimate/pdf")
async def estimate_pdf(request: Request, req: EstimateRequest) -> Response:
    try:
        metric_incr("pdf_single_generated")
        est = await estimate(req)

        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        w, h = letter

        c.setTitle("Repair Estimate")

        y = pdf_draw_header(c, w, h, show_generated_date=req.showGeneratedDate)

         # ---------------- Vehicle ----------------
        c.setFont("Helvetica-Bold", 12)
        c.drawString(72, y, "Vehicle")
        y -= 16
        c.setFont("Helvetica", 11)
        c.drawString(72, y, customer_vehicle_line(req.year, req.make, req.model, req.displayModel))
        y -= 22

        # ---------------- Service ----------------
        c.setFont("Helvetica-Bold", 12)
        c.drawString(72, y, "Service")
        y -= 16
        c.setFont("Helvetica", 11)
        c.drawString(72, y, est.service_name)
        y -= 22

        # ---------------- Total ----------------
        c.setFont("Helvetica-Bold", 12)
        c.drawString(72, y, "Estimated Total")
        y -= 16
        c.setFont("Helvetica-Bold", 13)
        c.drawString(72, y, f"${est.estimate:,} {est.currency}")
        y -= 26

        # ---------------- Customer-facing Breakdown ----------------
        c.setFont("Helvetica-Bold", 12)
        c.drawString(72, y, "Breakdown")
        y -= 16

        c.setFont("Helvetica", 10)

        labor_hours = float(est.breakdown.get("labor_hours") or 0)
        labor_cost = float(est.breakdown.get("labor") or 0)
        parts_cost = float(est.breakdown.get("parts") or 0)
        breakdown_rows = [
            ("Total", f"${est.estimate:,.2f} {est.currency}"),
        ]

        if req.showLaborColumn:
            breakdown_rows.insert(0, ("Labor", f"${labor_cost:,.2f}"))
            breakdown_rows.insert(0, ("Labor Hours", f"{labor_hours:.1f} hr"))

        if req.showPartsColumn:
            insert_at = 2 if req.showLaborColumn else 0
            breakdown_rows.insert(insert_at, ("Parts", f"${parts_cost:,.2f}"))

        if req.showHourlyRate:
            insert_at = 1 if req.showLaborColumn else 0
            breakdown_rows.insert(insert_at, ("Hourly Labor Rate", f"${float(est.breakdown.get('labor_rate') or 0):,.2f}/hr"))

        for label, value in breakdown_rows:
            c.drawString(72, y, label)
            c.drawRightString(540, y, value)
            y -= 14

        y -= 6

        if req.showDetailedLaborBreakdown and est.labor_breakdown and est.labor_breakdown.get("steps"):
            c.setFont("Helvetica-Bold", 12)
            c.drawString(72, y, "Detailed Labor Breakdown")
            y -= 14

            c.setFont("Helvetica", 9)
            for step in est.labor_breakdown["steps"]:
                label = step.get("label", "")
                hours = float(step.get("hours", 0))
                c.drawString(82, y, f"- {label}")
                c.drawRightString(540, y, f"{hours:.1f} hr")
                y -= 12

            y -= 6

        # ---------------- Customer ----------------
        c.setFont("Helvetica-Bold", 12)
        c.drawString(72, y, "Customer")
        y -= 16

        c.setFont("Helvetica", 11)
        c.drawString(72, y, f"Customer agrees: {'Yes' if req.customerAgrees else 'No'}")
        y -= 14

        if req.customerName:
            c.drawString(72, y, f"Name: {req.customerName}")
            y -= 14

        customer_phone = format_pdf_phone(req.customerPhone)
        if customer_phone:
            c.drawString(72, y, f"Phone: {customer_phone}")
            y -= 14

        # ---------------- Notes Box ----------------
        if req.notes:
            y -= 10
            box_x = 72
            box_w = 468
            box_h = 50

            c.setFillGray(0.97)
            c.rect(box_x, y - box_h, box_w, box_h, fill=1, stroke=0)
            c.setFillGray(0)

            c.setStrokeGray(0.85)
            c.rect(box_x, y - box_h, box_w, box_h, fill=0, stroke=1)
            c.setStrokeGray(0)

            c.setFont("Helvetica-Bold", 10)
            c.drawString(box_x + 8, y - 16, "Notes:")

            c.setFont("Helvetica", 10)
            lines = wrap_text(req.notes.strip(), max_chars=80)
            text_y = y - 30
            for line in lines[:2]:
                c.drawString(box_x + 8, text_y, line)
                text_y -= 12

            y -= (box_h + 14)

        # ---------------- Signature + Note + Footer (unified) ----------------
        if req.signatureDataUrl:
            y -= 12
            y = pdf_ensure_space(
                c, w, h, y,
                needed=178,
                title="Repair Estimate",
                vehicle_line=customer_vehicle_line(req.year, req.make, req.model, req.displayModel),
                left=72, right=72,
                show_generated_date=req.showGeneratedDate,
            )
            y = pdf_draw_signature_block(c, w, y, signature_data_url=req.signatureDataUrl, left=72, right=72)
        pdf_draw_footer(c, w)

        c.save()

        buf.seek(0)
        pdf_bytes = buf.read()
        save_repair_estimate_pdf_if_available(
            req,
            pdf_bytes,
            request=request,
            vehicle_line=customer_vehicle_line(req.year, req.make, req.model, req.displayModel),
            related_title=estimate_pdf_related_title(req, est.service_name),
            estimate_total=float(est.estimate or 0),
        )

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": "attachment; filename=torquemech_estimate.pdf"}
        )

    except HTTPException:
        raise
    except Exception:
        logging.exception("PDF_SINGLE_FAILED")
        metric_incr("errors_pdf_single")
        metric_incr("errors_total")  
        raise



from fastapi import Request

class LineItemPDF(BaseModel):
    serviceCode: str
    serviceText: Optional[str] = None
    displayServiceText: Optional[str] = None
    quantity: int = Field(1, ge=1)
    partsUnitCost: Optional[float] = Field(None, ge=0)
    laborHoursInput: Optional[float] = Field(None, ge=0)
    laborCalculationMode: Optional[str] = None
    pricingMode: Optional[str] = None
    status: Optional[str] = "recommended"
    flatRatePrice: Optional[float] = None
    laborHours: float
    partsPrice: float
    laborRate: float
    travelFee: float = 0
    estimate: Optional[float] = None
    inspectionFindings: Optional[str] = None

class MultiPDFRequest(BaseModel):
    year: int
    make: str
    model: str
    displayModel: Optional[str] = None
    notes: Optional[str] = None
    customerName: Optional[str] = None
    customerPhone: Optional[str] = None
    source: Optional[str] = None
    customerId: Optional[str] = None
    vehicleId: Optional[str] = None
    findingId: Optional[str] = None
    problemFound: Optional[str] = None
    recommendedRepair: Optional[str] = None
    sourceContext: Optional[Dict[str, Any]] = None
    businessName: Optional[str] = None
    mechanicName: Optional[str] = None
    businessPhone: Optional[str] = None
    businessNote: Optional[str] = None
    customerAgrees: bool = True
    signatureDataUrl: Optional[str] = None
    showGeneratedDate: bool = True
    showHourlyRate: bool = False
    showLaborColumn: bool = False
    showPartsColumn: bool = False
    showRiskNotes: bool = True
    showInspectionFindings: bool = True
    showDetailedLaborBreakdown: bool = False
    includeServiceEducation: bool = False
    lineItems: List[LineItemPDF]

def load_service_education_catalog() -> Dict[str, Any]:
    if not SERVICE_EDUCATION_PATH.exists():
        return {}

    try:
        payload = json.loads(
            SERVICE_EDUCATION_PATH.read_text(encoding="utf-8-sig")
        )
    except (OSError, json.JSONDecodeError):
        logging.exception("SERVICE_EDUCATION_LOAD_FAILED")
        return {}

    services = payload.get("services") if isinstance(payload, dict) else None
    return services if isinstance(services, dict) else {}


def estimate_service_education(service_code: str = "") -> Dict[str, Any]:
    normalized_code = str(service_code or "").strip()
    if not normalized_code:
        return {}

    structured = load_service_education_catalog().get(normalized_code)
    if isinstance(structured, dict):
        symptoms = [
            str(item).strip()
            for item in (structured.get("symptoms") or [])
            if str(item or "").strip()
        ][:3]
        education = {
            "title": str(structured.get("title") or "").strip(),
            "summary": str(structured.get("summary") or "").strip(),
            "symptoms": symptoms,
            "delay_risk": str(structured.get("delay_risk") or "").strip(),
            "customer_note": str(structured.get("customer_note") or "").strip(),
        }
        if any(
            education.get(key)
            for key in ("title", "summary", "symptoms", "delay_risk", "customer_note")
        ):
            return education

    service = find_service_by_code(normalized_code) or {}
    summary = str(service.get("summary") or "").strip()
    symptoms = [
        str(item).strip()
        for item in (service.get("symptoms") or [])
        if str(item or "").strip()
    ][:3]

    if not summary and not symptoms:
        return {}

    return {
        "title": "",
        "summary": summary,
        "symptoms": symptoms,
        "delay_risk": "",
        "customer_note": "",
    }


GENERIC_ESTIMATE_RISK_NOTE = (
    "Additional diagnostics or related system inspection may be required if access, "
    "corrosion, or vehicle condition changes the repair path. Labor time may vary "
    "based on vehicle condition."
)

BRAKE_ESTIMATE_RISK_NOTE = (
    "Inspect rotor condition, caliper hardware, slide pins, and brake fluid condition "
    "before final approval. Labor time may vary based on vehicle condition."
)


def estimate_risk_note_for_service(service_code: str = "", service_text: str = "") -> str:
    service_value = f"{service_code or ''} {service_text or ''}".lower().replace("_", " ")
    if "water pump" in service_value:
        return (
            "Inspect coolant condition, thermostat behavior, belt drive, and seized "
            "hardware risk before final approval. Labor time may vary based on vehicle condition."
        )
    if "alternator" in service_value:
        return (
            "Verify charging output, battery condition, belt tensioner, cables, and "
            "grounds before final approval. Labor time may vary based on vehicle condition."
        )
    if "starter" in service_value:
        return (
            "Verify battery condition, cable voltage drop, and starter circuit command "
            "before final approval. Labor time may vary based on vehicle condition."
        )
    brake_terms = [
        "brake pad",
        "brake pads",
        "brake rotor",
        "brake rotors",
        "brake caliper",
        "brake hardware",
        "wheel cylinder",
    ]
    if "brake" in service_value or any(term in service_value for term in brake_terms):
        return BRAKE_ESTIMATE_RISK_NOTE
    return GENERIC_ESTIMATE_RISK_NOTE


CUSTOMER_FINAL_PRICE_NOTE = (
    "Final pricing may vary after inspection, taxes, parts confirmation, "
    "vehicle condition, or additional repair needs."
)

REPAIR_STATUS_LABELS = {
    "diagnosed": "Diagnosed",
    "recommended": "Recommended",
    "urgent": "Urgent",
    "monitor": "Monitor",
}


def pdf_repair_status_label(value: Any) -> str:
    status = str(value or "").strip().lower()
    return REPAIR_STATUS_LABELS.get(status, "Recommended")


def estimate_request_source_value(req: Any, key: str) -> str:
    direct = getattr(req, key, None)
    if direct not in (None, ""):
        return str(direct).strip()
    source_context = getattr(req, "sourceContext", None)
    if isinstance(source_context, dict):
        camel_key = key[0].lower() + key[1:]
        for candidate in (key, camel_key):
            value = source_context.get(candidate)
            if value not in (None, ""):
                return str(value).strip()
    return ""


def estimate_pdf_approval_status(req: Any) -> str:
    if estimate_request_source_value(req, "source") == "finding":
        return "Prepared estimate"
    if getattr(req, "signatureDataUrl", None):
        return "Signed customer approval"
    if getattr(req, "customerAgrees", False):
        return "Customer reviewed estimate"
    return "Prepared estimate"


def estimate_pdf_related_title(req: Any, fallback_title: str = "") -> str:
    title = (
        estimate_request_source_value(req, "recommendedRepair")
        or estimate_request_source_value(req, "problemFound")
        or fallback_title
        or "Recommended Repair"
    )
    return re.sub(r"\s+", " ", str(title or "")).strip()


def estimate_pdf_payload(req: Any, *, related_title: str, estimate_total: float) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "source": estimate_request_source_value(req, "source") or "estimator",
        "customer_id": estimate_request_source_value(req, "customerId"),
        "vehicle_id": estimate_request_source_value(req, "vehicleId"),
        "finding_id": estimate_request_source_value(req, "findingId"),
        "appointment_id": estimate_request_source_value(req, "appointmentId"),
        "year": getattr(req, "year", None),
        "make": str(getattr(req, "make", "") or "").strip(),
        "model": str(getattr(req, "model", "") or "").strip(),
        "display_model": str(getattr(req, "displayModel", "") or "").strip(),
        "customer_name": str(getattr(req, "customerName", "") or "").strip(),
        "customer_phone": str(getattr(req, "customerPhone", "") or "").strip(),
        "problem_found": estimate_request_source_value(req, "problemFound"),
        "recommended_repair": estimate_request_source_value(req, "recommendedRepair") or related_title,
        "related_title": related_title,
        "estimate_total": float(estimate_total or 0),
        "notes": str(getattr(req, "notes", "") or "").strip(),
    }
    line_items = getattr(req, "lineItems", None)
    if line_items:
        normalized_line_items = []
        for item in line_items:
            is_flat_rate = str(getattr(item, "pricingMode", "") or "").strip().lower() == "flat"
            labor_hours = estimate_line_billable_labor_hours(item)
            labor_rate = getattr(item, "laborRate", None)
            flat_rate_price = getattr(item, "flatRatePrice", None)
            labor_total = max(0.0, float(flat_rate_price or 0)) if is_flat_rate else labor_hours * max(0.0, float(labor_rate or 0))
            parts_total = max(0.0, float(getattr(item, "partsPrice", 0) or 0))
            travel_total = max(0.0, float(getattr(item, "travelFee", 0) or 0))
            line_total = round(labor_total + parts_total + travel_total)
            normalized_line_items.append(
                {
                "service_code": getattr(item, "serviceCode", "") or "",
                "service_text": getattr(item, "displayServiceText", None) or getattr(item, "serviceText", None) or "",
                "quantity": getattr(item, "quantity", 1) or 1,
                "pricing_mode": getattr(item, "pricingMode", None) or "hourly",
                "flat_rate_price": flat_rate_price,
                "labor_hours": labor_hours,
                "labor_hours_input": getattr(item, "laborHoursInput", None),
                "labor_calculation_mode": getattr(item, "laborCalculationMode", None),
                "labor_rate": labor_rate,
                "labor_total": labor_total,
                "parts_total": parts_total,
                "line_total": line_total,
                "grand_total": line_total,
                "status": getattr(item, "status", None) or "recommended",
                "inspection_findings": getattr(item, "inspectionFindings", None) or "",
                }
            )
        payload["line_items"] = normalized_line_items
    else:
        is_flat_rate = str(getattr(req, "pricingMode", "") or "").strip().lower() == "flat"
        labor_hours = float(getattr(req, "laborHours", 0) or 0)
        labor_rate = getattr(req, "laborRate", None)
        flat_rate_price = getattr(req, "flatRatePrice", None)
        labor_total = max(0.0, float(flat_rate_price or 0)) if is_flat_rate else labor_hours * max(0.0, float(labor_rate or 0))
        parts_total = max(0.0, float(getattr(req, "partsPrice", 0) or 0))
        travel_total = max(0.0, float(getattr(req, "travelFee", 0) or 0))
        line_total = round(labor_total + parts_total + travel_total)
        payload["line_items"] = [
            {
                "service_code": getattr(req, "serviceCode", "") or "",
                "service_text": getattr(req, "service", None) or related_title,
                "quantity": getattr(req, "quantity", 1) or 1,
                "pricing_mode": getattr(req, "pricingMode", None) or "hourly",
                "flat_rate_price": flat_rate_price,
                "labor_hours": labor_hours,
                "labor_calculation_mode": getattr(req, "laborCalculationMode", None),
                "labor_rate": labor_rate,
                "labor_total": labor_total,
                "parts_total": parts_total,
                "line_total": line_total,
                "grand_total": line_total,
                "status": "recommended",
            }
        ]
    return payload


def save_repair_estimate_pdf_if_available(
    req: Any,
    pdf_bytes: bytes,
    *,
    request: Request | None = None,
    vehicle_line: str,
    related_title: str,
    estimate_total: float,
) -> None:
    customer_id = estimate_request_source_value(req, "customerId")
    vehicle_id = estimate_request_source_value(req, "vehicleId")
    if not customer_id or not vehicle_id:
        return
    if estimate_request_source_value(req, "source") == "finding":
        if request is None:
            raise HTTPException(status_code=403, detail="Finding estimate saves require shop access.")
        finding_id = estimate_request_source_value(req, "findingId")
        if not finding_id:
            raise HTTPException(status_code=400, detail="Finding estimate saves require a finding id.")
        conn = app_db_conn()
        try:
            if current_user(conn, request) is None:
                raise HTTPException(status_code=403, detail="Finding estimate saves require shop access.")
            shop_context = current_shop_context(conn, request)
            access_context = shop_subscription_access_context(conn, shop_context.get("id"))
            if not shop_context.get("id") or not shop_can_write(access_context):
                raise HTTPException(status_code=403, detail="Read-only subscription mode blocks saving finding estimates.")
            try:
                customer_id_int = int(customer_id)
                vehicle_id_int = int(vehicle_id)
                finding_id_int = int(finding_id)
                shop_id_int = int(shop_context.get("id"))
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Invalid finding estimate link.")
            linked_finding = conn.execute(
                """
                SELECT fr.id
                FROM findings_records fr
                JOIN customer_vehicles cv
                  ON cv.id = fr.vehicle_id
                 AND cv.customer_id = ?
                JOIN customers c
                  ON c.id = cv.customer_id
                 AND c.shop_id = ?
                WHERE fr.id = ?
                  AND fr.customer_id = ?
                  AND fr.vehicle_id = ?
                LIMIT 1
                """,
                (customer_id_int, shop_id_int, finding_id_int, customer_id_int, vehicle_id_int),
            ).fetchone()
            if not linked_finding:
                raise HTTPException(status_code=404, detail="Finding estimate link not found.")
        finally:
            conn.close()
    try:
        record_estimate_pdf_document(
            pdf_bytes=pdf_bytes,
            customer_id=customer_id,
            vehicle_id=vehicle_id,
            finding_id=estimate_request_source_value(req, "findingId"),
            estimate_date=local_today_iso(),
            customer_name=str(getattr(req, "customerName", "") or "").strip(),
            vehicle_label=vehicle_line,
            related_title=related_title,
            estimate_total=estimate_total,
            approval_status=estimate_pdf_approval_status(req),
            payload=estimate_pdf_payload(req, related_title=related_title, estimate_total=estimate_total),
        )
    except Exception:
        logging.exception("ESTIMATE_TIMELINE_SAVE_FAILED")


def estimate_display_service_name(service_name: Any, quantity: Any = 1) -> str:
    name = str(service_name or "Repair service").strip() or "Repair service"
    quantity_suffix_re = re.compile(
        r"(?:\s*(?:[-\u2013\u2014]\s*)?(?:Qty\.?|Quantity)\s*\d+(?:\.\d+)?"
        r"|\s*[\u00d7xX]\s*\d+(?:\.\d+)?)\s*$",
        re.IGNORECASE,
    )
    while True:
        match = quantity_suffix_re.search(name)
        if not match:
            break
        name = name[: match.start()].rstrip(" -\u2013\u2014") or "Repair service"
    try:
        qty = int(quantity or 1)
    except (TypeError, ValueError):
        qty = 1
    qty = max(1, qty)
    return f"{name} × {qty}" if qty > 1 else name


def estimate_line_billable_labor_hours(item: Any) -> float:
    mode = str(getattr(item, "laborCalculationMode", "") or "").strip()
    quantity = max(1, int(getattr(item, "quantity", 1) or 1))
    if mode == "per_item":
        entered = getattr(item, "laborHoursInput", None)
        if entered is None:
            entered = getattr(item, "laborHours", 0) or 0
        return max(0.0, float(entered or 0)) * quantity
    return max(0.0, float(getattr(item, "laborHours", 0) or 0))


def format_pdf_phone(value: Any) -> str:
    raw = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(raw) == 11 and raw.startswith("1"):
        raw = raw[1:]
    if len(raw) == 10:
        return f"({raw[:3]}){raw[3:6]}-{raw[6:]}"
    return str(value or "").strip()


def pdf_multi_summary_approval_needed(
    *,
    final_note_lines: List[str],
    customer_name: Optional[str],
    customer_phone: Optional[str],
    has_signature: bool,
) -> int:
    needed = 74
    needed += (len(final_note_lines) * 9) + 10 if final_note_lines else 4
    needed += pdf_multi_approval_block_height(
        customer_name=customer_name,
        customer_phone=customer_phone,
        has_signature=has_signature,
    )
    return needed


def pdf_multi_approval_block_height(
    *,
    customer_name: Optional[str],
    customer_phone: Optional[str],
    has_signature: bool,
) -> int:
    block_h = 62
    if customer_name:
        block_h += 12
    if customer_phone:
        block_h += 12
    if has_signature:
        block_h += 78
    return block_h + 14


def pdf_draw_multi_approval_block(
    c,
    w: float,
    y: float,
    *,
    left: float,
    right: float,
    approval_title: str,
    approval_line: str,
    approval_note: str,
    customer_name: Optional[str],
    customer_phone: Optional[str],
    signature_data_url: Optional[str],
) -> float:
    has_signature = bool(signature_data_url)
    box_h = pdf_multi_approval_block_height(
        customer_name=customer_name,
        customer_phone=customer_phone,
        has_signature=has_signature,
    ) - 14
    box_w = w - left - right
    box_bottom = y - box_h + 8

    c.setFillColorRGB(0.985, 0.99, 0.995)
    c.roundRect(left, box_bottom, box_w, box_h, 7, fill=1, stroke=0)
    c.setStrokeGray(0.86)
    c.roundRect(left, box_bottom, box_w, box_h, 7, fill=0, stroke=1)
    c.setStrokeGray(0)
    c.setFillGray(0)

    text_y = y - 5
    c.setFont("Helvetica-Bold", 11)
    c.drawString(left + 12, text_y, approval_title)
    text_y -= 18

    c.setFont("Helvetica", 9.5)
    c.setFillGray(0.24)
    c.drawString(left + 12, text_y, approval_line)
    text_y -= 12

    c.setFont("Helvetica-Oblique", 8.5)
    c.setFillGray(0.42)
    c.drawString(left + 12, text_y, approval_note)
    text_y -= 12

    if customer_name:
        c.setFont("Helvetica", 9.5)
        c.setFillGray(0.24)
        c.drawString(left + 12, text_y, f"Customer: {customer_name}")
        text_y -= 12

    if customer_phone:
        c.setFont("Helvetica", 9.5)
        c.setFillGray(0.24)
        c.drawString(left + 12, text_y, f"Phone: {customer_phone}")
        text_y -= 12

    if has_signature:
        sig_x = left + 12
        sig_y = box_bottom + 14
        sig_w = box_w - 24
        sig_h = 58

        c.setStrokeGray(0.72)
        c.setFillColorRGB(1, 1, 1)
        c.roundRect(sig_x, sig_y, sig_w, sig_h, 5, fill=1, stroke=1)
        c.setStrokeGray(0)
        c.setFillGray(0)

        try:
            sig_reader = signature_to_dark_imagereader(signature_data_url)
            if sig_reader:
                pad = 6
                c.drawImage(
                    sig_reader,
                    sig_x + pad,
                    sig_y + pad,
                    width=sig_w - pad * 2,
                    height=sig_h - pad * 2,
                    preserveAspectRatio=True,
                    mask="auto",
                )
        except Exception:
            c.setFont("Helvetica-Oblique", 9)
            c.setFillGray(0.5)
            c.drawString(sig_x + 8, sig_y + sig_h - 14, "Signature could not be rendered")

    c.setFillGray(0)
    return box_bottom - 14


@app.post("/estimate/pdf_multi")
async def estimate_pdf_multi(request: Request, req: MultiPDFRequest) -> Response:
    try:
        metric_incr("pdf_multi_generated")

        # 🔒 Defensive Guard (VERY IMPORTANT)
        if not req.lineItems:
            raise HTTPException(status_code=400, detail="No line items provided.")

        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        w, h = letter

        vehicle_line = customer_vehicle_line(req.year, req.make, req.model, req.displayModel)
        y = pdf_start_page(
            c,
            w,
            h,
            title="Repair Estimate",
            vehicle_line=None,
            left=50,
            right=50,
            show_generated_date=req.showGeneratedDate,
        )

        business_name = (req.businessName or "").strip()[:80]
        mechanic_name = (req.mechanicName or "").strip()[:80]
        business_phone = format_pdf_phone(req.businessPhone)[:32]
        business_note = (req.businessNote or "").strip()[:180]
        business_note_lines = wrap_text(business_note, max_chars=48)[:3] if business_note else []

        identity_box_h = 92 + (len(business_note_lines) * 8)
        y = pdf_ensure_space(
            c, w, h, y,
            needed=identity_box_h + 12,
            title="Repair Estimate",
            vehicle_line=None,
            left=50,
            right=50,
            show_generated_date=req.showGeneratedDate,
        )
        card_x = 50
        card_w = w - 100
        card_bottom = y - identity_box_h + 8
        card_top = y + 8
        divider_x = card_x + (card_w * 0.52)

        c.setFillColorRGB(0.985, 0.991, 0.991)
        c.roundRect(card_x, card_bottom, card_w, identity_box_h, 8, fill=1, stroke=0)
        c.setStrokeColorRGB(0.82, 0.88, 0.88)
        c.roundRect(card_x, card_bottom, card_w, identity_box_h, 8, fill=0, stroke=1)
        c.setStrokeColorRGB(0.88, 0.92, 0.92)
        c.line(divider_x, card_bottom + 12, divider_x, card_top - 12)
        c.setStrokeGray(0)

        c.setFont("Helvetica-Bold", 8)
        c.setFillColorRGB(0.12, 0.32, 0.62)
        c.drawString(card_x + 14, card_top - 18, "PREPARED BY")
        c.drawString(divider_x + 18, card_top - 18, "VEHICLE")

        c.setFillColorRGB(0.06, 0.08, 0.12)
        c.setFont("Helvetica-Bold", 13)
        c.drawString(card_x + 14, card_top - 36, business_name or "Prepared Repair Estimate")

        prepared_y = card_top - 51
        c.setFont("Helvetica", 9)
        c.setFillGray(0.30)
        if mechanic_name:
            c.drawString(card_x + 14, prepared_y, f"Mechanic: {mechanic_name}")
            prepared_y -= 11
        if business_phone:
            c.drawString(card_x + 14, prepared_y, f"Phone: {business_phone}")
            prepared_y -= 11
        if not mechanic_name and not business_phone:
            c.drawString(card_x + 14, prepared_y, "Customer repair estimate")
            prepared_y -= 11

        if business_note_lines:
            c.setFont("Helvetica-Oblique", 8.2)
            c.setFillGray(0.42)
            for note_line in business_note_lines:
                c.drawString(card_x + 14, prepared_y, note_line)
                prepared_y -= 8

        vehicle_y = card_top - 36
        c.setFillColorRGB(0.06, 0.08, 0.12)
        c.setFont("Helvetica-Bold", 12.5)
        for line in wrap_text(vehicle_line.strip() or "Vehicle", max_chars=30)[:2]:
            c.drawString(divider_x + 18, vehicle_y, line)
            vehicle_y -= 13
        c.setFont("Helvetica", 9)
        c.setFillGray(0.38)
        c.drawString(divider_x + 18, vehicle_y - 2, "Prepared for customer review.")

        c.setFillGray(0)
        y = card_bottom - 18

        # ---- Column anchors ----
        LEFT = 50
        RIGHT = 50
        X_TOTAL  = w - RIGHT
        row_total_w = 112
        row_pad_x = 12
        row_detail_x = LEFT + row_pad_x
        row_total_x = X_TOTAL - row_pad_x
        detail_max_chars = 72
        title_max_chars = 45
        row_pad_top = 18
        row_pad_bottom = 20
        row_gap = 14

        # Services header 
        service_count = len(req.lineItems or [])
        service_count_label = f"{service_count} quoted service{'s' if service_count != 1 else ''}"
        min_service_row_height = 92 if service_count == 1 else 68
        c.setFillColorRGB(0.94, 0.965, 0.99)
        c.roundRect(LEFT, y - 28, X_TOTAL - LEFT, 35, 7, fill=1, stroke=0)
        c.setStrokeColorRGB(0.72, 0.82, 0.95)
        c.roundRect(LEFT, y - 28, X_TOTAL - LEFT, 35, 7, fill=0, stroke=1)
        c.setStrokeGray(0)
        c.setFillGray(0)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(LEFT + 12, y - 7, f"Repair Services ({service_count})")
        c.setFillGray(0.38)
        c.setFont("Helvetica", 8)
        c.drawString(LEFT + 12, y - 21, "Professional estimate line items with status, notes, and totals")
        c.setFillGray(0)
        y -= 39

        grand_total = 0.0
        for it in (req.lineItems or []):
            service_name = estimate_display_service_name(
                it.displayServiceText or it.serviceText or it.serviceCode or "Repair service",
                it.quantity,
            ).strip()
            service_name_lines = wrap_text(service_name, max_chars=title_max_chars)[:3]
            status_label = pdf_repair_status_label(it.status)

            risk_note_lines = []
            if req.showRiskNotes:
                risk_note = estimate_risk_note_for_service(it.serviceCode, it.serviceText or "")
                risk_note_lines = wrap_text(risk_note, max_chars=72)[:3]

            findings_lines = []
            if req.showInspectionFindings:
                findings_text = (it.inspectionFindings or "").strip()[:240]
                findings_lines = wrap_text(findings_text, max_chars=72) if findings_text else []

            education_summary_lines = []
            education_symptom_lines = []
            if req.includeServiceEducation:
                education = estimate_service_education(it.serviceCode)
                education_summary = str(education.get("summary") or "").strip()
                if education_summary:
                    education_summary_lines = wrap_text(education_summary, max_chars=72)[:4]
                education_symptoms = education.get("symptoms") or []
                if education_symptoms:
                    symptom_text = "Common reasons this service may be recommended: " + ", ".join(education_symptoms) + "."
                    education_symptom_lines = wrap_text(symptom_text, max_chars=72)[:3]

            labor_breakdown_steps = []
            if req.showDetailedLaborBreakdown:
                lb = build_labor_breakdown(
                    it.serviceCode,
                    estimate_line_billable_labor_hours(it),
                    display_name=it.serviceText,
                )
                if lb and lb.get("steps"):
                    labor_breakdown_steps = lb["steps"]

            is_flat_rate = str(it.pricingMode or "").strip().lower() == "flat"
            billable_labor_hours = estimate_line_billable_labor_hours(it)
            labor_total = max(0.0, float(it.flatRatePrice or 0)) if is_flat_rate else billable_labor_hours * max(0.0, float(it.laborRate or 0))
            parts_total = max(0.0, float(it.partsPrice or 0))
            travel_total = max(0.0, float(it.travelFee or 0))
            computed_total = round(labor_total + parts_total + travel_total)
            cost_parts = []
            if req.showLaborColumn:
                cost_parts.append(f"Labor ${labor_total:,.0f}")
            if req.showPartsColumn:
                cost_parts.append(f"Parts ${parts_total:,.0f}" if parts_total > 0 else "Parts not added")
            if cost_parts and travel_total > 0:
                cost_parts.append(f"Travel ${travel_total:,.0f}")
            if req.showLaborColumn:
                cost_parts.append("Flat-rate service" if is_flat_rate else f"{billable_labor_hours:.1f} labor hrs")
            if cost_parts and req.showHourlyRate and not is_flat_rate:
                cost_parts.append(f"${it.laborRate:.0f}/hr")
            cost_summary_lines = wrap_text("  |  ".join(cost_parts), max_chars=72)[:2]

            content_space = len(service_name_lines) * 12
            content_space += 17  # Status line and gap.
            content_space += len(cost_summary_lines) * 10
            content_space += 7
            if risk_note_lines:
                content_space += 15 + (len(risk_note_lines) * 9)
            if findings_lines:
                content_space += 16 + (len(findings_lines) * 9)
            if education_summary_lines or education_symptom_lines:
                content_space += 16 + ((len(education_summary_lines) + len(education_symptom_lines)) * 9) + 4
            if labor_breakdown_steps:
                breakdown_line_count = 0
                for step in labor_breakdown_steps:
                    label = step.get("label", "")
                    hours = float(step.get("hours", 0))
                    breakdown_line_count += len(wrap_text(f"- {label} ({hours:.1f} hr)", max_chars=64)[:2]) or 1
                content_space += 15 + (breakdown_line_count * 10) + 5
            row_height = max(min_service_row_height, row_pad_top + content_space + row_pad_bottom)
            item_space = row_height + row_gap
            y = pdf_ensure_space(
                c, w, h, y,
                needed=item_space,
                title="Repair Estimate",
                vehicle_line=vehicle_line,
                left=LEFT, right=RIGHT,
                continued_label="Services (continued)",
                show_generated_date=req.showGeneratedDate,
            )
            row_top = y
            row_bottom = row_top - row_height
            c.setFillColorRGB(0.996, 0.998, 0.998)
            c.roundRect(LEFT, row_bottom, X_TOTAL - LEFT, row_height, 7, fill=1, stroke=0)
            c.setStrokeColorRGB(0.80, 0.86, 0.95)
            c.roundRect(LEFT, row_bottom, X_TOTAL - LEFT, row_height, 7, fill=0, stroke=1)
            c.setStrokeGray(0)
            c.setFillGray(0)

            est = float(it.estimate) if it.estimate is not None else computed_total
            grand_total += est

            c.setFont("Helvetica-Bold", 10.6)
            title_y = row_top - row_pad_top
            for index, service_line in enumerate(service_name_lines):
                if index == 0:
                    c.drawString(row_detail_x, title_y, service_line)
                    c.drawRightString(row_total_x, title_y, f"${est:,.0f}")
                else:
                    c.drawString(row_detail_x, title_y, service_line)
                title_y -= 12
            y = title_y - 2

            status_text = f"Status: {status_label}"
            c.setFont("Helvetica-Bold", 8.2)
            c.setFillGray(0.34)
            c.drawString(row_detail_x, y, status_text)
            c.setFillGray(0)
            y -= 15

            c.setFillGray(0.45)
            c.setFont("Helvetica", 8.9)
            for cost_line in cost_summary_lines:
                c.drawString(row_detail_x, y, cost_line)
                y -= 10
            c.setFillGray(0)
            y -= 3

            if risk_note_lines:
                c.setStrokeColorRGB(0.70, 0.82, 0.96)
                c.setLineWidth(1)
                c.line(row_detail_x + 2, y + 2, row_detail_x + 2, y - 9 - (len(risk_note_lines) * 9))
                c.setStrokeGray(0)
                c.setFillGray(0.42)
                c.setFont("Helvetica-Bold", 8)
                c.drawString(row_detail_x + 10, y, "Estimate note")
                y -= 9
                c.setFont("Helvetica", 8.4)
                for note_line in risk_note_lines:
                    c.drawString(row_detail_x + 16, y, note_line)
                    y -= 9
                c.setFillGray(0)
                y -= 2

            if findings_lines:
                c.setStrokeColorRGB(0.80, 0.86, 0.92)
                c.setLineWidth(1)
                c.line(row_detail_x + 2, y + 2, row_detail_x + 2, y - 9 - (len(findings_lines) * 9))
                c.setStrokeGray(0)
                c.setFillGray(0.35)
                c.setFont("Helvetica-Bold", 8)
                c.drawString(row_detail_x + 10, y, "Inspection notes")
                y -= 9

                c.setFillGray(0.28)
                c.setFont("Helvetica", 8.6)
                for finding_line in findings_lines:
                    c.drawString(row_detail_x + 16, y, finding_line)
                    y -= 9
                c.setFillGray(0)
                y -= 2

            if education_summary_lines or education_symptom_lines:
                c.setStrokeColorRGB(0.72, 0.86, 0.78)
                c.setLineWidth(1)
                education_line_count = len(education_summary_lines) + len(education_symptom_lines)
                c.line(row_detail_x + 2, y + 2, row_detail_x + 2, y - 9 - (education_line_count * 9))
                c.setStrokeGray(0)
                c.setFillGray(0.30)
                c.setFont("Helvetica-Bold", 8)
                c.drawString(row_detail_x + 10, y, "Service education")
                y -= 9
                c.setFillGray(0.25)
                c.setFont("Helvetica", 8.4)
                for education_line in education_summary_lines:
                    c.drawString(row_detail_x + 16, y, education_line)
                    y -= 9
                if education_summary_lines and education_symptom_lines:
                    y -= 2
                c.setFont("Helvetica-Oblique", 8.2)
                for symptom_line in education_symptom_lines:
                    c.drawString(row_detail_x + 16, y, symptom_line)
                    y -= 9
                c.setFillGray(0)
                y -= 3

            if labor_breakdown_steps:
                c.setFillGray(0.35)
                c.setFont("Helvetica-Bold", 8)
                c.drawString(row_detail_x + 10, y, "Labor breakdown")
                y -= 10

                c.setFillGray(0.25)
                c.setFont("Helvetica", 8.8)
                for step in labor_breakdown_steps:
                    label = step.get("label", "")
                    hours = float(step.get("hours", 0))
                    for breakdown_line in wrap_text(f"- {label} ({hours:.1f} hr)", max_chars=64)[:2]:
                        c.drawString(row_detail_x + 16, y, breakdown_line)
                        y -= 10

                c.setFillGray(0)
                y -= 3

            y = row_bottom - row_gap

        final_note_lines = wrap_text(CUSTOMER_FINAL_PRICE_NOTE, max_chars=96)[:2] if req.showRiskNotes else []
        customer_note_lines = wrap_text(req.notes.strip(), max_chars=90) if req.notes else []
        customer_phone = format_pdf_phone(req.customerPhone)
        has_signature = bool(req.signatureDataUrl)
        summary_needed = pdf_multi_summary_approval_needed(
            final_note_lines=final_note_lines,
            customer_name=req.customerName,
            customer_phone=customer_phone,
            has_signature=has_signature,
        )

        # Keep Estimate Summary + Customer Approval/signature together when possible.
        y = pdf_ensure_space(
            c, w, h, y,
            needed=summary_needed,
            title="Repair Estimate",
            vehicle_line=vehicle_line,
            left=LEFT, right=RIGHT,
            show_generated_date=req.showGeneratedDate,
        )

        # Grand total
        totals_box_h = 74
        c.setFillColorRGB(0.94, 0.965, 0.99)
        c.roundRect(LEFT, y - totals_box_h + 12, X_TOTAL - LEFT, totals_box_h, 8, fill=1, stroke=0)
        c.setStrokeColorRGB(0.70, 0.81, 0.95)
        c.roundRect(LEFT, y - totals_box_h + 12, X_TOTAL - LEFT, totals_box_h, 8, fill=0, stroke=1)
        c.setStrokeGray(0)
        c.setFont("Helvetica-Bold", 8.5)
        c.setFillColorRGB(0.12, 0.32, 0.62)
        c.drawString(LEFT + 16, y - 3, "ESTIMATE SUMMARY")
        c.setFillColorRGB(0.05, 0.08, 0.13)
        c.setFont("Helvetica-Bold", 13)
        c.drawString(LEFT + 16, y - 22, "Estimated Total")
        c.setFont("Helvetica", 8.6)
        c.setFillGray(0.34)
        c.drawString(LEFT + 16, y - 38, "Includes quoted services, labor, parts, travel, and selected PDF details.")
        c.drawString(LEFT + 16, y - 51, f"{service_count_label.capitalize()} prepared for customer review.")
        c.setFillColorRGB(0.05, 0.08, 0.13)
        c.setFont("Helvetica-Bold", 26)
        c.drawRightString(X_TOTAL - 18, y - 19, f"${grand_total:,.0f}")
        c.setFont("Helvetica", 8.5)
        c.setFillGray(0.38)
        c.drawRightString(X_TOTAL - 18, y - 36, "Customer estimate total")
        c.setFillGray(0)
        y -= 74

        if req.showRiskNotes:
            c.setFillGray(0.42)
            c.setFont("Helvetica", 8.5)
            for line in final_note_lines:
                c.drawString(LEFT + 8, y, line)
                y -= 9
            c.setFillGray(0)
            y -= 10
        else:
            y -= 4

        # Customer review / approval state
        if has_signature:
            approval_title = "Signed Customer Approval"
            approval_line = "Customer reviewed and approved the estimate details with a signature."
            approval_note = "Estimate approval only. No payment is collected or recorded on this PDF."
        elif req.customerAgrees:
            approval_title = "Customer Reviewed Estimate"
            approval_line = "Customer reviewed the estimate details. No signature was captured."
            approval_note = "No payment is collected or recorded on this PDF."
        else:
            approval_title = "Prepared Estimate"
            approval_line = "Prepared for customer review. Not marked reviewed or approved."
            approval_note = "No payment is collected or recorded on this PDF."

        y = pdf_draw_multi_approval_block(
            c,
            w,
            y,
            left=LEFT,
            right=RIGHT,
            approval_title=approval_title,
            approval_line=approval_line,
            approval_note=approval_note,
            customer_name=req.customerName,
            customer_phone=customer_phone,
            signature_data_url=req.signatureDataUrl,
        )

        if customer_note_lines:
            y -= 4
            c.setFont("Helvetica-Bold", 11)
            c.drawString(LEFT, y, "Customer-facing notes")
            y -= 12

            c.setFont("Helvetica", 10)
            for line in customer_note_lines:
                y = pdf_ensure_space(
                    c, w, h, y,
                    needed=12,
                    title="Repair Estimate",
                    vehicle_line=vehicle_line,
                    left=LEFT, right=RIGHT,
                    show_generated_date=req.showGeneratedDate,
                )
                c.drawString(LEFT, y, line)
                y -= 11

            y -= 5

        pdf_draw_footer(c, w)

        c.save()
        buf.seek(0)
        pdf_bytes = buf.getvalue()
        first_line_item = (req.lineItems or [None])[0]
        first_service_title = ""
        if first_line_item is not None:
            first_service_title = estimate_display_service_name(
                first_line_item.displayServiceText or first_line_item.serviceText or first_line_item.serviceCode or "Repair service",
                first_line_item.quantity,
            )
        save_repair_estimate_pdf_if_available(
            req,
            pdf_bytes,
            request=request,
            vehicle_line=vehicle_line,
            related_title=estimate_pdf_related_title(req, first_service_title),
            estimate_total=float(grand_total or 0),
        )

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": "inline; filename=torquemech_estimate.pdf"},
        )

    except HTTPException:
        raise
    except Exception:
        logging.exception("PDF_MULTI_FAILED")
        metric_incr("errors_pdf_multi")
        metric_incr("errors_total")
        raise

@app.get("/electrical", response_class=HTMLResponse)
def electrical_hub(request: Request):
    return templates.TemplateResponse("electrical/index.html", {"request": request})

@app.get("/electrical/wiring", response_class=HTMLResponse)
def electrical_wiring_index(request: Request):
    return templates.TemplateResponse("electrical/wiring/index.html", {"request": request})

@app.get("/electrical/wiring/relay-wiring", response_class=HTMLResponse)
def electrical_relay_wiring(request: Request):
    return templates.TemplateResponse("electrical/wiring/relay_wiring.html", {"request": request})

@app.get("/electrical/wiring/trailer-wiring", response_class=HTMLResponse)
def electrical_trailer_wiring(request: Request):
    return templates.TemplateResponse("electrical/wiring/trailer_wiring.html", {"request": request})

@app.get("/electrical/components", response_class=HTMLResponse)
def electrical_components_index(request: Request):
    return templates.TemplateResponse("electrical/components/index.html", {"request": request})

@app.get("/electrical/fuse-relay", response_class=HTMLResponse)
def electrical_fuse_relay_index(request: Request):
    return templates.TemplateResponse("electrical/fuse_relay/index.html", {"request": request})

@app.get("/electrical/fuse-relay/fuse-guide", response_class=HTMLResponse)
def electrical_fuse_guide(request: Request):
    return templates.TemplateResponse(
        "electrical/fuse_relay/fuse-guide.html",
        {"request": request},
    )

@app.get("/electrical/diagnostics", response_class=HTMLResponse)
def electrical_diagnostics_index(request: Request):
    return templates.TemplateResponse("electrical/diagnostics/index.html", {"request": request})

@app.get("/electrical/diagnostics/voltage-drop-test", response_class=HTMLResponse)
def electrical_voltage_drop_test(request: Request):
    return templates.TemplateResponse(
        "electrical/diagnostics/voltage-drop-test.html",
        {"request": request},
    )

@app.get("/electrical/pinouts", response_class=HTMLResponse)
def electrical_pinouts_index(request: Request):
    return templates.TemplateResponse("electrical/pinouts/index.html", {"request": request})

@app.get("/electrical/fundamentals", response_class=HTMLResponse)
def electrical_fundamentals_index(request: Request):
    return templates.TemplateResponse("electrical/fundamentals/index.html", {"request": request})

@app.get("/electrical/fundamentals/ground-circuits")
def electrical_ground_circuits(request: Request):
    return templates.TemplateResponse(
        "electrical/fundamentals/ground-circuits.html",
        {"request": request}
    )

@app.get("/electrical/wiring/5-pin-relay", response_class=HTMLResponse)
def electrical_5_pin_relay(request: Request):
    return templates.TemplateResponse(
        "electrical/wiring/5_pin_relay.html",
        {"request": request},
    )

@app.get("/electrical/wiring/starter", response_class=HTMLResponse)
def electrical_starter(request: Request):
    return templates.TemplateResponse(
        "electrical/wiring/starter_system.html",
        {"request": request},
    )

@app.get("/electrical/wiring/sensor-circuit", response_class=HTMLResponse)
def electrical_sensor_circuit(request: Request):
    return templates.TemplateResponse(
        "electrical/wiring/sensor_circuit.html",
        {"request": request},
    )

@app.get("/estimate/share/{estimate_id}", response_class=HTMLResponse)
def open_shared_estimate(request: Request, estimate_id: str):
    return templates.TemplateResponse(
        "estimator.html",
        {
            "request": request,
            "shared_id": estimate_id,
        },
    )

@app.get("/repair-guides/{slug}")
async def repair_guide_page(request: Request, slug: str):
    raw_guide = load_json_file("repair_guides", f"{slug.replace('-', '_')}.json")
    guide = normalize_repair_guide(raw_guide, slug=slug)
    vehicle_context = build_vehicle_context_from_request(request)
    workflow_context = build_workflow_context(
        request,
        vehicle_context=vehicle_context,
        service_code=str((guide.get("estimate") or {}).get("service_code") or ""),
        source="repair-guide",
    )

    if guide.get("estimate"):
        estimate = dict(guide["estimate"])
        estimate_href = build_estimator_service_href(
            estimate.get("service_code") or "",
            estimate.get("estimator_link") or "/estimator",
        )
        estimate["href"] = append_workflow_context_to_href(estimate_href, workflow_context)
        guide["estimate"] = estimate

    vehicle_torque_specs = get_repair_guide_vehicle_torque_specs(
        slug,
        vehicle_context.get("year") or "",
        vehicle_context.get("make") or "",
        vehicle_context.get("model") or "",
    )
    if vehicle_torque_specs:
        guide["verified_torque_specs"] = [
            {"label": label, "value": value}
            for label, value in vehicle_torque_specs.items()
        ]

    guide = apply_repair_intelligence_network(guide)
    guide["recommended_repairs"] = apply_workflow_context_to_repair_items(
        guide.get("recommended_repairs") or [],
        workflow_context,
    )
    guide["related_repair_guides"] = apply_workflow_context_to_repair_items(
        guide.get("related_repair_guides") or [],
        workflow_context,
    )
    guide["related_symptoms"] = apply_workflow_context_to_repair_items(
        guide.get("related_symptoms") or [],
        workflow_context,
    )
    guide["related_diagnostic_links"] = apply_workflow_context_to_repair_items(
        guide.get("related_diagnostic_links") or [],
        workflow_context,
    )
    guide["bundled_repair_suggestions"] = apply_workflow_context_to_repair_items(
        guide.get("bundled_repair_suggestions") or [],
        workflow_context,
    )
    if guide.get("diagnostic_context"):
        diagnostic_context = dict(guide["diagnostic_context"])
        for key in ("common_symptoms_link", "diagnostic_tools_link"):
            linked_href = str(diagnostic_context.get(key) or "").strip()
            if linked_href.startswith(("/symptoms/", "/diagnostics", "/repair-systems/", "/obd/")):
                diagnostic_context[key] = append_workflow_context_to_href(
                    linked_href,
                    workflow_context,
                )
        guide["diagnostic_context"] = diagnostic_context
    guide["related_system_hubs"] = apply_workflow_context_to_repair_items(
        infer_related_system_hubs(
            guide.get("title"),
            guide.get("summary"),
            guide.get("category"),
            guide.get("subcategory"),
            " ".join(guide.get("related_systems") or []),
            " ".join(guide.get("symptoms") or []),
            " ".join(guide.get("inspect_first") or []),
            " ".join(item.get("code") or "" for item in guide.get("related_obd_codes") or []),
        ),
        workflow_context,
    )
    workflow_signal = [
        guide.get("title"),
        guide.get("summary"),
        guide.get("category"),
        guide.get("subcategory"),
        " ".join(guide.get("related_systems") or []),
        " ".join(guide.get("symptoms") or []),
        " ".join(guide.get("inspect_first") or []),
        " ".join(item.get("code") or "" for item in guide.get("related_obd_codes") or []),
    ]
    guide["workflow_next_steps"] = apply_workflow_context_to_repair_items(
        infer_workflow_next_steps(*workflow_signal),
        workflow_context,
    )
    guide["related_inspections"] = apply_workflow_context_to_repair_items(
        infer_related_inspections(*workflow_signal),
        workflow_context,
    )
    guide.update(build_repair_guide_intelligence_expansion(guide))

    return templates.TemplateResponse(
        "repair_guide.html",
        {
            "request": request,
            "guide": guide,
            "vehicle_context": vehicle_context,
            "workflow_context": workflow_context,
            "page_title": f"{guide.get('title', 'Repair Guide')} | TorqueMech",
            "meta_description": guide.get("summary", "TorqueMech repair guide"),
        },
    )


@app.get("/repair-systems/{slug}", response_class=HTMLResponse)
async def repair_system_hub_page(request: Request, slug: str):
    raw_hub, source_slug = load_system_hub_source(slug)
    hub = normalize_system_hub_entry(raw_hub, file_slug=source_slug)
    vehicle_context = build_vehicle_context_from_request(request)
    workflow_context = build_workflow_context(
        request,
        vehicle_context=vehicle_context,
        service_code=str((hub.get("estimate") or {}).get("service_code") or ""),
        source="system-hub",
    )

    hub["common_symptoms"] = apply_workflow_context_to_repair_items(
        hub.get("common_symptoms") or [],
        workflow_context,
    )
    hub["related_symptoms"] = apply_workflow_context_to_repair_items(
        hub.get("related_symptoms") or [],
        workflow_context,
    )
    hub["related_obd_codes"] = apply_workflow_context_to_repair_items(
        hub.get("related_obd_codes") or [],
        workflow_context,
    )
    hub["related_repairs"] = apply_workflow_context_to_repair_items(
        hub.get("related_repairs") or [],
        workflow_context,
    )
    contextual_path_sections = []
    for section in hub.get("diagnostic_path_sections") or []:
        contextual_section = dict(section)
        contextual_section["repairs"] = apply_workflow_context_to_repair_items(
            section.get("repairs") or [],
            workflow_context,
        )
        contextual_path_sections.append(contextual_section)
    hub["diagnostic_path_sections"] = contextual_path_sections
    hub["related_systems"] = apply_workflow_context_to_repair_items(
        hub.get("related_systems") or [],
        workflow_context,
    )
    hub_signal = [
        hub.get("title"),
        hub.get("summary"),
        hub.get("intro"),
        " ".join(item.get("code") or "" for item in hub.get("related_obd_codes") or []),
        " ".join(item.get("title") or "" for item in hub.get("related_repairs") or []),
        " ".join(item.get("title") or "" for item in hub.get("common_symptoms") or []),
    ]
    hub["workflow_next_steps"] = apply_workflow_context_to_repair_items(
        infer_workflow_next_steps(*hub_signal),
        workflow_context,
    )
    hub["related_inspections"] = apply_workflow_context_to_repair_items(
        infer_related_inspections(*hub_signal),
        workflow_context,
    )

    estimate = dict(hub.get("estimate") or {})
    estimate["href"] = append_workflow_context_to_href(
        estimate.get("href") or estimate.get("estimator_link") or "/estimator",
        workflow_context,
    )
    hub["estimate"] = estimate

    return templates.TemplateResponse(
        "repair_system_hub.html",
        {
            "request": request,
            "hub": hub,
            "vehicle_context": vehicle_context,
            "workflow_context": workflow_context,
            "page_title": f"{hub.get('title', 'Repair System Hub')} | TorqueMech",
            "meta_description": hub.get("summary", "TorqueMech repair system hub"),
        },
    )


@app.get("/symptoms/{slug}")
async def symptom_page(request: Request, slug: str):
    raw_symptom = load_json_file("symptoms", f"{slug.replace('-', '_')}.json")
    repair_guides = load_normalized_repair_guides_map()
    symptom = normalize_symptom_entry(raw_symptom, file_slug=slug.replace("-", "_"), repair_guides=repair_guides)
    vehicle_context = build_vehicle_context_from_request(request)
    workflow_context = build_workflow_context(
        request,
        vehicle_context=vehicle_context,
        source="symptom",
    )
    symptom["related_repair_guides"] = apply_workflow_context_to_repair_items(
        symptom.get("related_repair_guides") or [],
        workflow_context,
    )
    symptom["recommended_repairs"] = apply_workflow_context_to_repair_items(
        symptom.get("recommended_repairs") or [],
        workflow_context,
    )
    contextual_path_sections = []
    for section in symptom.get("diagnostic_path_sections") or []:
        contextual_section = dict(section)
        contextual_section["repairs"] = apply_workflow_context_to_repair_items(
            section.get("repairs") or [],
            workflow_context,
        )
        contextual_path_sections.append(contextual_section)
    symptom["diagnostic_path_sections"] = contextual_path_sections
    symptom["related_obd_codes"] = apply_workflow_context_to_repair_items(
        symptom.get("related_obd_codes") or [],
        workflow_context,
    )
    symptom["related_symptoms"] = apply_workflow_context_to_repair_items(
        symptom.get("related_symptoms") or [],
        workflow_context,
    )
    symptom["estimator_links"] = apply_workflow_context_to_repair_items(
        symptom.get("estimator_links") or [],
        workflow_context,
    )
    symptom["likely_causes"] = [
        {
            "name": item.get("title"),
            "repair_guide_link": item.get("href"),
            "estimator_link": item.get("estimator_href"),
        }
        for item in symptom.get("related_repair_guides", [])
    ]
    symptom["estimator_link"] = (
        (symptom.get("estimator_links") or [{}])[0].get("href") or "/estimator"
    )
    symptom["estimator_link"] = append_workflow_context_to_href(
        symptom["estimator_link"],
        workflow_context,
    )
    symptom["related_system_hubs"] = apply_workflow_context_to_repair_items(
        infer_related_system_hubs(
            symptom.get("title"),
            symptom.get("summary"),
            symptom.get("system"),
            " ".join(symptom.get("possible_causes") or []),
            " ".join(symptom.get("quick_checks") or []),
            " ".join(item.get("code") or "" for item in symptom.get("related_obd_codes") or []),
            " ".join(item.get("title") or "" for item in symptom.get("recommended_repairs") or []),
        ),
        workflow_context,
    )
    symptom_signal = [
        symptom.get("title"),
        symptom.get("summary"),
        symptom.get("system"),
        " ".join(symptom.get("possible_causes") or []),
        " ".join(symptom.get("quick_checks") or []),
        " ".join(item.get("code") or "" for item in symptom.get("related_obd_codes") or []),
        " ".join(item.get("title") or "" for item in symptom.get("recommended_repairs") or []),
    ]
    symptom["workflow_next_steps"] = apply_workflow_context_to_repair_items(
        infer_workflow_next_steps(*symptom_signal),
        workflow_context,
    )
    symptom["related_inspections"] = apply_workflow_context_to_repair_items(
        infer_related_inspections(*symptom_signal),
        workflow_context,
    )
    return templates.TemplateResponse(
        "symptom_page.html",
        {
            "request": request,
            "symptom": symptom,
            "vehicle_context": vehicle_context,
            "workflow_context": workflow_context,
            "page_title": f"{symptom.get('title', 'Symptom Guide')} | TorqueMech",
            "meta_description": symptom.get("summary", "TorqueMech symptom guide"),
        },
    )


@app.get("/diagnostics/{slug}")
async def diagnostic_page(request: Request, slug: str):
    raw_diagnostic, source_slug = load_diagnostic_source(slug)
    repair_guides = load_normalized_repair_guides_map()
    diagnostic = normalize_diagnostic_entry(raw_diagnostic, file_slug=source_slug, repair_guides=repair_guides)
    vehicle_context = build_vehicle_context_from_request(request)
    workflow_context = build_workflow_context(
        request,
        vehicle_context=vehicle_context,
        source="diagnostic",
    )
    diagnostic["related_repair_guides"] = apply_workflow_context_to_repair_items(
        diagnostic.get("related_repair_guides") or [],
        workflow_context,
    )
    diagnostic["estimator_links"] = apply_workflow_context_to_repair_items(
        diagnostic.get("estimator_links") or [],
        workflow_context,
    )
    diagnostic["common_causes"] = diagnostic.get("possible_causes") or []
    diagnostic["likely_repairs"] = [
        {
            "name": item.get("title"),
            "repair_guide_link": item.get("href"),
            "estimator_link": item.get("estimator_href"),
        }
        for item in diagnostic.get("related_repair_guides", [])
    ]
    diagnostic["estimator_link"] = (
        (diagnostic.get("estimator_links") or [{}])[0].get("href") or "/estimator"
    )
    diagnostic["estimator_link"] = append_workflow_context_to_href(
        diagnostic["estimator_link"],
        workflow_context,
    )
    return templates.TemplateResponse(
        "diagnostic_page.html",
        {
            "request": request,
            "diagnostic": diagnostic,
            "vehicle_context": vehicle_context,
            "workflow_context": workflow_context,
            "page_title": f"{diagnostic.get('title', 'Diagnostic Guide')} | TorqueMech",
            "meta_description": diagnostic.get("summary", "TorqueMech diagnostic guide"),
        },
    )


@app.get("/{maybe_estimate_id}", include_in_schema=False)
def shared_estimate_short_link(maybe_estimate_id: str):
    if not SHARED_ESTIMATE_UUID_RE.fullmatch(maybe_estimate_id or ""):
        raise HTTPException(status_code=404, detail="Not found")

    return RedirectResponse(
        url=f"/estimate/share/{maybe_estimate_id}",
        status_code=307,
    )

@app.get("/cost/ignition-coil-replacement", response_class=HTMLResponse)
async def cost_ignition_coil(request: Request):
    return templates.TemplateResponse(
        "cost_ignition_coil_replacement.html",
        {"request": request}
    )


@app.get("/cost/mass-air-flow-sensor-replacement", response_class=HTMLResponse)
async def cost_maf(request: Request):
    return templates.TemplateResponse(
        "cost_mass_air_flow_sensor_replacement.html",
        {"request": request}
    )


@app.get("/cost/evap-purge-valve-replacement", response_class=HTMLResponse)
async def cost_purge_valve(request: Request):
    return templates.TemplateResponse(
        "cost_evap_purge_valve_replacement.html",
        {"request": request}
    )


@app.get("/cost/evap-vent-valve-replacement", response_class=HTMLResponse)
async def cost_vent_valve(request: Request):
    return templates.TemplateResponse(
        "cost_evap_vent_valve_replacement.html",
        {"request": request}
    )


@app.get("/cost/throttle-body-replacement", response_class=HTMLResponse)
async def cost_throttle_body(request: Request):
    return templates.TemplateResponse(
        "cost_throttle_body_replacement.html",
        {"request": request}
    )


@app.get("/cost/camshaft-position-sensor-replacement", response_class=HTMLResponse)
async def cost_camshaft_sensor(request: Request):
    return templates.TemplateResponse(
        "cost_camshaft_position_sensor_replacement.html",
        {"request": request}
    )

@app.get("/cost")
def cost_index(request: Request):
    return templates.TemplateResponse(
        "cost_index.html",
        {"request": request}
    )
