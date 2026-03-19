from __future__ import annotations

import base64
import io
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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

    for category in catalog["categories"]:
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

# --- Paths ---
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
SERVICES_CATALOG_PATH = BASE_DIR / "services_catalog.json"

DATA_DIR = Path("/data") if Path("/data").exists() else BASE_DIR
DB_PATH = str((DATA_DIR / "app.db").resolve())

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

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

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

OBD_SQLITE_PATH = DATA_DIR / "obd.sqlite"
OBD_ADMIN_META_PATH = DATA_DIR / "obd_admin_meta.json"
OBD_SEED_JSON_PATH = BASE_DIR / "data" / "obd_codes.json"

def init_obd_db() -> None:
    conn = sqlite3.connect(OBD_SQLITE_PATH)
    conn.row_factory = sqlite3.Row
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
    conn = sqlite3.connect(str(OBD_SQLITE_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def obd_seed_from_json_if_empty() -> None:
    conn = obd_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS n FROM dtc")
    n = int(cur.fetchone()["n"])
    conn.close()
    if n > 0:
        return

    if not OBD_SEED_JSON_PATH.exists():
        return

    try:
        data = json.loads(OBD_SEED_JSON_PATH.read_text(encoding="utf-8"))
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
        data = json.loads(OBD_SEED_JSON_PATH.read_text(encoding="utf-8"))
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
    return templates.TemplateResponse(
        "obd.html",
        {"request": request},
    )

def build_related_codes(code: str):
    code = code.upper()

    clusters = {
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
        "fuel_trim": [
            ("P0171", "System Too Lean (Bank 1)"),
            ("P0174", "System Too Lean (Bank 2)"),
            ("P0172", "System Too Rich (Bank 1)"),
            ("P0175", "System Too Rich (Bank 2)"),
        ],
        "catalyst": [
            ("P0420", "Catalyst Efficiency Below Threshold (Bank 1)"),
            ("P0430", "Catalyst Efficiency Below Threshold (Bank 2)"),
        ],
        "evap": [
            ("P0440", "EVAP System Malfunction"),
            ("P0442", "EVAP Small Leak Detected"),
            ("P0455", "EVAP Large Leak Detected"),
            ("P0456", "EVAP Very Small Leak Detected"),
        ],
    }

    for group in clusters.values():
        codes = [c[0] for c in group]
        if code in codes:
            return [
                {"code": c, "label": label}
                for c, label in group
                if c != code
            ]

    return []

def build_common_repairs(code: str):
    code = code.upper().strip()

    repair_map = {
        "P0300": [
            {"label": "Ignition coil replacement", "service_query": "ignition coil"},
            {"label": "Spark plug replacement", "service_query": "spark plugs"},
            {"label": "Fuel injector diagnosis", "service_query": "fuel injector"},
            {"label": "Vacuum leak diagnosis", "service_query": "vacuum leak"},
        ],
        "P0301": [
            {"label": "Ignition coil replacement", "service_query": "ignition coil"},
            {"label": "Spark plug replacement", "service_query": "spark plugs"},
            {"label": "Fuel injector diagnosis", "service_query": "fuel injector"},
            {"label": "Vacuum leak diagnosis", "service_query": "vacuum leak"},
        ],
        "P0302": [
            {"label": "Ignition coil replacement", "service_query": "ignition coil"},
            {"label": "Spark plug replacement", "service_query": "spark plugs"},
            {"label": "Fuel injector diagnosis", "service_query": "fuel injector"},
            {"label": "Vacuum leak diagnosis", "service_query": "vacuum leak"},
        ],
        "P0303": [
            {"label": "Ignition coil replacement", "service_query": "ignition coil"},
            {"label": "Spark plug replacement", "service_query": "spark plugs"},
            {"label": "Fuel injector diagnosis", "service_query": "fuel injector"},
            {"label": "Vacuum leak diagnosis", "service_query": "vacuum leak"},
        ],
        "P0304": [
            {"label": "Ignition coil replacement", "service_query": "ignition coil"},
            {"label": "Spark plug replacement", "service_query": "spark plugs"},
            {"label": "Fuel injector diagnosis", "service_query": "fuel injector"},
            {"label": "Vacuum leak diagnosis", "service_query": "vacuum leak"},
        ],
        "P0171": [
            {"label": "Vacuum leak diagnosis", "service_query": "vacuum leak"},
            {"label": "MAF sensor diagnosis", "service_query": "maf sensor"},
            {"label": "Fuel system diagnosis", "service_query": "fuel system"},
        ],
        "P0420": [
            {"label": "Catalytic converter diagnosis", "service_query": "catalytic converter"},
            {"label": "O2 sensor diagnosis", "service_query": "o2 sensor"},
            {"label": "Exhaust leak diagnosis", "service_query": "exhaust leak"},
        ],
        "P0442": [
            {"label": "EVAP leak diagnosis", "service_query": "evap leak"},
            {"label": "Gas cap replacement", "service_query": "gas cap"},
            {"label": "Purge valve diagnosis", "service_query": "purge valve"},
        ],
    }

    return repair_map.get(code, [])

@app.get("/obd/{code}", response_class=HTMLResponse)
async def obd_code_page(request: Request, code: str):
    # normalize like the API routes do
    norm = "".join(ch for ch in (code or "").upper() if ch.isalnum())[:7]
    if len(norm) < 4:
        raise HTTPException(status_code=400, detail="Invalid OBD code.")
    
    metric_incr("page_obd_code")

    conn = obd_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM dtc WHERE code = ?", (norm,))
    row = cur.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="OBD code not found")

    # dtc.possible_causes + dtc.quick_checks are stored as JSON strings
    try:
        possible_causes = json.loads(row["possible_causes"] or "[]")
    except Exception:
        possible_causes = []
    try:
        quick_checks = json.loads(row["quick_checks"] or "[]")
    except Exception:
        quick_checks = []

    related_codes = build_related_codes(row["code"])

    common_repairs = build_common_repairs(row["code"])

    diagnostic_summary = build_diagnostic_summary(row["code"])

    return templates.TemplateResponse(
        "obd_code.html",
        {
            "request": request,
            "code": row["code"],
            "title": row["title"] or "",
            "description": row["description"] or "",
            "possible_causes": possible_causes,
            "quick_checks": quick_checks,
            "system": row["system"],
            "generic": bool(row["generic"]),
            "related_codes": related_codes,
            "common_repairs": common_repairs,
            "diagnostic_summary": diagnostic_summary,
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

@app.get("/repair-cost/{service_slug}", response_class=HTMLResponse)
def repair_cost_page(request: Request, service_slug: str):

    metric_incr("page_repair_cost")

    catalog = load_services_catalog()

    service_match = None

    for category in catalog["categories"]:
        for service in category.get("services", []):
            slug = slugify_service_name(service.get("name", ""))
            if slug == service_slug:
                service_match = service
                break

    if not service_match:
        raise HTTPException(status_code=404, detail="Service not found")

    labor_min = float(service_match.get("labor_hours_min", 0))
    labor_max = float(service_match.get("labor_hours_max", 0))

    rate = default_labor_rate()

    labor_low = int(labor_min * rate)
    labor_high = int(labor_max * rate)

    return templates.TemplateResponse(
        "repair_cost.html",
        {
            "request": request,
            "service": service_match,
            "labor_min": labor_min,
            "labor_max": labor_max,
            "labor_low": labor_low,
            "labor_high": labor_high,
        },
    )

@app.get("/repair-cost", response_class=HTMLResponse)
def repair_cost_index(request: Request):
    metric_incr("page_repair_cost_index")

    catalog = load_services_catalog()
    repair_pages = []

    for category in catalog["categories"]:
        for service in category.get("services", []):
            name = service.get("name", "").strip()
            if not name:
                continue

            slug = slugify_service_name(name)

            labor_min = float(service.get("labor_hours_min", 0) or 0)
            labor_max = float(service.get("labor_hours_max", 0) or 0)

            repair_pages.append({
                "name": name,
                "slug": slug,
                "category": category.get("name", "General Repair"),
                "labor_min": labor_min,
                "labor_max": labor_max,
            })

    repair_pages.sort(key=lambda x: x["name"].lower())

    return templates.TemplateResponse(
        "repair_cost_index.html",
        {
            "request": request,
            "repair_pages": repair_pages,
        },
    )

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

@app.get("/sitemap.xml", response_class=Response)
def sitemap(request: Request):
    conn = obd_conn()
    cur = conn.cursor()

    cur.execute("SELECT code FROM dtc")
    rows = cur.fetchall()

    conn.close()

    base_url = str(request.base_url).rstrip("/")
    urls = []

    urls.append(f"<url><loc>{base_url}/</loc></url>")
    urls.append(f"<url><loc>{base_url}/estimator</loc></url>")
    urls.append(f"<url><loc>{base_url}/obd</loc></url>")
    urls.append(f"<url><loc>{base_url}/repair-cost</loc></url>")
    urls.append(f"<url><loc>{base_url}/symptoms</loc></url>")
    urls.append(f"<url><loc>{base_url}/knowledge</loc></url>")
    urls.append(f"<url><loc>{base_url}/symptoms/engine-misfire</loc></url>")
    urls.append(f"<url><loc>{base_url}/symptoms/check-engine-light</loc></url>")
    urls.append(f"<url><loc>{base_url}/symptoms/car-wont-start</loc></url>")
    urls.append(f"<url><loc>{base_url}/symptoms/rough-idle</loc></url>")

    # OBD pages
    for r in rows:
        code = r["code"].lower()
        urls.append(f"<url><loc>{base_url}/obd/{code}</loc></url>")

    # Repair-cost pages
    catalog = load_services_catalog()
    for category in catalog["categories"]:
        for service in category.get("services", []):
            slug = slugify_service_name(service.get("name", ""))
            if slug:
                urls.append(f"<url><loc>{base_url}/repair-cost/{slug}</loc></url>")

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{''.join(urls)}
</urlset>
"""

    return Response(content=xml, media_type="application/xml")

@app.get("/robots.txt")
def robots():

    content = """
User-agent: *
Allow: /

Sitemap: https://torquemech.com/sitemap.xml
"""

    return Response(content=content.strip(), media_type="text/plain")

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

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
@app.get("/privacy", include_in_schema=False)
def privacy():
    return FileResponse(BASE_DIR / "static" / "privacy.html")

@app.get("/terms", include_in_schema=False)
def terms():
    return FileResponse(BASE_DIR / "static" / "terms.html")

@app.get("/disclaimer", include_in_schema=False)
def disclaimer():
    return FileResponse(BASE_DIR / "static" / "disclaimer.html")

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
MODELS_TTL_SECONDS = 60 * 60 * 24  # 24h


def _cache_get(make_upper: str) -> Optional[List[str]]:
    item = _models_cache.get(make_upper)
    if not item:
        return None
    expires, models = item
    if time.time() > expires:
        _models_cache.pop(make_upper, None)
        return None
    return models


def _cache_set(make_upper: str, models: List[str]) -> None:
    _models_cache[make_upper] = (time.time() + MODELS_TTL_SECONDS, models)


async def fetch_models_from_vpic(make: str) -> List[str]:
    make_clean = (make or "").strip()
    if not make_clean:
        return []

    make_upper = make_clean.upper()
    cached = _cache_get(make_upper)
    if cached is not None:
        return cached

    url = f"{VPIC_BASE}/GetModelsForMake/{make_clean}"
    params = {"format": "json"}

    last_err: Optional[Exception] = None
    for _ in range(2):
        try:
            async with httpx.AsyncClient(timeout=VPIC_TIMEOUT_S, follow_redirects=True) as client:
                r = await client.get(url, params=params)
                r.raise_for_status()
                data = r.json()

            results = data.get("Results", []) if isinstance(data, dict) else []
            models: List[str] = []
            seen = set()

            for item in results:
                name = (item.get("Model_Name") or "").strip()
                if not name:
                    continue
                key = name.upper()
                if key in seen:
                    continue
                seen.add(key)
                models.append(name)

            models.sort(key=lambda s: s.upper())
            _cache_set(make_upper, models)
            return models

        except Exception as e:
            last_err = e

    stale = _models_cache.get(make_upper)
    if stale:
        return stale[1]

    raise HTTPException(status_code=502, detail=f"NHTSA vPIC unavailable: {last_err}")


# ===============================
# DB
# ===============================

def init_metrics_db() -> None:
    conn = sqlite3.connect(DB_PATH)
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
    conn = sqlite3.connect(DB_PATH)
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
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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
    breakdown: Dict[str, float]
    service_name: str

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

# ===============================
# MAKES / MODELS API
# ===============================
@app.get("/api/makes")
def get_makes() -> List[str]:
    return POPULAR_MAKES


@app.get("/api/models/{make}")
async def get_models(make: str) -> List[str]:
    make_upper = (make or "").strip().upper()
    if make_upper not in POPULAR_MAKES:
        raise HTTPException(status_code=404, detail=f"Make '{make}' not supported")
    return await fetch_models_from_vpic(make_upper)


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

    return {
        "year": int(year),
        "make": make.title(),
        "model": model.title(),
    }

# ===============================
# ESTIMATE
# ===============================
@app.post("/estimate", response_model=EstimateResponse)
async def estimate(req: EstimateRequest) -> EstimateResponse:
    metric_incr("estimate_requests")
    make_key = (req.make or "").strip().upper()
    if make_key not in POPULAR_MAKES:
        raise HTTPException(status_code=400, detail="Invalid make")

    model = (req.model or "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="Model is required")

    models = await fetch_models_from_vpic(make_key)
    if models:
        allowed = {m.upper() for m in models}
        if model.upper() not in allowed:
            raise HTTPException(status_code=400, detail="Invalid model for selected make")

    service_name = ""
    hours_default = 0.0

    if req.serviceCode:
        s = find_service_by_code(req.serviceCode)
        if not s:
            raise HTTPException(status_code=400, detail="Invalid serviceCode")
        service_name = str(s.get("name", "")).strip()

        mn = float(s.get("labor_hours_min", 0))
        mx = float(s.get("labor_hours_max", 0))
        if mx > 0 and mx >= mn:
            hours_default = (mn + mx) / 2.0
    else:
        service_name = (req.service or "").strip()
        if not service_name:
            raise HTTPException(status_code=400, detail="Select a service")

    labor_rate = float(req.laborRate) if req.laborRate is not None else default_labor_rate()
    labor_hours = float(req.laborHours) if req.laborHours and req.laborHours > 0 else hours_default

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
        },
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
    Convert the e-signature canvas PNG (dataURL) into solid black-ink on white for PDF.

    UI pad draws WHITE ink on dark background → composite on black, invert, threshold.
    """
    if not data_url:
        return None

    m = re.match(r"data:image\/png;base64,(.*)", data_url)
    b64 = m.group(1) if m else data_url
    raw = base64.b64decode(b64)

    im = Image.open(io.BytesIO(raw)).convert("RGBA")

    # Assume dark pad behind the ink
    black_bg = Image.new("RGBA", im.size, (0, 0, 0, 255))
    im = Image.alpha_composite(black_bg, im).convert("L")

    # White strokes -> dark strokes
    im = ImageOps.invert(im)

    # Force pure black ink
    im = im.point(lambda p: 0 if p < 200 else 255).convert("RGB")

    out = io.BytesIO()
    im.save(out, format="PNG")
    out.seek(0)
    return ImageReader(out)

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
            c.drawString(72, y, f"{k}: {v:.2f}")
            y -= 14

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

        c.save()
        buf.seek(0)

        metric_incr("pdf_single_generated")

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
    serviceText: str
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

        for it in (req.lineItems or []):
            y = pdf_ensure_space(
                c, w, h, y,
                needed=46,
                title="Repair Estimate",
                vehicle_line=vehicle_line,
                left=LEFT, right=RIGHT,
                continued_label="Services (continued)",
                draw_columns_fn=draw_service_columns,
            )

            try:
                est = float(it.estimate) if it.estimate is not None else 0.0
            except Exception:
                est = 0.0
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
            y -= 14

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