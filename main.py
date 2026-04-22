from __future__ import annotations

import base64
import io
import json
import sqlite3
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

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
from routers.pro import router as pro_router

from fastapi.middleware.cors import CORSMiddleware
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

from dotenv import load_dotenv
import os

from fastapi.templating import Jinja2Templates


import sqlite3
import json


from pathlib import Path
from fastapi.responses import HTMLResponse

from app.data.labor_profiles import build_labor_breakdown, get_service_labor_profile

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

app = FastAPI()

from starlette.middleware.base import BaseHTTPMiddleware

class CanonicalHostMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        host = request.headers.get("host", "")
        if host.startswith("www."):
            url = request.url.replace(netloc=host.replace("www.", ""))
            return RedirectResponse(str(url), status_code=301)
        return await call_next(request)

app.add_middleware(CanonicalHostMiddleware)

# --- Paths ---
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
SERVICES_CATALOG_PATH = BASE_DIR / "services_catalog.json"

STATE_DIR = Path("/data") if Path("/data").exists() else BASE_DIR / ".localstate"
STATE_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = str((STATE_DIR / "app.db").resolve())
USE_LOCAL_SQLITE_COMPAT = not Path("/data").exists()

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


def app_db_conn(*, row_factory: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    if USE_LOCAL_SQLITE_COMPAT:
        # OneDrive-backed local workspaces can fail on default rollback-journal commits.
        conn.execute("PRAGMA journal_mode=MEMORY")
        conn.execute("PRAGMA synchronous=NORMAL")
    if row_factory:
        conn.row_factory = sqlite3.Row
    return conn


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
        value_text = str(value or "").strip()
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
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.state.templates = templates

# routers
app.include_router(knowledge_router)
app.include_router(pro_router)

# --- Static Mount ---
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
        conn.execute("PRAGMA journal_mode=MEMORY")
        conn.execute("PRAGMA synchronous=NORMAL")
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
    seed_entry = load_obd_seed_entry(code) or {}
    return {
        "causes": normalize_obd_text_list(seed_entry.get("causes")),
        "symptoms": normalize_obd_text_list(seed_entry.get("symptoms")),
        "diagnostic_steps": normalize_obd_text_list(seed_entry.get("diagnostic_steps")),
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

@app.get("/about", response_class=HTMLResponse)
def about(request: Request):
    return templates.TemplateResponse("about.html", {"request": request})


@app.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
def privacy(request: Request):
    return templates.TemplateResponse("privacy.html", {"request": request})


@app.get("/terms", response_class=HTMLResponse, include_in_schema=False)
def terms(request: Request):
    return templates.TemplateResponse("terms.html", {"request": request})


@app.get("/disclaimer", response_class=HTMLResponse, include_in_schema=False)
def disclaimer(request: Request):
    return templates.TemplateResponse("disclaimer.html", {"request": request})

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

    init_db()
    init_metrics_db()
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
    normalized["repair_overview"] = normalize_repair_guide_list(guide.get("repair_overview"))
    normalized["tools_required"] = normalize_repair_guide_list(
        guide.get("tools_required") or guide.get("tools")
    )
    normalized["repair_steps"] = normalize_repair_guide_list(
        guide.get("repair_steps") or guide.get("steps")
    )
    normalized["pro_tips"] = normalize_repair_guide_list(guide.get("pro_tips"))
    normalized["warnings"] = normalize_repair_guide_list(
        guide.get("warnings") or guide.get("watchouts")
    )
    normalized["bolt_sizes"] = normalize_repair_guide_list(guide.get("bolt_sizes"))
    normalized["coming_next"] = normalize_repair_guide_list(guide.get("coming_next"))

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
    for spec in guide.get("torque_specs", []):
        if not isinstance(spec, dict):
            continue

        label = str(spec.get("label") or spec.get("part") or "").strip()
        value = str(spec.get("value") or spec.get("spec") or "").strip()
        if label and value:
            normalized_specs.append({"label": label, "value": value})
    normalized["torque_specs"] = normalized_specs

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

    return {
        "slug": canonical_slug,
        "code": code,
        "title": title,
        "summary": str(raw.get("summary") or "").strip(),
        "meaning": str(raw.get("meaning") or "").strip(),
        "quick_checks": normalize_repair_guide_list(raw.get("quick_checks")),
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

    return {
        "slug": file_slug.replace("_", "-"),
        "title": str(raw.get("title") or file_slug.replace("-", " ").replace("_", " ").title()).strip(),
        "summary": str(raw.get("summary") or "").strip(),
        "intro": str(raw.get("intro") or "").strip(),
        "system": str(raw.get("system") or raw.get("category") or "").strip(),
        "common_sounds": normalize_repair_guide_list(raw.get("common_sounds")),
        "quick_checks": normalize_repair_guide_list(raw.get("quick_checks")),
        "possible_causes": possible_causes,
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
        {
            "request": request,
            "quick_find_items": build_quick_find_items(),
        },
    )


@app.get("/estimator", response_class=HTMLResponse)
def estimator(request: Request):
    metric_incr("page_estimator")
    return templates.TemplateResponse(
        "estimator.html",
        {"request": request},
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

    def add_candidate(candidate_code: str, fallback_label: str = "") -> None:
        candidate_code = str(candidate_code or "").upper().strip()
        if not candidate_code or candidate_code in seen_codes or candidate_code not in available_titles:
            return
        seen_codes.add(candidate_code)
        related.append(
            {
                "code": candidate_code,
                "label": available_titles.get(candidate_code) or fallback_label or candidate_code,
            }
        )

    for candidate_code in preferred_codes or []:
        if len(related) >= max_items:
            break
        add_candidate(candidate_code)

    if len(related) >= max_items:
        return related[:max_items]

    curated_related_map = {
        "P0300": ["P0301", "P0302", "P0303", "P0304"],
        "P0301": ["P0300", "P0302", "P0303", "P0304"],
        "P0302": ["P0300", "P0301", "P0303", "P0304"],
        "P0303": ["P0300", "P0301", "P0302", "P0304"],
        "P0304": ["P0300", "P0301", "P0302", "P0303"],
        "P0171": ["P0174"],
        "P0128": [],
        "P0420": ["P0430"],
        "P0430": ["P0420"],
        "P0562": ["P0563"],
        "P0563": ["P0562"],
    }

    if code in curated_related_map:
        for candidate_code in curated_related_map[code]:
            add_candidate(candidate_code)
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
            {"label": "Vacuum leak smoke test", "service_query": "vacuum leak diagnosis"},
            {"label": "Fuel system diagnostic", "service_query": "fuel system diagnostic"},
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
            {"label": "EVAP vent valve replacement", "service_query": "evap vent valve replacement"},
        ],
        "P0456": [
            {"label": "Gas cap replacement", "service_query": "gas cap replacement"},
            {"label": "EVAP leak smoke test", "service_query": "evap leak smoke test"},
            {"label": "EVAP small leak diagnosis", "service_query": "evap small leak diagnosis"},
            {"label": "EVAP vent valve replacement", "service_query": "evap vent valve replacement"},
        ],
        "P2195": [
            {"label": "Air fuel ratio sensor replacement", "service_query": "air fuel ratio sensor replacement"},
            {"label": "Upstream oxygen sensor replacement", "service_query": "oxygen sensor replacement upstream"},
            {"label": "Vacuum leak smoke test", "service_query": "vacuum leak diagnosis"},
            {"label": "Fuel system diagnostic", "service_query": "fuel system diagnostic"},
        ],
    }

    return repair_map.get(code, [])

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
                "description": "Useful when testing confirms worn or fouled plugs are driving the misfire.",
            },
            {
                "label": "Ignition Coil Replacement Cost",
                "href": "/cost/ignition-coil-replacement",
                "description": "A strong next cost check when the misfire path points to a weak or failing coil.",
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
        ],
        "P0303": [
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
        ],
        "P0101": [
            {
                "label": "Mass Air Flow Sensor Replacement Cost",
                "href": "/cost/mass-air-flow-sensor-replacement",
                "description": "A strong next cost check when airflow readings or contamination point back to the MAF sensor.",
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
                "description": "Relevant when large-leak diagnosis shows the purge valve is stuck open or not sealing.",
            },
        ],
        "P0456": [
            {
                "label": "EVAP Purge Valve Replacement Cost",
                "href": "/cost/evap-purge-valve-replacement",
                "description": "Useful when very small leak diagnosis traces back to purge-valve seepage or poor sealing.",
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
                "description": "Useful when low system voltage starts with a weak battery that fails load or conductance testing.",
            },
            {
                "label": "Alternator Replacement Cost",
                "href": "/cost/alternator-replacement",
                "description": "A strong next cost check when charging voltage stays low because the alternator is not keeping up.",
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
                "description": "A strong next cost check when airflow readings or contamination point to the MAF sensor as the lean-condition trigger.",
            },
        ],
        "P0174": [
            {
                "label": "Mass Air Flow Sensor Replacement Cost",
                "href": "/cost/mass-air-flow-sensor-replacement",
                "description": "A strong next cost check when airflow readings or contamination point to the MAF sensor as the lean-condition trigger.",
            },
        ],
        "P0128": [
            {
                "label": "Thermostat Replacement Cost",
                "href": "/cost/thermostat-replacement",
                "description": "The strongest cost guide when slow warm-up and temperature data point to a thermostat stuck open.",
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
                "description": "A direct cost guide when catalyst-efficiency testing confirms the converter is no longer doing the job.",
            },
            {
                "label": "Oxygen Sensor Replacement Cost",
                "href": "/cost/oxygen-sensor-replacement",
                "description": "Relevant when testing points to a weak downstream O2 sensor instead of a failed converter.",
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
            "title": "P0303 Cylinder 3 Misfire: Start With Plug or Coil | TorqueMech",
            "description": "P0303 means cylinder 3 is misfiring. Start with the plug or coil, then check injector, intake-runner leak, and compression faults with practical repair guidance.",
        },
        "P0304": {
            "title": "P0304 Cylinder 4 Misfire: Causes & Repairs | TorqueMech",
            "description": "P0304 means cylinder 4 is misfiring. Review the most common ignition, fuel, air, or compression causes, likely repairs, and cost guidance.",
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
            "title": "P0420 Catalyst Efficiency Bank 1: Causes & Repairs | TorqueMech",
            "description": "P0420 means bank 1 catalyst efficiency is below threshold. Review common converter and O2 sensor causes, likely repairs, and related cost guidance.",
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
            "title": "P0562 System Voltage Low: Causes & Repairs | TorqueMech",
            "description": "P0562 means system voltage is low. Check for a weak battery, charging issues, or wiring faults with likely repairs and battery or alternator cost guidance.",
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
            "meaning": "P0300 means the ECM has detected misfires across multiple cylinders instead of one isolated cylinder. The next step is separating an ignition problem from a fuel, air, or engine condition affecting more than one cylinder.",
            "diagnostic_insight_intro": "P0300 points to a shared problem affecting multiple cylinders, not just one isolated hole.",
            "diagnostic_insight_points": [
                "Multiple-cylinder misfires usually mean ignition, fuel delivery, airflow, or timing should be checked before replacing single-cylinder parts.",
                "If cylinder-specific codes appear with P0300, use them to narrow the first physical checks.",
                "A flashing check-engine light means catalyst damage risk is higher.",
            ],
            "symptoms": [
                "Rough idle or shaking at a stop",
                "Hesitation or stumbling under load",
                "Flashing check-engine light during active misfire",
            ],
            "quick_checks": [
                "Check whether cylinder-specific misfire codes are stored with P0300",
                "Inspect spark plugs, coil boots, and plug wells for wear, oil, or carbon tracking",
                "Check for intake or vacuum leaks at hoses, PCV plumbing, and the air duct",
                "Review fuel trims and fuel pressure before condemning ignition parts alone",
            ],
        },
        "P0301": {
            "meaning": "P0301 means cylinder 1 is misfiring often enough for the ECM to flag it. Diagnosis should confirm whether the fault follows the plug or coil, stays with the injector, or points to a mechanical problem in that cylinder.",
            "diagnostic_insight_intro": "A cylinder-specific misfire usually means the fault is localized to that cylinder's ignition, fuel delivery, or mechanical condition.",
            "diagnostic_insight_points": [
                "If the misfire follows a coil or spark plug swap, the part is the likely cause.",
                "If it stays on cylinder 1, injector or compression testing becomes more important.",
                "Cold-start misfires that improve when warm can point to ignition weakness or minor sealing problems.",
            ],
            "symptoms": [
                "Rough idle or shake tied to one cylinder",
                "Hesitation or light bucking on acceleration",
                "Fuel smell or flashing MIL if the misfire is severe",
            ],
            "quick_checks": [
                "Swap the cylinder 1 coil with another cylinder and see whether the misfire follows",
                "Inspect the cylinder 1 spark plug for wear, gap, oil, or fuel fouling",
                "Check injector connector fit and listen for injector operation on cylinder 1",
                "Run compression or leak-down testing if the misfire stays on cylinder 1",
            ],
        },
        "P0302": {
            "meaning": "P0302 means cylinder 2 is misfiring often enough for the ECM to detect it. The most useful next step is confirming whether the fault tracks with the ignition parts, fuel injector, intake leak, or cylinder condition.",
            "diagnostic_insight_intro": "P0302 should be treated as a focused cylinder fault until testing proves otherwise.",
            "diagnostic_insight_points": [
                "Swap-based testing is often the fastest way to separate ignition faults from engine faults.",
                "If the misfire remains fixed on cylinder 2, move to injector and compression checks.",
                "Under-load misfires often raise fuel delivery suspicion.",
            ],
            "symptoms": [
                "Rough idle or uneven engine note",
                "Reduced power or stumble on throttle",
                "Fuel smell or flashing MIL during a heavy misfire",
            ],
            "quick_checks": [
                "Swap the cylinder 2 coil with another cylinder and see whether the misfire follows",
                "Inspect the cylinder 2 spark plug for wear, gap, oil, or fuel fouling",
                "Check injector connector fit and listen for injector operation on cylinder 2",
                "Run compression or leak-down testing if the misfire stays on cylinder 2",
            ],
        },
        "P0303": {
            "meaning": "P0303 means cylinder 3 is misfiring often enough for the ECM to flag it. Good diagnosis confirms whether the problem follows the plug or coil, stays with the injector, or points to compression loss on that cylinder.",
            "diagnostic_insight_intro": "P0303 is typically isolated to ignition, fuel delivery, or mechanical condition within cylinder 3.",
            "diagnostic_insight_points": [
                "If the misfire is worse on cold start and improves when warm, suspect ignition weakness or minor sealing issues.",
                "If the misfire is constant under load, fuel delivery becomes more likely.",
                "If the misfire does not move when swapping components, compression or internal engine issues should be considered.",
            ],
            "symptoms": [
                "Noticeable shake at idle",
                "Hesitation or stumble under load",
                "Reduced fuel economy while the misfire is active",
            ],
            "quick_checks": [
                "Swap the cylinder 3 coil with another cylinder and see whether the misfire follows",
                "Inspect the cylinder 3 spark plug for wear, gap, oil, or fuel fouling",
                "Check injector connector fit and listen for injector operation on cylinder 3",
                "Run compression or leak-down testing if the misfire stays on cylinder 3",
            ],
        },
        "P0304": {
            "meaning": "P0304 means cylinder 4 is misfiring often enough for the ECM to detect it. The repair path usually becomes clear once you confirm whether the fault follows ignition parts, stays with fuel delivery, or points to a mechanical issue.",
            "diagnostic_insight_intro": "P0304 usually becomes clear once you confirm whether the fault follows ignition parts or stays with the cylinder.",
            "diagnostic_insight_points": [
                "A moved misfire usually means the swapped part is bad.",
                "A fixed misfire often means injector, compression, or valve-sealing checks come next.",
                "A severe active misfire should be repaired quickly to reduce catalyst risk.",
            ],
            "symptoms": [
                "Rough idle or steady vibration",
                "Hesitation or weak pull during acceleration",
                "Flashing MIL if the misfire is strong enough to threaten the catalyst",
            ],
            "quick_checks": [
                "Swap the cylinder 4 coil with another cylinder and see whether the misfire follows",
                "Inspect the cylinder 4 spark plug for wear, gap, oil, or fuel fouling",
                "Check injector connector fit and listen for injector operation on cylinder 4",
                "Run compression or leak-down testing if the misfire stays on cylinder 4",
            ],
        },
        "P0171": {
            "meaning": "P0171 means bank 1 is running lean because the engine is seeing more air than expected, less fuel than needed, or an airflow reading that is off. The fault is often a vacuum leak, MAF problem, or low-fuel-delivery issue rather than a single bad part by itself.",
            "symptoms": [
                "Rough idle or light surge at idle",
                "Hesitation on tip-in or light acceleration",
                "Poor cold-start behavior or reduced fuel economy",
            ],
            "quick_checks": [
                "Check short- and long-term fuel trims at idle and at cruise",
                "Inspect intake boots, PCV hoses, and vacuum lines for leaks after the MAF",
                "Inspect and clean the MAF sensor and confirm the air filter box is sealed",
                "Check fuel pressure or delivery volume if trims stay lean under load",
            ],
        },
        "P0174": {
            "meaning": "P0174 means bank 2 is running lean because the ECM is correcting for too much air, too little fuel, or an inaccurate airflow signal on that bank. Common root causes include vacuum leaks, MAF issues, and fuel-delivery problems that affect both banks or bank 2 more strongly.",
            "symptoms": [
                "Light surge or rough idle",
                "Hesitation during part-throttle driving",
                "Weak cold-start or lean stumble symptoms",
            ],
            "quick_checks": [
                "Check short- and long-term fuel trims at idle and cruise to see when the lean condition appears",
                "Inspect intake boots, PCV plumbing, and vacuum lines for leaks after the MAF",
                "Inspect and clean the MAF sensor and confirm the intake tract is sealed",
                "Check fuel pressure or delivery volume if trims stay lean under load",
            ],
        },
        "P0420": {
            "meaning": "P0420 means bank 1 catalyst efficiency tested below the expected threshold after the ECM compared upstream and downstream oxygen sensor behavior. The converter may be weak, but exhaust leaks, sensor faults, or an unresolved misfire or fuel-control problem can set the same code.",
            "symptoms": [
                "Often no clear drivability symptom",
                "Failed emissions readiness or inspection",
                "Possible sulfur smell or loss of power if the converter is restricted",
            ],
            "quick_checks": [
                "Fix active misfire, rich-running, or fuel-trim codes before blaming the converter",
                "Check for exhaust leaks ahead of the converter and oxygen sensors",
                "Compare upstream and downstream O2 sensor behavior on a scan tool",
                "Confirm the engine reaches normal operating temperature before final catalyst judgment",
            ],
        },
        "P0430": {
            "meaning": "P0430 means bank 2 catalyst efficiency tested below the expected threshold after the ECM compared upstream and downstream oxygen sensor behavior. The converter may be aging, but exhaust leaks, sensor faults, or unresolved fueling issues can also trigger it.",
            "symptoms": [
                "Often no obvious drivability complaint",
                "Failed emissions readiness or inspection",
                "Possible sulfur smell or power loss if the converter is restricted",
            ],
            "quick_checks": [
                "Fix active misfire, rich-running, or fuel-trim codes before blaming the converter",
                "Check for exhaust leaks ahead of the bank 2 converter and sensors",
                "Compare upstream and downstream bank 2 O2 sensor behavior on a scan tool",
                "Confirm the engine reaches normal operating temperature before final catalyst judgment",
            ],
        },
        "P0455": {
            "meaning": "P0455 means the EVAP system detected a large leak during its self-test. In many cases the leak is simple, like a loose gas cap or disconnected hose, but a stuck valve or cracked line can also keep the system from sealing.",
            "symptoms": [
                "Check-engine light with little or no drivability change",
                "Fuel vapor smell near the vehicle in some cases",
                "Readiness monitor or emissions test failure",
            ],
            "quick_checks": [
                "Check that the gas cap is present, tightened correctly, and sealing",
                "Inspect EVAP hoses and canister connections for a disconnected or split line",
                "Command the purge and vent valves if possible and verify they seal when closed",
                "Smoke test the EVAP system if the leak is not obvious on inspection",
            ],
        },
        "P0442": {
            "meaning": "P0442 means the EVAP system detected a small leak that is harder to see than a gross leak. The fault is commonly a weak gas-cap seal, a small hose crack, or a valve that is not fully sealing during the monitor test.",
            "symptoms": [
                "Check-engine light with no major drivability complaint",
                "Usually no noticeable change in how the vehicle runs",
                "Readiness monitor or emissions test failure",
            ],
            "quick_checks": [
                "Inspect the gas cap seal and make sure the cap tightens correctly",
                "Inspect EVAP lines near the canister and tank for small cracks or loose fittings",
                "Verify the purge and vent valves are sealing when commanded closed",
                "Run a smoke test if the leak is not visible during inspection",
            ],
        },
        "P0456": {
            "meaning": "P0456 means the EVAP system detected a very small leak during its self-test. The leak is usually a minor seep at the gas cap, hose, fitting, or valve rather than a hose that is fully disconnected.",
            "symptoms": [
                "Check-engine light with no real drivability change",
                "Usually no fuel-control or idle complaint",
                "Readiness monitor or emissions test failure",
            ],
            "quick_checks": [
                "Inspect the gas cap seal for hardening, cracks, or poor fit",
                "Inspect the canister, vent valve, and nearby lines for subtle seepage",
                "Verify purge and vent valves are not leaking when closed",
                "Smoke test the EVAP system for a tiny leak source",
            ],
        },
        "P0128": {
            "meaning": "P0128 means the engine is taking too long to reach normal operating temperature. A thermostat stuck open is the most common cause, but low coolant level, trapped air, or a temperature-sensor issue can also mislead the monitor.",
            "symptoms": [
                "Weak cabin heat in cool weather",
                "Temperature gauge staying low longer than normal",
                "Reduced fuel economy from extended warm-up",
            ],
            "quick_checks": [
                "Compare scan-tool coolant temperature to actual warm-up behavior from a cold start",
                "Check coolant level and make sure the system is properly full with no trapped air",
                "Inspect the thermostat housing area for seepage or poor sealing",
                "Compare ECT data to ambient temperature if a sensor issue is suspected",
            ],
        },
        "P0101": {
            "meaning": "P0101 means the mass air flow signal is outside the expected range for the current load and RPM. That can be caused by a dirty or failing MAF sensor, unmetered air entering the intake, or an intake setup that changes airflow readings.",
            "symptoms": [
                "Hesitation or flat throttle response",
                "Rough idle or light surge",
                "Poor fuel economy or transmission shift feel changes on some vehicles",
            ],
            "quick_checks": [
                "Inspect the intake boot, clamps, and ducting for unmetered air leaks",
                "Inspect and clean the MAF sensor with MAF-safe cleaner if appropriate",
                "Check the air filter and intake tract for restriction or poor sealing",
                "Compare MAF readings to expected load and RPM on the scan tool",
            ],
        },
        "P0138": {
            "meaning": "P0138 means the downstream oxygen sensor on bank 1 is staying high or reading richer than expected for too long. The sensor itself may be failing, but wiring faults or an actual rich-running condition can also hold the signal high.",
            "symptoms": [
                "Check-engine light with little or no drivability change",
                "Possible rich-running smell if fueling is actually excessive",
                "Failed emissions readiness or inspection",
            ],
            "quick_checks": [
                "Check for signs of rich running or fuel-control faults before replacing the sensor",
                "Inspect the bank 1 sensor 2 wiring near the exhaust for heat damage or shorting",
                "Verify downstream sensor voltage behavior on the scan tool",
                "Confirm there is no exhaust contamination or connector damage affecting the reading",
            ],
        },
        "P0141": {
            "meaning": "P0141 means the heater circuit for the downstream oxygen sensor on bank 1 is not working as expected. The sensor may be bad, but a blown fuse, poor power supply, ground issue, or damaged wiring can set the same code.",
            "symptoms": [
                "Check-engine light with no major drivability complaint",
                "Delayed oxygen-sensor readiness on cold start",
                "Failed emissions readiness or inspection",
            ],
            "quick_checks": [
                "Inspect the downstream sensor wiring away from the hot exhaust",
                "Verify heater circuit power, ground, and related fuse protection",
                "Check connector condition and terminal tension at bank 1 sensor 2",
                "Confirm the heater fault with scan data or meter checks before replacing parts",
            ],
        },
        "P0158": {
            "meaning": "P0158 means the downstream oxygen sensor on bank 2 is staying high or reading richer than expected for too long. The fault can be a biased sensor, damaged wiring, or a real rich-running condition that keeps the signal elevated.",
            "symptoms": [
                "Check-engine light with little or no drivability change",
                "Possible rich exhaust smell if the engine is actually overfueling",
                "Failed emissions readiness or inspection",
            ],
            "quick_checks": [
                "Check for rich-running signs or related fuel-control codes first",
                "Inspect the bank 2 sensor 2 wiring near the exhaust for heat damage or shorting",
                "Verify downstream sensor voltage behavior on the scan tool",
                "Confirm connector condition and harness routing before replacing the sensor",
            ],
        },
        "P0562": {
            "meaning": "P0562 means system voltage dropped below the expected range. The most common causes are a weak battery, low alternator output, or excessive resistance at the battery, ground, or charging connections.",
            "symptoms": [
                "Slow cranking or intermittent no-start",
                "Dim lights or unstable electrical accessories",
                "Multiple low-voltage communication or module codes",
            ],
            "quick_checks": [
                "Check resting battery voltage and perform a battery load or conductance test",
                "Measure charging voltage with the engine running and electrical load applied",
                "Inspect battery terminals, main grounds, and charging-cable connections for voltage drop",
                "Check for a belt or alternator-drive issue if charging output is low",
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
        "P0118": {
            "meaning": "P0118 means the coolant temperature signal is reading colder than expected because the sensor circuit is open or the signal is stuck high. That can come from an unplugged sensor, damaged wiring, corrosion, or a failed coolant temperature sensor.",
            "symptoms": [
                "Hard cold starts or overly rich warm-up fueling",
                "Cooling fans running unexpectedly on some vehicles",
                "Poor fuel economy while the fault is active",
            ],
            "quick_checks": [
                "Make sure the coolant temperature sensor connector is fully seated",
                "Inspect the wiring for breaks, corrosion, or coolant intrusion",
                "Compare ECT reading to ambient temperature on a cold engine",
                "Check sensor resistance or reference voltage if the signal stays unrealistically cold",
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
    structured_data = build_obd_structured_data(
        row["code"],
        page_title,
        page_description,
        str(request.url),
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
            "diagnostic_summary": diagnostic_summary,
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

    return templates.TemplateResponse(
        "diagnostics.html",
        {
            "request": request,
            "obd_entries": obd_entries,
            "symptom_entries": symptom_entries,
            "system_entries": system_entries,
            "featured_obd_codes": build_featured_obd_codes(),
            "platform_sections": build_platform_sections("/diagnostics"),
            "page_title": "Diagnostics | TorqueMech",
            "meta_description": "Structured diagnostic entry points for OBD codes, symptoms, and vehicle systems.",
        },
    )


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
            "description": "Cooling system pricing guidance when leaks, overheating, or cracked tanks show up.",
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
            "title": "Brake Rotor Replacement Cost",
            "description": "Labor and parts context when brake pulsation, scoring, or rotor wear is part of the job.",
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
        title = guide.get("title", slug.replace("-", " ").title())
        summary = guide.get("summary", "")

        item = {
            "slug": slug,
            "title": title,
            "summary": summary,
            "sort_order": guide.get("sort_order", 999),
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

    lastmod_sources: Dict[str, List[Path]] = {
        "/": [BASE_DIR / "main.py", TEMPLATES_DIR / "home.html"],
        "/repair-costs": [BASE_DIR / "main.py", TEMPLATES_DIR / "repair_costs.html"],
        "/obd": [BASE_DIR / "main.py", TEMPLATES_DIR / "obd_index.html", BASE_DIR / "data" / "obd_codes.json"],
        "/obd-codes": [BASE_DIR / "main.py", TEMPLATES_DIR / "obd_codes_index.html", BASE_DIR / "data" / "obd_codes.json"],
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

    return 0.5

@app.get("/sitemap.xml", response_class=Response)
def sitemap():
    base_url = "https://torquemech.com"
    paths = [
        *SITEMAP_STATIC_PATHS,
        *SITEMAP_OBD_RANGE_PATHS,
        *[item["href"] for item in build_repair_cost_guide_cards() if item.get("href")],
        *build_sitemap_obd_detail_paths(),
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
    return FileResponse(str(STATIC_DIR / "favicon.ico"))

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
            service["category"] = category_key
            services_lookup[service["code"]] = service

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

    category: Optional[str] = None
    serviceCode: Optional[str] = None
    service: Optional[str] = None

    laborHours: float = Field(0, ge=0)
    partsPrice: float = Field(0, ge=0)
    laborRate: Optional[float] = Field(None, ge=0)

    notes: Optional[str] = None
    customerName: Optional[str] = None
    customerPhone: Optional[str] = None

    customerAgrees: bool = False
    zip: Optional[str] = Field(default="00000", min_length=5, max_length=10)
    signatureDataUrl: Optional[str] = None


class EstimateResponse(BaseModel):
    estimate: int
    currency: str = "USD"
    service_name: str
    breakdown: Dict[str, Any]
    labor_breakdown: Optional[Dict[str, Any]] = None

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

_vehicle_catalog_cache: Optional[Dict[str, List[str]]] = None
_vehicle_catalog_mtime: Optional[float] = None

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

# ===============================
# MAKES / MODELS API
# ===============================
import httpx

@app.get("/api/makes")
def get_makes() -> List[str]:
    catalog = load_vehicle_catalog()
    return sorted(catalog.keys())


@app.get("/api/models/{make}")
def get_models(make: str) -> List[str]:
    catalog = load_vehicle_catalog()
    make_upper = (make or "").strip().upper()

    if make_upper not in catalog:
        raise HTTPException(status_code=404, detail=f"Make '{make}' not supported")

    return catalog[make_upper]


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
    vin = (vin or "").strip().upper()
    if len(vin) != 17:
        raise HTTPException(status_code=400, detail="VIN must be 17 characters")

    url = f"https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValuesExtended/{vin}?format=json"

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url)

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

    engine = row.get("DisplacementL") or row.get("EngineModel") or row.get("EngineCylinders")
    trim = row.get("Trim") or row.get("Series") or row.get("Series2")

    return {
        "year": int(year),
        "make": make.title(),
        "model": model.title(),
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

    catalog = load_vehicle_catalog()
    models = catalog.get(make_key, [])
    if models:
        allowed = {m.upper() for m in models}
        if model.upper() not in allowed:
            raise HTTPException(status_code=400, detail="Invalid model for selected make")

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

def pdf_draw_header(c, w, h, *, title="Repair Estimate", left=50, right=50, top=50):
    """
    Consistent header for BOTH pdf and pdf_multi:
    - left title
    - top-right logo.png
    - generated timestamp under header
    Returns the new cursor y.
    """
    y = h - top

    # Title (left)
    c.setFont("Helvetica-Bold", 18)
    c.setFillGray(0)
    c.drawString(left, y, title)

    # Logo (top-right)
    try:
        logo_path = STATIC_DIR / "logo.png"
        logo = ImageReader(str(logo_path))
        logo_w, logo_h = 140, 32
        x = w - right - logo_w
        y_img = y - logo_h + 10
        c.drawImage(logo, x, y_img, width=logo_w, height=logo_h, mask="auto")
    except Exception:
        # If logo missing, fail gracefully (don’t crash PDF)
        pass

    # Generated time
    y -= 18
    c.setFont("Helvetica", 10)
    c.setFillGray(0.4)
    c.drawString(left, y, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    c.setFillGray(0)

    return y - 22

def pdf_start_page(c, w, h, *, title="Repair Estimate", vehicle_line: Optional[str]=None, left=50, right=50, top=50):
    """Start a new PDF page with consistent header (+ optional vehicle line). Returns cursor y."""
    y = pdf_draw_header(c, w, h, title=title, left=left, right=right, top=top)
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
):
    bottom_margin = 90
    if y - needed < bottom_margin:
        c.showPage()
        y = pdf_start_page(c, w, h, title=title, vehicle_line=vehicle_line, left=left, right=right)

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
    # Signature label
    c.setFont("Helvetica-Bold", 11)
    c.drawString(left, y, "Signature")
    y -= 10

    sig_box_h = 80
    sig_box_w = w - left - right
    sig_x = left
    sig_y = y - sig_box_h

    c.setLineWidth(1)
    c.rect(sig_x, sig_y, sig_box_w, sig_box_h)

    if signature_data_url:
        try:
            sig_reader = signature_to_dark_imagereader(signature_data_url)
            if sig_reader:
                pad = 6
                c.drawImage(
                    sig_reader,
                    sig_x + pad,
                    sig_y + pad,
                    width=sig_box_w - pad * 2,
                    height=sig_box_h - pad * 2,
                    preserveAspectRatio=True,
                    mask="auto",
                )
        except Exception:
            c.setFont("Helvetica-Oblique", 9)
            c.setFillGray(0.5)
            c.drawString(sig_x + 8, sig_y + sig_box_h - 14, "Signature could not be rendered")
            c.setFillGray(0)

    # Note directly under signature
    c.setFont("Helvetica-Oblique", 9)
    c.setFillGray(0.4)
    c.drawString(left, sig_y - 14, "Note: This is an estimate. Final pricing may vary after inspection.")
    c.setFillGray(0)

    return sig_y - 26


def pdf_draw_footer(c, w):
    """
    Consistent footer for BOTH PDFs.
    """
    c.setFont("Helvetica-Oblique", 9)
    c.setFillGray(0.5)
    c.drawCentredString(w / 2, 40, "Generated by TorqueMech — Free Beta Version")
    c.drawCentredString(w / 2, 28, "Upgrade to Pro for white-label estimates")
    c.setFillGray(0)

# ===============================
# PDF
# ===============================
@app.post("/estimate/pdf")
async def estimate_pdf(req: EstimateRequest) -> Response:
    try:
        metric_incr("pdf_single_generated")
        est = await estimate(req)

        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        w, h = letter

        c.setTitle("Repair Estimate")

        y = pdf_draw_header(c, w, h)

         # ---------------- Vehicle ----------------
        c.setFont("Helvetica-Bold", 12)
        c.drawString(72, y, "Vehicle")
        y -= 16
        c.setFont("Helvetica", 11)
        c.drawString(72, y, f"{req.year} {req.make} {req.model}")
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

        # ---------------- Breakdown ----------------
        c.setFont("Helvetica-Bold", 12)
        c.drawString(72, y, "Breakdown")
        y -= 16

        c.setFont("Helvetica", 10)

        for k, v in est.breakdown.items():
            try:
                c.drawString(72, y, f"{k}: {v:.2f}")
            except:
                c.drawString(72, y, f"{k}: {v}")
            y -= 14

        y -= 6

        # ---------------- Labor Breakdown ----------------
        lb = est.labor_breakdown

        if lb and lb.get("steps"):
            c.setFont("Helvetica-Bold", 12)
            c.drawString(72, y, "Labor Breakdown")
            y -= 14

            c.setFont("Helvetica", 10)

            for step in lb["steps"]:
                label = step.get("label", "")
                hours = step.get("hours", 0)

                c.drawString(82, y, f"- {label}")
                c.drawRightString(540, y, f"{hours:.1f} hr")
                y -= 12

            y -= 10

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

        if req.customerPhone:
            c.drawString(72, y, f"Phone: {req.customerPhone}")
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
        y -= 12
        y = pdf_draw_signature_block(c, w, y, signature_data_url=req.signatureDataUrl, left=72, right=72)
        pdf_draw_footer(c, w)

        c.save()

        buf.seek(0)

        return Response(
            content=buf.read(),
            media_type="application/pdf",
            headers={"Content-Disposition": "attachment; filename=torquemech_estimate.pdf"}
        )

    except Exception:
        logging.exception("PDF_SINGLE_FAILED")
        metric_incr("errors_pdf_single")
        metric_incr("errors_total")  
        raise



from fastapi import Request

class LineItemPDF(BaseModel):
    serviceCode: str
    serviceText: Optional[str] = None
    laborHours: float
    partsPrice: float
    laborRate: float
    estimate: Optional[float] = None

class MultiPDFRequest(BaseModel):
    year: int
    make: str
    model: str
    notes: Optional[str] = None
    customerName: Optional[str] = None
    customerPhone: Optional[str] = None
    customerAgrees: bool = True
    signatureDataUrl: Optional[str] = None
    lineItems: List[LineItemPDF]

@app.post("/estimate/pdf_multi")
async def estimate_pdf_multi(req: MultiPDFRequest) -> Response:
    try:
        metric_incr("pdf_multi_generated")

        # 🔒 Defensive Guard (VERY IMPORTANT)
        if not req.lineItems:
            raise HTTPException(status_code=400, detail="No line items provided.")

        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        w, h = letter

        vehicle_line = f"{req.year} {req.make} {req.model}"
        y = pdf_start_page(c, w, h, title="Repair Estimate", vehicle_line=vehicle_line, left=50, right=50)

        # ---- Column anchors ----
        LEFT = 50
        RIGHT = 50
        X_SERVICE = LEFT
        X_LABOR  = 360
        X_PARTS  = 440
        X_TOTAL  = w - RIGHT

        def draw_service_columns(ypos: float) -> float:
            c.setFont("Helvetica-Bold", 10)
            c.drawString(X_SERVICE, ypos, "Service")
            c.drawRightString(X_LABOR, ypos, "Labor")
            c.drawRightString(X_PARTS, ypos, "Parts")
            c.drawRightString(X_TOTAL, ypos, "Total")
            ypos -= 10

            c.setStrokeGray(0.85)
            c.line(LEFT, ypos, X_TOTAL, ypos)
            c.setStrokeGray(0)

            return ypos - 14

        # Services header 
        c.setFont("Helvetica-Bold", 12)
        c.drawString(LEFT, y, "Services")
        y -= 16

        # Column headers 
        y = draw_service_columns(y)

        grand_total = 0.0
        X_BREAKDOWN_HOURS = 500

        for it in (req.lineItems or []):
            y = pdf_ensure_space(
                c, w, h, y,
                needed=140,
                title="Repair Estimate",
                vehicle_line=vehicle_line,
                left=LEFT, right=RIGHT,
                continued_label="Services (continued)",
                draw_columns_fn=draw_service_columns,
            )

            est = float(it.estimate) if it.estimate is not None else 0.0
            grand_total += est

            service_name = (it.serviceText or it.serviceCode or "").strip()

            c.setFont("Helvetica-Bold", 10)
            c.drawString(X_SERVICE, y, service_name)

            c.setFont("Helvetica", 10)
            c.drawRightString(X_LABOR, y, f"{it.laborHours:.1f}h")
            c.drawRightString(X_PARTS, y, f"${it.partsPrice:,.0f}")
            c.drawRightString(X_TOTAL, y, f"${est:,.0f}")
            y -= 18

            c.setFillGray(0.45)
            c.setFont("Helvetica", 9)
            c.drawString(X_SERVICE, y, f"Rate: ${it.laborRate:.0f}/hr")
            c.setFillGray(0)
            y -= 12

            lb = build_labor_breakdown(
                it.serviceCode,
                it.laborHours,
                display_name=it.serviceText,
            )

            if lb and lb.get("steps"):
                if lb.get("labor_hours") and lb["labor_hours"].get("range"):
                    rng = lb["labor_hours"]["range"]
                    rmin = rng.get("min")
                    rmax = rng.get("max")

                    if rmin is not None and rmax is not None:
                        c.setFont("Helvetica", 8)
                        c.setFillGray(0.45)
                        c.drawString(X_SERVICE + 20, y, f"Typical range: {rmin:.1f} - {rmax:.1f} hrs")
                        c.setFillGray(0)
                        y -= 10

                c.setFont("Helvetica-Bold", 8)
                if lb.get("labor_hours") and lb["labor_hours"].get("range"):
                    rng = lb["labor_hours"]["range"]
                    rmin = rng.get("min")
                    rmax = rng.get("max")

                    if rmin is not None and rmax is not None:
                        c.setFont("Helvetica", 8)
                        c.setFillGray(0.45)
                        c.drawString(X_SERVICE + 20, y, f"Typical range: {rmin:.1f} - {rmax:.1f} hrs")
                        c.setFillGray(0)
                        y -= 10
                c.drawString(X_SERVICE + 20, y, "Labor Breakdown")
                y -= 11

                c.setFont("Helvetica", 9)
                for step in lb["steps"]:
                    label = step.get("label", "")
                    hours = float(step.get("hours", 0))

                    c.drawString(X_SERVICE + 26, y, f"- {label}")
                    c.drawRightString(X_BREAKDOWN_HOURS, y, f"{hours:.1f} hr")
                    y -= 11

                y -= 4

            c.setStrokeGray(0.88)
            c.line(X_SERVICE, y, X_TOTAL, y)
            c.setStrokeGray(0)
            y -= 10

        # Ensure space for totals + customer + signature
        y = pdf_ensure_space(
            c, w, h, y,
            needed=220,
            title="Repair Estimate",
            vehicle_line=vehicle_line,
            left=LEFT, right=RIGHT,
        )

        # Divider line above Grand Total
        c.setStrokeGray(0.85)
        c.setLineWidth(1)
        c.line(LEFT, y + 12, X_TOTAL, y + 12)
        c.setStrokeGray(0)

        # Grand total
        c.setFont("Helvetica-Bold", 13)
        c.drawString(LEFT, y, "Grand Total")
        c.drawRightString(X_TOTAL, y, f"${grand_total:,.0f}")
        y -= 30

        # Customer
        c.setFont("Helvetica-Bold", 12)
        c.drawString(LEFT, y, "Customer")
        y -= 16

        c.setFont("Helvetica", 11)
        c.drawString(LEFT, y, f"Customer agrees: {'Yes' if req.customerAgrees else 'No'}")
        y -= 14

        if req.customerName:
            c.drawString(LEFT, y, f"Name: {req.customerName}")
            y -= 14

        if req.customerPhone:
            c.drawString(LEFT, y, f"Phone: {req.customerPhone}")
            y -= 14

        if req.notes:
            y -= 6
            c.setFont("Helvetica-Bold", 11)
            c.drawString(LEFT, y, "Notes:")
            y -= 14

            c.setFont("Helvetica", 10)
            for line in wrap_text(req.notes.strip(), max_chars=90):
                y = pdf_ensure_space(
                    c, w, h, y,
                    needed=14,
                    title="Repair Estimate",
                    vehicle_line=vehicle_line,
                    left=LEFT, right=RIGHT,
                )
                c.drawString(LEFT, y, line)
                y -= 12

            y -= 8

        # Signature + footer
        y -= 20
        y = pdf_draw_signature_block(c, w, y, signature_data_url=req.signatureDataUrl, left=LEFT, right=RIGHT)
        pdf_draw_footer(c, w)

        c.save()
        buf.seek(0)

        return Response(
            content=buf.getvalue(),
            media_type="application/pdf",
            headers={"Content-Disposition": "inline; filename=torquemech_estimate.pdf"},
        )

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

    return templates.TemplateResponse(
        "repair_guide.html",
        {
            "request": request,
            "guide": guide,
            "page_title": f"{guide.get('title', 'Repair Guide')} | TorqueMech",
            "meta_description": guide.get("summary", "TorqueMech repair guide"),
        },
    )


@app.get("/symptoms/{slug}")
async def symptom_page(request: Request, slug: str):
    raw_symptom = load_json_file("symptoms", f"{slug.replace('-', '_')}.json")
    repair_guides = load_normalized_repair_guides_map()
    symptom = normalize_symptom_entry(raw_symptom, file_slug=slug.replace("-", "_"), repair_guides=repair_guides)
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
    return templates.TemplateResponse(
        "symptom_page.html",
        {
            "request": request,
            "symptom": symptom,
            "page_title": f"{symptom.get('title', 'Symptom Guide')} | TorqueMech",
            "meta_description": symptom.get("summary", "TorqueMech symptom guide"),
        },
    )


@app.get("/diagnostics/{slug}")
async def diagnostic_page(request: Request, slug: str):
    raw_diagnostic, source_slug = load_diagnostic_source(slug)
    repair_guides = load_normalized_repair_guides_map()
    diagnostic = normalize_diagnostic_entry(raw_diagnostic, file_slug=source_slug, repair_guides=repair_guides)
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
    return templates.TemplateResponse(
        "diagnostic_page.html",
        {
            "request": request,
            "diagnostic": diagnostic,
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
