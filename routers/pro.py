import io
import logging
import re
import sqlite3
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlencode
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.data.maintenance_library import (
    MAINTENANCE_INTERVAL_PRESETS,
    MAINTENANCE_SERVICE_ALIASES,
    MAINTENANCE_SERVICE_OPTIONS,
    maintenance_defaults_for,
    normalize_maintenance_service_type,
)
from app.data.repair_blueprints import (
    blueprint_summary,
    get_repair_blueprint_for_work_item,
)

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
VISUAL_REFERENCE_SEED_PATH = BASE_DIR / "data" / "visual_reference_seed.json"
REPAIR_INTELLIGENCE_SEED_PATH = BASE_DIR / "data" / "repair_intelligence_seed.json"
STATE_DIR = Path("/data") if Path("/data").exists() else BASE_DIR / ".localstate"
DB_PATH = str((STATE_DIR / "app.db").resolve())
LOCAL_FALLBACK_DB_PATH = str((STATE_DIR / "dev_runtime_app.db").resolve())
USE_LOCAL_SQLITE_COMPAT = not Path("/data").exists()
logger = logging.getLogger(__name__)

VISUAL_REFERENCE_IMAGE_TYPES = {
    "component_location",
    "exploded_view",
    "belt_routing",
    "connector_view",
    "reference_image",
}
VISUAL_REFERENCE_UPLOAD_DIR = STATIC_DIR / "visual-references" / "uploads"
VISUAL_REFERENCE_UPLOAD_URL_PREFIX = "/static/visual-references/uploads"
VISUAL_REFERENCE_ALLOWED_UPLOAD_EXTENSIONS = {".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"}
PHOTO_UPLOAD_ALLOWED_EXTENSIONS = {".jpeg", ".jpg", ".png", ".webp"}
PHOTO_UPLOAD_MAX_FILES = 5
ESTIMATE_PDF_DIR = STATE_DIR / "estimate_pdfs"
DEFAULT_PARTS_SOURCE_LABELS = [
    "O'Reilly",
    "AutoZone",
    "NAPA",
    "RockAuto",
    "OEM/dealer catalog",
    "Amazon",
    "eBay",
    "Google Shopping",
    "1A Auto",
]
DEFAULT_SHOP_TIMEZONE = "America/Los_Angeles"
try:
    SHOP_ZONEINFO = ZoneInfo(DEFAULT_SHOP_TIMEZONE)
except ZoneInfoNotFoundError:
    SHOP_ZONEINFO = timezone(timedelta(hours=-7), DEFAULT_SHOP_TIMEZONE)

PARTS_SEARCH_KEYWORDS = {
    "coolant drain refill": "engine coolant",
    "coolant drain and refill": "engine coolant",
    "coolant flush": "engine coolant",
    "oil change": "engine oil filter",
    "oil and filter change": "engine oil filter",
    "water pump replacement": "water pump",
    "radiator replacement": "radiator",
    "alternator replacement": "alternator",
    "starter replacement": "starter",
    "battery replacement": "battery",
    "brake pad replacement": "brake pads",
    "front brake pad replacement": "front brake pads",
    "front brake pads replacement": "front brake pads",
    "rear brake pad replacement": "rear brake pads",
    "rear brake pads replacement": "rear brake pads",
    "ignition coil replacement": "ignition coil",
    "spark plug replacement": "spark plugs",
    "spark plugs replacement": "spark plugs",
    "air filter replacement": "engine air filter",
    "engine air filter replacement": "engine air filter",
    "cabin air filter replacement": "cabin air filter",
    "transmission fluid service": "transmission fluid",
    "differential fluid service": "differential fluid",
    "oxygen sensor replacement": "oxygen sensor",
    "thermostat replacement": "thermostat",
    "fuel pump replacement": "fuel pump",
    "serpentine belt replacement": "serpentine belt",
    "control arm replacement": "control arm",
    "tie rod replacement": "tie rod",
    "wheel bearing replacement": "wheel bearing",
    "strut replacement": "strut",
    "shock replacement": "shock",
    "shock absorber replacement": "shock",
    "headlight replacement": "headlight",
    "taillight replacement": "taillight",
    "tail light replacement": "taillight",
}


def record_value(record: dict[str, Any] | sqlite3.Row | None, key: str) -> Any:
    if not record:
        return None
    try:
        return record.get(key)  # type: ignore[attr-defined]
    except AttributeError:
        return record[key] if key in record.keys() else None


def customer_display_name(customer: dict[str, Any] | sqlite3.Row | None) -> str:
    if not customer:
        return ""
    first_name = str(record_value(customer, "first_name") or "").strip()
    last_name = str(record_value(customer, "last_name") or "").strip()
    return " ".join(part for part in (first_name, last_name) if part).strip()


def build_finding_estimator_href(
    customer: dict[str, Any] | sqlite3.Row | None,
    vehicle: dict[str, Any] | sqlite3.Row | None,
    finding: dict[str, Any] | sqlite3.Row | None,
) -> str:
    params: dict[str, Any] = {"source": "finding"}
    customer_id = record_value(customer, "id")
    vehicle_id = record_value(vehicle, "id")
    finding_id = record_value(finding, "id")
    if customer_id is not None:
        params["customer_id"] = customer_id
    customer_name = customer_display_name(customer)
    if customer_name:
        params["customer_name"] = customer_name
    if vehicle_id is not None:
        params["vehicle_id"] = vehicle_id
    if vehicle:
        for key in ("year", "make", "model"):
            value = str(record_value(vehicle, key) or "").strip()
            if value:
                params[key] = value
    if finding_id is not None:
        params["finding_id"] = finding_id
    if finding:
        problem = str(record_value(finding, "finding") or "").strip()
        recommendation = str(record_value(finding, "recommendation") or record_value(finding, "labor_description") or "").strip()
        if problem:
            params["problem_found"] = problem
        if recommendation:
            params["recommended_repair"] = recommendation
    return f"/estimator?{urlencode(params)}"

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
templates.env.globals["build_finding_estimator_href"] = build_finding_estimator_href

router = APIRouter(prefix="/pro", tags=["pro"])
public_router = APIRouter(tags=["booking"])

FINDING_STATUS_OPTIONS = ("Approved", "Open", "Completed", "Deferred", "Declined")
FINDING_SEVERITY_OPTIONS = ("Low", "Medium", "High", "Critical")
FINDING_REQUEST_TYPES = ("finding", "labor")
CUSTOMER_DECISION_LOG_STATUSES = {"Approved", "Deferred", "Declined"}
APPROVAL_REQUEST_TYPES = ("finding", "labor", "parts")
APPROVAL_DECISION_OPTIONS = ("pending", "approved", "declined", "deferred")
REPAIR_WORK_STATUS_OPTIONS = ("ready", "in_progress", "waiting_parts", "completed")
REPAIR_EXECUTION_STATUS_OPTIONS = ("ready", "in_progress", "waiting_parts")
REPAIR_WORK_STATUS_LABELS = {
    "ready": "Ready for Repair",
    "in_progress": "In Progress",
    "waiting_parts": "Waiting on Parts",
    "completed": "Done",
}
REPAIR_COMPLETION_CHECKS = (
    ("torque_verified", "Torque fasteners verified"),
    ("fluids_verified", "Fluids filled / topped off"),
    ("leaks_checked", "Leak check completed"),
    ("codes_cleared", "Codes cleared if applicable"),
    ("road_test_completed", "Road test completed"),
    ("customer_concern_resolved", "Customer concern resolved"),
)

APPOINTMENT_STATUS_OPTIONS = ("Requested", "Confirmed", "Handled", "Declined", "Completed", "Cancelled")
APPOINTMENT_LENGTH_OPTIONS = (30, 45, 60, 90, 120)
APPOINTMENT_BUFFER_OPTIONS = (0, 15, 30)
SHOP_SCHEDULE_DAYS = (
    {"index": 0, "name": "Monday"},
    {"index": 1, "name": "Tuesday"},
    {"index": 2, "name": "Wednesday"},
    {"index": 3, "name": "Thursday"},
    {"index": 4, "name": "Friday"},
    {"index": 5, "name": "Saturday"},
    {"index": 6, "name": "Sunday"},
)

REPAIR_JOB_PART_STATUS_OPTIONS = (
    "Needed",
    "Researching",
    "Ordered",
    "Arrived",
    "Installed",
    "Returned",
    "Customer Supplied",
    "Not Needed",
)
REPAIR_JOB_PART_EXCLUDED_TOTAL_STATUSES = {"Returned", "Not Needed"}


def crm_db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    if USE_LOCAL_SQLITE_COMPAT:
        try:
            conn.execute("PRAGMA journal_mode=MEMORY")
            conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.OperationalError as exc:
            logger.warning("Falling back to TRUNCATE journal mode for Pro DB: %s", exc)
            try:
                conn.execute("PRAGMA journal_mode=TRUNCATE")
                conn.execute("PRAGMA synchronous=NORMAL")
            except sqlite3.OperationalError as fallback_exc:
                logger.warning("Using local fallback Pro DB after PRAGMA failure: %s", fallback_exc)
                conn.close()
                conn = sqlite3.connect(LOCAL_FALLBACK_DB_PATH)
                conn.execute("PRAGMA journal_mode=TRUNCATE")
                conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    return conn


async def read_form_data(request: Request) -> dict[str, str]:
    raw_body = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qs(raw_body, keep_blank_values=True)
    return {key: values[0].strip() for key, values in parsed.items()}


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def optional_int(form: dict[str, str], name: str) -> int | None:
    raw = form.get(name, "").replace(",", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def optional_float(form: dict[str, str], name: str) -> float | None:
    raw = form.get(name, "").replace("$", "").replace(",", "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def normalize_finding_status(raw_status: str | None) -> str:
    status = (raw_status or "").strip().title()
    if status not in FINDING_STATUS_OPTIONS:
        raise HTTPException(status_code=400, detail="Invalid finding status")
    return status


def normalize_finding_severity(raw_severity: str | None) -> str:
    severity = (raw_severity or "").strip().title()
    if severity not in FINDING_SEVERITY_OPTIONS:
        raise HTTPException(status_code=400, detail="Invalid finding severity")
    return severity


def normalize_finding_request_type(raw_request_type: str | None) -> str:
    request_type = (raw_request_type or "finding").strip().lower().replace("-", "_")
    return request_type if request_type in FINDING_REQUEST_TYPES else "finding"


def finding_labor_amount(labor_hours: float | None, labor_rate: float | None) -> float | None:
    if labor_hours is None or labor_rate is None:
        return None
    return round(max(0.0, labor_hours) * max(0.0, labor_rate), 2)


def finding_parts_cost(form: dict[str, str]) -> float | None:
    value = optional_float(form, "parts_cost")
    if value is None:
        return None
    return max(0.0, value)


def normalize_approval_request_type(raw_request_type: str | None) -> str:
    request_type = (raw_request_type or "general").strip().lower().replace("-", "_")
    if request_type == "general":
        return "finding"
    return request_type if request_type in APPROVAL_REQUEST_TYPES else "finding"


def normalize_approval_decision(raw_decision: str | None) -> str:
    decision = (raw_decision or "pending").strip().lower()
    return decision if decision in APPROVAL_DECISION_OPTIONS else "pending"


def normalize_repair_work_status(raw_status: str | None) -> str:
    status = (raw_status or "ready").strip().lower().replace("-", "_")
    if status not in REPAIR_WORK_STATUS_OPTIONS:
        raise HTTPException(status_code=400, detail="Invalid repair workflow status")
    return status


def repair_work_status_label(value: Any) -> str:
    status = normalize_repair_work_status(str(value or "ready"))
    return REPAIR_WORK_STATUS_LABELS[status]


REPAIR_WORKSPACE_STATUS_LABELS = {
    "ready": "Ready for Repair",
    "in_progress": "In Progress",
    "waiting_parts": "In Progress",
    "completed": "Completed",
}

def repair_workspace_status_label(value: Any) -> str:
    status = normalize_repair_work_status(str(value or "ready"))
    return REPAIR_WORKSPACE_STATUS_LABELS[status]


def repair_workspace_display_status(item: dict[str, Any]) -> tuple[str, str]:
    if item.get("repair_work_status") == "completed" or item.get("record_status") == "Completed":
        return "completed", "Completed"
    checklist = item.get("checklist_summary") if isinstance(item.get("checklist_summary"), dict) else {}
    if (
        item.get("linked_repair_record_id")
        and int(checklist.get("total") or 0) > 0
        and int(checklist.get("incomplete") or 0) == 0
    ):
        return "ready_to_complete", "Ready to Complete"
    if item.get("repair_work_status") in {"in_progress", "waiting_parts"}:
        return "in_progress", "In Progress"
    if item.get("source_type") == "repair":
        return "open", "Open"
    return "approved", "Approved"


def repair_workspace_primary_action(item: dict[str, Any], status_key: str) -> dict[str, str]:
    repair_url = str(item.get("repair_record_url") or item.get("url") or "")
    source_url = str(item.get("source_action_url") or item.get("url") or "")
    estimate_url = str(item.get("estimate_document_url") or "")
    create_estimate_url = str(item.get("create_estimate_url") or source_url)
    invoice_url = str(item.get("invoice_url") or "")
    if status_key == "completed":
        if invoice_url:
            return {"label": "Open Final Invoice", "url": invoice_url, "kind": "link"}
        return {"label": "View Completed Repair", "url": repair_url or source_url, "kind": "link"}
    if estimate_url and status_key in {"approved", "in_progress", "ready_to_complete"}:
        return {"label": "Open Repair / Track Parts", "url": repair_url or source_url, "kind": "repair"}
    if estimate_url and item.get("source_label") == "Source: Finding":
        return {"label": "Open Estimate", "url": estimate_url, "kind": "link"}
    if (
        item.get("source_label") == "Source: Finding"
        and item.get("source_type") == "finding"
    ):
        return {"label": "Create Estimate", "url": create_estimate_url, "kind": "link"}
    if status_key == "ready_to_complete":
        return {"label": "Mark Completed", "url": f"{repair_url}#repair-completion" if repair_url else source_url, "kind": "link"}
    if status_key == "in_progress":
        return {"label": "Continue Repair / Track Parts", "url": repair_url or source_url, "kind": "link"}
    if item.get("source_type") == "repair" or item.get("linked_repair_record_id"):
        return {"label": "Open Repair / Track Parts", "url": repair_url or source_url, "kind": "link"}
    if status_key == "approved":
        return {"label": "Start Repair", "url": repair_url or source_url, "kind": "repair"}
    return {"label": "Review Repair", "url": repair_url or source_url, "kind": "link"}


def enrich_repair_workspace_item(item: dict[str, Any]) -> dict[str, Any]:
    status_key, status_label = repair_workspace_display_status(item)
    action = repair_workspace_primary_action(item, status_key)
    date_value = (
        item.get("approved_at")
        or item.get("repair_record_created_at")
        or item.get("updated_at")
        or item.get("created_at")
        or ""
    )
    date_label = "Approved" if status_key in {"approved", "in_progress", "ready_to_complete"} else "Created"
    if status_key == "completed":
        date_label = "Completed"
    item.update(
        {
            "workspace_status_key": status_key,
            "workspace_status_label": status_label,
            "workspace_group_key": status_key,
            "primary_action_label": action["label"],
            "primary_action_url": action["url"],
            "primary_action_kind": action.get("kind") or "link",
            "date_label": date_label,
            "date_value": date_value,
        }
    )
    return item


def repair_workspace_blank_totals() -> dict[str, Any]:
    return {
        "labor_total": None,
        "parts_total": None,
        "grand_total": None,
        "has_pricing": False,
    }


def repair_workspace_detail_from_notes(notes: Any) -> str:
    lines = [
        line.strip()
        for line in str(notes or "").splitlines()
        if line.strip() and not line.strip().lower().startswith("source:")
    ]
    return "\n".join(lines)


def normalize_workflow_source_type(raw_source_type: str | None) -> str:
    source_type = (raw_source_type or "").strip().lower()
    return source_type if source_type in {"finding", "approval"} else ""


def approval_labor_amount(labor_hours: float | None, labor_rate: float | None) -> float | None:
    if labor_hours is None or labor_rate is None:
        return None
    return round(max(0.0, labor_hours) * max(0.0, labor_rate), 2)


def approval_parts_amount(quantity: float | None, unit_cost: float | None) -> float | None:
    if quantity is None or unit_cost is None:
        return None
    return round(max(0.0, quantity) * max(0.0, unit_cost), 2)


def approval_parts_event_label(decision: str | None = None) -> str:
    decision_value = (decision or "").strip().lower()
    if decision_value == "approved":
        return "Customer Approved Parts Request"
    if decision_value == "declined":
        return "Customer Declined Parts Request"
    if decision_value == "deferred":
        return "Customer Deferred Parts Request"
    return "Parts Request Created"


def approval_request_type_label(value: Any) -> str:
    request_type = normalize_approval_request_type(str(value or "general"))
    return {
        "finding": "Finding",
        "labor": "Labor Request",
        "parts": "Parts Request",
    }[request_type]


def format_quantity(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value).strip()
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def format_engine_badge(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "Engine: Not recorded"
    if raw.lower().startswith("engine"):
        return raw
    return f"Engine: {raw}"


def format_phone(value: Any) -> str:
    raw = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(raw) == 11 and raw.startswith("1"):
        raw = raw[1:]
    if len(raw) == 10:
        return f"({raw[:3]}){raw[3:6]}-{raw[6:]}"
    return str(value or "").strip()


def clean_phone(value: Any) -> str:
    raw = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(raw) == 11 and raw.startswith("1"):
        raw = raw[1:]
    return raw if len(raw) == 10 else str(value or "").strip()


def clean_shop_phone(value: Any) -> str:
    raw = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(raw) == 11 and raw.startswith("1"):
        raw = raw[1:]
    return raw[:10] if len(raw) >= 10 else ""


DEMO_SHOP_NAME_PREFIXES = (
    "flow test auto",
    "test shop",
    "demo shop",
    "torquemech demo",
    "dee's auto services",
    "dee\u2019s auto services",
)
DEMO_SHOP_EMAILS = {"test@example.com", "demo@example.com", "service@example.com", "service@shop.com"}
DEMO_SHOP_ADDRESSES = {"1 test st", "123 test st", "123 main st"}
DEMO_SHOP_PHONE_DIGITS = {"5551212", "5552223333"}


def scrub_demo_shop_name(value: Any) -> str:
    name = str(value or "").strip()
    lowered = name.lower()
    return "" if any(lowered.startswith(prefix) for prefix in DEMO_SHOP_NAME_PREFIXES) else name


def scrub_demo_shop_phone(value: Any) -> str:
    raw_digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if raw_digits in DEMO_SHOP_PHONE_DIGITS or raw_digits[:10] in DEMO_SHOP_PHONE_DIGITS:
        return ""
    return clean_shop_phone(value)


def scrub_demo_shop_email(value: Any) -> str:
    email = str(value or "").strip()
    return "" if email.lower() in DEMO_SHOP_EMAILS else email


def scrub_demo_shop_address(value: Any) -> str:
    address = str(value or "").strip()
    return "" if address.lower() in DEMO_SHOP_ADDRESSES else address


def format_mileage(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def format_currency(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def format_decimal_input(value: Any, decimals: int = 2) -> str:
    if value is None or value == "":
        return ""
    try:
        return f"{float(value):.{decimals}f}"
    except (TypeError, ValueError):
        return str(value)


def local_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(SHOP_ZONEINFO)


def local_today() -> date:
    return local_now().date()


def parse_local_datetime_value(raw: Any) -> datetime | None:
    if not raw:
        return None
    value = str(raw).strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(SHOP_ZONEINFO)


def format_pro_date(value: Any) -> str:
    parsed = parse_date_value(value)
    if not parsed:
        return str(value or "")
    return parsed.strftime("%m/%d/%Y")


def format_pro_datetime(value: Any) -> str:
    if not value:
        return ""
    raw = str(value).strip()
    parsed = parse_local_datetime_value(raw)
    if parsed:
        return parsed.strftime("%m/%d/%Y")
    return format_pro_date(raw)


def format_pro_time(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "AM" in raw.upper() or "PM" in raw.upper():
        return raw
    try:
        parsed = datetime.strptime(raw[:5], "%H:%M")
    except ValueError:
        return raw
    return parsed.strftime("%I:%M %p").lstrip("0")


def service_total_value(record: dict[str, Any]) -> float | None:
    actual = record.get("actual_total")
    estimate = record.get("estimate_total")
    return actual if actual is not None else estimate


def service_total_from_form(form: dict[str, str]) -> float | None:
    if "service_total" in form:
        return optional_float(form, "service_total")
    actual = optional_float(form, "actual_total")
    if actual is not None:
        return actual
    return optional_float(form, "estimate_total")


def maintenance_interval_value(
    form: dict[str, str],
    service_type: str,
    field_name: str,
) -> int | None:
    submitted = optional_int(form, field_name)
    if submitted is not None:
        return submitted
    defaults = maintenance_defaults_for(service_type)
    value = defaults.get(field_name)
    return int(value) if value is not None else None


def calculated_due_mileage(
    mileage_performed: int | None,
    interval_miles: int | None,
) -> int | None:
    if mileage_performed is None or interval_miles is None:
        return None
    return mileage_performed + interval_miles


def calculated_due_date(
    date_performed: str,
    interval_months: int | None,
) -> str:
    performed_date = parse_date_value(date_performed)
    if not performed_date or interval_months is None:
        return ""
    return add_months(performed_date, interval_months).isoformat()


def maintenance_due_values(
    form: dict[str, str],
    mileage_performed: int | None,
    date_performed: str,
    interval_miles: int | None,
    interval_months: int | None,
) -> tuple[int | None, str]:
    due_mileage = optional_int(form, "due_mileage")
    if due_mileage is None and not form.get("due_mileage", "").strip():
        due_mileage = calculated_due_mileage(mileage_performed, interval_miles)

    due_date = form.get("due_date", "").strip()
    if not due_date:
        due_date = calculated_due_date(date_performed, interval_months)

    return due_mileage, due_date


def maintenance_prediction_source_candidates(
    maintenance_records: list[dict[str, Any]],
    service_history_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates = []
    for record in maintenance_records:
        candidates.append(
            {
                "source_type": "maintenance",
                "date": parse_date_value(record.get("date_performed")),
                "mileage": optional_int_value(record.get("mileage_performed")),
            }
        )
    for record in service_history_records:
        candidates.append(
            {
                "source_type": "service",
                "date": parse_date_value(record.get("service_date")),
                "mileage": optional_int_value(record.get("mileage")),
            }
        )
    return candidates


def estimate_vehicle_driving_rate(
    maintenance_records: list[dict[str, Any]],
    service_history_records: list[dict[str, Any]],
    vehicle: dict[str, Any],
    today: date,
) -> dict[str, Any] | None:
    current_mileage = optional_int_value(vehicle.get("mileage"))
    if current_mileage is None:
        return None

    reliable = []
    for candidate in maintenance_prediction_source_candidates(maintenance_records, service_history_records):
        previous_date = candidate.get("date")
        previous_mileage = candidate.get("mileage")
        if previous_date is None or previous_mileage is None:
            continue
        days = (today - previous_date).days
        miles = current_mileage - previous_mileage
        if days < 14 or miles < 0:
            continue
        reliable.append(
            {
                **candidate,
                "days": days,
                "miles": miles,
                "miles_per_month": miles / days * 30.4375 if days else 0,
            }
        )

    if not reliable:
        return None

    latest = max(reliable, key=lambda item: (item["date"], item["mileage"]))
    return {
        "miles_per_month": int(round(latest["miles_per_month"])),
        "miles_per_day": latest["miles"] / latest["days"] if latest["days"] else 0,
        "source_date": latest["date"].isoformat(),
        "source_mileage": latest["mileage"],
        "source_type": latest["source_type"],
    }


def estimated_due_date_by_mileage(
    *,
    current_mileage: int | None,
    next_due_mileage: int | None,
    miles_per_day: float | None,
    today: date,
) -> date | None:
    if current_mileage is None or next_due_mileage is None or not miles_per_day or miles_per_day <= 0:
        return None
    remaining_miles = max(0, next_due_mileage - current_mileage)
    days_until_due = int((remaining_miles + miles_per_day - 1) // miles_per_day)
    return today + timedelta(days=days_until_due)


def vehicle_reminder_label(vehicle: dict[str, Any]) -> str:
    label = " ".join(
        str(vehicle.get(key) or "").strip()
        for key in ("year", "make", "model")
    ).strip()
    return label or "vehicle"


def _context_lookup(context: Any, key: str) -> Any:
    if context is None:
        return None
    if isinstance(context, dict):
        return context.get(key)
    return getattr(context, key, None)


def _sender_contexts(context: Any) -> list[Any]:
    if context is None:
        return []
    contexts = [context]
    for key in ("shop_profile", "profile", "current_user", "account", "user", "session"):
        nested = _context_lookup(context, key)
        if nested is not None and nested is not context:
            contexts.append(nested)
    return contexts


def resolve_sender_display_name(*contexts: Any) -> str:
    priority_keys = (
        ("shop_name",),
        ("business_name",),
        ("mechanic_name",),
        ("full_name", "user_full_name", "account_full_name", "name", "display_name"),
        ("first_name", "user_first_name", "account_first_name"),
    )
    expanded_contexts: list[Any] = []
    for context in contexts:
        expanded_contexts.extend(_sender_contexts(context))

    for keys in priority_keys:
        for context in expanded_contexts:
            for key in keys:
                value = str(_context_lookup(context, key) or "").strip()
                if value:
                    return value
    return "your mechanic"


def resolve_scheduling_link(*contexts: Any) -> str:
    expanded_contexts: list[Any] = []
    for context in contexts:
        expanded_contexts.extend(_sender_contexts(context))
    for context in expanded_contexts:
        value = str(_context_lookup(context, "booking_link") or "").strip()
        if value:
            return value
    for context in expanded_contexts:
        value = str(_context_lookup(context, "scheduling_link") or "").strip()
        if value:
            return value
    return ""


def resolve_scheduling_link_source(*contexts: Any) -> str:
    expanded_contexts: list[Any] = []
    for context in contexts:
        expanded_contexts.extend(_sender_contexts(context))
    for context in expanded_contexts:
        if str(_context_lookup(context, "booking_link") or "").strip():
            return "builtin"
    for context in expanded_contexts:
        if str(_context_lookup(context, "scheduling_link") or "").strip():
            return "external"
    return ""


def slugify_shop_name(value: Any) -> str:
    slug = re.sub(r"[^a-z0-9\s-]", "", str(value or "").strip().lower())
    slug = re.sub(r"\s+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "torquemech-shop"


def shop_booking_slug(profile: dict[str, Any] | None = None) -> str:
    profile = profile or {}
    return slugify_shop_name(profile.get("shop_slug") or profile.get("shop_name") or "torquemech-shop")


def request_base_url(request: Request | None = None) -> str:
    if request is not None:
        hostname = str(request.url.hostname or "").lower()
        if hostname == "torquemech.com" or hostname.endswith(".torquemech.com"):
            return "https://torquemech.com"
        return str(request.base_url).rstrip("/")
    return "http://127.0.0.1:8125"


def build_shop_booking_link(profile: dict[str, Any] | None = None, request: Request | None = None) -> str:
    return f"{request_base_url(request)}/book/{shop_booking_slug(profile)}"


def attach_shop_booking_context(
    profile: dict[str, Any] | None,
    request: Request | None = None,
) -> dict[str, Any]:
    enriched = dict(profile or {})
    enriched["booking_slug"] = shop_booking_slug(enriched)
    enriched["booking_link"] = build_shop_booking_link(enriched, request)
    return enriched


def add_column_if_missing(conn: sqlite3.Connection, table_name: str, column_name: str, column_sql: str) -> None:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
    if column_name not in columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")


def ensure_calendar_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shop_availability (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          shop_id INTEGER,
          day_of_week INTEGER NOT NULL,
          is_open INTEGER NOT NULL DEFAULT 0,
          start_time TEXT NOT NULL DEFAULT '09:00',
          end_time TEXT NOT NULL DEFAULT '17:00',
          appointment_length_minutes INTEGER NOT NULL DEFAULT 60,
          buffer_minutes INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """
    )
    for column_name, column_sql in {
        "shop_id": "shop_id INTEGER",
        "day_of_week": "day_of_week INTEGER NOT NULL DEFAULT 0",
        "is_open": "is_open INTEGER NOT NULL DEFAULT 0",
        "start_time": "start_time TEXT NOT NULL DEFAULT '09:00'",
        "end_time": "end_time TEXT NOT NULL DEFAULT '17:00'",
        "appointment_length_minutes": "appointment_length_minutes INTEGER NOT NULL DEFAULT 60",
        "buffer_minutes": "buffer_minutes INTEGER NOT NULL DEFAULT 0",
        "created_at": "created_at TEXT",
        "updated_at": "updated_at TEXT",
    }.items():
        add_column_if_missing(conn, "shop_availability", column_name, column_sql)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_shop_availability_shop_day ON shop_availability (shop_id, day_of_week)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shop_closed_days (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          shop_id INTEGER,
          closed_date TEXT NOT NULL,
          reason TEXT,
          created_at TEXT NOT NULL
        )
        """
    )
    for column_name, column_sql in {
        "shop_id": "shop_id INTEGER",
        "closed_date": "closed_date TEXT",
        "reason": "reason TEXT",
        "created_at": "created_at TEXT",
    }.items():
        add_column_if_missing(conn, "shop_closed_days", column_name, column_sql)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_shop_closed_days_date ON shop_closed_days (shop_id, closed_date)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS service_appointments (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          shop_id INTEGER,
          customer_id INTEGER,
          vehicle_id INTEGER,
          estimate_id INTEGER,
          repair_id INTEGER,
          invoice_id INTEGER,
          customer_name TEXT NOT NULL,
          customer_phone TEXT NOT NULL,
          customer_email TEXT,
          vehicle_label TEXT,
          service_name TEXT NOT NULL,
          requested_date TEXT NOT NULL,
          requested_time TEXT NOT NULL,
          notes TEXT,
          source TEXT NOT NULL DEFAULT 'customer_booking',
          status TEXT NOT NULL DEFAULT 'Requested',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """
    )
    for column_name, column_sql in {
        "shop_id": "shop_id INTEGER",
        "customer_id": "customer_id INTEGER",
        "vehicle_id": "vehicle_id INTEGER",
        "estimate_id": "estimate_id INTEGER",
        "repair_id": "repair_id INTEGER",
        "invoice_id": "invoice_id INTEGER",
        "customer_name": "customer_name TEXT",
        "customer_phone": "customer_phone TEXT",
        "customer_email": "customer_email TEXT",
        "vehicle_label": "vehicle_label TEXT",
        "service_name": "service_name TEXT",
        "requested_date": "requested_date TEXT",
        "requested_time": "requested_time TEXT",
        "notes": "notes TEXT",
        "source": "source TEXT NOT NULL DEFAULT 'customer_booking'",
        "status": "status TEXT NOT NULL DEFAULT 'Requested'",
        "created_at": "created_at TEXT",
        "updated_at": "updated_at TEXT",
    }.items():
        add_column_if_missing(conn, "service_appointments", column_name, column_sql)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_service_appointments_date ON service_appointments (requested_date, requested_time)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_service_appointments_status ON service_appointments (status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_service_appointments_customer ON service_appointments (customer_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_service_appointments_vehicle ON service_appointments (vehicle_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_service_appointments_estimate ON service_appointments (estimate_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_service_appointments_repair ON service_appointments (repair_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_service_appointments_invoice ON service_appointments (invoice_id)")
    conn.commit()


def default_shop_availability_rows() -> list[dict[str, Any]]:
    return [
        {
            "day_of_week": day["index"],
            "day_name": day["name"],
            "is_open": day["index"] < 5,
            "start_time": "09:00",
            "end_time": "17:00",
            "appointment_length_minutes": 60,
            "buffer_minutes": 0,
        }
        for day in SHOP_SCHEDULE_DAYS
    ]


def load_shop_availability(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    ensure_calendar_schema(conn)
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM shop_availability
            WHERE shop_id IS NULL OR shop_id = 1
            ORDER BY day_of_week ASC, id ASC
            """
        ).fetchall()
    ]
    by_day: dict[int, dict[str, Any]] = {}
    for row in rows:
        day_index = optional_int_value(row.get("day_of_week"))
        if day_index is not None and day_index not in by_day:
            by_day[day_index] = row
    defaults = default_shop_availability_rows()
    availability = []
    for default_row in defaults:
        row = dict(default_row)
        saved = by_day.get(int(default_row["day_of_week"]))
        if saved:
            row.update(saved)
            row["is_open"] = bool(saved.get("is_open"))
        availability.append(row)
    return availability


def save_shop_availability(
    conn: sqlite3.Connection,
    availability: list[dict[str, Any]],
    *,
    appointment_length_minutes: int = 60,
    buffer_minutes: int = 0,
) -> None:
    ensure_calendar_schema(conn)
    appointment_length_minutes = appointment_length_minutes if appointment_length_minutes in APPOINTMENT_LENGTH_OPTIONS else 60
    buffer_minutes = buffer_minutes if buffer_minutes in APPOINTMENT_BUFFER_OPTIONS else 0
    now = datetime.utcnow().isoformat()
    for item in availability:
        day_index = optional_int_value(item.get("day_of_week"))
        if day_index is None or day_index < 0 or day_index > 6:
            continue
        is_open = 1 if item.get("is_open") else 0
        start_time = str(item.get("start_time") or "09:00").strip()[:5] or "09:00"
        end_time = str(item.get("end_time") or "17:00").strip()[:5] or "17:00"
        existing = conn.execute(
            """
            SELECT id
            FROM shop_availability
            WHERE (shop_id IS NULL OR shop_id = 1) AND day_of_week = ?
            ORDER BY id ASC
            LIMIT 1
            """,
            (day_index,),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE shop_availability
                SET is_open = ?, start_time = ?, end_time = ?,
                    appointment_length_minutes = ?, buffer_minutes = ?, updated_at = ?
                WHERE id = ?
                """,
                (is_open, start_time, end_time, appointment_length_minutes, buffer_minutes, now, existing["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO shop_availability (
                  shop_id, day_of_week, is_open, start_time, end_time,
                  appointment_length_minutes, buffer_minutes, created_at, updated_at
                )
                VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (day_index, is_open, start_time, end_time, appointment_length_minutes, buffer_minutes, now, now),
            )
    conn.commit()


def load_closed_days(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    ensure_calendar_schema(conn)
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM shop_closed_days
            WHERE shop_id IS NULL OR shop_id = 1
            ORDER BY closed_date ASC, id ASC
            """
        ).fetchall()
    ]


def create_closed_day(conn: sqlite3.Connection, closed_date: str, reason: str = "") -> int | None:
    parsed_date = parse_date_value(closed_date)
    if not parsed_date:
        return None
    ensure_calendar_schema(conn)
    now = datetime.utcnow().isoformat()
    cur = conn.execute(
        """
        INSERT INTO shop_closed_days (shop_id, closed_date, reason, created_at)
        VALUES (NULL, ?, ?, ?)
        """,
        (parsed_date.isoformat(), str(reason or "").strip(), now),
    )
    conn.commit()
    return int(cur.lastrowid)


def delete_closed_day(conn: sqlite3.Connection, closed_day_id: int) -> None:
    ensure_calendar_schema(conn)
    conn.execute("DELETE FROM shop_closed_days WHERE id = ?", (closed_day_id,))
    conn.commit()


def format_time_label(value: Any) -> str:
    raw = str(value or "").strip()[:5]
    try:
        hour_text, minute_text = raw.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except (TypeError, ValueError):
        return raw
    suffix = "AM" if hour < 12 else "PM"
    display_hour = hour % 12 or 12
    return f"{display_hour}:{minute:02d} {suffix}"


def public_booking_schedule(conn: sqlite3.Connection) -> dict[str, Any]:
    availability = load_shop_availability(conn)
    first_row = availability[0] if availability else {}
    return {
        "days": [
            {
                "day_of_week": int(row.get("day_of_week") or 0),
                "day_name": str(row.get("day_name") or ""),
                "is_open": bool(row.get("is_open")),
                "start_time": str(row.get("start_time") or "09:00")[:5],
                "end_time": str(row.get("end_time") or "17:00")[:5],
                "start_label": format_time_label(row.get("start_time")),
                "end_label": format_time_label(row.get("end_time")),
            }
            for row in availability
        ],
        "appointment_length_minutes": int(first_row.get("appointment_length_minutes") or 60),
        "buffer_minutes": int(first_row.get("buffer_minutes") or 0),
    }


def is_closed_booking_day(conn: sqlite3.Connection, requested_date: str) -> tuple[bool, str]:
    parsed_date = parse_date_value(requested_date)
    if not parsed_date:
        return False, ""
    ensure_calendar_schema(conn)
    closed_day = conn.execute(
        """
        SELECT reason
        FROM shop_closed_days
        WHERE (shop_id IS NULL OR shop_id = 1) AND closed_date = ?
        LIMIT 1
        """,
        (parsed_date.isoformat(),),
    ).fetchone()
    if closed_day:
        return True, "The shop is closed on this day. Please choose another day."
    availability = load_shop_availability(conn)
    day_row = next((row for row in availability if int(row.get("day_of_week") or 0) == parsed_date.weekday()), None)
    if day_row and not bool(day_row.get("is_open")):
        return True, "The shop is closed on this day. Please choose another day."
    return False, ""


def shop_today() -> date:
    return datetime.now(SHOP_ZONEINFO).date()


def is_booking_time_available(
    conn: sqlite3.Connection,
    requested_date: str,
    requested_time: str,
    requested_duration: Any = None,
    exclude_appointment_id: int | None = None,
) -> tuple[bool, str]:
    parsed_date = parse_date_value(requested_date)
    if not parsed_date:
        return False, "Please choose a valid appointment date."
    if parsed_date < shop_today():
        return False, "Please choose a date that has not passed."
    raw_time = str(requested_time or "").strip()[:5]
    try:
        requested_start = datetime.strptime(raw_time, "%H:%M")
    except ValueError:
        return False, "Please choose a valid appointment time."
    availability = load_shop_availability(conn)
    day_row = next((row for row in availability if int(row.get("day_of_week") or 0) == parsed_date.weekday()), None)
    if not day_row or not bool(day_row.get("is_open")):
        return False, "The shop is closed on this day. Please choose another day."
    start_time = str(day_row.get("start_time") or "09:00").strip()[:5]
    end_time = str(day_row.get("end_time") or "17:00").strip()[:5]
    duration = int(day_row.get("appointment_length_minutes") or 60)
    if requested_duration not in (None, ""):
        supplied_duration = optional_int_value(requested_duration)
        if supplied_duration not in APPOINTMENT_LENGTH_OPTIONS or supplied_duration != duration:
            return False, "The shop's booking interval has changed. Please refresh the page and choose another drop-off time."
    try:
        opening = datetime.strptime(start_time, "%H:%M")
        closing = datetime.strptime(end_time, "%H:%M")
    except ValueError:
        return False, "This time is not available based on the shop's schedule. Please choose another available time."
    requested_end = requested_start + timedelta(minutes=duration)
    if requested_start < opening or requested_end > closing:
        return False, "This time is outside the shop's business hours. Please choose a time during the available schedule."

    buffer_minutes = int(day_row.get("buffer_minutes") or 0)
    requested_block_start = requested_start - timedelta(minutes=buffer_minutes)
    requested_block_end = requested_end + timedelta(minutes=buffer_minutes)
    params: list[Any] = [parsed_date.isoformat()]
    exclusion_sql = ""
    if exclude_appointment_id is not None:
        exclusion_sql = " AND id <> ?"
        params.append(exclude_appointment_id)
    existing_rows = conn.execute(
        f"""
        SELECT id, requested_time
        FROM service_appointments
        WHERE requested_date = ?
          AND status IN ('Requested', 'Confirmed')
          {exclusion_sql}
        """,
        params,
    ).fetchall()
    for existing in existing_rows:
        try:
            existing_start = datetime.strptime(str(existing["requested_time"] or "")[:5], "%H:%M")
        except ValueError:
            continue
        existing_end = existing_start + timedelta(minutes=duration)
        if requested_block_start < existing_end and requested_block_end > existing_start:
            return False, "This time is not available based on the shop's schedule. Please choose another available time."
    return True, ""


def available_booking_times(
    conn: sqlite3.Connection,
    requested_date: str,
    exclude_appointment_id: int | None = None,
) -> dict[str, Any]:
    parsed_date = parse_date_value(requested_date)
    if not parsed_date:
        return {"state": "invalid", "message": "Please choose a valid appointment date.", "times": []}
    if parsed_date < shop_today():
        return {"state": "past", "message": "Please choose a date that has not passed.", "times": []}
    closed, closed_message = is_closed_booking_day(conn, requested_date)
    if closed:
        return {"state": "closed", "message": closed_message, "times": []}
    availability = load_shop_availability(conn)
    day_row = next(
        (row for row in availability if int(row.get("day_of_week") or 0) == parsed_date.weekday()),
        None,
    )
    if not day_row:
        return {
            "state": "closed",
            "message": "The shop is closed on this day. Please choose another day.",
            "times": [],
        }
    duration = int(day_row.get("appointment_length_minutes") or 60)
    buffer_minutes = int(day_row.get("buffer_minutes") or 0)
    try:
        slot = datetime.strptime(str(day_row.get("start_time") or "09:00")[:5], "%H:%M")
        closing = datetime.strptime(str(day_row.get("end_time") or "17:00")[:5], "%H:%M")
    except ValueError:
        return {
            "state": "unavailable",
            "message": "No appointment times are available for this day. Please choose another day.",
            "times": [],
        }
    times: list[dict[str, str]] = []
    while slot + timedelta(minutes=duration) <= closing:
        raw_time = slot.strftime("%H:%M")
        available, _ = is_booking_time_available(
            conn,
            requested_date,
            raw_time,
            duration,
            exclude_appointment_id,
        )
        if available:
            times.append({"value": raw_time, "label": format_time_label(raw_time)})
        slot += timedelta(minutes=duration + buffer_minutes)
    if not times:
        return {
            "state": "unavailable",
            "message": "No appointment times are available for this day. Please choose another day.",
            "times": [],
        }
    return {"state": "available", "message": "", "times": times}


def booking_availability_for_month(
    conn: sqlite3.Connection,
    month: str,
    exclude_appointment_id: int | None = None,
) -> dict[str, Any]:
    try:
        month_start = datetime.strptime(str(month or ""), "%Y-%m").date().replace(day=1)
    except ValueError:
        raise HTTPException(status_code=400, detail="Please choose a valid month.")
    if month_start.year < shop_today().year or month_start.year > shop_today().year + 2:
        raise HTTPException(status_code=400, detail="That month is outside the booking window.")
    next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    month_end = next_month - timedelta(days=1)
    days: list[dict[str, Any]] = []
    current = month_start
    while current <= month_end:
        result = available_booking_times(conn, current.isoformat(), exclude_appointment_id)
        days.append({"date": current.isoformat(), "available": result["state"] == "available"})
        current += timedelta(days=1)
    return {"month": month_start.strftime("%Y-%m"), "days": days}


def create_service_appointment(conn: sqlite3.Connection, data: dict[str, Any]) -> int:
    ensure_calendar_schema(conn)
    status = str(data.get("status") or "Requested").strip()
    if status not in APPOINTMENT_STATUS_OPTIONS:
        status = "Requested"
    source = str(data.get("source") or "customer_booking").strip() or "customer_booking"
    now = datetime.utcnow().isoformat()
    cur = conn.execute(
        """
        INSERT INTO service_appointments (
          shop_id, customer_id, vehicle_id, estimate_id, repair_id, invoice_id, customer_name, customer_phone,
          customer_email, vehicle_label, service_name, requested_date,
          requested_time, notes, source, status, created_at, updated_at
        )
        VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            optional_int_value(data.get("customer_id")),
            optional_int_value(data.get("vehicle_id")),
            optional_int_value(data.get("estimate_id")),
            optional_int_value(data.get("repair_id")),
            optional_int_value(data.get("invoice_id")),
            str(data.get("customer_name") or "").strip(),
            str(data.get("customer_phone") or "").strip(),
            str(data.get("customer_email") or "").strip(),
            str(data.get("vehicle_label") or "").strip(),
            str(data.get("service_name") or "").strip(),
            str(data.get("requested_date") or "").strip(),
            str(data.get("requested_time") or "").strip(),
            str(data.get("notes") or "").strip(),
            source,
            status,
            now,
            now,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def update_service_appointment_status(conn: sqlite3.Connection, appointment_id: int, status: str) -> None:
    if status not in APPOINTMENT_STATUS_OPTIONS:
        raise HTTPException(status_code=400, detail="Invalid appointment status")
    ensure_calendar_schema(conn)
    conn.execute(
        """
        UPDATE service_appointments
        SET status = ?, updated_at = ?
        WHERE id = ?
        """,
        (status, datetime.utcnow().isoformat(), appointment_id),
    )
    conn.commit()


def load_service_appointment(conn: sqlite3.Connection, appointment_id: int) -> dict[str, Any] | None:
    ensure_calendar_schema(conn)
    row = conn.execute(
        "SELECT * FROM service_appointments WHERE id = ? LIMIT 1",
        (appointment_id,),
    ).fetchone()
    return dict(row) if row else None


def reschedule_service_appointment(
    conn: sqlite3.Connection,
    appointment_id: int,
    requested_date: str,
    requested_time: str,
) -> None:
    appointment = load_service_appointment(conn, appointment_id)
    if not appointment or appointment.get("status") != "Confirmed":
        raise HTTPException(status_code=404, detail="Confirmed appointment not found.")
    available, warning = is_booking_time_available(
        conn,
        requested_date,
        requested_time,
        exclude_appointment_id=appointment_id,
    )
    generated_times = available_booking_times(conn, requested_date, appointment_id)
    if available:
        available = requested_time in {slot["value"] for slot in generated_times.get("times", [])}
    if not available:
        raise HTTPException(
            status_code=400,
            detail=warning
            or generated_times.get("message")
            or "No appointment times are available for this day. Please choose another day.",
        )
    conn.execute(
        """
        UPDATE service_appointments
        SET requested_date = ?, requested_time = ?, updated_at = ?
        WHERE id = ? AND status = 'Confirmed'
        """,
        (requested_date, requested_time, datetime.utcnow().isoformat(), appointment_id),
    )
    conn.commit()


def load_service_appointments(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    ensure_calendar_schema(conn)
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM service_appointments
            ORDER BY requested_date ASC, requested_time ASC, id ASC
            """
        ).fetchall()
    ]


def group_calendar_appointments(appointments: list[dict[str, Any]], today: date | None = None) -> dict[str, list[dict[str, Any]]]:
    today = today or local_today()
    week_end = today + timedelta(days=6)
    grouped = {"today": [], "this_week": [], "upcoming": [], "completed_cancelled": []}
    for appointment in appointments:
        status = str(appointment.get("status") or "")
        if status in {"Completed", "Cancelled"}:
            grouped["completed_cancelled"].append(appointment)
            continue
        requested = parse_date_value(appointment.get("requested_date"))
        if requested == today:
            grouped["today"].append(appointment)
        elif requested and today < requested <= week_end:
            grouped["this_week"].append(appointment)
        else:
            grouped["upcoming"].append(appointment)
    return grouped


def group_booking_review_appointments(appointments: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped = {"pending": [], "confirmed": [], "handled": [], "declined_cancelled": []}
    for appointment in appointments:
        status = str(appointment.get("status") or "Requested")
        if status == "Requested":
            grouped["pending"].append(appointment)
        elif status == "Confirmed":
            grouped["confirmed"].append(appointment)
        elif status in {"Handled", "Completed"}:
            grouped["handled"].append(appointment)
        else:
            grouped["declined_cancelled"].append(appointment)
    return grouped


def appointment_customer_messages(
    appointment: dict[str, Any],
    sender_context: Any | None = None,
) -> dict[str, str]:
    customer_name = str(appointment.get("customer_name") or "").strip() or "there"
    shop_name = resolve_sender_display_name(sender_context)
    appointment_date = format_pro_date(appointment.get("requested_date")) or "the scheduled date"
    appointment_time = format_pro_time(appointment.get("requested_time")) or "the scheduled time"
    service_name = str(appointment.get("service_name") or "").strip() or "your requested service"
    phone = format_phone(
        _context_lookup(sender_context, "shop_phone")
        or _context_lookup(sender_context, "phone")
        or ""
    )
    email = str(
        _context_lookup(sender_context, "shop_email")
        or _context_lookup(sender_context, "email")
        or ""
    ).strip()
    contact_parts = [part for part in (phone, email) if part]
    contact = " or ".join(contact_parts)
    reschedule_contact = (
        f"If you need to reschedule or cancel, please contact us at {contact}."
        if contact
        else "If you need to reschedule or cancel, please contact the shop directly."
    )
    alternate_contact = (
        f"If not, contact us at {contact}."
        if contact
        else "If not, please contact the shop directly."
    )
    booking_contact = (
        f"please contact us at {contact}."
        if contact
        else "please contact the shop directly."
    )
    duration_note = (
        "Repair duration depends on the service, inspection, parts availability, "
        "and shop schedule."
    )
    return {
        "confirmation_message": (
            f"Hi {customer_name}, this is {shop_name}. Your appointment request for "
            f"{service_name} has been confirmed for {appointment_date} at {appointment_time}. "
            f"This is your drop-off / appointment time. {duration_note} "
            f"{reschedule_contact}"
        ),
        "reschedule_message": (
            f"Hi {customer_name}, this is {shop_name}. We need to reschedule your appointment "
            f"for {service_name}. Your new drop-off / appointment time is {appointment_date} "
            f"at {appointment_time}. Please confirm this works for you. {duration_note} "
            f"{alternate_contact}"
        ),
        "cancellation_message": (
            f"Hi {customer_name}, this is {shop_name}. Your appointment request for "
            f"{service_name} on {appointment_date} at {appointment_time} has been canceled. "
            f"{duration_note} If you would like to book another appointment, "
            f"{booking_contact}"
        ),
    }


def attach_appointment_customer_messages(
    appointments: list[dict[str, Any]],
    sender_context: Any | None = None,
) -> list[dict[str, Any]]:
    for appointment in appointments:
        appointment.update(appointment_customer_messages(appointment, sender_context))
    return appointments


def ensure_shop_profile_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shop_profile (
          id INTEGER PRIMARY KEY CHECK (id = 1),
          shop_name TEXT,
          phone TEXT,
          email TEXT,
          address TEXT,
          shop_phone TEXT,
          shop_email TEXT,
          shop_address TEXT,
          shop_city TEXT,
          shop_state TEXT,
          shop_zip TEXT,
          website TEXT,
          scheduling_link TEXT,
          external_scheduling_link TEXT,
          logo_url TEXT,
          labor_rate_default REAL,
          tax_rate_default REAL,
          default_labor_rate REAL,
          tax_rate REAL,
          shop_supplies_fee REAL,
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
    for column_name, column_sql in {
        "shop_name": "shop_name TEXT",
        "phone": "phone TEXT",
        "email": "email TEXT",
        "address": "address TEXT",
        "shop_phone": "shop_phone TEXT",
        "shop_email": "shop_email TEXT",
        "shop_address": "shop_address TEXT",
        "shop_city": "shop_city TEXT",
        "shop_state": "shop_state TEXT",
        "shop_zip": "shop_zip TEXT",
        "website": "website TEXT",
        "scheduling_link": "scheduling_link TEXT",
        "external_scheduling_link": "external_scheduling_link TEXT",
        "logo_url": "logo_url TEXT",
        "labor_rate_default": "labor_rate_default REAL",
        "tax_rate_default": "tax_rate_default REAL",
        "default_labor_rate": "default_labor_rate REAL",
        "tax_rate": "tax_rate REAL",
        "shop_supplies_fee": "shop_supplies_fee REAL",
        "warranty_note": "warranty_note TEXT",
        "quote_expiration_days": "quote_expiration_days INTEGER",
        "custom_footer_note": "custom_footer_note TEXT",
        "updated_at": "updated_at TEXT",
    }.items():
        if column_name not in columns:
            conn.execute(f"ALTER TABLE shop_profile ADD COLUMN {column_sql}")
    conn.commit()


def normalize_shop_profile_context(profile: dict[str, Any] | None) -> dict[str, Any]:
    normalized = dict(profile or {})
    raw_shop_name = str(normalized.get("shop_name") or "").strip()
    is_demo_profile = bool(raw_shop_name) and not scrub_demo_shop_name(raw_shop_name)
    normalized["shop_name"] = "" if is_demo_profile else raw_shop_name
    legacy_phone = normalized.get("phone")
    legacy_email = scrub_demo_shop_email(normalized.get("email"))
    legacy_address = scrub_demo_shop_address(normalized.get("address"))

    normalized["shop_phone"] = scrub_demo_shop_phone(normalized.get("shop_phone") or legacy_phone or "")
    normalized["shop_email"] = scrub_demo_shop_email(normalized.get("shop_email")) or legacy_email
    normalized["shop_address"] = scrub_demo_shop_address(normalized.get("shop_address")) or legacy_address
    normalized["shop_city"] = "" if is_demo_profile else str(normalized.get("shop_city") or "").strip()
    normalized["shop_state"] = "" if is_demo_profile else str(normalized.get("shop_state") or "").strip()
    normalized["shop_zip"] = "" if is_demo_profile else str(normalized.get("shop_zip") or "").strip()
    normalized["phone"] = normalized["shop_phone"]
    normalized["email"] = normalized["shop_email"]
    normalized["address"] = normalized["shop_address"]
    normalized["external_scheduling_link"] = str(
        normalized.get("external_scheduling_link") or normalized.get("scheduling_link") or ""
    ).strip()
    normalized["scheduling_link"] = normalized["external_scheduling_link"]
    normalized["default_labor_rate"] = normalized.get("default_labor_rate")
    if normalized["default_labor_rate"] in (None, ""):
        normalized["default_labor_rate"] = normalized.get("labor_rate_default")
    if normalized["default_labor_rate"] in (None, ""):
        normalized["default_labor_rate"] = 90.0
    normalized["labor_rate_default"] = normalized["default_labor_rate"]
    normalized["tax_rate"] = normalized.get("tax_rate")
    if normalized["tax_rate"] in (None, ""):
        normalized["tax_rate"] = normalized.get("tax_rate_default")
    if normalized["tax_rate"] in (None, ""):
        normalized["tax_rate"] = 0.0
    normalized["tax_rate_default"] = normalized["tax_rate"]
    if normalized.get("shop_supplies_fee") in (None, ""):
        normalized["shop_supplies_fee"] = 0.0
    for key in ("default_labor_rate", "labor_rate_default", "tax_rate", "tax_rate_default", "shop_supplies_fee"):
        try:
            normalized[key] = max(0.0, float(normalized.get(key) or 0.0))
        except (TypeError, ValueError):
            normalized[key] = 0.0
    normalized["default_labor_rate_input"] = format_decimal_input(normalized.get("default_labor_rate"))
    normalized["tax_rate_input"] = format_decimal_input(normalized.get("tax_rate"), 3)
    normalized["shop_supplies_fee_input"] = format_decimal_input(normalized.get("shop_supplies_fee"))
    return normalized


def load_shop_profile_context(conn: sqlite3.Connection) -> dict[str, Any]:
    try:
        ensure_shop_profile_schema(conn)
        row = conn.execute("SELECT * FROM shop_profile WHERE id = 1").fetchone()
    except sqlite3.OperationalError:
        return {}
    return normalize_shop_profile_context(dict(row) if row else {})


def save_shop_settings(conn: sqlite3.Connection, form: dict[str, str]) -> dict[str, Any]:
    ensure_shop_profile_schema(conn)
    current = load_shop_profile_context(conn)
    field_aliases = {
        "shop_address": ("shop_address", "street_address"),
        "shop_city": ("shop_city", "city"),
        "shop_state": ("shop_state", "state"),
        "shop_zip": ("shop_zip", "zip_code"),
    }
    for key in ("shop_name", "shop_phone", "shop_email", "shop_address", "shop_city", "shop_state", "shop_zip"):
        aliases = field_aliases.get(key, (key,))
        submitted_key = next((alias for alias in aliases if alias in form), None)
        if submitted_key is None:
            continue
        current[key] = clean_shop_phone(form.get(submitted_key, "")) if key == "shop_phone" else form.get(submitted_key, "")
    for key, legacy_key in (("shop_phone", "phone"), ("shop_email", "email"), ("shop_address", "address")):
        if key in current and any(alias in form for alias in field_aliases.get(key, (key,))):
            current[legacy_key] = current[key]
    for key in ("default_labor_rate", "shop_supplies_fee"):
        if key in form:
            current[key] = max(0.0, optional_float(form, key) or 0.0)
    current["tax_rate"] = max(0.0, optional_float(form, "tax_rate") or 0.0) if "use_tax_rate" in form else 0.0
    if "external_scheduling_link" in form or "scheduling_link" in form:
        current["external_scheduling_link"] = form.get("external_scheduling_link", form.get("scheduling_link", ""))
        current["scheduling_link"] = current["external_scheduling_link"]
    current = normalize_shop_profile_context(current)
    current["updated_at"] = datetime.utcnow().isoformat()
    conn.execute(
        """
        INSERT INTO shop_profile (
          id, shop_name, phone, email, address, shop_phone, shop_email,
          shop_address, shop_city, shop_state, shop_zip, website, scheduling_link,
          external_scheduling_link, logo_url, labor_rate_default, tax_rate_default,
          default_labor_rate, tax_rate, shop_supplies_fee, warranty_note,
          quote_expiration_days, custom_footer_note, updated_at
        )
        VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          shop_name = excluded.shop_name,
          phone = excluded.phone,
          email = excluded.email,
          address = excluded.address,
          shop_phone = excluded.shop_phone,
          shop_email = excluded.shop_email,
          shop_address = excluded.shop_address,
          shop_city = excluded.shop_city,
          shop_state = excluded.shop_state,
          shop_zip = excluded.shop_zip,
          scheduling_link = excluded.scheduling_link,
          external_scheduling_link = excluded.external_scheduling_link,
          labor_rate_default = excluded.labor_rate_default,
          tax_rate_default = excluded.tax_rate_default,
          default_labor_rate = excluded.default_labor_rate,
          tax_rate = excluded.tax_rate,
          shop_supplies_fee = excluded.shop_supplies_fee,
          updated_at = excluded.updated_at
        """,
        (
            current.get("shop_name") or "",
            current.get("shop_phone") or "",
            current.get("shop_email") or "",
            current.get("shop_address") or "",
            current.get("shop_phone") or "",
            current.get("shop_email") or "",
            current.get("shop_address") or "",
            current.get("shop_city") or "",
            current.get("shop_state") or "",
            current.get("shop_zip") or "",
            current.get("website") or "",
            current.get("external_scheduling_link") or "",
            current.get("external_scheduling_link") or "",
            current.get("logo_url") or "",
            current.get("default_labor_rate") or 0.0,
            current.get("tax_rate") or 0.0,
            current.get("default_labor_rate") or 0.0,
            current.get("tax_rate") or 0.0,
            current.get("shop_supplies_fee") or 0.0,
            current.get("warranty_note") or "",
            current.get("quote_expiration_days") or 30,
            current.get("custom_footer_note") or "",
            current["updated_at"],
        ),
    )
    conn.commit()
    return current


def save_shop_scheduling_link(conn: sqlite3.Connection, scheduling_link: str) -> dict[str, Any]:
    return save_shop_settings(conn, {"external_scheduling_link": scheduling_link})


def build_maintenance_reminder_message(
    *,
    customer: dict[str, Any],
    vehicle: dict[str, Any],
    record: dict[str, Any],
    sender_context: Any | None = None,
) -> str:
    first_name = str(customer.get("first_name") or "").strip()
    greeting = f"Hi {first_name}," if first_name else "Hi,"
    sender_display_name = resolve_sender_display_name(sender_context)
    scheduling_link = resolve_scheduling_link(sender_context)
    vehicle_label_text = vehicle_reminder_label(vehicle)
    service_type = str(record.get("service_type") or "maintenance service").strip()
    service_phrase = service_type
    due_mileage = record.get("next_due_mileage")
    due_date = record.get("earliest_estimated_due_date") or record.get("due_date_by_time_interval") or record.get("next_due_date")
    current_mileage = optional_int_value(vehicle.get("mileage"))
    if current_mileage is None:
        current_mileage = optional_int_value(record.get("current_mileage"))

    status_text = "due"
    if record.get("maintenance_status_key") == "overdue":
        status_text = "overdue"
    elif record.get("maintenance_status_key") == "due_soon":
        status_text = "due"
    elif record.get("maintenance_status"):
        status_text = str(record.get("maintenance_status") or "").strip().lower()

    due_parts = []
    if due_mileage is not None:
        due_parts.append(f"around {format_mileage(due_mileage)} miles")
    if due_date:
        due_parts.append(f"by {format_pro_date(due_date)}")

    message_parts = [
        f"{greeting} this is {sender_display_name}. Your {vehicle_label_text} is {status_text} for {service_phrase}."
    ]
    if due_parts:
        message_parts.append(f"Our records show it was due {' or '.join(due_parts)}.")
    if current_mileage is not None:
        message_parts.append(f"You are currently at about {format_mileage(current_mileage)} miles.")
    body = " ".join(message_parts)
    if scheduling_link:
        return f"{body}\n\nSchedule your service here:\n{scheduling_link}\n\nReply here if you have any questions."
    return f"{body}\n\nReply here when you're ready to schedule."


MAINTENANCE_REMINDER_EVENT_STATUSES = {
    "drafted",
    "copied",
    "marked_sent",
    "snoozed",
    "customer_replied",
    "completed",
}
MAINTENANCE_REMINDER_METHODS = {"manual", "sms", "email", "phone", "other"}
MAINTENANCE_REMINDER_STATUS_LABELS = {
    "drafted": "Drafted",
    "copied": "Copied",
    "marked_sent": "Sent / Waiting",
    "snoozed": "Snoozed",
    "customer_replied": "Customer Replied",
    "completed": "Completed",
}
AUTOMATIC_MAINTENANCE_REMINDER_STATUSES = {"drafted", "copied"}


def maintenance_reminder_status_label(status: Any) -> str:
    key = str(status or "").strip()
    return MAINTENANCE_REMINDER_STATUS_LABELS.get(key, key.replace("_", " ").title() if key else "")


def ensure_maintenance_reminder_events_schema(conn: sqlite3.Connection) -> None:
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
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_maintenance_reminder_events_customer_id "
        "ON maintenance_reminder_events (customer_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_maintenance_reminder_events_vehicle_id "
        "ON maintenance_reminder_events (vehicle_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_maintenance_reminder_events_record_id "
        "ON maintenance_reminder_events (maintenance_record_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_maintenance_reminder_events_status "
        "ON maintenance_reminder_events (status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_maintenance_reminder_events_snoozed_until "
        "ON maintenance_reminder_events (snoozed_until)"
    )
    conn.commit()


def create_maintenance_reminder_event(
    conn: sqlite3.Connection,
    *,
    customer_id: int,
    vehicle_id: int,
    maintenance_record_id: int,
    service_type: str,
    status: str,
    method: str = "manual",
    message: str = "",
    sent_at: str | None = None,
    snoozed_until: str | None = None,
    notes: str = "",
    created_at: str | None = None,
) -> int:
    if status not in MAINTENANCE_REMINDER_EVENT_STATUSES:
        raise ValueError("Invalid maintenance reminder status")
    if method not in MAINTENANCE_REMINDER_METHODS:
        raise ValueError("Invalid maintenance reminder method")
    ensure_maintenance_reminder_events_schema(conn)
    now = created_at or datetime.utcnow().isoformat()
    cur = conn.execute(
        """
        INSERT INTO maintenance_reminder_events (
          customer_id, vehicle_id, maintenance_record_id, service_type,
          status, method, message, created_at, sent_at, snoozed_until, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            customer_id,
            vehicle_id,
            maintenance_record_id,
            service_type,
            status,
            method,
            message,
            now,
            sent_at,
            snoozed_until,
            notes,
        ),
    )
    return int(cur.lastrowid)


def load_maintenance_reminder_events_map(
    conn: sqlite3.Connection,
    maintenance_record_ids: set[int] | list[int],
) -> dict[int, list[dict[str, Any]]]:
    record_ids = sorted({int(record_id) for record_id in maintenance_record_ids if record_id})
    if not record_ids:
        return {}
    ensure_maintenance_reminder_events_schema(conn)
    placeholders = ",".join("?" for _ in record_ids)
    rows = [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT *
            FROM maintenance_reminder_events
            WHERE maintenance_record_id IN ({placeholders})
            ORDER BY created_at DESC, id DESC
            """,
            record_ids,
        ).fetchall()
    ]
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        row["status_label"] = maintenance_reminder_status_label(row.get("status"))
        record_id = optional_int_value(row.get("maintenance_record_id"))
        if record_id is not None:
            grouped.setdefault(record_id, []).append(row)
    return grouped


def attach_maintenance_reminder_events(
    records: list[dict[str, Any]],
    events_map: dict[int, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    for record in records:
        record_id = optional_int_value(record.get("id")) or 0
        events = events_map.get(record_id, [])
        automatic_events = [
            event for event in events if event.get("status") in AUTOMATIC_MAINTENANCE_REMINDER_STATUSES
        ]
        record["reminder_events"] = events
        record["automatic_reminder_events"] = automatic_events
        record["latest_reminder_event"] = events[0] if events else None
        record["latest_automatic_reminder_event"] = automatic_events[0] if automatic_events else None
    return records


def mark_active_maintenance_baselines(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    active_ids = {
        optional_int_value(record.get("id"))
        for record in latest_maintenance_records_by_vehicle_service(records)
    }
    for record in records:
        record["is_active_maintenance_baseline"] = optional_int_value(record.get("id")) in active_ids
        if not record["is_active_maintenance_baseline"]:
            record["reminder_message"] = ""
    return records


def maintenance_reminder_follow_up_bucket(record: dict[str, Any], today: date | None = None) -> str | None:
    if record.get("is_active_maintenance_baseline") is False:
        return None
    status_key = record.get("maintenance_status_key")
    if status_key not in {"overdue", "due_soon"}:
        return None
    event = record.get("latest_automatic_reminder_event") or record.get("latest_reminder_event") or {}
    event_status = event.get("status")
    if event_status in AUTOMATIC_MAINTENANCE_REMINDER_STATUSES:
        return "sent_waiting"
    return str(status_key)


def latest_maintenance_records_by_vehicle_service(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[tuple[int, int, str], dict[str, Any]] = {}

    def sort_key(record: dict[str, Any]) -> tuple[date, int, int]:
        record_date = parse_date_value(record.get("date_performed")) or date.min
        mileage = optional_int_value(record.get("mileage_performed")) or -1
        record_id = optional_int_value(record.get("id")) or -1
        return record_date, mileage, record_id

    for record in records:
        customer_id = optional_int_value(record.get("customer_id")) or 0
        vehicle_id = optional_int_value(record.get("vehicle_id")) or 0
        service_key = normalize_maintenance_service_type(record.get("service_type") or "")
        if not service_key:
            service_key = f"maintenance-{optional_int_value(record.get('id')) or 0}"
        alias_key = MAINTENANCE_SERVICE_ALIASES.get(service_key)
        key = (customer_id, vehicle_id, alias_key or service_key)
        existing = latest.get(key)
        if existing is None or sort_key(record) > sort_key(existing):
            latest[key] = record

    return list(latest.values())


def follow_up_item_from_maintenance_record(record: dict[str, Any]) -> dict[str, Any]:
    customer = customer_name(record)
    vehicle = vehicle_label(record)
    interval_parts = []
    if record.get("interval_miles"):
        interval_parts.append(f"{format_miles(record['interval_miles'])} miles")
    if record.get("interval_months"):
        interval_parts.append(f"{int(record['interval_months'])} months")

    reason = "Maintenance is due soon."
    if record.get("maintenance_status_key") == "overdue":
        if record.get("remaining_miles") is not None and int(record["remaining_miles"]) <= 0:
            reason = "Past the next due mileage."
        elif record.get("remaining_days") is not None and int(record["remaining_days"]) < 0:
            reason = "Past the next due date."
        else:
            reason = "Maintenance is overdue."
    elif record.get("remaining_miles") is not None and int(record["remaining_miles"]) <= 1000:
        reason = "Within 1,000 miles of the next due mileage."
    elif record.get("remaining_days") is not None and int(record["remaining_days"]) <= 30:
        reason = "Within 30 days of the next due date."

    return {
        **record,
        "customer_name": customer,
        "vehicle_label": vehicle,
        "customer_url": f"/pro/customers/{record['customer_id']}",
        "vehicle_url": f"/pro/customers/{record['customer_id']}/vehicles/{record['vehicle_id']}",
        "status": record.get("maintenance_status") or "",
        "status_key": record.get("maintenance_status_key") or "",
        "current_mileage": record.get("current_mileage"),
        "due_mileage": record.get("next_due_mileage"),
        "due_date": record.get("next_due_date") or record.get("earliest_estimated_due_date") or "",
        "interval_label": " / ".join(interval_parts) if interval_parts else "-",
        "reason": reason,
        "suggested_message": record.get("reminder_message") or "",
    }


def annotate_vehicle_maintenance_records(
    records: list[dict[str, Any]],
    vehicle: dict[str, Any],
    customer: dict[str, Any],
    today: date,
    driving_rate: dict[str, Any] | None = None,
    sender_context: Any | None = None,
) -> list[dict[str, Any]]:
    current_mileage = optional_int_value(vehicle.get("mileage"))
    scheduling_link = resolve_scheduling_link(sender_context)
    scheduling_link_source = resolve_scheduling_link_source(sender_context)
    for record in records:
        mileage_performed = optional_int_value(record.get("mileage_performed"))
        interval_miles = optional_int_value(record.get("interval_miles"))
        interval_months = optional_int_value(record.get("interval_months"))
        defaults = maintenance_defaults_for(record.get("service_type") or "")
        if interval_miles is None:
            interval_miles = defaults.get("interval_miles")
        if interval_months is None:
            interval_months = defaults.get("interval_months")

        completed_date = parse_date_value(record.get("date_performed"))
        next_due_mileage = calculated_due_mileage(mileage_performed, interval_miles)
        next_due_date = add_months(completed_date, int(interval_months)) if completed_date and interval_months is not None else None
        remaining_miles = (
            next_due_mileage - current_mileage
            if next_due_mileage is not None and current_mileage is not None
            else None
        )
        remaining_days = (next_due_date - today).days if next_due_date else None
        mileage_due_date = estimated_due_date_by_mileage(
            current_mileage=current_mileage,
            next_due_mileage=next_due_mileage,
            miles_per_day=driving_rate.get("miles_per_day") if driving_rate else None,
            today=today,
        )
        earliest_due_date = None
        if mileage_due_date and next_due_date:
            earliest_due_date = min(mileage_due_date, next_due_date)
        else:
            earliest_due_date = mileage_due_date or next_due_date

        overdue = (
            (remaining_miles is not None and remaining_miles <= 0)
            or (next_due_date is not None and today > next_due_date)
        )
        due_soon = (
            (remaining_miles is not None and 0 <= remaining_miles <= 1000)
            or (remaining_days is not None and 0 <= remaining_days <= 30)
        )
        upcoming = (
            (remaining_miles is not None and 0 <= remaining_miles <= 3000)
            or (remaining_days is not None and 0 <= remaining_days <= 90)
        )

        if overdue:
            status = "Overdue"
            status_key = "overdue"
        elif due_soon:
            status = "Due Soon"
            status_key = "due_soon"
        elif upcoming:
            status = "Upcoming"
            status_key = "upcoming"
        else:
            status = "Current"
            status_key = "current"

        record.update(
            {
                "interval_miles": interval_miles,
                "interval_months": interval_months,
                "next_due_mileage": next_due_mileage,
                "next_due_date": next_due_date.isoformat() if next_due_date else "",
                "remaining_miles": remaining_miles,
                "remaining_days": remaining_days,
                "estimated_due_date_by_mileage": mileage_due_date.isoformat() if mileage_due_date else "",
                "due_date_by_time_interval": next_due_date.isoformat() if next_due_date else "",
                "earliest_estimated_due_date": earliest_due_date.isoformat() if earliest_due_date else "",
                "maintenance_status": status,
                "maintenance_status_key": status_key,
                "scheduling_link": scheduling_link,
                "scheduling_link_source": scheduling_link_source,
                "has_scheduling_link": bool(scheduling_link),
                "scheduling_link_warning": "" if scheduling_link else "Add your scheduling link in Shop Settings to include it in customer reminders.",
                "reminder_message": "",
            }
        )
        if status_key in {"overdue", "due_soon"}:
            record["reminder_message"] = build_maintenance_reminder_message(
                customer=customer,
                vehicle=vehicle,
                record=record,
                sender_context=sender_context,
            )
    return records


templates.env.filters["pro_phone"] = format_phone
templates.env.filters["pro_miles"] = format_mileage
templates.env.filters["pro_currency"] = format_currency
templates.env.filters["pro_date"] = format_pro_date
templates.env.filters["pro_datetime"] = format_pro_datetime
templates.env.filters["pro_time"] = format_pro_time
templates.env.filters["pro_quantity"] = format_quantity
templates.env.filters["pro_engine_badge"] = format_engine_badge
templates.env.filters["service_total"] = service_total_value


def parse_date_value(raw: Any) -> date | None:
    if not raw:
        return None
    value = str(raw).strip()
    if not value:
        return None
    if "T" in value or " " in value or value.endswith("Z"):
        parsed_datetime = parse_local_datetime_value(value)
        if parsed_datetime:
            return parsed_datetime.date()
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def parse_datetime_value(raw: Any) -> datetime | None:
    if not raw:
        return None
    value = str(raw).strip()
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        parsed_date = parse_date_value(value)
        return datetime.combine(parsed_date, datetime.min.time()) if parsed_date else None


def add_months(start: date, months: int) -> date:
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    month_lengths = [
        31,
        29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    ]
    return date(year, month, min(start.day, month_lengths[month - 1]))


def format_miles(value: Any) -> str:
    return f"{int(value):,}" if value is not None else "-"


def vehicle_label(record: dict[str, Any]) -> str:
    label = " ".join(
        str(record.get(key) or "").strip()
        for key in ("vehicle_year", "vehicle_make", "vehicle_model")
    ).strip()
    return label or "Vehicle"


def customer_name(record: dict[str, Any]) -> str:
    name = f"{record.get('first_name') or ''} {record.get('last_name') or ''}".strip()
    return name or "Customer"


def build_follow_up_record(row: sqlite3.Row, today: date) -> dict[str, Any]:
    record = dict(row)
    current_mileage = record.get("current_mileage")
    mileage_performed = record.get("mileage_performed")
    interval_miles = record.get("interval_miles")
    interval_months = record.get("interval_months")
    library_defaults = maintenance_defaults_for(record.get("service_type") or "")
    if interval_miles is None:
        interval_miles = library_defaults.get("interval_miles")
    if interval_months is None:
        interval_months = library_defaults.get("interval_months")
    record["interval_miles"] = interval_miles
    record["interval_months"] = interval_months
    performed_date = parse_date_value(record.get("date_performed"))

    due_mileage = record.get("due_mileage")
    if due_mileage is None and mileage_performed is not None and interval_miles:
        due_mileage = int(mileage_performed) + int(interval_miles)

    due_date = parse_date_value(record.get("due_date"))
    if due_date is None and performed_date and interval_months:
        due_date = add_months(performed_date, int(interval_months))

    status = "Future"
    status_key = "future"
    reason = "Maintenance is outside the follow-up window."

    if due_mileage is None and due_date is None:
        status = "Unknown"
        status_key = "unknown"
        reason = "Next due mileage and date are missing."
    else:
        overdue_by_mileage = (
            due_mileage is not None
            and current_mileage is not None
            and int(current_mileage) > due_mileage
        )
        overdue_by_date = due_date is not None and today > due_date
        due_soon_by_mileage = (
            due_mileage is not None
            and current_mileage is not None
            and int(current_mileage) <= due_mileage
            and due_mileage - int(current_mileage) <= 1000
        )
        due_soon_by_date = (
            due_date is not None
            and today <= due_date
            and due_date - today <= timedelta(days=30)
        )
        candidate_by_mileage = (
            due_mileage is not None
            and current_mileage is not None
            and int(current_mileage) <= due_mileage
            and due_mileage - int(current_mileage) <= 3000
        )
        candidate_by_date = (
            due_date is not None
            and today <= due_date
            and due_date - today <= timedelta(days=90)
        )

        if overdue_by_mileage or overdue_by_date:
            status = "Overdue"
            status_key = "overdue"
            if overdue_by_mileage and overdue_by_date:
                reason = "Past due by mileage and date."
            elif overdue_by_mileage:
                reason = "Vehicle mileage exceeds the next due mileage."
            else:
                reason = "Current date is past the next due date."
        elif due_soon_by_mileage or due_soon_by_date:
            status = "Due Soon"
            status_key = "due_soon"
            if due_soon_by_mileage and due_soon_by_date:
                reason = "Within 1,000 miles and 30 days of the follow-up point."
            elif due_soon_by_mileage:
                reason = "Within 1,000 miles of the next due mileage."
            else:
                reason = "Within 30 days of the next due date."
        elif candidate_by_mileage or candidate_by_date:
            status = "Candidate"
            status_key = "candidate"
            if candidate_by_mileage and candidate_by_date:
                reason = "Within 3,000 miles and 90 days of the follow-up point."
            elif candidate_by_mileage:
                reason = "Within 3,000 miles of the next due mileage."
            else:
                reason = "Within 90 days of the next due date."

    customer = customer_name(record)
    vehicle = vehicle_label(record)
    service_type = (record.get("service_type") or "maintenance").strip()
    reminder_record = {
        **record,
        "service_type": service_type,
        "maintenance_status": status,
        "maintenance_status_key": status_key,
        "next_due_mileage": due_mileage,
        "next_due_date": due_date.isoformat() if due_date else "",
    }
    reminder_customer = {
        "first_name": record.get("first_name"),
        "last_name": record.get("last_name"),
    }
    reminder_vehicle = {
        "year": record.get("vehicle_year"),
        "make": record.get("vehicle_make"),
        "model": record.get("vehicle_model"),
        "mileage": record.get("current_mileage"),
    }

    interval_parts = []
    if interval_miles:
        interval_parts.append(f"{format_miles(interval_miles)} miles")
    if interval_months:
        interval_parts.append(f"{int(interval_months)} months")

    record.update(
        {
            "customer_name": customer,
            "vehicle_label": vehicle,
            "customer_url": f"/pro/customers/{record['customer_id']}",
            "vehicle_url": f"/pro/customers/{record['customer_id']}/vehicles/{record['vehicle_id']}",
            "due_mileage": due_mileage,
            "due_date": due_date.isoformat() if due_date else "",
            "status": status,
            "status_key": status_key,
            "reason": reason,
            "interval_label": " / ".join(interval_parts) if interval_parts else "-",
            "suggested_message": build_maintenance_reminder_message(
                customer=reminder_customer,
                vehicle=reminder_vehicle,
                record=reminder_record,
                sender_context=record,
            ),
        }
    )
    return record


def load_shop_name(conn: sqlite3.Connection) -> str:
    return str(load_shop_profile_context(conn).get("shop_name") or "").strip()


def ensure_discrepancy_approvals_schema(conn: sqlite3.Connection) -> None:
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
          parts_cost REAL,
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
    columns = {row[1] for row in conn.execute("PRAGMA table_info(discrepancy_approvals)").fetchall()}
    if "request_type" not in columns:
        conn.execute("ALTER TABLE discrepancy_approvals ADD COLUMN request_type TEXT NOT NULL DEFAULT 'finding'")
    if "labor_hours" not in columns:
        conn.execute("ALTER TABLE discrepancy_approvals ADD COLUMN labor_hours REAL")
    if "labor_rate" not in columns:
        conn.execute("ALTER TABLE discrepancy_approvals ADD COLUMN labor_rate REAL")
    if "labor_amount" not in columns:
        conn.execute("ALTER TABLE discrepancy_approvals ADD COLUMN labor_amount REAL")
    if "labor_reason" not in columns:
        conn.execute("ALTER TABLE discrepancy_approvals ADD COLUMN labor_reason TEXT")
    if "part_description" not in columns:
        conn.execute("ALTER TABLE discrepancy_approvals ADD COLUMN part_description TEXT")
    if "part_name" not in columns:
        conn.execute("ALTER TABLE discrepancy_approvals ADD COLUMN part_name TEXT")
    if "part_number" not in columns:
        conn.execute("ALTER TABLE discrepancy_approvals ADD COLUMN part_number TEXT")
    if "quantity" not in columns:
        conn.execute("ALTER TABLE discrepancy_approvals ADD COLUMN quantity REAL")
    if "unit_cost" not in columns:
        conn.execute("ALTER TABLE discrepancy_approvals ADD COLUMN unit_cost REAL")
    if "parts_amount" not in columns:
        conn.execute("ALTER TABLE discrepancy_approvals ADD COLUMN parts_amount REAL")
    if "parts_total" not in columns:
        conn.execute("ALTER TABLE discrepancy_approvals ADD COLUMN parts_total REAL")
    if "repair_work_status" not in columns:
        conn.execute("ALTER TABLE discrepancy_approvals ADD COLUMN repair_work_status TEXT")
    if "repair_work_updated_at" not in columns:
        conn.execute("ALTER TABLE discrepancy_approvals ADD COLUMN repair_work_updated_at TEXT")
    if "linked_repair_record_id" not in columns:
        conn.execute("ALTER TABLE discrepancy_approvals ADD COLUMN linked_repair_record_id INTEGER")
    if "repair_record_created_at" not in columns:
        conn.execute("ALTER TABLE discrepancy_approvals ADD COLUMN repair_record_created_at TEXT")
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
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'discrepancy_approvals'"
    ).fetchone()
    table_sql = (sql["sql"] if isinstance(sql, sqlite3.Row) else sql[0]) if sql else ""
    if "deferred" not in (table_sql or ""):
        rebuild_discrepancy_approvals_for_deferred(conn)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_discrepancy_approvals_customer_id ON discrepancy_approvals (customer_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_discrepancy_approvals_vehicle_id ON discrepancy_approvals (vehicle_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_discrepancy_approvals_service_history_id ON discrepancy_approvals (service_history_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_discrepancy_approvals_decision ON discrepancy_approvals (customer_decision)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_discrepancy_approvals_created_at ON discrepancy_approvals (created_at)")


def rebuild_discrepancy_approvals_for_deferred(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE discrepancy_approvals RENAME TO discrepancy_approvals_old")
    conn.execute(
        """
        CREATE TABLE discrepancy_approvals (
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
    conn.execute(
        """
        INSERT INTO discrepancy_approvals (
          id, customer_id, vehicle_id, service_history_id, shop_id, request_type,
          finding_title, finding_description, recommended_repair, estimated_cost,
          labor_hours, labor_rate, labor_amount, labor_reason, part_description,
          part_name, part_number, quantity, unit_cost, parts_amount, parts_total,
          customer_decision, repair_work_status, repair_work_updated_at,
          linked_repair_record_id, repair_record_created_at,
          decision_notes, decision_recorded_at, created_at, updated_at
        )
        SELECT
          id, customer_id, vehicle_id, service_history_id, shop_id,
          CASE WHEN request_type = 'general' OR request_type IS NULL OR TRIM(request_type) = '' THEN 'finding' ELSE request_type END,
          finding_title, finding_description, recommended_repair, estimated_cost,
          labor_hours, labor_rate, labor_amount, labor_reason, part_description,
          COALESCE(NULLIF(part_name, ''), part_description), part_number,
          quantity, unit_cost, parts_amount, COALESCE(parts_total, parts_amount),
          customer_decision, NULL, NULL, NULL, NULL,
          decision_notes, decision_recorded_at, created_at, updated_at
        FROM discrepancy_approvals_old
        """
    )
    conn.execute("DROP TABLE discrepancy_approvals_old")


def ensure_discrepancy_approval_events_schema(conn: sqlite3.Connection) -> None:
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
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_discrepancy_approval_events_vehicle "
        "ON discrepancy_approval_events (vehicle_id, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_discrepancy_approval_events_approval "
        "ON discrepancy_approval_events (approval_id)"
    )


def append_discrepancy_approval_event(
    conn: sqlite3.Connection,
    approval_id: int,
    customer_id: int,
    vehicle_id: int,
    event_type: str,
    event_label: str,
    created_at: str,
) -> None:
    ensure_discrepancy_approval_events_schema(conn)
    conn.execute(
        """
        INSERT INTO discrepancy_approval_events (
          approval_id, customer_id, vehicle_id, event_type, event_label, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (approval_id, customer_id, vehicle_id, event_type, event_label, created_at),
    )


def ensure_customer_status_schema(conn: sqlite3.Connection) -> None:
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
    columns = {row[1] for row in conn.execute("PRAGMA table_info(customers)").fetchall()}
    if "customer_status" not in columns:
        conn.execute("ALTER TABLE customers ADD COLUMN customer_status TEXT NOT NULL DEFAULT 'active'")
    conn.execute(
        """
        UPDATE customers
        SET customer_status = 'active'
        WHERE customer_status IS NULL OR TRIM(customer_status) = ''
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_customers_status ON customers (customer_status)")
    conn.commit()


def ensure_visual_reference_schema(conn: sqlite3.Connection) -> None:
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
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_visual_reference_records_vehicle "
        "ON visual_reference_records (vehicle_identifier)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_visual_reference_records_service "
        "ON visual_reference_records (service_type)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_visual_reference_images_reference "
        "ON visual_reference_images (visual_reference_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_visual_reference_specs_reference "
        "ON visual_reference_specs (visual_reference_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_visual_reference_oem_parts_reference "
        "ON visual_reference_oem_parts (visual_reference_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_visual_reference_hotspots_reference "
        "ON visual_reference_hotspots (visual_reference_id, sort_order)"
    )
    conn.commit()


def normalize_visual_reference_token(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def normalize_visual_reference_service(value: Any) -> str:
    normalized = normalize_visual_reference_token(value)
    return "_".join(normalized.replace("-", " ").split())


def normalize_visual_reference_image_type(value: Any) -> str:
    image_type = str(value or "").strip()
    if image_type not in VISUAL_REFERENCE_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Invalid visual reference image type")
    return image_type


def public_visual_reference_image_types() -> list[str]:
    return sorted(VISUAL_REFERENCE_IMAGE_TYPES)


def visual_reference_vehicle_identifiers(vehicle: dict[str, Any]) -> list[str]:
    year = str(vehicle.get("year") or "").strip()
    make = str(vehicle.get("make") or "").strip()
    model = str(vehicle.get("model") or "").strip()
    engine = str(vehicle.get("engine") or "").strip()
    vin = str(vehicle.get("vin") or "").strip()
    identifiers = [
        " ".join(part for part in [year, make, model, engine] if part),
        " ".join(part for part in [year, make, model] if part),
    ]
    if vin:
        identifiers.append(vin)
    seen: set[str] = set()
    result: list[str] = []
    for identifier in identifiers:
        normalized = normalize_visual_reference_token(identifier)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(identifier)
    return result


def load_visual_reference_record(
    conn: sqlite3.Connection,
    visual_reference_id: int,
) -> dict[str, Any]:
    ensure_visual_reference_schema(conn)
    record = row_to_dict(
        conn.execute(
            """
            SELECT *
            FROM visual_reference_records
            WHERE id = ?
            """,
            (visual_reference_id,),
        ).fetchone()
    )
    if not record:
        raise HTTPException(status_code=404, detail="Visual reference not found")
    record["images"] = [
        dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM visual_reference_images
            WHERE visual_reference_id = ?
            ORDER BY image_type ASC, id ASC
            """,
            (visual_reference_id,),
        ).fetchall()
    ]
    record["specs"] = [
        dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM visual_reference_specs
            WHERE visual_reference_id = ?
            ORDER BY id ASC
            """,
            (visual_reference_id,),
        ).fetchall()
    ]
    record["oem_parts"] = [
        dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM visual_reference_oem_parts
            WHERE visual_reference_id = ?
            ORDER BY id ASC
            """,
            (visual_reference_id,),
        ).fetchall()
    ]
    record["hotspots"] = [
        dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM visual_reference_hotspots
            WHERE visual_reference_id = ?
            ORDER BY sort_order ASC, id ASC
            """,
            (visual_reference_id,),
        ).fetchall()
    ]
    return record


def load_visual_reference_child(
    conn: sqlite3.Connection,
    table_name: str,
    visual_reference_id: int,
    child_id: int,
) -> dict[str, Any]:
    allowed_tables = {
        "visual_reference_images",
        "visual_reference_specs",
        "visual_reference_oem_parts",
        "visual_reference_hotspots",
    }
    if table_name not in allowed_tables:
        raise HTTPException(status_code=400, detail="Invalid visual reference child table")
    child = row_to_dict(
        conn.execute(
            f"""
            SELECT *
            FROM {table_name}
            WHERE id = ? AND visual_reference_id = ?
            """,
            (child_id, visual_reference_id),
        ).fetchone()
    )
    if not child:
        raise HTTPException(status_code=404, detail="Visual reference child record not found")
    return child


def parse_multipart_disposition(value: str) -> dict[str, str]:
    parts = [part.strip() for part in value.split(";")]
    result: dict[str, str] = {}
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, raw_value = part.split("=", 1)
        result[key.strip().lower()] = raw_value.strip().strip('"')
    return result


async def read_multipart_form_data(request: Request) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" not in content_type or "boundary=" not in content_type:
        raise HTTPException(status_code=400, detail="Expected multipart form data")
    boundary = content_type.split("boundary=", 1)[1].split(";", 1)[0].strip().strip('"')
    if not boundary:
        raise HTTPException(status_code=400, detail="Missing multipart boundary")
    body = await request.body()
    delimiter = b"--" + boundary.encode("utf-8")
    fields: dict[str, str] = {}
    files: dict[str, dict[str, Any]] = {}
    for raw_part in body.split(delimiter):
        part = raw_part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        if part.endswith(b"--"):
            part = part[:-2].strip(b"\r\n")
        header_blob, separator, payload = part.partition(b"\r\n\r\n")
        if not separator:
            continue
        headers: dict[str, str] = {}
        for header_line in header_blob.decode("utf-8", errors="replace").split("\r\n"):
            if ":" not in header_line:
                continue
            key, value = header_line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
        disposition = parse_multipart_disposition(headers.get("content-disposition", ""))
        name = disposition.get("name", "")
        if not name:
            continue
        payload = payload.rstrip(b"\r\n")
        filename = disposition.get("filename")
        if filename is not None:
            upload = {
                "filename": filename,
                "content_type": headers.get("content-type", ""),
                "content": payload,
            }
            if name in files:
                existing = files[name]
                if isinstance(existing, list):
                    existing.append(upload)
                else:
                    files[name] = [existing, upload]
            else:
                files[name] = upload
        else:
            fields[name] = payload.decode("utf-8", errors="replace").strip()
    return fields, files


def save_visual_reference_upload(
    upload: dict[str, Any] | None,
    *,
    allowed_extensions: set[str] | None = None,
) -> str:
    if not upload:
        return ""
    content = upload.get("content") or b""
    filename = str(upload.get("filename") or "").strip()
    if not content or not filename:
        return ""
    suffix = Path(filename).suffix.lower()
    if suffix not in (allowed_extensions or VISUAL_REFERENCE_ALLOWED_UPLOAD_EXTENSIONS):
        raise HTTPException(status_code=400, detail="Unsupported image upload type")
    VISUAL_REFERENCE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid4().hex}{suffix}"
    target = (VISUAL_REFERENCE_UPLOAD_DIR / stored_name).resolve()
    target.relative_to(VISUAL_REFERENCE_UPLOAD_DIR.resolve())
    target.write_bytes(content)
    return f"{VISUAL_REFERENCE_UPLOAD_URL_PREFIX}/{stored_name}"


def normalize_upload_list(upload_or_uploads: Any) -> list[dict[str, Any]]:
    if isinstance(upload_or_uploads, list):
        return [upload for upload in upload_or_uploads if isinstance(upload, dict)]
    if isinstance(upload_or_uploads, dict):
        return [upload_or_uploads]
    return []


def save_image_upload_paths(
    upload_or_uploads: Any,
    *,
    max_files: int | None = None,
    allowed_extensions: set[str] | None = None,
) -> list[str]:
    paths: list[str] = []
    uploads = [
        upload
        for upload in normalize_upload_list(upload_or_uploads)
        if upload.get("filename") and upload.get("content")
    ]
    if max_files is not None and len(uploads) > max_files:
        raise HTTPException(status_code=400, detail=f"Upload up to {max_files} photos.")
    for upload in uploads:
        path = save_visual_reference_upload(upload, allowed_extensions=allowed_extensions)
        if path:
            paths.append(path)
    return paths


def parse_stored_photo_paths(value: Any) -> list[str]:
    try:
        paths = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return []
    if not isinstance(paths, list):
        return []
    return [str(path) for path in paths if str(path or "").strip()]


def attach_finding_photo_urls(record: dict[str, Any]) -> dict[str, Any]:
    record["before_inspection_photo_urls"] = parse_stored_photo_paths(
        record.get("before_inspection_photo_paths")
    )
    return record


def seed_visual_references(conn: sqlite3.Connection) -> None:
    ensure_visual_reference_schema(conn)
    if not VISUAL_REFERENCE_SEED_PATH.exists():
        return
    try:
        records = json.loads(VISUAL_REFERENCE_SEED_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    now = datetime.now(timezone.utc).isoformat()
    for record in records if isinstance(records, list) else []:
        vehicle_identifier = str(record.get("vehicle_identifier") or "").strip()
        service_type = normalize_visual_reference_service(record.get("service_type"))
        if not vehicle_identifier or not service_type:
            continue
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO visual_reference_records (
              vehicle_identifier, service_type, title, quick_reference, created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                vehicle_identifier,
                service_type,
                str(record.get("title") or "").strip(),
                str(record.get("quick_reference") or "").strip(),
                str(record.get("created_at") or now).strip() or now,
            ),
        )
        visual_reference_id = cur.lastrowid
        if not visual_reference_id:
            continue
        for image in record.get("images") or []:
            image_type = str(image.get("image_type") or "").strip()
            image_path = str(image.get("image_path") or "").strip()
            if image_type not in VISUAL_REFERENCE_IMAGE_TYPES or not image_path:
                continue
            conn.execute(
                """
                INSERT INTO visual_reference_images (
                  visual_reference_id, image_type, image_path, caption
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    visual_reference_id,
                    image_type,
                    image_path,
                    str(image.get("caption") or "").strip(),
                ),
            )
        for spec in record.get("specs") or []:
            spec_name = str(spec.get("spec_name") or "").strip()
            spec_value = str(spec.get("spec_value") or "").strip()
            if not spec_name or not spec_value:
                continue
            conn.execute(
                """
                INSERT INTO visual_reference_specs (
                  visual_reference_id, spec_name, spec_value, spec_unit
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    visual_reference_id,
                    spec_name,
                    spec_value,
                    str(spec.get("spec_unit") or "").strip(),
                ),
            )
        for part in record.get("oem_parts") or []:
            part_name = str(part.get("part_name") or "").strip()
            oem_part_number = str(part.get("oem_part_number") or "").strip()
            if not part_name or not oem_part_number:
                continue
            conn.execute(
                """
                INSERT INTO visual_reference_oem_parts (
                  visual_reference_id, part_name, oem_part_number, future_parts_intelligence_id
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    visual_reference_id,
                    part_name,
                    oem_part_number,
                    part.get("future_parts_intelligence_id"),
                ),
            )
        for hotspot in record.get("hotspots") or []:
            label = str(hotspot.get("label") or "").strip()
            hotspot_type = str(hotspot.get("hotspot_type") or "").strip()
            title = str(hotspot.get("title") or label).strip()
            try:
                x_percent = float(hotspot.get("x_percent"))
                y_percent = float(hotspot.get("y_percent"))
            except (TypeError, ValueError):
                continue
            try:
                sort_order = int(hotspot.get("sort_order") or 0)
            except (TypeError, ValueError):
                sort_order = 0
            if not label or not hotspot_type or not title:
                continue
            conn.execute(
                """
                INSERT INTO visual_reference_hotspots (
                  visual_reference_id, label, hotspot_type, x_percent, y_percent,
                  title, description, torque_spec, fastener_size, tool_size,
                  oem_part_number, related_part_name, parts_intelligence_id,
                  sort_order, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    visual_reference_id,
                    label,
                    hotspot_type,
                    max(0.0, min(100.0, x_percent)),
                    max(0.0, min(100.0, y_percent)),
                    title,
                    str(hotspot.get("description") or "").strip(),
                    str(hotspot.get("torque_spec") or "").strip(),
                    str(hotspot.get("fastener_size") or "").strip(),
                    str(hotspot.get("tool_size") or "").strip(),
                    str(hotspot.get("oem_part_number") or "").strip(),
                    str(hotspot.get("related_part_name") or "").strip(),
                    hotspot.get("parts_intelligence_id"),
                    sort_order,
                    str(hotspot.get("created_at") or now).strip() or now,
                ),
            )
    conn.commit()


def ensure_repair_intelligence_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS repair_intelligence (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          year TEXT NOT NULL DEFAULT '',
          make TEXT NOT NULL DEFAULT '',
          model TEXT NOT NULL DEFAULT '',
          engine TEXT NOT NULL DEFAULT '',
          repair_name TEXT NOT NULL,
          difficulty TEXT,
          labor_time_range TEXT,
          repair_snapshot TEXT,
          critical_checks TEXT,
          known_failure_patterns TEXT,
          inspection_opportunities TEXT,
          critical_specs TEXT,
          required_parts TEXT,
          recommended_parts TEXT,
          vendor_sources TEXT,
          special_tools TEXT,
          torque_specs TEXT,
          visual_layout TEXT,
          notes TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """
    )
    existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(repair_intelligence)").fetchall()}
    migrations = {
        "critical_specs": "ALTER TABLE repair_intelligence ADD COLUMN critical_specs TEXT",
        "vendor_sources": "ALTER TABLE repair_intelligence ADD COLUMN vendor_sources TEXT",
    }
    for column, statement in migrations.items():
        if column not in existing_columns:
            conn.execute(statement)
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_repair_intelligence_vehicle_repair
        ON repair_intelligence (year, make, model, engine, repair_name)
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_repair_intelligence_vehicle "
        "ON repair_intelligence (year, make, model, engine)"
    )
    conn.commit()


def repair_intelligence_json(value: Any) -> str:
    if value is None:
        return "[]"
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return "[]"
        try:
            json.loads(stripped)
            return stripped
        except json.JSONDecodeError:
            return json.dumps([stripped])
    return json.dumps(value)


def seed_repair_intelligence(conn: sqlite3.Connection) -> None:
    ensure_repair_intelligence_schema(conn)
    if not REPAIR_INTELLIGENCE_SEED_PATH.exists():
        return
    try:
        records = json.loads(REPAIR_INTELLIGENCE_SEED_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    now = datetime.now(timezone.utc).isoformat()
    json_fields = (
        "repair_snapshot",
        "critical_checks",
        "known_failure_patterns",
        "inspection_opportunities",
        "critical_specs",
        "required_parts",
        "recommended_parts",
        "vendor_sources",
        "special_tools",
        "torque_specs",
        "visual_layout",
    )
    for record in records if isinstance(records, list) else []:
        repair_name = str(record.get("repair_name") or "").strip()
        if not repair_name:
            continue
        values = {
            "year": str(record.get("year") or "").strip(),
            "make": str(record.get("make") or "").strip(),
            "model": str(record.get("model") or "").strip(),
            "engine": str(record.get("engine") or "").strip(),
            "repair_name": repair_name,
            "difficulty": str(record.get("difficulty") or "").strip(),
            "labor_time_range": str(record.get("labor_time_range") or "").strip(),
            "notes": str(record.get("notes") or "").strip(),
            "created_at": str(record.get("created_at") or now).strip() or now,
            "updated_at": str(record.get("updated_at") or now).strip() or now,
        }
        for field in json_fields:
            values[field] = repair_intelligence_json(record.get(field))
        conn.execute(
            """
            INSERT INTO repair_intelligence (
              year, make, model, engine, repair_name, difficulty, labor_time_range,
              repair_snapshot, critical_checks, known_failure_patterns,
              inspection_opportunities, critical_specs, required_parts, recommended_parts,
              vendor_sources, special_tools, torque_specs, visual_layout, notes, created_at, updated_at
            )
            VALUES (
              :year, :make, :model, :engine, :repair_name, :difficulty, :labor_time_range,
              :repair_snapshot, :critical_checks, :known_failure_patterns,
              :inspection_opportunities, :critical_specs, :required_parts, :recommended_parts,
              :vendor_sources, :special_tools, :torque_specs, :visual_layout, :notes, :created_at, :updated_at
            )
            ON CONFLICT(year, make, model, engine, repair_name) DO UPDATE SET
              difficulty = excluded.difficulty,
              labor_time_range = excluded.labor_time_range,
              repair_snapshot = excluded.repair_snapshot,
              critical_checks = excluded.critical_checks,
              known_failure_patterns = excluded.known_failure_patterns,
              inspection_opportunities = excluded.inspection_opportunities,
              critical_specs = excluded.critical_specs,
              required_parts = excluded.required_parts,
              recommended_parts = excluded.recommended_parts,
              vendor_sources = excluded.vendor_sources,
              special_tools = excluded.special_tools,
              torque_specs = excluded.torque_specs,
              visual_layout = excluded.visual_layout,
              notes = excluded.notes,
              updated_at = excluded.updated_at
            """,
            values,
        )
    conn.commit()


def repair_intelligence_token(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("-", " ").split())


def repair_intelligence_engine_token(value: Any) -> str:
    token = repair_intelligence_token(value).replace(" ", "")
    for suffix in ("liter", "litre", "l"):
        if token.endswith(suffix):
            token = token[: -len(suffix)]
            break
    return token


def repair_intelligence_repair_token(value: Any) -> str:
    token = repair_intelligence_token(value)
    for prefix in ("generic ",):
        if token.startswith(prefix):
            token = token[len(prefix):]
    replacements = {
        "replacement": "replace",
        "replacing": "replace",
        "evaluation": "evaluate",
        "inspection": "inspect",
    }
    return " ".join(replacements.get(part, part) for part in token.split())


def repair_intelligence_list(value: Any) -> list[Any]:
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        parsed = str(value)
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        return [parsed]
    text = str(parsed).strip()
    return [text] if text else []


def repair_intelligence_value(value: Any) -> Any:
    if not value:
        return None
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        text = str(value).strip()
        return text if text else None


def repair_intelligence_matches_vehicle(record: dict[str, Any], vehicle: dict[str, Any]) -> bool:
    record_year = repair_intelligence_token(record.get("year"))
    record_make = repair_intelligence_token(record.get("make"))
    record_model = repair_intelligence_token(record.get("model"))
    record_engine = repair_intelligence_engine_token(record.get("engine"))
    if not any((record_year, record_make, record_model, record_engine)):
        return True
    vehicle_year = repair_intelligence_token(vehicle.get("year"))
    vehicle_make = repair_intelligence_token(vehicle.get("make"))
    vehicle_model = repair_intelligence_token(vehicle.get("model"))
    vehicle_engine = repair_intelligence_engine_token(vehicle.get("engine"))
    return (
        (not record_year or record_year == vehicle_year)
        and (not record_make or record_make == vehicle_make)
        and (not record_model or record_model == vehicle_model)
        and (not record_engine or record_engine == vehicle_engine)
    )


def repair_intelligence_matches_repair(record: dict[str, Any], repair_name: Any) -> bool:
    record_repair = repair_intelligence_repair_token(record.get("repair_name"))
    target_repair = repair_intelligence_repair_token(repair_name)
    if not record_repair or not target_repair:
        return False
    return (
        record_repair == target_repair
        or record_repair in target_repair
        or target_repair in record_repair
    )


def hydrate_repair_intelligence_record(record: dict[str, Any]) -> dict[str, Any]:
    list_fields = (
        "repair_snapshot",
        "critical_checks",
        "known_failure_patterns",
        "inspection_opportunities",
        "critical_specs",
        "required_parts",
        "recommended_parts",
        "vendor_sources",
        "special_tools",
        "torque_specs",
    )
    hydrated = dict(record)
    for field in list_fields:
        hydrated[field] = repair_intelligence_list(hydrated.get(field))
    if not hydrated.get("critical_specs") and hydrated.get("torque_specs"):
        hydrated["critical_specs"] = hydrated["torque_specs"]
    hydrated["visual_layout"] = repair_intelligence_value(hydrated.get("visual_layout"))
    return hydrated


def load_repair_intelligence_seed_records() -> list[dict[str, Any]]:
    if not REPAIR_INTELLIGENCE_SEED_PATH.exists():
        return []
    try:
        records = json.loads(REPAIR_INTELLIGENCE_SEED_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(records, list):
        return []
    hydrated_records = []
    json_fields = (
        "repair_snapshot",
        "critical_checks",
        "known_failure_patterns",
        "inspection_opportunities",
        "critical_specs",
        "required_parts",
        "recommended_parts",
        "vendor_sources",
        "special_tools",
        "torque_specs",
        "visual_layout",
    )
    for record in records:
        if not isinstance(record, dict):
            continue
        normalized = dict(record)
        for field in json_fields:
            normalized[field] = repair_intelligence_json(normalized.get(field))
        hydrated_records.append(hydrate_repair_intelligence_record(normalized))
    return hydrated_records


def load_repair_intelligence_for_vehicle(
    conn: sqlite3.Connection,
    vehicle: dict[str, Any],
) -> list[dict[str, Any]]:
    ensure_repair_intelligence_schema(conn)
    records = []
    for row in conn.execute(
        """
        SELECT *
        FROM repair_intelligence
        ORDER BY
          CASE WHEN year = '' AND make = '' AND model = '' AND engine = '' THEN 1 ELSE 0 END,
          make ASC, model ASC, repair_name ASC, id ASC
        """
    ).fetchall():
        record = dict(row)
        if not repair_intelligence_matches_vehicle(record, vehicle):
            continue
        records.append(hydrate_repair_intelligence_record(record))
    if not records:
        for record in load_repair_intelligence_seed_records():
            if repair_intelligence_matches_vehicle(record, vehicle):
                records.append(record)
    return records


def load_repair_intelligence_for_repair(
    conn: sqlite3.Connection,
    vehicle: dict[str, Any],
    repair_name: Any,
) -> list[dict[str, Any]]:
    ensure_repair_intelligence_schema(conn)
    matches = []
    for row in conn.execute(
        """
        SELECT *
        FROM repair_intelligence
        ORDER BY
          CASE WHEN year = '' AND make = '' AND model = '' AND engine = '' THEN 1 ELSE 0 END,
          make ASC, model ASC, repair_name ASC, id ASC
        """
    ).fetchall():
        record = dict(row)
        if not repair_intelligence_matches_vehicle(record, vehicle):
            continue
        if not repair_intelligence_matches_repair(record, repair_name):
            continue
        matches.append(hydrate_repair_intelligence_record(record))
    if not matches:
        for record in load_repair_intelligence_seed_records():
            if not repair_intelligence_matches_vehicle(record, vehicle):
                continue
            if not repair_intelligence_matches_repair(record, repair_name):
                continue
            matches.append(record)
    return matches


def load_visual_references_for_vehicle(
    conn: sqlite3.Connection,
    vehicle: dict[str, Any],
) -> list[dict[str, Any]]:
    ensure_visual_reference_schema(conn)
    identifiers = visual_reference_vehicle_identifiers(vehicle)
    if not identifiers:
        return []
    normalized_identifiers = {normalize_visual_reference_token(identifier) for identifier in identifiers}
    records = [
        dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM visual_reference_records
            ORDER BY service_type ASC, title ASC, id ASC
            """
        ).fetchall()
        if normalize_visual_reference_token(row["vehicle_identifier"]) in normalized_identifiers
    ]
    for record in records:
        visual_reference_id = record["id"]
        record["images"] = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM visual_reference_images
                WHERE visual_reference_id = ?
                ORDER BY image_type ASC, id ASC
                """,
                (visual_reference_id,),
            ).fetchall()
        ]
        record["specs"] = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM visual_reference_specs
                WHERE visual_reference_id = ?
                ORDER BY id ASC
                """,
                (visual_reference_id,),
            ).fetchall()
        ]
        record["oem_parts"] = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM visual_reference_oem_parts
                WHERE visual_reference_id = ?
                ORDER BY id ASC
                """,
                (visual_reference_id,),
            ).fetchall()
        ]
        record["hotspots"] = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM visual_reference_hotspots
                WHERE visual_reference_id = ?
                ORDER BY sort_order ASC, id ASC
                """,
                (visual_reference_id,),
            ).fetchall()
        ]
    return records


def ensure_maintenance_records_schema(conn: sqlite3.Connection) -> None:
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
          updated_at TEXT NOT NULL
        )
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(maintenance_records)").fetchall()}
    if "due_mileage" not in columns:
        conn.execute("ALTER TABLE maintenance_records ADD COLUMN due_mileage INTEGER")
    if "due_date" not in columns:
        conn.execute("ALTER TABLE maintenance_records ADD COLUMN due_date TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_maintenance_records_vehicle_date "
        "ON maintenance_records (vehicle_id, date_performed)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_maintenance_records_vehicle_mileage_date "
        "ON maintenance_records (vehicle_id, mileage_performed, date_performed)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_maintenance_records_due_mileage ON maintenance_records (due_mileage)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_maintenance_records_due_date ON maintenance_records (due_date)")
    conn.commit()


def ensure_repair_records_schema(conn: sqlite3.Connection) -> None:
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
          labor_rate REAL,
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
    columns = {row[1] for row in conn.execute("PRAGMA table_info(repair_records)").fetchall()}
    if "track_as_maintenance" not in columns:
        conn.execute(
            "ALTER TABLE repair_records ADD COLUMN "
            "track_as_maintenance INTEGER NOT NULL DEFAULT 0"
        )
        maintenance_records = [
            dict(row)
            for row in conn.execute(
                """
                SELECT customer_id, vehicle_id, service_type
                FROM maintenance_records
                """
            ).fetchall()
        ]
        tracked_services = {
            (
                record["customer_id"],
                record["vehicle_id"],
                normalize_maintenance_service_type(record["service_type"]),
            )
            for record in maintenance_records
        }
        for repair in conn.execute(
            "SELECT id, customer_id, vehicle_id, repair_name FROM repair_records"
        ).fetchall():
            key = (
                repair["customer_id"],
                repair["vehicle_id"],
                normalize_maintenance_service_type(repair["repair_name"]),
            )
            if key in tracked_services:
                conn.execute(
                    "UPDATE repair_records SET track_as_maintenance = 1 WHERE id = ?",
                    (repair["id"],),
                )
    if "workflow_source_type" not in columns:
        conn.execute("ALTER TABLE repair_records ADD COLUMN workflow_source_type TEXT")
    if "workflow_source_id" not in columns:
        conn.execute("ALTER TABLE repair_records ADD COLUMN workflow_source_id INTEGER")
    if "parts_search_term" not in columns:
        conn.execute("ALTER TABLE repair_records ADD COLUMN parts_search_term TEXT")
    if "pricing_mode" not in columns:
        conn.execute("ALTER TABLE repair_records ADD COLUMN pricing_mode TEXT")
    if "flat_rate_price" not in columns:
        conn.execute("ALTER TABLE repair_records ADD COLUMN flat_rate_price REAL")
    if "approved_estimate_total" not in columns:
        conn.execute("ALTER TABLE repair_records ADD COLUMN approved_estimate_total REAL")
    if "labor_rate" not in columns:
        conn.execute("ALTER TABLE repair_records ADD COLUMN labor_rate REAL")
    if "status" not in columns:
        conn.execute("ALTER TABLE repair_records ADD COLUMN status TEXT NOT NULL DEFAULT 'Open'")
    if "completed_at" not in columns:
        conn.execute("ALTER TABLE repair_records ADD COLUMN completed_at TEXT")
    conn.execute(
        """
        UPDATE repair_records
        SET status = 'Open'
        WHERE status IS NULL OR TRIM(status) = ''
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_repair_records_customer_id ON repair_records (customer_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_repair_records_vehicle_id ON repair_records (vehicle_id)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_repair_records_vehicle_date_mileage "
        "ON repair_records (vehicle_id, repair_date, mileage)"
    )
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
    conn.commit()


def repair_labor_totals(repair: dict[str, Any]) -> dict[str, Any]:
    try:
        labor_hours = float(repair.get("labor_hours")) if repair.get("labor_hours") is not None else None
    except (TypeError, ValueError):
        labor_hours = None
    try:
        labor_rate = float(repair.get("labor_rate")) if repair.get("labor_rate") is not None else None
    except (TypeError, ValueError):
        labor_rate = None
    try:
        legacy_labor_total = float(repair.get("labor_cost") or 0)
    except (TypeError, ValueError):
        legacy_labor_total = 0.0
    pricing_mode = str(repair.get("pricing_mode") or "").strip().lower()
    try:
        flat_rate_price = float(repair.get("flat_rate_price") or 0)
    except (TypeError, ValueError):
        flat_rate_price = 0.0

    if pricing_mode == "flat":
        labor_total = round(max(0.0, flat_rate_price or legacy_labor_total), 2)
        return {
            "labor_hours": labor_hours,
            "labor_rate": None,
            "labor_total": labor_total,
            "labor_rate_is_legacy": True if labor_total else False,
        }

    if labor_hours is not None and labor_rate is not None:
        labor_total = round(max(0.0, labor_hours) * max(0.0, labor_rate), 2)
        return {
            "labor_hours": labor_hours,
            "labor_rate": labor_rate,
            "labor_total": labor_total,
            "labor_rate_is_legacy": False,
        }

    return {
        "labor_hours": labor_hours,
        "labor_rate": None,
        "labor_total": round(max(0.0, legacy_labor_total), 2),
        "labor_rate_is_legacy": True if legacy_labor_total else False,
    }


def repair_cost_totals(repair: dict[str, Any]) -> dict[str, Any]:
    labor = repair_labor_totals(repair)
    try:
        parts_total = float(repair.get("parts_cost") or 0)
    except (TypeError, ValueError):
        parts_total = 0.0
    try:
        tracked_parts_total = float(repair.get("tracked_parts_total") or 0)
    except (TypeError, ValueError):
        tracked_parts_total = 0.0
    labor_total = round(float(labor["labor_total"] or 0), 2)
    parts_total = round(max(0.0, parts_total) + max(0.0, tracked_parts_total), 2)
    return {
        **labor,
        "parts_total": parts_total,
        "grand_total": round(labor_total + parts_total, 2),
    }


def ensure_repair_job_parts_schema(conn: sqlite3.Connection) -> None:
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
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_repair_job_parts_repair_record_id "
        "ON repair_job_parts (repair_record_id)"
    )
    conn.commit()


def normalize_repair_job_part_status(raw_status: Any) -> str:
    value = re.sub(r"\s+", " ", str(raw_status or "").strip()).title()
    aliases = {
        "Customer Supplied": "Customer Supplied",
        "Customer-Supplied": "Customer Supplied",
        "Not Needed": "Not Needed",
        "Not-Needed": "Not Needed",
    }
    value = aliases.get(value, value)
    return value if value in REPAIR_JOB_PART_STATUS_OPTIONS else "Needed"


def repair_job_part_subtotal(qty: Any, unit_cost: Any) -> float:
    try:
        qty_value = float(qty if qty not in (None, "") else 1)
    except (TypeError, ValueError):
        qty_value = 1.0
    try:
        unit_cost_value = float(unit_cost if unit_cost not in (None, "") else 0)
    except (TypeError, ValueError):
        unit_cost_value = 0.0
    return round(max(0.0, qty_value) * max(0.0, unit_cost_value), 2)


def repair_job_part_display_record(part: dict[str, Any]) -> dict[str, Any]:
    record = dict(part)
    record["status"] = normalize_repair_job_part_status(record.get("status"))
    record["subtotal"] = repair_job_part_subtotal(record.get("qty"), record.get("unit_cost"))
    record["qty_display"] = format_quantity(record.get("qty"))
    record["included_in_tracked_total"] = record["status"] not in REPAIR_JOB_PART_EXCLUDED_TOTAL_STATUSES
    return record


def repair_job_parts_summary(parts: list[dict[str, Any]]) -> dict[str, Any]:
    total = round(
        sum(
            float(part.get("subtotal") or 0)
            for part in parts
            if normalize_repair_job_part_status(part.get("status")) not in REPAIR_JOB_PART_EXCLUDED_TOTAL_STATUSES
        ),
        2,
    )
    return {"parts": parts, "tracked_parts_total": total, "count": len(parts)}


def load_repair_job_parts(conn: sqlite3.Connection, repair_record_id: int) -> list[dict[str, Any]]:
    ensure_repair_job_parts_schema(conn)
    return [
        repair_job_part_display_record(dict(row))
        for row in conn.execute(
            """
            SELECT *
            FROM repair_job_parts
            WHERE repair_record_id = ?
            ORDER BY id ASC
            """,
            (repair_record_id,),
        ).fetchall()
    ]


def load_repair_job_parts_map(
    conn: sqlite3.Connection,
    repair_record_ids: list[int] | set[int],
) -> dict[int, dict[str, Any]]:
    ensure_repair_job_parts_schema(conn)
    repair_ids: set[int] = set()
    for value in repair_record_ids:
        try:
            repair_id = int(value or 0)
        except (TypeError, ValueError):
            continue
        if repair_id > 0:
            repair_ids.add(repair_id)
    ids = sorted(repair_ids)
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    grouped: dict[int, list[dict[str, Any]]] = {repair_id: [] for repair_id in ids}
    for row in conn.execute(
        f"""
        SELECT *
        FROM repair_job_parts
        WHERE repair_record_id IN ({placeholders})
        ORDER BY repair_record_id ASC, id ASC
        """,
        ids,
    ).fetchall():
        part = repair_job_part_display_record(dict(row))
        grouped[int(part["repair_record_id"])].append(part)
    return {repair_id: repair_job_parts_summary(parts) for repair_id, parts in grouped.items()}


def attach_repair_job_parts(
    records: list[dict[str, Any]],
    parts_map: dict[int, dict[str, Any]],
    *,
    id_key: str = "id",
) -> None:
    for record in records:
        repair_id = int(record.get(id_key) or record.get("linked_repair_record_id") or 0)
        summary = parts_map.get(repair_id) or repair_job_parts_summary([])
        record["tracked_parts"] = summary["parts"]
        record["tracked_parts_total"] = summary["tracked_parts_total"]
        record["tracked_parts_count"] = summary["count"]


def create_repair_job_part(
    conn: sqlite3.Connection,
    repair_record_id: int,
    form: dict[str, str],
    now: str,
) -> int:
    ensure_repair_job_parts_schema(conn)
    part_name = str(form.get("part_name") or "").strip()
    if not part_name:
        raise HTTPException(status_code=400, detail="Part name is required")
    qty = optional_float(form, "qty")
    if qty is None:
        qty = 1.0
    unit_cost = optional_float(form, "unit_cost")
    if unit_cost is None:
        unit_cost = 0.0
    status = normalize_repair_job_part_status(form.get("status"))
    subtotal = repair_job_part_subtotal(qty, unit_cost)
    cur = conn.execute(
        """
        INSERT INTO repair_job_parts (
          repair_record_id, part_name, qty, vendor, part_number,
          unit_cost, subtotal, status, notes, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            repair_record_id,
            part_name,
            qty,
            str(form.get("vendor") or "").strip(),
            str(form.get("part_number") or "").strip(),
            unit_cost,
            subtotal,
            status,
            str(form.get("notes") or "").strip(),
            now,
            now,
        ),
    )
    return int(cur.lastrowid)


def update_repair_job_part(
    conn: sqlite3.Connection,
    repair_record_id: int,
    part_id: int,
    form: dict[str, str],
    now: str,
) -> None:
    ensure_repair_job_parts_schema(conn)
    existing = conn.execute(
        "SELECT * FROM repair_job_parts WHERE id = ? AND repair_record_id = ?",
        (part_id, repair_record_id),
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Tracked part not found")
    current = dict(existing)
    part_name = str(form.get("part_name", current.get("part_name") or "") or "").strip()
    if not part_name:
        raise HTTPException(status_code=400, detail="Part name is required")
    qty = optional_float(form, "qty") if "qty" in form else current.get("qty")
    unit_cost = optional_float(form, "unit_cost") if "unit_cost" in form else current.get("unit_cost")
    status = normalize_repair_job_part_status(form.get("status", current.get("status")))
    subtotal = repair_job_part_subtotal(qty, unit_cost)
    conn.execute(
        """
        UPDATE repair_job_parts
        SET part_name = ?, qty = ?, vendor = ?, part_number = ?,
            unit_cost = ?, subtotal = ?, status = ?, notes = ?, updated_at = ?
        WHERE id = ? AND repair_record_id = ?
        """,
        (
            part_name,
            qty,
            str(form.get("vendor", current.get("vendor") or "") or "").strip(),
            str(form.get("part_number", current.get("part_number") or "") or "").strip(),
            unit_cost,
            subtotal,
            status,
            str(form.get("notes", current.get("notes") or "") or "").strip(),
            now,
            part_id,
            repair_record_id,
        ),
    )


def delete_repair_job_part(conn: sqlite3.Connection, repair_record_id: int, part_id: int) -> None:
    ensure_repair_job_parts_schema(conn)
    cur = conn.execute(
        "DELETE FROM repair_job_parts WHERE id = ? AND repair_record_id = ?",
        (part_id, repair_record_id),
    )
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Tracked part not found")


def ensure_invoices_schema(conn: sqlite3.Connection) -> None:
    ensure_repair_records_schema(conn)
    ensure_repair_job_parts_schema(conn)
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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS invoice_items (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          invoice_id INTEGER NOT NULL,
          repair_record_id INTEGER NOT NULL UNIQUE,
          created_at TEXT NOT NULL,
          FOREIGN KEY (invoice_id) REFERENCES invoices(id),
          FOREIGN KEY (repair_record_id) REFERENCES repair_records(id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_invoices_customer_id ON invoices (customer_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_invoices_vehicle_id ON invoices (vehicle_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_invoices_created_at ON invoices (created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_invoice_items_invoice_id ON invoice_items (invoice_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_invoice_items_repair_record_id ON invoice_items (repair_record_id)")
    for invoice in conn.execute("SELECT id, repair_record_id, created_at FROM invoices").fetchall():
        if not invoice["repair_record_id"]:
            continue
        existing = conn.execute(
            "SELECT id FROM invoice_items WHERE invoice_id = ? AND repair_record_id = ?",
            (invoice["id"], invoice["repair_record_id"]),
        ).fetchone()
        if existing:
            continue
        try:
            conn.execute(
                """
                INSERT INTO invoice_items (invoice_id, repair_record_id, created_at)
                VALUES (?, ?, ?)
                """,
                (invoice["id"], invoice["repair_record_id"], invoice["created_at"]),
            )
        except sqlite3.IntegrityError:
            pass
    conn.commit()


def invoice_number_for(invoice_id: int, created_at: str) -> str:
    return f"TM-INV-{int(invoice_id):04d}"


def next_invoice_number(conn: sqlite3.Connection, invoice_id: int) -> str:
    highest = 0
    for row in conn.execute("SELECT invoice_number FROM invoices").fetchall():
        number = str(row["invoice_number"] or "")
        match = re.fullmatch(r"TM-INV-(\d+)", number)
        if not match:
            match = re.fullmatch(r"TM-(\d+)", number)
        if match:
            highest = max(highest, int(match.group(1)))
    next_number = highest + 1 if highest else int(invoice_id)
    return f"TM-INV-{next_number:04d}"


def load_invoice_for_repair(
    conn: sqlite3.Connection,
    repair_record_id: int,
) -> dict[str, Any] | None:
    ensure_invoices_schema(conn)
    invoice = row_to_dict(
        conn.execute(
            """
            SELECT i.*
            FROM invoice_items ii
            JOIN invoices i ON i.id = ii.invoice_id
            WHERE ii.repair_record_id = ?
            LIMIT 1
            """,
            (repair_record_id,),
        ).fetchone()
    )
    if invoice:
        return invoice
    return row_to_dict(
        conn.execute(
            """
            SELECT *
            FROM invoices
            WHERE repair_record_id = ?
            """,
            (repair_record_id,),
        ).fetchone()
    )


def clean_invoice_repair_notes(
    notes: Any,
    *,
    service_title: str = "",
    source_recommendation: str = "",
) -> str:
    text = str(notes or "").replace("\r", "\n").strip()
    if not text:
        return ""
    internal_prefixes = (
        "source:",
        "created from estimator",
        "created from approved finding",
        "recommended repair:",
    )
    cleaned_lines: list[str] = []
    for raw_line in re.split(r"[\n;]+", text):
        line = re.sub(r"\s+", " ", raw_line).strip(" -")
        if not line:
            continue
        lowered = line.lower()
        if lowered.startswith(internal_prefixes):
            continue
        line = re.sub(r"\bSource:\s*(Finding|Estimate|Manual Repair|Approved finding)\b", "", line, flags=re.I)
        line = re.sub(r"\bRecommended Repair:\s*", "", line, flags=re.I)
        line = re.sub(r"\s+", " ", line).strip(" -")
        if not line:
            continue
        if service_title and line.lower() == service_title.lower():
            continue
        if source_recommendation and line.lower() == source_recommendation.lower():
            continue
        if line not in cleaned_lines:
            cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def invoice_item_display_record(record: dict[str, Any]) -> dict[str, Any]:
    item = dict(record)
    totals = repair_cost_totals(item)
    item["service_title"] = clean_service_quantity_title(item.get("repair_name") or "Repair")
    item["labor_total"] = totals["labor_total"]
    item["parts_total"] = totals["parts_total"]
    item["grand_total"] = totals["grand_total"]
    item["labor_rate"] = totals["labor_rate"]
    item["labor_rate_is_legacy"] = totals["labor_rate_is_legacy"]
    item["completion_notes"] = str(item.get("completion_notes") or "").strip()
    item["final_inspection_passed"] = 1 if item.get("final_inspection_passed") else 0
    item["final_inspection_status"] = "Passed" if item["final_inspection_passed"] else "Not marked passed"
    item["final_inspection_notes"] = str(item.get("final_inspection_notes") or "").strip()
    source_finding = str(item.get("source_finding") or "").strip()
    source_recommendation = str(item.get("source_recommendation") or "").strip()
    item["repair_notes"] = clean_invoice_repair_notes(
        item.get("repair_notes") or item.get("notes") or item.get("completion_notes") or "",
        service_title=item["service_title"],
        source_recommendation=source_recommendation,
    )
    for internal_key in ("notes", "source_finding", "source_recommendation"):
        item.pop(internal_key, None)
    return item


def load_invoice_item_records(
    conn: sqlite3.Connection,
    invoice_id: int,
) -> list[dict[str, Any]]:
    ensure_invoices_schema(conn)
    ensure_repair_completion_schema(conn)
    ensure_findings_records_schema(conn)
    items = [
        invoice_item_display_record(dict(row))
        for row in conn.execute(
            """
            SELECT
              ii.id AS invoice_item_id,
              ii.invoice_id,
              rr.id AS repair_record_id,
              rr.repair_name,
              rr.repair_date,
              rr.labor_hours,
              rr.labor_rate,
              rr.pricing_mode,
              rr.flat_rate_price,
              rr.labor_cost,
              rr.parts_cost,
              rr.total_cost,
              rr.mileage AS repair_mileage,
              rr.notes,
              rr.status,
              rr.completed_at,
              rr.workflow_source_type,
              rr.workflow_source_id,
              rc.completion_notes,
              rc.final_inspection_passed,
              rc.final_inspection_notes,
              fr.finding AS source_finding,
              fr.recommendation AS source_recommendation
            FROM invoice_items ii
            JOIN repair_records rr ON rr.id = ii.repair_record_id
            LEFT JOIN repair_completions rc ON rc.repair_record_id = rr.id
            LEFT JOIN findings_records fr
              ON rr.workflow_source_type = 'finding'
             AND fr.id = rr.workflow_source_id
            WHERE ii.invoice_id = ?
            ORDER BY ii.id ASC
            """,
            (invoice_id,),
        ).fetchall()
    ]
    parts_map = load_repair_job_parts_map(
        conn,
        {int(item.get("repair_record_id") or 0) for item in items if item.get("repair_record_id")},
    )
    attach_repair_job_parts(items, parts_map, id_key="repair_record_id")
    for item in items:
        totals = repair_cost_totals(item)
        item["parts_total"] = totals["parts_total"]
        item["grand_total"] = totals["grand_total"]
    return items


def load_invoice_record(
    conn: sqlite3.Connection,
    customer_id: int,
    vehicle_id: int,
    invoice_id: int,
) -> dict[str, Any]:
    ensure_invoices_schema(conn)
    invoice = row_to_dict(
        conn.execute(
            """
            SELECT i.*
            FROM invoices i
            WHERE i.id = ?
              AND i.customer_id = ?
              AND i.vehicle_id = ?
            """,
            (invoice_id, customer_id, vehicle_id),
        ).fetchone()
    )
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    invoice["items"] = load_invoice_item_records(conn, invoice_id)
    record = invoice_display_record(invoice)
    estimate_summary = invoice_estimate_summary(conn, invoice_id, final_total=record.get("grand_total"))
    record.update(estimate_summary)
    return record


def invoice_display_record(invoice: dict[str, Any]) -> dict[str, Any]:
    record = dict(invoice)
    items = list(record.get("items") or [])
    record["items"] = items
    record["service_count"] = len(items)
    record["labor_total"] = round(sum(float(item.get("labor_total") or 0) for item in items), 2)
    record["parts_total"] = round(sum(float(item.get("parts_total") or 0) for item in items), 2)
    record["parts_cost"] = record["parts_total"]
    record["grand_total"] = round(sum(float(item.get("grand_total") or 0) for item in items), 2)
    primary = items[0] if items else {}
    record["repair_name"] = primary.get("service_title") or "Repair"
    record["repair_mileage"] = primary.get("repair_mileage")
    record["completed_at"] = primary.get("completed_at")
    record["labor_hours"] = primary.get("labor_hours")
    record["labor_rate"] = primary.get("labor_rate")
    record["labor_rate_is_legacy"] = primary.get("labor_rate_is_legacy")
    record["repair_notes"] = primary.get("repair_notes") or ""
    record["completion_notes"] = primary.get("completion_notes") or ""
    record["final_inspection_notes"] = primary.get("final_inspection_notes") or ""
    completed_dates = sorted(
        {
            str(item.get("completed_at") or "").strip()[:10]
            for item in items
            if str(item.get("completed_at") or "").strip()
        }
    )
    inspections = {item.get("final_inspection_status") or "Not marked passed" for item in items}
    record["completion_summary_date"] = completed_dates[0] if len(completed_dates) == 1 else ""
    record["completion_summary_status"] = (
        inspections.pop() if len(inspections) == 1 else ("Mixed" if inspections else "Not marked passed")
    )
    return record


def invoice_estimate_summary(conn: sqlite3.Connection, invoice_id: int, *, final_total: Any = None) -> dict[str, Any]:
    ensure_repair_estimate_documents_schema(conn)
    repair_rows = conn.execute(
        """
        SELECT rr.approved_estimate_total
        FROM invoice_items ii
        JOIN repair_records rr ON rr.id = ii.repair_record_id
        WHERE ii.invoice_id = ?
          AND rr.approved_estimate_total IS NOT NULL
        """,
        (invoice_id,),
    ).fetchall()
    rows = repair_rows or conn.execute(
        """
        SELECT estimate_total
        FROM repair_estimate_documents
        WHERE invoice_id = ?
          AND estimate_total IS NOT NULL
        """,
        (invoice_id,),
    ).fetchall()
    if not rows:
        return {
            "approved_estimate_total": None,
            "estimate_final_difference": None,
            "estimate_difference_label": "",
        }
    approved_total = round(
        sum(float((row["approved_estimate_total"] if "approved_estimate_total" in row.keys() else row["estimate_total"]) or 0) for row in rows),
        2,
    )
    try:
        difference = round(float(final_total or 0) - approved_total, 2)
    except (TypeError, ValueError):
        difference = 0.0
    if difference > 0:
        label = "Additional Approved Amount"
    elif difference < 0:
        label = "Final Invoice Credit"
    else:
        label = "Difference"
    return {
        "approved_estimate_total": approved_total,
        "estimate_final_difference": difference,
        "estimate_difference_label": label,
    }


def repair_invoice_warnings(repair: dict[str, Any]) -> list[str]:
    if (repair.get("status") or "") != "Completed":
        return ["Final invoice can only include completed repair work."]
    totals = repair_cost_totals(repair)
    if totals["grand_total"] <= 0:
        return [
            "Invoice cannot be generated until this completed repair has labor or parts totals recorded."
        ]
    return []


def load_repair_invoice_map(
    conn: sqlite3.Connection,
    customer_id: int,
    vehicle_id: int,
) -> dict[int, dict[str, Any]]:
    ensure_invoices_schema(conn)
    rows = conn.execute(
        """
        SELECT
          ii.repair_record_id,
          i.id AS invoice_id,
          i.invoice_number,
          i.created_at
        FROM invoice_items ii
        JOIN invoices i ON i.id = ii.invoice_id
        WHERE i.customer_id = ?
          AND i.vehicle_id = ?
        """,
        (customer_id, vehicle_id),
    ).fetchall()
    invoice_map = {
        int(row["repair_record_id"]): {
            "invoice_id": row["invoice_id"],
            "invoice_number": row["invoice_number"],
            "created_at": row["created_at"],
        }
        for row in rows
    }
    for row in conn.execute(
        """
        SELECT id, repair_record_id, invoice_number, created_at
        FROM invoices
        WHERE customer_id = ?
          AND vehicle_id = ?
        """,
        (customer_id, vehicle_id),
    ).fetchall():
        repair_id = int(row["repair_record_id"] or 0)
        if repair_id and repair_id not in invoice_map:
            invoice_map[repair_id] = {
                "invoice_id": row["id"],
                "invoice_number": row["invoice_number"],
                "created_at": row["created_at"],
            }
    return invoice_map


def annotate_repairs_with_invoice_status(
    repair_records: list[dict[str, Any]],
    invoice_map: dict[int, dict[str, Any]],
) -> None:
    for repair in repair_records:
        repair_id = int(repair.get("id") or 0)
        invoice = invoice_map.get(repair_id)
        repair["invoice"] = invoice
        repair["invoice_id"] = invoice.get("invoice_id") if invoice else None
        repair["invoice_number"] = invoice.get("invoice_number") if invoice else ""
        repair["invoice_url"] = (
            f"/pro/customers/{repair['customer_id']}/vehicles/{repair['vehicle_id']}/invoices/{invoice['invoice_id']}"
            if invoice else ""
        )
        repair["is_invoiced"] = bool(invoice)


def invoice_builder_status_group(repair: dict[str, Any]) -> str:
    if repair.get("is_invoiced"):
        return "already_invoiced"
    if (repair.get("status") or "") == "Completed":
        return "ready"
    return "approved"


def load_invoice_builder_jobs(
    conn: sqlite3.Connection,
    customer_id: int,
    vehicle_id: int,
) -> dict[str, list[dict[str, Any]]]:
    ensure_repair_records_schema(conn)
    invoice_map = load_repair_invoice_map(conn, customer_id, vehicle_id)
    jobs: list[dict[str, Any]] = []
    for row in conn.execute(
        """
        SELECT *
        FROM repair_records
        WHERE customer_id = ?
          AND vehicle_id = ?
          AND COALESCE(status, '') NOT IN ('Declined', 'Deleted', 'Denied')
        ORDER BY
          CASE status WHEN 'Completed' THEN 0 WHEN 'Open' THEN 1 ELSE 2 END,
          repair_date DESC,
          id DESC
        """,
        (customer_id, vehicle_id),
    ).fetchall():
        repair = dict(row)
        totals = repair_cost_totals(repair)
        repair["labor_total"] = totals["labor_total"]
        repair["parts_total"] = totals["parts_total"]
        repair["grand_total"] = totals["grand_total"]
        repair["labor_rate"] = totals["labor_rate"]
        repair["labor_rate_is_legacy"] = totals["labor_rate_is_legacy"]
        repair["source_label"] = repair_workspace_source_label(repair)
        repair["status_label"] = "Ready for Invoice" if repair.get("status") == "Completed" else repair_workspace_status_label("ready")
        jobs.append(repair)
    annotate_repairs_with_invoice_status(jobs, invoice_map)
    grouped = {"ready": [], "approved": [], "already_invoiced": []}
    for job in jobs:
        grouped[invoice_builder_status_group(job)].append(job)
    return grouped


def load_vehicle_invoice_records(
    conn: sqlite3.Connection,
    customer_id: int,
    vehicle_id: int,
) -> list[dict[str, Any]]:
    ensure_invoices_schema(conn)
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT
              i.*,
              COALESCE(first_rr.repair_name, rr.repair_name, 'Invoice') AS repair_name,
              GROUP_CONCAT(ii.repair_record_id) AS repair_record_ids,
              COUNT(ii.id) AS service_count
            FROM invoices i
            LEFT JOIN invoice_items ii ON ii.invoice_id = i.id
            LEFT JOIN repair_records first_rr ON first_rr.id = (
              SELECT repair_record_id
              FROM invoice_items
              WHERE invoice_id = i.id
              ORDER BY id ASC
              LIMIT 1
            )
            LEFT JOIN repair_records rr ON rr.id = i.repair_record_id
            WHERE i.customer_id = ?
              AND i.vehicle_id = ?
            GROUP BY i.id
            ORDER BY i.created_at DESC, i.id DESC
            """,
            (customer_id, vehicle_id),
        ).fetchall()
    ]


def ensure_repair_estimate_documents_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS repair_estimate_documents (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          customer_id INTEGER NOT NULL,
          vehicle_id INTEGER NOT NULL,
          finding_id INTEGER,
          estimate_date TEXT NOT NULL,
          customer_name TEXT,
          vehicle_label TEXT,
          related_title TEXT,
          estimate_total REAL,
          approval_status TEXT,
          pdf_path TEXT NOT NULL,
          invoice_id INTEGER,
          payload_json TEXT,
          created_at TEXT NOT NULL
        )
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(repair_estimate_documents)").fetchall()}
    for column_name, column_type in {
        "finding_id": "INTEGER",
        "estimate_date": "TEXT",
        "customer_name": "TEXT",
        "vehicle_label": "TEXT",
        "related_title": "TEXT",
        "estimate_total": "REAL",
        "approval_status": "TEXT",
        "invoice_id": "INTEGER",
        "payload_json": "TEXT",
    }.items():
        if column_name not in columns:
            conn.execute(f"ALTER TABLE repair_estimate_documents ADD COLUMN {column_name} {column_type}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_repair_estimate_documents_customer_vehicle ON repair_estimate_documents (customer_id, vehicle_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_repair_estimate_documents_finding_id ON repair_estimate_documents (finding_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_repair_estimate_documents_invoice_id ON repair_estimate_documents (invoice_id)")
    conn.commit()


def optional_int_value(value: Any) -> int | None:
    raw = "" if value is None else str(value).replace(",", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def record_estimate_pdf_document(
    *,
    pdf_bytes: bytes,
    customer_id: Any,
    vehicle_id: Any,
    finding_id: Any = None,
    estimate_date: str = "",
    customer_name: str = "",
    vehicle_label: str = "",
    related_title: str = "",
    estimate_total: Any = None,
    approval_status: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    parsed_customer_id = optional_int_value(customer_id)
    parsed_vehicle_id = optional_int_value(vehicle_id)
    if not parsed_customer_id or not parsed_vehicle_id or not pdf_bytes:
        return None

    created_at = datetime.now(timezone.utc).isoformat()
    estimate_date = str(estimate_date or "").strip() or local_today().isoformat()
    try:
        parsed_total = float(estimate_total) if estimate_total is not None and str(estimate_total).strip() != "" else None
    except (TypeError, ValueError):
        parsed_total = None

    ESTIMATE_PDF_DIR.mkdir(parents=True, exist_ok=True)
    file_name = f"repair-estimate-{uuid4().hex}.pdf"
    pdf_path = ESTIMATE_PDF_DIR / file_name
    pdf_path.write_bytes(pdf_bytes)

    conn = crm_db_conn()
    try:
        ensure_repair_estimate_documents_schema(conn)
        exists = conn.execute(
            """
            SELECT 1
            FROM customer_vehicles
            WHERE id = ? AND customer_id = ?
            """,
            (parsed_vehicle_id, parsed_customer_id),
        ).fetchone()
        if not exists:
            return None
        cur = conn.execute(
            """
            INSERT INTO repair_estimate_documents (
              customer_id, vehicle_id, finding_id, estimate_date,
              customer_name, vehicle_label, related_title, estimate_total,
              approval_status, pdf_path, invoice_id, payload_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                parsed_customer_id,
                parsed_vehicle_id,
                optional_int_value(finding_id),
                estimate_date,
                str(customer_name or "").strip(),
                str(vehicle_label or "").strip(),
                str(related_title or "").strip(),
                parsed_total,
                str(approval_status or "").strip(),
                str(pdf_path.resolve()),
                None,
                json.dumps(payload or {}, ensure_ascii=True),
                created_at,
            ),
        )
        conn.commit()
        return {"id": int(cur.lastrowid), "pdf_path": str(pdf_path.resolve())}
    finally:
        conn.close()


def load_vehicle_estimate_documents(
    conn: sqlite3.Connection,
    customer_id: int,
    vehicle_id: int,
) -> list[dict[str, Any]]:
    ensure_repair_estimate_documents_schema(conn)
    ensure_invoices_schema(conn)
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT
              red.*,
              i.invoice_number
            FROM repair_estimate_documents red
            LEFT JOIN invoices i ON i.id = red.invoice_id
            WHERE red.customer_id = ?
              AND red.vehicle_id = ?
            ORDER BY red.estimate_date DESC, red.created_at DESC, red.id DESC
            """,
            (customer_id, vehicle_id),
        ).fetchall()
    ]


def estimate_document_url(customer_id: int, vehicle_id: int, estimate_id: Any) -> str:
    return f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/estimates/{estimate_id}/pdf" if estimate_id else ""


def estimate_document_payload(record: dict[str, Any]) -> dict[str, Any]:
    raw_payload = record.get("payload_json") or "{}"
    try:
        payload = json.loads(raw_payload)
    except (TypeError, ValueError):
        payload = {}
    return payload if isinstance(payload, dict) else {}


def estimate_document_edit_url(customer_id: int, vehicle_id: int, record: dict[str, Any]) -> str:
    payload = estimate_document_payload(record)
    params: dict[str, Any] = {
        "source": payload.get("source") or ("finding" if record.get("finding_id") else "estimator"),
        "customer_id": customer_id,
        "vehicle_id": vehicle_id,
        "estimate_id": record.get("id"),
    }
    if record.get("finding_id"):
        params["finding_id"] = record.get("finding_id")
    for key, param_name in (
        ("problem_found", "problem_found"),
        ("recommended_repair", "recommended_repair"),
        ("notes", "notes"),
    ):
        value = str(payload.get(key) or "").strip()
        if value:
            params[param_name] = value
    line_items = payload.get("line_items") if isinstance(payload.get("line_items"), list) else []
    if line_items:
        first = line_items[0] if isinstance(line_items[0], dict) else {}
        if first.get("service_code"):
            params["service"] = first.get("service_code")
        if first.get("service_text"):
            params["service_text"] = first.get("service_text")
        if first.get("labor_hours") is not None:
            params["labor_hours"] = first.get("labor_hours")
        if first.get("labor_rate") is not None:
            params["labor_rate"] = first.get("labor_rate")
        if first.get("parts_total") is not None:
            params["parts_total"] = first.get("parts_total")
        if first.get("pricing_mode"):
            params["pricing_mode"] = first.get("pricing_mode")
        if first.get("quantity") is not None:
            params["quantity"] = first.get("quantity")
        if len(line_items) > 1:
            params["estimate_payload"] = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    return f"/estimator?{urlencode(params)}"


def latest_estimate_documents_by_finding_id(
    estimate_document_records: list[dict[str, Any]] | None,
) -> dict[int, dict[str, Any]]:
    by_finding_id: dict[int, dict[str, Any]] = {}
    for record in estimate_document_records or []:
        finding_id = optional_int_value(record.get("finding_id"))
        if not finding_id:
            continue
        existing = by_finding_id.get(finding_id)
        if not existing:
            by_finding_id[finding_id] = record
            continue
        existing_sort = (
            parse_date_value(existing.get("estimate_date")) or date.min,
            parse_datetime_value(existing.get("created_at")) or datetime.min,
            int(existing.get("id") or 0),
        )
        record_sort = (
            parse_date_value(record.get("estimate_date")) or date.min,
            parse_datetime_value(record.get("created_at")) or datetime.min,
            int(record.get("id") or 0),
        )
        if record_sort > existing_sort:
            by_finding_id[finding_id] = record
    return by_finding_id


def attach_estimate_documents_to_findings(
    findings_records: list[dict[str, Any]],
    estimate_document_records: list[dict[str, Any]] | None,
    *,
    customer_id: int,
    vehicle_id: int,
) -> None:
    by_finding_id = latest_estimate_documents_by_finding_id(estimate_document_records)
    for record in findings_records:
        finding_id = optional_int_value(record.get("id"))
        estimate_doc = by_finding_id.get(finding_id or 0)
        if not estimate_doc:
            record["estimate_document_id"] = None
            record["estimate_document_url"] = ""
            record["estimate_document_status"] = ""
            continue
        record["estimate_document_id"] = estimate_doc.get("id")
        record["estimate_document_url"] = estimate_document_url(customer_id, vehicle_id, estimate_doc.get("id"))
        record["estimate_document_status"] = estimate_doc.get("approval_status") or ""
        record["estimate_total"] = estimate_doc.get("estimate_total")


def link_estimate_documents_for_invoice(
    conn: sqlite3.Connection,
    *,
    invoice_id: int,
    customer_id: int,
    vehicle_id: int,
    repairs: list[dict[str, Any]],
) -> None:
    ensure_repair_estimate_documents_schema(conn)
    finding_ids = [
        int(repair.get("workflow_source_id") or 0)
        for repair in repairs
        if (repair.get("workflow_source_type") or "") == "finding" and repair.get("workflow_source_id")
    ]
    for finding_id in finding_ids:
        conn.execute(
            """
            UPDATE repair_estimate_documents
            SET invoice_id = ?
            WHERE customer_id = ?
              AND vehicle_id = ?
              AND finding_id = ?
              AND invoice_id IS NULL
            """,
            (invoice_id, customer_id, vehicle_id, finding_id),
        )


def create_invoice_for_repair(
    conn: sqlite3.Connection,
    *,
    repair: dict[str, Any],
    customer_id: int,
    vehicle_id: int,
    now: str,
) -> dict[str, Any]:
    return create_invoice_for_repairs(
        conn,
        repairs=[repair],
        customer_id=customer_id,
        vehicle_id=vehicle_id,
        now=now,
    )


def create_invoice_for_repairs(
    conn: sqlite3.Connection,
    *,
    repairs: list[dict[str, Any]],
    customer_id: int,
    vehicle_id: int,
    now: str,
) -> dict[str, Any]:
    ensure_invoices_schema(conn)
    selected_repairs = [repair for repair in repairs if repair.get("id")]
    if not selected_repairs:
        raise HTTPException(status_code=400, detail="Select at least one repair job")

    for repair in selected_repairs:
        if int(repair.get("customer_id") or customer_id) != customer_id or int(repair.get("vehicle_id") or vehicle_id) != vehicle_id:
            raise HTTPException(status_code=400, detail="Selected repair does not match this customer vehicle")
        existing = load_invoice_for_repair(conn, int(repair["id"]))
        if existing:
            raise HTTPException(status_code=400, detail="One or more selected repair jobs are already invoiced")
        warnings = repair_invoice_warnings(repair)
        if warnings:
            raise HTTPException(status_code=400, detail=warnings[0])

    totals = [repair_cost_totals(repair) for repair in selected_repairs]
    labor_total = round(sum(float(total["labor_total"] or 0) for total in totals), 2)
    parts_total = round(sum(float(total["parts_total"] or 0) for total in totals), 2)
    grand_total = round(sum(float(total["grand_total"] or 0) for total in totals), 2)
    primary_repair = selected_repairs[0]
    cur = conn.execute(
        """
        INSERT INTO invoices (
          invoice_number, repair_record_id, customer_id, vehicle_id,
          labor_total, parts_total, grand_total, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"PENDING-{uuid4().hex[:12]}",
            primary_repair["id"],
            customer_id,
            vehicle_id,
            labor_total,
            parts_total,
            grand_total,
            now,
        ),
    )
    invoice_id = int(cur.lastrowid)
    for repair in selected_repairs:
        conn.execute(
            """
            INSERT INTO invoice_items (invoice_id, repair_record_id, created_at)
            VALUES (?, ?, ?)
            """,
            (invoice_id, repair["id"], now),
        )
    invoice_number = next_invoice_number(conn, invoice_id)
    conn.execute(
        "UPDATE invoices SET invoice_number = ? WHERE id = ?",
        (invoice_number, invoice_id),
    )
    link_estimate_documents_for_invoice(
        conn,
        invoice_id=invoice_id,
        customer_id=customer_id,
        vehicle_id=vehicle_id,
        repairs=selected_repairs,
    )
    return load_invoice_record(conn, customer_id, vehicle_id, invoice_id)


def recalculate_invoice_from_repair(
    conn: sqlite3.Connection,
    *,
    invoice_id: int,
    customer_id: int,
    vehicle_id: int,
) -> dict[str, Any]:
    invoice = load_invoice_record(conn, customer_id, vehicle_id, invoice_id)
    repairs = [
        load_repair_record(conn, customer_id, vehicle_id, int(item["repair_record_id"]))
        for item in invoice.get("items", [])
    ]
    if not repairs:
        repairs = [load_repair_record(conn, customer_id, vehicle_id, int(invoice["repair_record_id"]))]
    totals = [repair_cost_totals(repair) for repair in repairs]
    conn.execute(
        """
        UPDATE invoices
        SET labor_total = ?,
            parts_total = ?,
            grand_total = ?
        WHERE id = ?
          AND repair_record_id = ?
          AND customer_id = ?
          AND vehicle_id = ?
        """,
        (
            round(sum(float(total["labor_total"] or 0) for total in totals), 2),
            round(sum(float(total["parts_total"] or 0) for total in totals), 2),
            round(sum(float(total["grand_total"] or 0) for total in totals), 2),
            invoice_id,
            invoice["repair_record_id"],
            customer_id,
            vehicle_id,
        ),
    )
    return load_invoice_record(conn, customer_id, vehicle_id, invoice_id)


def pdf_lines(text: Any, max_chars: int = 92) -> list[str]:
    words = str(text or "").replace("\r", "").split()
    if not words:
        return [""]
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > max_chars and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def invoice_filename(invoice: dict[str, Any]) -> str:
    number = re.sub(r"[^A-Za-z0-9_-]+", "-", str(invoice.get("invoice_number") or "invoice")).strip("-")
    return f"{number or 'invoice'}.pdf"


INVOICE_PDF_DEFAULT_OPTIONS = {
    "show_completion_date": True,
    "show_labor_hours": False,
    "show_labor_rate": False,
    "show_labor_total": True,
    "show_parts_total": True,
    "show_repair_notes": True,
    "show_final_inspection_notes": False,
}


def invoice_pdf_options_from_query(query_params: Any) -> dict[str, bool]:
    options = dict(INVOICE_PDF_DEFAULT_OPTIONS)
    if not query_params:
        return options
    for key in options:
        if key in query_params:
            options[key] = str(query_params.get(key) or "").lower() in {"1", "true", "on", "yes"}
        else:
            options[key] = False
    return options


def build_invoice_pdf_bytes(
    *,
    invoice: dict[str, Any],
    customer: dict[str, Any],
    vehicle: dict[str, Any],
    shop_name: str,
    display_options: dict[str, bool] | None = None,
) -> bytes:
    options = {**INVOICE_PDF_DEFAULT_OPTIONS, **(display_options or {})}
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter, pageCompression=0)
    w, h = letter
    left = 54
    right = w - 54
    y = h - 54

    c.setFont("Helvetica-Bold", 18)
    c.drawString(left, y, shop_name or "TorqueMech Pro")
    c.setFont("Helvetica-Bold", 24)
    c.drawRightString(right, y, "INVOICE")
    y -= 28
    c.setFont("Helvetica", 10)
    c.drawString(left, y, f"Invoice Number: {invoice.get('invoice_number') or '-'}")
    c.drawRightString(right, y, f"Created: {format_pro_date(invoice.get('created_at')) or '-'}")
    y -= 26
    c.line(left, y, right, y)
    y -= 24

    def section(title: str) -> None:
        nonlocal y
        if y < 120:
            c.showPage()
            y = h - 54
        c.setFont("Helvetica-Bold", 12)
        c.drawString(left, y, title)
        y -= 16

    def row(label: str, value: Any) -> None:
        nonlocal y
        if y < 80:
            c.showPage()
            y = h - 54
        c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(left, y, label)
        c.setFont("Helvetica", 10)
        c.drawString(left + 120, y, str(value or "-"))
        y -= 15

    def service_row(label: str, value: Any, *, emphasis: bool = False) -> None:
        nonlocal y
        if y < 80:
            c.showPage()
            y = h - 54
        c.setFillColorRGB(0.38, 0.45, 0.55)
        c.setFont("Helvetica-Bold" if emphasis else "Helvetica", 9)
        c.drawString(left + 18, y, label)
        c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica-Bold" if emphasis else "Helvetica", 10)
        display_value = value if isinstance(value, str) else str(value or "-")
        c.drawRightString(right, y, display_value)
        y -= 14 if emphasis else 13

    section("Customer")
    row("Name", customer_name(customer))
    row("Phone", format_phone(customer.get("phone")))
    row("Email", customer.get("email") or "-")
    y -= 8

    section("Vehicle")
    vehicle_title = " ".join(str(vehicle.get(key) or "").strip() for key in ("year", "make", "model")).strip()
    row("Vehicle", vehicle_title or "Vehicle")
    row("VIN", vehicle.get("vin") or "-")
    row("Mileage", f"{format_mileage(invoice.get('repair_mileage') or vehicle.get('mileage'))} miles" if (invoice.get("repair_mileage") or vehicle.get("mileage")) is not None else "-")
    y -= 8

    def notes_block(label: str, value: Any, max_lines: int = 8) -> None:
        nonlocal y
        if y < 100:
            c.showPage()
            y = h - 54
        c.setFont("Helvetica-Bold", 9)
        c.drawString(left, y, label)
        c.setFont("Helvetica", 10)
        note_y = y
        for line in pdf_lines(value or "-", 76)[:max_lines]:
            c.drawString(left + 120, note_y, line)
            note_y -= 13
        y = note_y - 10

    section("Completed Services")
    for index, item in enumerate(invoice.get("items") or [], start=1):
        if y < 150:
            c.showPage()
            y = h - 54
        if index > 1:
            c.setStrokeColorRGB(0.84, 0.87, 0.91)
            c.line(left, y, right, y)
            c.setStrokeColorRGB(0, 0, 0)
            y -= 14
        c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica-Bold", 11)
        title = f"{index}. {item.get('service_title') or 'Repair'}"
        title_lines = pdf_lines(title, 66)
        for line in title_lines:
            c.drawString(left, y, line)
            y -= 13
        y -= 5
        if options["show_labor_hours"]:
            service_row("Labor Hours", item.get("labor_hours") if item.get("labor_hours") is not None else "-")
        if options["show_labor_rate"]:
            service_row("Labor Rate", f"{format_currency(item.get('labor_rate'))}/hr" if item.get("labor_rate") is not None else "-")
        if options["show_labor_total"]:
            service_row("Flat Rate / Labor" if item.get("pricing_mode") == "flat" else "Labor", format_currency(item.get("labor_total")) or "$0.00")
        if options["show_parts_total"]:
            service_row("Parts", format_currency(item.get("parts_total")) or "$0.00")
        service_row("Line Total", format_currency(item.get("grand_total")) or "$0.00", emphasis=True)
        tracked_parts = item.get("tracked_parts") or []
        if tracked_parts:
            y -= 2
            c.setFont("Helvetica-Bold", 9)
            c.drawString(left + 18, y, "Tracked Parts")
            y -= 13
            c.setFont("Helvetica", 9)
            for part in tracked_parts:
                if y < 80:
                    c.showPage()
                    y = h - 54
                    c.setFont("Helvetica", 9)
                qty = part.get("qty_display") or part.get("qty") or 1
                subtotal = format_currency(part.get("subtotal")) or "$0.00"
                status = part.get("status") or "Needed"
                vendor = str(part.get("vendor") or "").strip()
                part_label = f"{qty} x {part.get('part_name') or 'Part'}"
                if vendor:
                    part_label = f"{part_label} from {vendor}"
                for line in pdf_lines(f"{part_label} | {status} | {subtotal}", 72)[:2]:
                    c.drawString(left + 30, y, line)
                    y -= 12
        y -= 3
        if options["show_repair_notes"] and item.get("repair_notes"):
            notes_block("Notes", item.get("repair_notes"))
        if options["show_final_inspection_notes"]:
            notes_block("Final Inspection Comments", item.get("final_inspection_notes") or "-")
        y -= 6

    if invoice.get("items"):
        section("Completion Summary")
        if options["show_completion_date"]:
            row("Completion Date", format_pro_date(invoice.get("completion_summary_date")) or "-")
        row("Final Inspection", invoice.get("completion_summary_status") or "Not marked passed")

    c.setFont("Helvetica-Bold", 12)
    c.drawString(left, y, "Totals")
    y -= 18
    totals = []
    if options["show_labor_hours"]:
        totals.append(("Labor Hours", invoice.get("labor_hours") if invoice.get("labor_hours") is not None else "-"))
    if options["show_labor_rate"]:
        totals.append(("Labor Rate", f"{format_currency(invoice.get('labor_rate'))}/hr" if invoice.get("labor_rate") is not None else "-"))
    if options["show_labor_total"]:
        totals.append(("Labor Total", invoice.get("labor_total")))
    if options["show_parts_total"]:
        totals.append(("Parts Total", invoice.get("parts_total")))
    if invoice.get("approved_estimate_total") is not None:
        totals.append(("Approved Estimate Total", invoice.get("approved_estimate_total")))
        totals.append(("Final Invoice Total", invoice.get("grand_total")))
        if invoice.get("estimate_final_difference"):
            totals.append((invoice.get("estimate_difference_label") or "Difference", invoice.get("estimate_final_difference")))
    else:
        totals.append(("Grand Total", invoice.get("grand_total")))
    for label, value in totals:
        c.setFont("Helvetica-Bold" if label == "Grand Total" else "Helvetica", 11)
        c.drawString(left, y, label)
        display_value = value if isinstance(value, str) else (format_currency(value) or "$0.00")
        c.drawRightString(right, y, display_value)
        y -= 17

    c.setFont("Helvetica", 9)
    c.setFillColorRGB(0.38, 0.45, 0.55)
    c.drawString(left, 42, "Thank you for choosing TorqueMech Pro.")
    c.save()
    return buf.getvalue()


def ensure_repair_checklist_schema(conn: sqlite3.Connection) -> None:
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
    conn.commit()


def ensure_repair_completion_schema(conn: sqlite3.Connection) -> None:
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
    columns = {row[1] for row in conn.execute("PRAGMA table_info(repair_completions)").fetchall()}
    for key, _label in REPAIR_COMPLETION_CHECKS:
        if key not in columns:
            conn.execute(f"ALTER TABLE repair_completions ADD COLUMN {key} INTEGER NOT NULL DEFAULT 0")
    if "final_inspection_notes" not in columns:
        conn.execute("ALTER TABLE repair_completions ADD COLUMN final_inspection_notes TEXT")
    if "final_inspection_passed" not in columns:
        conn.execute("ALTER TABLE repair_completions ADD COLUMN final_inspection_passed INTEGER NOT NULL DEFAULT 0")
    if "after_repair_photo_paths" not in columns:
        conn.execute("ALTER TABLE repair_completions ADD COLUMN after_repair_photo_paths TEXT")
    if "completion_date" not in columns:
        conn.execute("ALTER TABLE repair_completions ADD COLUMN completion_date TEXT")
    if "completion_mileage" not in columns:
        conn.execute("ALTER TABLE repair_completions ADD COLUMN completion_mileage INTEGER")
    if "technician_notes" not in columns:
        conn.execute("ALTER TABLE repair_completions ADD COLUMN technician_notes TEXT")
    if "override_reason" not in columns:
        conn.execute("ALTER TABLE repair_completions ADD COLUMN override_reason TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_repair_completions_repair_record_id "
        "ON repair_completions (repair_record_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_repair_completions_completed_at "
        "ON repair_completions (completed_at)"
    )
    conn.commit()


def default_repair_completion() -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": None,
        "repair_record_id": None,
        "completion_date": "",
        "completion_mileage": None,
        "technician_notes": "",
        "completion_notes": "",
        "final_inspection_passed": 0,
        "final_inspection_notes": "",
        "after_repair_photo_paths": "",
        "override_reason": "",
        "completed_at": "",
        "created_at": "",
        "updated_at": "",
    }
    for key, _label in REPAIR_COMPLETION_CHECKS:
        record[key] = 0
    return record


def load_repair_completion(
    conn: sqlite3.Connection,
    repair_record_id: int,
) -> dict[str, Any]:
    ensure_repair_completion_schema(conn)
    row = conn.execute(
        """
        SELECT *
        FROM repair_completions
        WHERE repair_record_id = ?
        """,
        (repair_record_id,),
    ).fetchone()
    completion = default_repair_completion()
    completion["repair_record_id"] = repair_record_id
    if row:
        completion.update(dict(row))
    try:
        after_photo_urls = json.loads(completion.get("after_repair_photo_paths") or "[]")
    except json.JSONDecodeError:
        after_photo_urls = []
    completion["after_repair_photo_urls"] = after_photo_urls if isinstance(after_photo_urls, list) else []
    return completion


def repair_completion_progress(completion: dict[str, Any]) -> dict[str, int]:
    return {"completed": 0, "total": 0, "incomplete": 0, "percent": 0}


def upsert_repair_completion(
    conn: sqlite3.Connection,
    *,
    repair_record_id: int,
    form: dict[str, str],
    completed_at: str | None,
    now: str,
    after_repair_photo_paths: list[str] | None = None,
) -> dict[str, Any]:
    ensure_repair_completion_schema(conn)
    values = {key: 1 if form.get(key) == "1" else 0 for key, _label in REPAIR_COMPLETION_CHECKS}
    completion_date = str(form.get("completion_date") or "").strip()
    completion_mileage = optional_int(form, "completion_mileage")
    technician_notes = str(form.get("technician_notes") or "").strip()
    completion_notes = str(form.get("completion_notes") or "").strip()
    final_inspection_passed = 1 if form.get("final_inspection_passed") == "1" else 0
    final_inspection_notes = str(form.get("final_inspection_notes") or "").strip()
    override_reason = str(form.get("override_reason") or "").strip()
    existing = load_repair_completion(conn, repair_record_id)
    try:
        existing_photo_paths = json.loads(existing.get("after_repair_photo_paths") or "[]")
    except json.JSONDecodeError:
        existing_photo_paths = []
    if not isinstance(existing_photo_paths, list):
        existing_photo_paths = []
    merged_photo_paths = [
        str(path)
        for path in [*existing_photo_paths, *(after_repair_photo_paths or [])]
        if str(path or "").strip()
    ]
    if after_repair_photo_paths and len(merged_photo_paths) > PHOTO_UPLOAD_MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Upload up to {PHOTO_UPLOAD_MAX_FILES} photos.")
    photo_paths_json = json.dumps(merged_photo_paths)
    existing_completed_at = existing.get("completed_at") or ""
    effective_completed_at = completed_at or existing_completed_at or ""
    if "completion_date" in form:
        effective_completion_date = completion_date
    else:
        effective_completion_date = existing.get("completion_date") or str(effective_completed_at or "")[:10]
    effective_completion_mileage = completion_mileage
    if effective_completion_mileage is None:
        effective_completion_mileage = existing.get("completion_mileage")
    conn.execute(
        """
        INSERT INTO repair_completions (
          repair_record_id, torque_verified, fluids_verified, leaks_checked,
          codes_cleared, road_test_completed, customer_concern_resolved,
          completion_date, completion_mileage, technician_notes,
          completion_notes, final_inspection_passed, final_inspection_notes, after_repair_photo_paths, override_reason, completed_at,
          created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(repair_record_id) DO UPDATE SET
          torque_verified = excluded.torque_verified,
          fluids_verified = excluded.fluids_verified,
          leaks_checked = excluded.leaks_checked,
          codes_cleared = excluded.codes_cleared,
          road_test_completed = excluded.road_test_completed,
          customer_concern_resolved = excluded.customer_concern_resolved,
          completion_date = excluded.completion_date,
          completion_mileage = COALESCE(excluded.completion_mileage, repair_completions.completion_mileage),
          technician_notes = excluded.technician_notes,
          completion_notes = excluded.completion_notes,
          final_inspection_passed = excluded.final_inspection_passed,
          final_inspection_notes = excluded.final_inspection_notes,
          after_repair_photo_paths = excluded.after_repair_photo_paths,
          override_reason = excluded.override_reason,
          completed_at = COALESCE(NULLIF(excluded.completed_at, ''), repair_completions.completed_at),
          updated_at = excluded.updated_at
        """,
        (
            repair_record_id,
            values["torque_verified"],
            values["fluids_verified"],
            values["leaks_checked"],
            values["codes_cleared"],
            values["road_test_completed"],
            values["customer_concern_resolved"],
            effective_completion_date,
            effective_completion_mileage,
            technician_notes,
            completion_notes,
            final_inspection_passed,
            final_inspection_notes,
            photo_paths_json,
            override_reason,
            effective_completed_at,
            now,
            now,
        ),
    )
    return load_repair_completion(conn, repair_record_id)


def load_repair_checklist_items(
    conn: sqlite3.Connection,
    repair_record_id: int,
) -> list[dict[str, Any]]:
    ensure_repair_checklist_schema(conn)
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM repair_checklist_items
            WHERE repair_record_id = ?
            ORDER BY task_order ASC, id ASC
            """,
            (repair_record_id,),
        ).fetchall()
    ]


def repair_checklist_progress(items: list[dict[str, Any]]) -> dict[str, int]:
    total = len(items)
    completed = sum(1 for item in items if int(item.get("completed") or 0))
    percent = int(round((completed / total) * 100)) if total else 0
    return {"completed": completed, "total": total, "percent": percent}


def repair_checklist_summary(
    conn: sqlite3.Connection,
    repair_record_id: int | None,
) -> dict[str, int]:
    if not repair_record_id:
        return {"completed": 0, "total": 0, "incomplete": 0, "percent": 0}
    progress = repair_checklist_progress(load_repair_checklist_items(conn, repair_record_id))
    return {
        **progress,
        "incomplete": max(progress["total"] - progress["completed"], 0),
    }


def repair_completion_requires_checklist_override(
    conn: sqlite3.Connection,
    repair_record_id: int | None,
) -> bool:
    summary = repair_checklist_summary(conn, repair_record_id)
    return summary["total"] > 0 and summary["incomplete"] > 0


def load_vehicle_repair_checklist_events(
    conn: sqlite3.Connection,
    customer_id: int,
    vehicle_id: int,
) -> list[dict[str, Any]]:
    ensure_repair_checklist_schema(conn)
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT rci.*, rr.customer_id, rr.vehicle_id, rr.repair_name
            FROM repair_checklist_items rci
            JOIN repair_records rr ON rr.id = rci.repair_record_id
            WHERE rr.customer_id = ?
              AND rr.vehicle_id = ?
              AND rci.completed = 1
              AND rci.completed_at IS NOT NULL
              AND TRIM(rci.completed_at) != ''
            ORDER BY rci.completed_at DESC, rci.id DESC
            """,
            (customer_id, vehicle_id),
        ).fetchall()
    ]


def load_vehicle_repair_completion_events(
    conn: sqlite3.Connection,
    customer_id: int,
    vehicle_id: int,
) -> list[dict[str, Any]]:
    ensure_repair_completion_schema(conn)
    records = [
        dict(row)
        for row in conn.execute(
            """
            SELECT
              rc.id,
              rc.repair_record_id,
              rc.completion_date,
              rc.completed_at,
              rc.after_repair_photo_paths,
              rc.override_reason,
              rc.created_at,
              rc.updated_at,
              rr.customer_id,
              rr.vehicle_id,
              rr.repair_name,
              rr.mileage,
              rr.workflow_source_type,
              rr.workflow_source_id,
              rr.total_cost
            FROM repair_completions rc
            JOIN repair_records rr ON rr.id = rc.repair_record_id
            WHERE rr.customer_id = ?
              AND rr.vehicle_id = ?
              AND rc.completed_at IS NOT NULL
              AND TRIM(rc.completed_at) != ''
            ORDER BY rc.completed_at DESC, rc.id DESC
            """,
            (customer_id, vehicle_id),
        ).fetchall()
    ]
    parts_map = load_repair_job_parts_map(
        conn,
        {int(record.get("repair_record_id") or 0) for record in records if record.get("repair_record_id")},
    )
    attach_repair_job_parts(records, parts_map, id_key="repair_record_id")
    return records


def table_has_column(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    return column_name in {
        row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }


def findings_records_has_customer_id(conn: sqlite3.Connection) -> bool:
    return table_has_column(conn, "findings_records", "customer_id")


def finding_record_where_sql(conn: sqlite3.Connection, table_alias: str | None = None) -> str:
    prefix = f"{table_alias}." if table_alias else ""
    if findings_records_has_customer_id(conn):
        return f"{prefix}id = ? AND {prefix}customer_id = ? AND {prefix}vehicle_id = ?"
    return f"{prefix}id = ? AND {prefix}vehicle_id = ?"


def finding_record_where_params(
    conn: sqlite3.Connection,
    finding_id: int,
    customer_id: int,
    vehicle_id: int,
) -> tuple[Any, ...]:
    if findings_records_has_customer_id(conn):
        return (finding_id, customer_id, vehicle_id)
    return (finding_id, vehicle_id)


def ensure_findings_records_schema(conn: sqlite3.Connection) -> None:
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
          before_inspection_photo_paths TEXT,
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
    columns = {row[1] for row in conn.execute("PRAGMA table_info(findings_records)").fetchall()}
    if "customer_notes" not in columns:
        conn.execute("ALTER TABLE findings_records ADD COLUMN customer_notes TEXT")
    if "internal_notes" not in columns:
        conn.execute("ALTER TABLE findings_records ADD COLUMN internal_notes TEXT")
    if "request_type" not in columns:
        conn.execute("ALTER TABLE findings_records ADD COLUMN request_type TEXT NOT NULL DEFAULT 'finding'")
    if "labor_description" not in columns:
        conn.execute("ALTER TABLE findings_records ADD COLUMN labor_description TEXT")
    if "labor_hours" not in columns:
        conn.execute("ALTER TABLE findings_records ADD COLUMN labor_hours REAL")
    if "labor_rate" not in columns:
        conn.execute("ALTER TABLE findings_records ADD COLUMN labor_rate REAL")
    if "labor_amount" not in columns:
        conn.execute("ALTER TABLE findings_records ADD COLUMN labor_amount REAL")
    if "parts_cost" not in columns:
        conn.execute("ALTER TABLE findings_records ADD COLUMN parts_cost REAL")
    if "labor_reason" not in columns:
        conn.execute("ALTER TABLE findings_records ADD COLUMN labor_reason TEXT")
    if "before_inspection_photo_paths" not in columns:
        conn.execute("ALTER TABLE findings_records ADD COLUMN before_inspection_photo_paths TEXT")
    if "severity" not in columns:
        conn.execute("ALTER TABLE findings_records ADD COLUMN severity TEXT NOT NULL DEFAULT 'Low'")
    if "repair_work_status" not in columns:
        conn.execute("ALTER TABLE findings_records ADD COLUMN repair_work_status TEXT")
    if "repair_work_updated_at" not in columns:
        conn.execute("ALTER TABLE findings_records ADD COLUMN repair_work_updated_at TEXT")
    if "linked_repair_record_id" not in columns:
        conn.execute("ALTER TABLE findings_records ADD COLUMN linked_repair_record_id INTEGER")
    if "repair_record_created_at" not in columns:
        conn.execute("ALTER TABLE findings_records ADD COLUMN repair_record_created_at TEXT")
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
    if findings_records_has_customer_id(conn):
        conn.execute("CREATE INDEX IF NOT EXISTS idx_findings_records_customer_id ON findings_records (customer_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_findings_records_vehicle_id ON findings_records (vehicle_id)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_findings_records_vehicle_mileage_date "
        "ON findings_records (vehicle_id, mileage, finding_date)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_findings_records_status ON findings_records (status)")
    conn.commit()


def ensure_finding_history_records_schema(conn: sqlite3.Connection) -> None:
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
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_finding_history_records_finding_id "
        "ON finding_history_records (finding_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_finding_history_records_created_at "
        "ON finding_history_records (created_at)"
    )
    conn.commit()


def append_finding_history_record(
    conn: sqlite3.Connection,
    finding_id: int,
    previous_status: str | None,
    new_status: str,
    event_type: str,
    created_at: str,
    *,
    notes: str = "",
) -> None:
    ensure_finding_history_records_schema(conn)
    conn.execute(
        """
        INSERT INTO finding_history_records (
          finding_id, previous_status, new_status, event_type,
          actor_name, notes, metadata_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            finding_id,
            previous_status,
            new_status,
            event_type,
            "",
            notes,
            "",
            created_at,
        ),
    )


def ensure_customer_decision_logs_schema(conn: sqlite3.Connection) -> None:
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
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_customer_decision_logs_finding_id "
        "ON customer_decision_logs (finding_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_customer_decision_logs_created_at "
        "ON customer_decision_logs (created_at)"
    )
    conn.commit()


def append_customer_decision_log_if_needed(
    conn: sqlite3.Connection,
    finding_id: int,
    decision_status: str,
    customer_display_name: str,
    created_at: str,
    *,
    notes: str = "",
) -> None:
    if decision_status not in CUSTOMER_DECISION_LOG_STATUSES:
        return
    ensure_customer_decision_logs_schema(conn)
    latest = conn.execute(
        """
        SELECT decision_status
        FROM customer_decision_logs
        WHERE finding_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (finding_id,),
    ).fetchone()
    if latest and latest["decision_status"] == decision_status:
        return
    conn.execute(
        """
        INSERT INTO customer_decision_logs (
          finding_id, decision_status, customer_name, source,
          approval_method, advisor_name, signature_path, approval_pdf_path,
          estimate_revision_id, notes, metadata_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            finding_id,
            decision_status,
            customer_display_name,
            "internal/manual",
            "",
            "",
            "",
            "",
            None,
            notes,
            "",
            created_at,
        ),
    )


def customer_decision_log_source_label(value: Any) -> str:
    source = str(value or "").strip()
    if source == "internal/manual":
        return "Manual/Internal"
    return source.title()


def vehicle_finding_activity_payload(
    conn: sqlite3.Connection,
    customer_id: int,
    vehicle_id: int,
) -> dict[str, Any]:
    ensure_finding_history_records_schema(conn)
    ensure_customer_decision_logs_schema(conn)
    ensure_discrepancy_approval_events_schema(conn)
    finding_history_records = [
        dict(row)
        for row in conn.execute(
            """
            SELECT fhr.*, fr.finding, fr.request_type, fr.labor_description
            FROM finding_history_records fhr
            JOIN findings_records fr ON fr.id = fhr.finding_id
            WHERE fr.vehicle_id = ?
            ORDER BY fhr.created_at DESC, fhr.id DESC
            """,
            (vehicle_id,),
        ).fetchall()
    ]
    customer_decision_logs = [
        dict(row)
        for row in conn.execute(
            """
            SELECT cdl.*, fr.finding
            FROM customer_decision_logs cdl
            JOIN findings_records fr ON fr.id = cdl.finding_id
            WHERE fr.vehicle_id = ?
            ORDER BY cdl.created_at DESC, cdl.id DESC
            """,
            (vehicle_id,),
        ).fetchall()
    ]
    approval_event_records = [
        dict(row)
        for row in conn.execute(
            """
            SELECT dae.*, da.request_type, da.part_name, da.part_number
            FROM discrepancy_approval_events dae
            JOIN discrepancy_approvals da ON da.id = dae.approval_id
            WHERE dae.customer_id = ? AND dae.vehicle_id = ?
            ORDER BY dae.created_at DESC, dae.id DESC
            """,
            (customer_id, vehicle_id),
        ).fetchall()
    ]
    _customer, vehicle = load_customer_vehicle(conn, customer_id, vehicle_id)
    ensure_maintenance_records_schema(conn)
    ensure_repair_records_schema(conn)
    ensure_repair_checklist_schema(conn)
    ensure_repair_completion_schema(conn)
    ensure_invoices_schema(conn)
    ensure_service_history_schema(conn)
    ensure_service_history_records_schema(conn)
    ensure_findings_records_schema(conn)
    service_history_records = [
        dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM service_history_records
            WHERE customer_id = ? AND vehicle_id = ?
            """,
            (customer_id, vehicle_id),
        ).fetchall()
    ]
    invoice_records = load_vehicle_invoice_records(conn, customer_id, vehicle_id)
    estimate_document_records = load_vehicle_estimate_documents(conn, customer_id, vehicle_id)
    findings_records = [
        dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM findings_records
            WHERE vehicle_id = ?
            """,
            (vehicle_id,),
        ).fetchall()
    ]
    for record in findings_records:
        record.setdefault("customer_id", customer_id)
    attach_estimate_documents_to_findings(
        findings_records,
        estimate_document_records,
        customer_id=customer_id,
        vehicle_id=vehicle_id,
    )
    vehicle_timeline = build_vehicle_timeline(
        customer_id,
        vehicle_id,
        vehicle,
        service_history_records,
        invoice_records,
        findings_records,
        finding_history_records,
        customer_decision_logs,
        approval_event_records,
        load_vehicle_repair_checklist_events(conn, customer_id, vehicle_id),
        load_vehicle_repair_completion_events(conn, customer_id, vehicle_id),
        estimate_document_records,
    )
    vehicle_timeline_total = sum(int(group.get("count") or 0) for group in vehicle_timeline)
    finding_history_count = len(finding_history_records)
    customer_decision_log_count = len(customer_decision_logs)
    return {
        "finding_history_count": finding_history_count,
        "customer_decision_log_count": customer_decision_log_count,
        "vehicle_timeline_total": vehicle_timeline_total,
        "vehicle_timeline": vehicle_timeline,
        "finding_history": {
            "count": finding_history_count,
            "records": [
                {
                    "created_at": record.get("created_at") or "",
                    "created_at_display": format_pro_datetime(record.get("created_at")),
                    "event_type": record.get("event_type") or "",
                    "previous_status": record.get("previous_status") or "",
                    "new_status": record.get("new_status") or "",
                    "finding": record.get("finding") or "Finding",
                }
                for record in finding_history_records
            ],
        },
        "customer_decision_logs": {
            "count": customer_decision_log_count,
            "records": [
                {
                    "created_at": record.get("created_at") or "",
                    "created_at_display": format_pro_datetime(record.get("created_at")),
                    "decision_status": record.get("decision_status") or "",
                    "source": record.get("source") or "",
                    "source_display": customer_decision_log_source_label(record.get("source")),
                    "customer_name": record.get("customer_name") or "",
                    "finding": record.get("finding") or "Finding",
                }
                for record in customer_decision_logs
            ],
        },
    }


def load_repair_record(
    conn: sqlite3.Connection,
    customer_id: int,
    vehicle_id: int,
    repair_id: int,
) -> dict[str, Any]:
    ensure_repair_records_schema(conn)
    repair = row_to_dict(
        conn.execute(
            """
            SELECT *
            FROM repair_records
            WHERE id = ? AND customer_id = ? AND vehicle_id = ?
            """,
            (repair_id, customer_id, vehicle_id),
        ).fetchone()
    )
    if not repair:
        raise HTTPException(status_code=404, detail="Repair record not found")
    summary = repair_job_parts_summary(load_repair_job_parts(conn, repair_id))
    repair["tracked_parts"] = summary["parts"]
    repair["tracked_parts_total"] = summary["tracked_parts_total"]
    repair["tracked_parts_count"] = summary["count"]
    totals = repair_cost_totals(repair)
    repair["labor_rate"] = totals["labor_rate"]
    repair["labor_rate_is_legacy"] = totals["labor_rate_is_legacy"]
    repair["labor_total"] = totals["labor_total"]
    repair["parts_total"] = totals["parts_total"]
    repair["grand_total"] = totals["grand_total"]
    repair["labor_cost"] = totals["labor_total"]
    repair["total_cost"] = totals["grand_total"]
    repair["default_part_name"] = default_part_name_for_repair(repair)
    return repair


def load_repair_source_finding_for_detail(
    conn: sqlite3.Connection,
    repair: dict[str, Any],
    customer_id: int,
    vehicle_id: int,
) -> dict[str, Any] | None:
    if normalize_workflow_source_type(repair.get("workflow_source_type")) != "finding":
        return None
    source_id = repair.get("workflow_source_id")
    if source_id is None:
        return None
    try:
        return load_finding_record(conn, customer_id, vehicle_id, int(source_id))
    except (ValueError, HTTPException):
        return None


def repair_execution_status_context(
    conn: sqlite3.Connection,
    repair: dict[str, Any],
    customer_id: int,
    vehicle_id: int,
) -> dict[str, Any] | None:
    source_type = normalize_workflow_source_type(repair.get("workflow_source_type"))
    source_id = repair.get("workflow_source_id")
    if not source_type or source_id is None:
        return None
    try:
        source_id = int(source_id)
    except (TypeError, ValueError):
        return None
    if source_type == "finding":
        source = load_finding_record(conn, customer_id, vehicle_id, source_id)
        current_status = source.get("repair_work_status") or (
            "completed" if source.get("status") == "Completed" else "ready"
        )
    else:
        source = load_approval_record(conn, customer_id, vehicle_id, source_id)
        current_status = source.get("repair_work_status") or "ready"
    try:
        current_status = normalize_repair_work_status(current_status)
    except HTTPException:
        current_status = "ready"
    return {
        "source_type": source_type,
        "source_id": source_id,
        "status": current_status,
        "status_label": repair_work_status_label(current_status),
        "options": [
            {"value": value, "label": REPAIR_WORK_STATUS_LABELS[value]}
            for value in REPAIR_EXECUTION_STATUS_OPTIONS
        ],
    }


def load_finding_record(
    conn: sqlite3.Connection,
    customer_id: int,
    vehicle_id: int,
    finding_id: int,
) -> dict[str, Any]:
    ensure_findings_records_schema(conn)
    finding = row_to_dict(
        conn.execute(
            f"""
            SELECT *
            FROM findings_records
            WHERE {finding_record_where_sql(conn)}
            """,
            finding_record_where_params(conn, finding_id, customer_id, vehicle_id),
        ).fetchone()
    )
    if not finding:
        raise HTTPException(status_code=404, detail="Finding record not found")
    return attach_finding_photo_urls(finding)


def load_finding_history_records(
    conn: sqlite3.Connection,
    finding_id: int,
) -> list[dict[str, Any]]:
    ensure_finding_history_records_schema(conn)
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM finding_history_records
            WHERE finding_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (finding_id,),
        ).fetchall()
    ]


def ensure_service_history_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS service_history (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          customer_id INTEGER NOT NULL,
          vehicle_id INTEGER NOT NULL,
          shop_id INTEGER,
          service_title TEXT,
          service_date TEXT,
          mileage_at_service INTEGER,
          service_notes TEXT,
          labor_amount REAL,
          parts_amount REAL,
          estimate_total REAL,
          actual_total REAL,
          created_at TEXT,
          updated_at TEXT
        )
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(service_history)").fetchall()}
    if "labor_amount" not in columns:
        conn.execute("ALTER TABLE service_history ADD COLUMN labor_amount REAL")
    if "parts_amount" not in columns:
        conn.execute("ALTER TABLE service_history ADD COLUMN parts_amount REAL")
    if "estimate_total" not in columns:
        conn.execute("ALTER TABLE service_history ADD COLUMN estimate_total REAL")
    if "actual_total" not in columns:
        conn.execute("ALTER TABLE service_history ADD COLUMN actual_total REAL")
    if "created_at" not in columns:
        conn.execute("ALTER TABLE service_history ADD COLUMN created_at TEXT")
    if "updated_at" not in columns:
        conn.execute("ALTER TABLE service_history ADD COLUMN updated_at TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_service_history_vehicle_date ON service_history (vehicle_id, service_date)")
    conn.commit()


def ensure_service_history_records_schema(conn: sqlite3.Connection) -> None:
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
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_service_history_records_vehicle_mileage_date "
        "ON service_history_records (vehicle_id, mileage, service_date)"
    )
    existing_tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    if "service_history" in existing_tables:
        conn.execute(
            """
            INSERT OR IGNORE INTO service_history_records (
              customer_id, vehicle_id, source_type, source_record_id, service_name,
              service_date, mileage, parts_cost, labor_cost, total_cost, notes, created_at
            )
            SELECT
              customer_id, vehicle_id, 'legacy', id, service_title,
              service_date, mileage_at_service, parts_amount, labor_amount,
              COALESCE(actual_total, estimate_total), service_notes,
              COALESCE(created_at, updated_at, service_date)
            FROM service_history
            """
        )
    if "maintenance_records" in existing_tables:
        conn.execute(
            """
            INSERT OR IGNORE INTO service_history_records (
              customer_id, vehicle_id, source_type, source_record_id, service_name,
              service_date, mileage, notes, created_at
            )
            SELECT
              customer_id, vehicle_id, 'maintenance', id, service_type,
              date_performed, mileage_performed, notes, created_at
            FROM maintenance_records
            """
        )
    if "repair_records" in existing_tables:
        conn.execute(
            """
            INSERT OR IGNORE INTO service_history_records (
              customer_id, vehicle_id, source_type, source_record_id, service_name,
              service_date, mileage, labor_hours, parts_cost, labor_cost,
              total_cost, notes, created_at
            )
            SELECT
              customer_id, vehicle_id, 'repair', id, repair_name,
              repair_date, mileage, labor_hours, parts_cost, labor_cost,
              total_cost, notes, created_at
            FROM repair_records
            WHERE status = 'Completed'
              AND completed_at IS NOT NULL
            """
        )
    if "repair_records" in existing_tables:
        conn.execute(
            """
            DELETE FROM service_history_records
            WHERE source_type = 'repair'
              AND EXISTS (
                SELECT 1
                FROM repair_records rr
                WHERE rr.id = service_history_records.source_record_id
                  AND (
                    COALESCE(rr.status, '') != 'Completed'
                    OR rr.completed_at IS NULL
                    OR TRIM(rr.completed_at) = ''
                  )
              )
            """
        )
        conn.execute(
            """
            DELETE FROM service_history_records
            WHERE source_type = 'maintenance'
              AND EXISTS (
                SELECT 1
                FROM service_history_records repair_history
                JOIN repair_records rr
                  ON rr.id = repair_history.source_record_id
                 AND repair_history.source_type = 'repair'
                WHERE rr.track_as_maintenance = 1
                  AND repair_history.customer_id = service_history_records.customer_id
                  AND repair_history.vehicle_id = service_history_records.vehicle_id
                  AND COALESCE(NULLIF(TRIM(LOWER(repair_history.service_name)), ''), '') =
                      COALESCE(NULLIF(TRIM(LOWER(service_history_records.service_name)), ''), '')
                  AND COALESCE(repair_history.service_date, '') = COALESCE(service_history_records.service_date, '')
                  AND COALESCE(repair_history.mileage, -1) = COALESCE(service_history_records.mileage, -1)
              )
            """
        )
    conn.commit()


def append_service_history_record(
    conn: sqlite3.Connection,
    *,
    customer_id: int,
    vehicle_id: int,
    source_type: str,
    source_record_id: int,
    service_name: str,
    service_date: str,
    mileage: int | None,
    labor_hours: float | None = None,
    parts_cost: float | None = None,
    labor_cost: float | None = None,
    total_cost: float | None = None,
    notes: str = "",
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO service_history_records (
          customer_id, vehicle_id, source_type, source_record_id, service_name,
          service_date, mileage, labor_hours, parts_cost, labor_cost,
          total_cost, notes, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            customer_id,
            vehicle_id,
            source_type,
            source_record_id,
            service_name,
            service_date,
            mileage,
            labor_hours,
            parts_cost,
            labor_cost,
            total_cost,
            notes,
            created_at,
        ),
    )


def upsert_service_history_record(
    conn: sqlite3.Connection,
    *,
    customer_id: int,
    vehicle_id: int,
    source_type: str,
    source_record_id: int,
    service_name: str,
    service_date: str,
    mileage: int | None,
    labor_hours: float | None = None,
    parts_cost: float | None = None,
    labor_cost: float | None = None,
    total_cost: float | None = None,
    notes: str = "",
    created_at: str,
) -> None:
    ensure_service_history_records_schema(conn)
    conn.execute(
        """
        INSERT INTO service_history_records (
          customer_id, vehicle_id, source_type, source_record_id, service_name,
          service_date, mileage, labor_hours, parts_cost, labor_cost,
          total_cost, notes, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_type, source_record_id) DO UPDATE SET
          customer_id = excluded.customer_id,
          vehicle_id = excluded.vehicle_id,
          service_name = excluded.service_name,
          service_date = excluded.service_date,
          mileage = excluded.mileage,
          labor_hours = excluded.labor_hours,
          parts_cost = excluded.parts_cost,
          labor_cost = excluded.labor_cost,
          total_cost = excluded.total_cost,
          notes = excluded.notes
        """,
        (
            customer_id,
            vehicle_id,
            source_type,
            source_record_id,
            service_name,
            service_date,
            mileage,
            labor_hours,
            parts_cost,
            labor_cost,
            total_cost,
            notes,
            created_at,
        ),
    )


def upsert_maintenance_from_repair(
    conn: sqlite3.Connection,
    *,
    customer_id: int,
    vehicle_id: int,
    service_type: str,
    date_performed: str,
    mileage_performed: int | None,
    notes: str,
    now: str,
) -> int:
    normalized = normalize_maintenance_service_type(service_type)
    existing = next(
        (
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM maintenance_records
                WHERE customer_id = ? AND vehicle_id = ?
                ORDER BY id DESC
                """,
                (customer_id, vehicle_id),
            ).fetchall()
            if normalize_maintenance_service_type(row["service_type"]) == normalized
        ),
        None,
    )
    defaults = maintenance_defaults_for(service_type)
    interval_miles = defaults.get("interval_miles")
    interval_months = defaults.get("interval_months")
    due_mileage = calculated_due_mileage(mileage_performed, interval_miles)
    due_date = calculated_due_date(date_performed, interval_months)
    if existing:
        conn.execute(
            """
            UPDATE maintenance_records
            SET date_performed = ?, mileage_performed = ?, interval_miles = ?,
                interval_months = ?, due_mileage = ?, due_date = ?, notes = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                date_performed,
                mileage_performed,
                interval_miles,
                interval_months,
                due_mileage,
                due_date,
                notes,
                now,
                existing["id"],
            ),
        )
        return int(existing["id"])

    cur = conn.execute(
        """
        INSERT INTO maintenance_records (
          customer_id, vehicle_id, service_type, date_performed,
          mileage_performed, interval_miles, interval_months,
          due_mileage, due_date, notes, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            customer_id,
            vehicle_id,
            service_type,
            date_performed,
            mileage_performed,
            interval_miles,
            interval_months,
            due_mileage,
            due_date,
            notes,
            now,
            now,
        ),
    )
    return int(cur.lastrowid)


def normalize_customer_status(value: str) -> str:
    status = str(value or "active").strip().lower()
    return status if status in {"active", "inactive", "all"} else "active"


def split_customer_name(full_name: str) -> tuple[str, str]:
    parts = [part for part in str(full_name or "").strip().split() if part]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def load_estimate_conversion_payload(raw_payload: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw_payload or "{}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid estimate payload")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid estimate payload")
    line_items = payload.get("lineItems")
    if not isinstance(line_items, list) or not line_items:
        raise HTTPException(status_code=400, detail="Estimate must include at least one service")
    normalized_items: list[dict[str, Any]] = []
    for idx, item in enumerate(line_items):
        if not isinstance(item, dict):
            continue
        service_name = str(
            item.get("serviceText")
            or item.get("service_name")
            or item.get("serviceCode")
            or item.get("service_code")
            or "Service"
        ).strip()
        if not service_name:
            continue
        quantity = normalize_payload_quantity(item.get("quantity", item.get("qty", 1)))
        display_service_name = str(
            item.get("displayServiceText")
            or item.get("display_service_name")
            or item.get("display_service_text")
            or ""
        ).strip()
        if not display_service_name:
            display_service_name = estimate_service_name_with_quantity(service_name, quantity)
        else:
            display_service_name = estimate_service_name_with_quantity(display_service_name, quantity)
        labor_hours = optional_payload_float(item.get("laborHours", item.get("labor_hours")))
        labor_hours_input = optional_payload_float(item.get("laborHoursInput", item.get("labor_hours_input")))
        labor_calculation_mode = str(item.get("laborCalculationMode") or item.get("labor_calculation_mode") or "total").strip()
        pricing_mode = str(item.get("pricingMode") or item.get("pricing_mode") or "hourly").strip().lower()
        pricing_mode = "flat" if pricing_mode == "flat" else "hourly"
        flat_rate_price = optional_payload_float(item.get("flatRatePrice", item.get("flat_rate_price")))
        if labor_calculation_mode == "per_item" and labor_hours_input is not None:
            labor_hours = round(float(labor_hours_input) * quantity, 2)
        labor_rate = optional_payload_float(item.get("laborRate", item.get("labor_rate")))
        labor_total = optional_payload_float(item.get("laborTotal", item.get("labor_total")))
        parts_total = optional_payload_float(item.get("partsTotal", item.get("parts_total")))
        parts_unit_cost = optional_payload_float(item.get("partsUnitCost", item.get("parts_unit_cost")))
        if parts_total is None and parts_unit_cost is not None:
            parts_total = round(float(parts_unit_cost) * quantity, 2)
        grand_total = optional_payload_float(item.get("grandTotal", item.get("grand_total")))
        if pricing_mode == "flat":
            if flat_rate_price is None:
                flat_rate_price = labor_total
            if labor_total is None:
                labor_total = flat_rate_price
        if labor_total is None and labor_hours is not None and labor_rate is not None:
            labor_total = round(labor_hours * labor_rate, 2)
        if grand_total is None:
            grand_total = round(float(labor_total or 0) + float(parts_total or 0), 2)
        normalized_items.append(
            {
                "index": idx,
                "service_name": display_service_name[:240],
                "base_service_name": service_name[:240],
                "service_code": str(item.get("serviceCode") or item.get("service_code") or "").strip()[:160],
                "parts_search_term": str(
                    item.get("partsSearchTerm")
                    or item.get("parts_search_term")
                    or item.get("partsKeyword")
                    or item.get("parts_keyword")
                    or ""
                ).strip()[:240],
                "quantity": quantity,
                "parts_unit_cost": parts_unit_cost,
                "labor_hours": labor_hours,
                "labor_hours_input": labor_hours_input,
                "labor_calculation_mode": labor_calculation_mode,
                "labor_rate": labor_rate,
                "pricing_mode": pricing_mode,
                "flat_rate_price": flat_rate_price,
                "labor_total": labor_total,
                "parts_total": parts_total,
                "grand_total": grand_total,
                "notes": str(item.get("notes") or item.get("description") or "").strip()[:1200],
            }
        )
    if not normalized_items:
        raise HTTPException(status_code=400, detail="Estimate must include at least one service")
    payload["lineItems"] = normalized_items
    vehicle = payload.get("vehicle") if isinstance(payload.get("vehicle"), dict) else {}
    payload["vehicle"] = {
        "year": str(vehicle.get("year") or "").strip(),
        "make": str(vehicle.get("make") or "").strip(),
        "model": str(vehicle.get("displayModel") or vehicle.get("model") or "").strip(),
        "mileage": optional_int(
            {
                "mileage": str(
                    vehicle.get("mileage")
                    or vehicle.get("current_mileage")
                    or vehicle.get("currentMileage")
                    or ""
                )
            },
            "mileage",
        ),
    }
    customer = payload.get("customer") if isinstance(payload.get("customer"), dict) else {}
    payload["customer"] = {
        "name": str(customer.get("name") or "").strip(),
        "phone": str(customer.get("phone") or "").strip(),
    }
    source_context = payload.get("sourceContext") if isinstance(payload.get("sourceContext"), dict) else {}
    payload["source"] = str(payload.get("source") or source_context.get("source") or "estimator").strip().lower()
    payload["customer_id"] = optional_int({"customer_id": str(payload.get("customerId") or payload.get("customer_id") or source_context.get("customerId") or "")}, "customer_id")
    payload["vehicle_id"] = optional_int({"vehicle_id": str(payload.get("vehicleId") or payload.get("vehicle_id") or source_context.get("vehicleId") or "")}, "vehicle_id")
    payload["finding_id"] = optional_int({"finding_id": str(payload.get("findingId") or payload.get("finding_id") or source_context.get("findingId") or "")}, "finding_id")
    payload["sourceContext"] = {
        "source": payload["source"],
        "customerName": str(source_context.get("customerName") or "").strip(),
        "problemFound": str(source_context.get("problemFound") or "").strip(),
        "recommendedRepair": str(source_context.get("recommendedRepair") or "").strip(),
    }
    payload["notes"] = str(payload.get("notes") or "").strip()[:1200]
    return payload


def optional_payload_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def normalize_payload_quantity(value: Any) -> int:
    try:
        quantity = int(float(value or 1))
    except (TypeError, ValueError):
        quantity = 1
    return max(1, quantity)


SERVICE_QUANTITY_SUFFIX_RE = re.compile(
    r"(?:\s*(?:[-\u2013\u2014]\s*)?(?:Qty\.?|Quantity)\s*(?P<qty_word>\d+(?:\.\d+)?)"
    r"|\s*[\u00d7xX]\s*(?P<qty_symbol>\d+(?:\.\d+)?))\s*$",
    re.IGNORECASE,
)


def split_service_quantity_suffix(service_name: Any) -> tuple[str, str]:
    name = str(service_name or "Service").strip() or "Service"
    quantity = ""
    while True:
        match = SERVICE_QUANTITY_SUFFIX_RE.search(name)
        if not match:
            break
        quantity = quantity or (match.group("qty_word") or match.group("qty_symbol") or "")
        name = name[: match.start()].rstrip(" -\u2013\u2014")
        if not name:
            name = "Service"
            break
    return name, quantity


def clean_service_quantity_title(service_name: Any) -> str:
    name, quantity = split_service_quantity_suffix(service_name)
    if quantity:
        return f"{name} \u00d7 {format_quantity(quantity)}"
    return name


def estimate_service_name_with_quantity(service_name: str, quantity: Any = 1) -> str:
    name, _existing_quantity = split_service_quantity_suffix(service_name)
    quantity_value = normalize_payload_quantity(quantity)
    return f"{name} × {quantity_value}" if quantity_value > 1 else name


def load_approval_record(
    conn: sqlite3.Connection,
    customer_id: int,
    vehicle_id: int,
    approval_id: int,
) -> dict[str, Any]:
    ensure_discrepancy_approvals_schema(conn)
    approval = row_to_dict(
        conn.execute(
            """
            SELECT *
            FROM discrepancy_approvals
            WHERE id = ? AND customer_id = ? AND vehicle_id = ?
            """,
            (approval_id, customer_id, vehicle_id),
        ).fetchone()
    )
    if not approval:
        raise HTTPException(status_code=404, detail="Approval record not found")
    return approval


def group_approval_records(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped = {"pending": [], "approved": [], "declined": [], "deferred": []}
    for record in records:
        key = str(record.get("customer_decision") or "pending").lower()
        grouped.setdefault(key, []).append(record)
    return grouped


def build_approval_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"pending": 0, "approved": 0, "deferred": 0, "declined": 0}
    approved_labor_total = 0.0
    approved_parts_total = 0.0
    for record in records:
        decision = normalize_approval_decision(record.get("customer_decision"))
        counts[decision] = counts.get(decision, 0) + 1
        if decision != "approved":
            continue
        request_type = normalize_approval_request_type(record.get("request_type"))
        if request_type == "labor":
            approved_labor_total += float(record.get("labor_amount") or 0)
        elif request_type == "parts":
            approved_parts_total += float(record.get("parts_total") or record.get("parts_amount") or 0)
    return {
        "pending": counts["pending"],
        "approved": counts["approved"],
        "deferred": counts["deferred"],
        "declined": counts["declined"],
        "approved_labor_total": approved_labor_total,
        "approved_parts_total": approved_parts_total,
        "total_approved_add_ons": approved_labor_total + approved_parts_total,
    }


def repair_work_title_from_finding(record: dict[str, Any]) -> str:
    if normalize_finding_request_type(record.get("request_type")) == "labor":
        return record.get("labor_description") or record.get("finding") or "Labor Request"
    return record.get("recommendation") or record.get("finding") or "Finding"


def repair_work_notes_from_finding(record: dict[str, Any]) -> str:
    return "\n".join(
        part
        for part in [
            "Source: Approved finding",
            record.get("finding") or "",
            record.get("recommendation") or "",
            record.get("labor_reason") or "",
        ]
        if str(part or "").strip()
    )


def ensure_repair_record_for_approved_finding(
    conn: sqlite3.Connection,
    *,
    customer_id: int,
    vehicle_id: int,
    finding_id: int,
    now: str,
) -> int | None:
    ensure_repair_records_schema(conn)
    ensure_findings_records_schema(conn)
    finding = load_finding_record(conn, customer_id, vehicle_id, finding_id)
    if (finding.get("status") or "") != "Approved":
        return None

    linked_repair_record_id = finding.get("linked_repair_record_id")
    if linked_repair_record_id:
        return int(linked_repair_record_id)

    existing_repair = conn.execute(
        """
        SELECT id
        FROM repair_records
        WHERE workflow_source_type = 'finding' AND workflow_source_id = ?
        LIMIT 1
        """,
        (finding_id,),
    ).fetchone()
    if existing_repair:
        repair_id = int(existing_repair["id"])
    else:
        try:
            cur = conn.execute(
                """
                INSERT INTO repair_records (
                  vehicle_id, customer_id, repair_name, repair_date, mileage,
                  labor_hours, labor_rate, parts_cost, labor_cost, total_cost,
                  track_as_maintenance, workflow_source_type, workflow_source_id,
                  notes, status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'finding', ?, ?, 'Open', ?)
                """,
                (
                    vehicle_id,
                    customer_id,
                    repair_work_title_from_finding(finding),
                    local_today().isoformat(),
                    finding.get("mileage"),
                    finding.get("labor_hours"),
                    finding.get("labor_rate"),
                    finding.get("parts_cost"),
                    finding.get("labor_amount"),
                    float(finding.get("labor_amount") or 0) + float(finding.get("parts_cost") or 0),
                    finding_id,
                    repair_work_notes_from_finding(finding),
                    now,
                ),
            )
            repair_id = int(cur.lastrowid)
        except sqlite3.IntegrityError:
            existing_repair = conn.execute(
                """
                SELECT id
                FROM repair_records
                WHERE workflow_source_type = 'finding' AND workflow_source_id = ?
                LIMIT 1
                """,
                (finding_id,),
            ).fetchone()
            if not existing_repair:
                raise
            repair_id = int(existing_repair["id"])

    conn.execute(
        f"""
        UPDATE findings_records
        SET linked_repair_record_id = ?,
            repair_record_created_at = COALESCE(NULLIF(repair_record_created_at, ''), ?),
            repair_work_status = COALESCE(NULLIF(repair_work_status, ''), 'ready'),
            repair_work_updated_at = COALESCE(NULLIF(repair_work_updated_at, ''), ?)
        WHERE {finding_record_where_sql(conn)}
        """,
        (repair_id, now, now, *finding_record_where_params(conn, finding_id, customer_id, vehicle_id)),
    )
    return repair_id


def repair_work_title_from_approval(record: dict[str, Any]) -> str:
    request_type = normalize_approval_request_type(record.get("request_type"))
    if request_type == "parts":
        return record.get("part_name") or record.get("part_description") or record.get("finding_title") or "Parts Request"
    if request_type == "labor":
        return record.get("finding_title") or record.get("recommended_repair") or "Labor Request"
    return record.get("recommended_repair") or record.get("finding_title") or "Approved Request"


def normalize_parts_service_title(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", " ", str(value or "").lower()).strip()
    return re.sub(r"\s+", " ", text)


def parts_keyword_for_service(service_title: Any) -> str:
    normalized = normalize_parts_service_title(service_title)
    if normalized in PARTS_SEARCH_KEYWORDS:
        return PARTS_SEARCH_KEYWORDS[normalized]

    cleaned = f" {normalized} "
    for phrase in (
        "customer approved",
        "approved",
        "replacement",
        "repair",
        "service",
        "recommended",
        "finding",
        "needed",
        "needs",
        "need",
        "replace",
        "work",
        "job",
    ):
        cleaned = re.sub(rf"\b{re.escape(phrase)}\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    mapped_cleaned = PARTS_SEARCH_KEYWORDS.get(f"{cleaned} replacement")
    return mapped_cleaned or PARTS_SEARCH_KEYWORDS.get(cleaned) or cleaned


def format_parts_vehicle_value(value: Any, *, title_case: bool = False) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text:
        return ""
    return text.title() if title_case else text


def format_parts_engine(value: Any) -> str:
    engine = re.sub(r"\s+", " ", str(value or "").strip())
    if not engine:
        return ""
    if re.fullmatch(r"\d+(?:\.\d+)?", engine):
        return f"{engine}L"
    if re.fullmatch(r"\d+(?:\.\d+)?\s*[Ll]", engine):
        return re.sub(r"\s+", "", engine).upper()
    return engine


def build_parts_search_query(vehicle: dict[str, Any] | sqlite3.Row | None, service_title: Any) -> str:
    vehicle_parts = [
        str(record_value(vehicle, "year") or "").strip(),
        format_parts_vehicle_value(record_value(vehicle, "make"), title_case=True),
        format_parts_vehicle_value(record_value(vehicle, "model"), title_case=True),
        format_parts_engine(record_value(vehicle, "engine")),
    ]
    keyword = parts_keyword_for_service(service_title)
    return re.sub(r"\s+", " ", " ".join(part for part in [*vehicle_parts, keyword] if part)).strip()


def repair_record_parts_search_title(record: dict[str, Any] | sqlite3.Row | None, fallback_title: Any = "") -> str:
    custom_keyword = str(record_value(record, "parts_search_term") or "").strip()
    return custom_keyword or str(fallback_title or record_value(record, "repair_name") or "Repair").strip()


BAD_PART_NAME_DEFAULTS = {
    "and refill",
    "drain refill",
    "drain and refill",
    "replacement",
    "service",
}


def default_part_name_for_repair(record: dict[str, Any] | sqlite3.Row | None, fallback_title: Any = "") -> str:
    custom_keyword = str(record_value(record, "parts_search_term") or "").strip()
    normalized_custom = normalize_parts_service_title(custom_keyword)
    if custom_keyword and normalized_custom not in BAD_PART_NAME_DEFAULTS:
        return custom_keyword.title()

    visible_title = str(fallback_title or record_value(record, "repair_name") or "").strip()
    mapped_keyword = PARTS_SEARCH_KEYWORDS.get(normalize_parts_service_title(visible_title))
    if mapped_keyword:
        return mapped_keyword.title()
    return ""


def parts_search_url(label: str, query: str) -> str:
    params = {"q": query}
    if label == "O'Reilly":
        return f"https://www.google.com/search?{urlencode({'q': f'site:oreillyauto.com {query}'})}"
    if label == "AutoZone":
        return f"https://www.google.com/search?{urlencode({'q': f'site:autozone.com {query}'})}"
    if label == "NAPA":
        return f"https://www.google.com/search?{urlencode({'q': f'site:napaonline.com {query}'})}"
    if label == "RockAuto":
        return f"https://www.google.com/search?{urlencode({'q': f'site:rockauto.com {query}'})}"
    if label == "OEM/dealer catalog":
        return f"https://www.google.com/search?{urlencode({'q': f'{query} OEM dealer parts catalog'})}"
    if label == "Amazon":
        return f"https://www.amazon.com/s?{urlencode({'k': query})}"
    if label == "eBay":
        return f"https://www.ebay.com/sch/i.html?{urlencode({'_nkw': query})}"
    if label == "Google Shopping":
        return f"https://www.google.com/search?{urlencode({'tbm': 'shop', 'q': query})}"
    if label == "1A Auto":
        return f"https://www.google.com/search?{urlencode({'q': f'site:1aauto.com {query}'})}"
    return f"https://www.google.com/search?{urlencode(params)}"


def parts_source_display_label(label: str, url: str) -> str:
    if label == "OEM/dealer catalog":
        return "OEM Catalog Search"
    if label == "O'Reilly":
        return "O'Reilly Catalog Search"
    if label == "AutoZone":
        return "AutoZone Catalog Search"
    if label == "Google Shopping":
        return "Google Shopping"
    if label == "RockAuto":
        return "RockAuto Catalog Search"
    if label == "NAPA":
        return "NAPA Catalog Search"
    if label == "1A Auto":
        return "1A Auto Catalog Search"
    return label


def parts_source_search_group(label: str) -> str:
    if label in {"Amazon", "eBay"}:
        return "Marketplace Search"
    return "Catalog Search"


def repair_workspace_parts_sources(
    blueprint: dict[str, Any] | None,
    vehicle: dict[str, Any] | sqlite3.Row | None,
    service_title: Any,
) -> list[dict[str, str]]:
    query = build_parts_search_query(vehicle, service_title)
    raw_sources = (blueprint or {}).get("vendor_sources") or (blueprint or {}).get("vendor_links") or []
    if not isinstance(raw_sources, list):
        raw_sources = [raw_sources]
    notes_by_label: dict[str, str] = {}
    sources: list[dict[str, str]] = []
    for source in raw_sources:
        if isinstance(source, dict):
            label = str(source.get("label") or source.get("name") or source.get("title") or "").strip()
            note = str(source.get("status") or source.get("value") or source.get("note") or "").strip()
        else:
            label = str(source or "").strip()
            note = ""
        if label:
            notes_by_label[label] = note

    ordered_labels = [label for label in notes_by_label if label in DEFAULT_PARTS_SOURCE_LABELS]
    for label in notes_by_label:
        if label in DEFAULT_PARTS_SOURCE_LABELS and label not in ordered_labels:
            ordered_labels.append(label)
    for label in DEFAULT_PARTS_SOURCE_LABELS:
        if label not in ordered_labels:
            ordered_labels.append(label)

    for label in ordered_labels:
        url = parts_search_url(label, query) if query else ""
        sources.append(
            {
                "label": parts_source_display_label(label, url),
                "source_label": label,
                "search_group": parts_source_search_group(label),
                "note": notes_by_label.get(label, ""),
                "url": url,
                "query": query,
            }
        )
    return sources


def repair_workspace_source_label(record: dict[str, Any]) -> str:
    source_type = str(record.get("workflow_source_type") or "").strip().lower()
    if source_type == "estimate":
        return "Source: Estimate"
    if source_type in {"finding", "approval"}:
        return "Source: Finding"
    return "Source: Manual Repair"


def repair_workspace_source_action(record: dict[str, Any]) -> dict[str, str]:
    source_type = str(record.get("workflow_source_type") or "").strip().lower()
    source_id = record.get("workflow_source_id")
    customer_id = record.get("customer_id")
    vehicle_id = record.get("vehicle_id")
    if source_type == "finding" and source_id and customer_id and vehicle_id:
        return {
            "label": "View Source Finding",
            "url": f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/findings/{source_id}",
        }
    if source_type == "estimate":
        return {"label": "View Source Estimate", "url": ""}
    return {"label": "View Repair Record", "url": ""}


def repair_workspace_item_from_repair_record(
    record: dict[str, Any],
    vehicle: dict[str, Any],
    *,
    status_label: str | None = None,
) -> dict[str, Any]:
    repair_id = int(record.get("id") or 0)
    totals = repair_cost_totals(record)
    source_label = repair_workspace_source_label(record)
    title = clean_service_quantity_title(record.get("repair_name") or "Repair")
    detail = repair_workspace_detail_from_notes(record.get("notes"))
    blueprint = get_repair_blueprint_for_work_item(title, detail, vehicle)
    source_action = repair_workspace_source_action(record)
    repair_record_url = f"/pro/customers/{record['customer_id']}/vehicles/{record['vehicle_id']}/repairs/{repair_id}"
    item = {
        "source_type": "repair",
        "source_id": repair_id,
        "title": title,
        "detail": detail,
        "request_type_label": "Repair Record",
        "approval_label": source_label.replace("Source: ", ""),
        "source_label": source_label,
        "customer_approval_label": "Customer Approved" if record.get("workflow_source_type") == "estimate" else "",
        "source_action_label": source_action["label"],
        "source_action_url": source_action["url"] or repair_record_url,
        "original_finding": "",
        "repair_work_status": "ready",
        "repair_work_status_label": status_label or repair_workspace_status_label("ready"),
        "record_status": record.get("status") or "Open",
        "workflow_source_type": record.get("workflow_source_type") or "",
        "linked_repair_record_id": repair_id,
        "repair_record_created_at": record.get("created_at") or "",
        "created_at": record.get("created_at") or "",
        "approved_at": record.get("customer_authorized_at") or "",
        "repair_record_url": repair_record_url,
        "repair_prefill": {},
        "updated_at": record.get("completed_at") or record.get("created_at") or record.get("repair_date") or "",
        "mileage": record.get("mileage"),
        "url": repair_record_url,
        "labor_total": totals["labor_total"],
        "parts_total": totals["parts_total"],
        "grand_total": totals["grand_total"],
        "tracked_parts": record.get("tracked_parts") or [],
        "tracked_parts_total": record.get("tracked_parts_total") or 0,
        "tracked_parts_count": record.get("tracked_parts_count") or 0,
        "default_part_name": default_part_name_for_repair(record, title),
        "has_pricing": any(
            float(totals.get(key) or 0) > 0
            for key in ("labor_total", "parts_total", "grand_total")
        ),
        "is_invoiced": bool(record.get("is_invoiced")),
        "invoice_number": record.get("invoice_number") or "",
        "invoice_url": record.get("invoice_url") or "",
        "estimate_badge": "Estimate" if record.get("workflow_source_type") == "estimate" else "",
    }
    if blueprint:
        item["blueprint"] = blueprint
        item["blueprint_summary"] = blueprint_summary(blueprint)
    item["parts_sources"] = repair_workspace_parts_sources(
        blueprint,
        vehicle,
        repair_record_parts_search_title(record, title),
    )
    return item


def build_repair_workspace_groups(
    vehicle: dict[str, Any],
    repair_work_items: list[dict[str, Any]],
    repair_records: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    ready_for_invoice: list[dict[str, Any]] = []
    invoiced: list[dict[str, Any]] = []
    recently_completed: list[dict[str, Any]] = []
    for record in repair_records:
        if (record.get("status") or "") != "Completed":
            continue
        item = repair_workspace_item_from_repair_record(
            record,
            vehicle,
            status_label="Ready for Invoice" if not record.get("is_invoiced") else "Invoiced",
        )
        item["repair_work_status"] = "completed"
        item["record_status"] = "Completed"
        item["updated_at"] = record.get("completed_at") or record.get("repair_date") or record.get("created_at") or ""
        enrich_repair_workspace_item(item)
        recently_completed.append(item)
        if record.get("is_invoiced"):
            invoiced.append(item)
        else:
            ready_for_invoice.append(item)
    active_groups: dict[str, list[dict[str, Any]]] = {
        "open": [],
        "approved": [],
        "in_progress": [],
        "ready_to_complete": [],
    }
    for item in repair_work_items:
        enrich_repair_workspace_item(item)
        group_key = item.get("workspace_group_key")
        if group_key in active_groups:
            active_groups[group_key].append(item)
    recently_completed.sort(
        key=lambda item: (
            parse_datetime_value(item.get("updated_at")) or datetime.min,
            int(item.get("source_id") or 0),
        ),
        reverse=True,
    )
    return {
        "active": repair_work_items,
        "open": active_groups["open"],
        "approved": active_groups["approved"],
        "in_progress": active_groups["in_progress"],
        "ready_to_complete": active_groups["ready_to_complete"],
        "recently_completed": recently_completed[:3],
        "recently_completed_count": len(recently_completed),
        "ready_for_invoice": ready_for_invoice,
        "invoiced": invoiced,
    }


def build_repair_work_items(
    vehicle: dict[str, Any],
    findings_records: list[dict[str, Any]],
    approval_records: list[dict[str, Any]],
    repair_records: list[dict[str, Any]] | None = None,
    estimate_document_records: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    current_vehicle_mileage = vehicle.get("mileage")
    linked_repair_ids: set[int] = set()
    repairs_by_id = {
        int(record.get("id") or 0): record
        for record in repair_records or []
        if int(record.get("id") or 0) > 0
    }
    estimates_by_finding_id = latest_estimate_documents_by_finding_id(estimate_document_records)
    for record in findings_records:
        if (record.get("status") or "") not in {"Approved", "Completed"}:
            continue
        if (record.get("linked_repair_status") or "") == "Completed":
            continue
        status = record.get("repair_work_status") or ("completed" if record.get("status") == "Completed" else "ready")
        try:
            status = normalize_repair_work_status(status)
        except HTTPException:
            status = "ready"
        if status == "completed" or (record.get("status") or "") == "Completed":
            continue
        if record.get("linked_repair_record_id"):
            linked_repair_ids.add(int(record.get("linked_repair_record_id") or 0))
        title = repair_work_title_from_finding(record)
        detail = record.get("finding") or record.get("labor_reason") or record.get("recommendation") or ""
        blueprint = get_repair_blueprint_for_work_item(title, detail, vehicle)
        finding_labor_total = float(record.get("labor_amount") or 0)
        finding_parts_total = float(record.get("parts_cost") or 0)
        finding_grand_total = finding_labor_total + finding_parts_total
        estimate_doc = estimates_by_finding_id.get(int(record.get("id") or 0))
        estimate_url = (
            estimate_document_url(int(record["customer_id"]), int(record["vehicle_id"]), estimate_doc.get("id"))
            if estimate_doc
            else ""
        )
        item = {
            "source_type": "finding",
            "source_id": record.get("id"),
            "title": title,
            "detail": detail,
            "request_type_label": "Labor Request" if normalize_finding_request_type(record.get("request_type")) == "labor" else "Finding",
            "approval_label": "Finding",
            "source_label": "Source: Finding",
            "customer_approval_label": "Customer Approved",
            "source_action_label": "View Source Finding",
            "source_action_url": f"/pro/customers/{record['customer_id']}/vehicles/{record['vehicle_id']}/findings/{record['id']}",
            "create_estimate_url": build_finding_estimator_href({"id": record["customer_id"]}, {"id": record["vehicle_id"], **vehicle}, record),
            "estimate_document_id": estimate_doc.get("id") if estimate_doc else None,
            "estimate_document_url": estimate_url,
            "estimate_document_status": estimate_doc.get("approval_status") if estimate_doc else "",
            "original_finding": record.get("finding") or detail,
            "repair_work_status": status,
            "repair_work_status_label": repair_workspace_status_label(status),
            "record_status": record.get("status") or "Approved",
            "workflow_source_type": "finding",
            "linked_repair_record_id": record.get("linked_repair_record_id"),
            "repair_record_created_at": record.get("repair_record_created_at") or "",
            "created_at": record.get("created_at") or "",
            "approved_at": record.get("repair_work_updated_at") or record.get("updated_at") or record.get("created_at") or "",
            "repair_record_url": (
                f"/pro/customers/{record['customer_id']}/vehicles/{record['vehicle_id']}"
                f"/repairs/{record.get('linked_repair_record_id')}"
                if record.get("linked_repair_record_id")
                else ""
            ),
            "repair_prefill": {
                "repair_name": repair_work_title_from_finding(record),
                "repair_date": local_today().isoformat(),
                "mileage": current_vehicle_mileage,
                "notes": "\n".join(
                    part
                    for part in [
                        f"Source: {record.get('status') or 'Approved'} {('labor request' if normalize_finding_request_type(record.get('request_type')) == 'labor' else 'finding')}",
                        record.get("finding") or "",
                        record.get("recommendation") or "",
                        record.get("labor_reason") or "",
                    ]
                    if str(part or "").strip()
                ),
            },
            "updated_at": record.get("repair_work_updated_at") or record.get("created_at") or "",
            "mileage": record.get("mileage"),
            "url": f"/pro/customers/{record['customer_id']}/vehicles/{record['vehicle_id']}/findings/{record['id']}",
            "labor_total": finding_labor_total,
            "parts_total": finding_parts_total,
            "grand_total": finding_grand_total,
            "tracked_parts": [],
            "tracked_parts_total": 0,
            "tracked_parts_count": 0,
            "default_part_name": default_part_name_for_repair(None, title),
            "has_pricing": finding_grand_total > 0,
            "estimate_badge": "Estimate PDF" if estimate_doc else "",
        }
        if blueprint:
            item["blueprint"] = blueprint
            item["blueprint_summary"] = blueprint_summary(blueprint)
        if item.get("linked_repair_record_id"):
            linked_repair = repairs_by_id.get(int(item.get("linked_repair_record_id") or 0), {})
            item["tracked_parts"] = linked_repair.get("tracked_parts") or []
            item["tracked_parts_total"] = linked_repair.get("tracked_parts_total") or 0
            item["tracked_parts_count"] = linked_repair.get("tracked_parts_count") or 0
            item["default_part_name"] = linked_repair.get("default_part_name") or item["default_part_name"]
        item["parts_sources"] = repair_workspace_parts_sources(blueprint, vehicle, title)
        items.append(item)
    for record in approval_records:
        if normalize_approval_decision(record.get("customer_decision")) != "approved":
            continue
        status = record.get("repair_work_status") or "ready"
        try:
            status = normalize_repair_work_status(status)
        except HTTPException:
            status = "ready"
        if status == "completed":
            continue
        if record.get("linked_repair_record_id"):
            linked_repair_ids.add(int(record.get("linked_repair_record_id") or 0))
        title = repair_work_title_from_approval(record)
        detail = record.get("finding_description") or record.get("recommended_repair") or ""
        blueprint = get_repair_blueprint_for_work_item(title, detail, vehicle)
        item = {
            "source_type": "approval",
            "source_id": record.get("id"),
            "title": title,
            "detail": detail,
            "request_type_label": approval_request_type_label(record.get("request_type")),
            "approval_label": "Finding",
            "source_label": "Source: Finding",
            "customer_approval_label": "Customer Approved",
            "source_action_label": "View Source Finding",
            "source_action_url": f"/pro/customers/{record['customer_id']}/vehicles/{record['vehicle_id']}/approvals/{record['id']}",
            "original_finding": record.get("finding_description") or detail,
            "repair_work_status": status,
            "repair_work_status_label": repair_workspace_status_label(status),
            "record_status": "Approved",
            "workflow_source_type": "approval",
            "linked_repair_record_id": record.get("linked_repair_record_id"),
            "repair_record_created_at": record.get("repair_record_created_at") or "",
            "created_at": record.get("created_at") or "",
            "approved_at": record.get("decision_recorded_at") or record.get("updated_at") or record.get("created_at") or "",
            "repair_record_url": (
                f"/pro/customers/{record['customer_id']}/vehicles/{record['vehicle_id']}"
                f"/repairs/{record.get('linked_repair_record_id')}"
                if record.get("linked_repair_record_id")
                else ""
            ),
            "repair_prefill": {
                "repair_name": repair_work_title_from_approval(record),
                "repair_date": local_today().isoformat(),
                "mileage": current_vehicle_mileage,
                "notes": "\n".join(
                    part
                    for part in [
                        f"Source: Approved {approval_request_type_label(record.get('request_type')).lower()} request",
                        record.get("finding_description") or "",
                        record.get("recommended_repair") or "",
                        record.get("labor_reason") or "",
                        record.get("part_number") and f"Part Number: {record.get('part_number')}",
                    ]
                    if str(part or "").strip()
                ),
            },
            "updated_at": record.get("repair_work_updated_at") or record.get("decision_recorded_at") or record.get("updated_at") or "",
            "mileage": None,
            "url": f"/pro/customers/{record['customer_id']}/vehicles/{record['vehicle_id']}/approvals/{record['id']}",
            **repair_workspace_blank_totals(),
            "tracked_parts": [],
            "tracked_parts_total": 0,
            "tracked_parts_count": 0,
            "default_part_name": default_part_name_for_repair(None, title),
        }
        if blueprint:
            item["blueprint"] = blueprint
            item["blueprint_summary"] = blueprint_summary(blueprint)
        if item.get("linked_repair_record_id"):
            linked_repair = repairs_by_id.get(int(item.get("linked_repair_record_id") or 0), {})
            item["tracked_parts"] = linked_repair.get("tracked_parts") or []
            item["tracked_parts_total"] = linked_repair.get("tracked_parts_total") or 0
            item["tracked_parts_count"] = linked_repair.get("tracked_parts_count") or 0
            item["default_part_name"] = linked_repair.get("default_part_name") or item["default_part_name"]
        item["parts_sources"] = repair_workspace_parts_sources(blueprint, vehicle, title)
        items.append(item)
    for record in repair_records or []:
        repair_id = int(record.get("id") or 0)
        if not repair_id or repair_id in linked_repair_ids:
            continue
        if (record.get("status") or "") == "Completed":
            continue
        items.append(repair_workspace_item_from_repair_record(record, vehicle))
    status_rank = {"ready": 1, "in_progress": 2, "waiting_parts": 3, "completed": 4}
    items.sort(
        key=lambda item: (
            status_rank.get(item["repair_work_status"], 9),
            parse_datetime_value(item.get("updated_at")) or datetime.min,
            int(item.get("source_id") or 0),
        ),
        reverse=False,
    )
    return items


def build_completed_repair_work_items(
    repair_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    def completed_sort_datetime(item: dict[str, Any]) -> datetime:
        parsed_datetime = parse_datetime_value(item.get("completed_at"))
        if parsed_datetime:
            return parsed_datetime
        parsed_date = parse_date_value(item.get("repair_date"))
        if parsed_date:
            return datetime.combine(parsed_date, datetime.min.time())
        return datetime.min

    items: list[dict[str, Any]] = []
    for record in repair_records:
        if (record.get("status") or "") != "Completed":
            continue
        totals = repair_cost_totals(record)
        completion = record.get("completion") if isinstance(record.get("completion"), dict) else {}
        after_photo_urls = completion.get("after_repair_photo_urls") or record.get("after_repair_photo_urls") or []
        if not isinstance(after_photo_urls, list):
            after_photo_urls = []
        repair_id = int(record.get("id") or 0)
        items.append(
            {
                "source_type": "repair",
                "source_id": repair_id,
                "title": clean_service_quantity_title(record.get("repair_name") or "Repair"),
                "source_label": repair_workspace_source_label(record),
                "repair_work_status_label": "Completed",
                "completed_at": record.get("completed_at") or completion.get("completed_at") or "",
                "repair_date": record.get("repair_date") or "",
                "labor_total": totals["labor_total"],
                "parts_total": totals["parts_total"],
                "grand_total": totals["grand_total"],
                "tracked_parts": record.get("tracked_parts") or [],
                "tracked_parts_total": record.get("tracked_parts_total") or 0,
                "tracked_parts_count": record.get("tracked_parts_count") or 0,
                "has_pricing": any(
                    float(totals.get(key) or 0) > 0
                    for key in ("labor_total", "parts_total", "grand_total")
                ),
                "after_repair_photo_urls": [str(url) for url in after_photo_urls if str(url or "").strip()],
                "repair_record_url": (
                    f"/pro/customers/{record['customer_id']}/vehicles/{record['vehicle_id']}/repairs/{repair_id}"
                    if repair_id
                    else ""
                ),
                "is_invoiced": bool(record.get("is_invoiced")),
                "invoice_number": record.get("invoice_number") or "",
                "invoice_url": record.get("invoice_url") or "",
            }
        )
    items.sort(
        key=lambda item: (
            completed_sort_datetime(item),
            int(item.get("source_id") or 0),
        ),
        reverse=True,
    )
    return items


def finding_history_timeline_label(record: dict[str, Any]) -> str:
    finding = record.get("finding") or "Finding"
    is_labor = normalize_finding_request_type(record.get("request_type")) == "labor"
    title = (record.get("labor_description") if is_labor else finding) or finding
    event_type = record.get("event_type") or ""
    if event_type == "finding_created":
        if is_labor:
            return f"Labor Request Created: {title}"
        return f"Finding Created: {title}"
    if event_type == "customer_notes_updated":
        return f"Customer Notes updated: {title}"
    if event_type == "internal_notes_updated":
        return f"Internal Notes updated: {title}"
    if event_type == "repair_work_status_changed":
        new_status = record.get("new_status") or "ready"
        return f"Repair Workflow {repair_work_status_label(new_status)}: {title}"
    new_status = record.get("new_status") or ""
    if new_status:
        if is_labor:
            return f"Labor Request {new_status}: {title}"
        return f"Finding {new_status}: {title}"
    if is_labor:
        return f"Labor Request Status Updated: {title}"
    return f"Finding Status Updated: {title}"


def build_vehicle_timeline(
    customer_id: int,
    vehicle_id: int,
    vehicle: dict[str, Any],
    service_history_records: list[dict[str, Any]],
    invoice_records: list[dict[str, Any]] | None,
    findings_records: list[dict[str, Any]],
    finding_history_records: list[dict[str, Any]],
    customer_decision_logs: list[dict[str, Any]],
    approval_event_records: list[dict[str, Any]] | None = None,
    repair_checklist_events: list[dict[str, Any]] | None = None,
    repair_completion_events: list[dict[str, Any]] | None = None,
    estimate_document_records: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {
        "findings": {"key": "findings", "title": "Findings", "records": []},
        "estimates": {"key": "estimates", "title": "Estimates", "records": []},
        "repaired": {"key": "repaired", "title": "Completed Repairs", "records": []},
        "invoices": {"key": "invoices", "title": "Invoices", "records": []},
        "maintenance": {"key": "maintenance", "title": "Maintenance Services", "records": []},
        "approvals": {"key": "approvals", "title": "Approvals / Decisions", "records": []},
    }

    def add_record(group_key: str, record: dict[str, Any]) -> None:
        record["record_type_key"] = group_key
        groups[group_key]["records"].append(record)

    def sort_records(records: list[dict[str, Any]]) -> None:
        def sortable_id(value: Any) -> int:
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        records.sort(
            key=lambda record: (
                parse_date_value(record.get("date")) is not None,
                parse_date_value(record.get("date")) or date.min,
                parse_datetime_value(record.get("created_at")) or datetime.min,
                sortable_id(record.get("id")),
            ),
            reverse=True,
        )

    repaired_source_ids = {
        int(record.get("source_record_id") or 0)
        for record in service_history_records
        if (record.get("source_type") or "") == "repair" and record.get("source_record_id")
    }
    completion_events_by_repair_id = {
        int(record.get("repair_record_id") or 0): record
        for record in repair_completion_events or []
        if record.get("repair_record_id")
    }
    invoice_by_repair_id: dict[int, dict[str, Any]] = {}
    for invoice_record in invoice_records or []:
        repair_ids = [
            int(value)
            for value in str(invoice_record.get("repair_record_ids") or "").split(",")
            if str(value or "").strip().isdigit()
        ]
        fallback_repair_id = invoice_record.get("repair_record_id")
        if str(fallback_repair_id or "").strip().isdigit() and int(fallback_repair_id) not in repair_ids:
            repair_ids.append(int(fallback_repair_id))
        for repair_id in repair_ids:
            invoice_by_repair_id[repair_id] = invoice_record

    for record in service_history_records:
        source_type = record.get("source_type") or ""
        if source_type == "repair":
            repair_id = int(record.get("source_record_id") or 0)
            completion_event = completion_events_by_repair_id.get(repair_id, {})
            invoice_record = invoice_by_repair_id.get(repair_id, {})
            photo_urls = parse_stored_photo_paths(completion_event.get("after_repair_photo_paths"))
            add_record(
                "repaired",
                {
                    "id": record["id"],
                    "date": completion_event.get("completion_date") or completion_event.get("completed_at") or record.get("service_date") or record.get("created_at") or "",
                    "created_at": completion_event.get("completed_at") or record.get("created_at") or "",
                    "service_name": clean_service_quantity_title(record.get("service_name") or "Repair"),
                    "mileage": record.get("mileage"),
                    "source_label": repair_workspace_source_label(completion_event),
                    "total": record.get("total_cost"),
                    "invoice_number": invoice_record.get("invoice_number") or "",
                    "invoice_url": (
                        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/invoices/{invoice_record.get('id')}"
                        if invoice_record.get("id")
                        else ""
                    ),
                    "status_label": "Completed",
                    "photo_count": len(photo_urls),
                    "tracked_parts": completion_event.get("tracked_parts") or [],
                    "tracked_parts_total": completion_event.get("tracked_parts_total") or 0,
                    "tracked_parts_count": completion_event.get("tracked_parts_count") or 0,
                    "url": f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{repair_id}",
                    "action_label": "Open Repair Record",
                },
            )
        else:
            add_record(
                "maintenance",
                {
                    "id": record["id"],
                    "date": record.get("service_date") or "",
                    "created_at": record.get("created_at") or "",
                    "service_name": clean_service_quantity_title(record.get("service_name") or "Service"),
                    "mileage": record.get("mileage"),
                    "url": (
                        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/maintenance/{record.get('source_record_id')}"
                        if source_type == "maintenance"
                        else "#vehicle-timeline"
                    ),
                },
            )

    for record in estimate_document_records or []:
        total = format_currency(record.get("estimate_total")) if record.get("estimate_total") is not None else ""
        related_title = clean_service_quantity_title(record.get("related_title") or "Recommended Repair")
        service_name = "Repair Estimate"
        if related_title:
            service_name = f"{service_name} | {related_title}"
        if total:
            service_name = f"{service_name} | {total}"
        add_record(
            "estimates",
            {
                "id": record["id"],
                "date": record.get("estimate_date") or record.get("created_at") or "",
                "created_at": record.get("created_at") or "",
                "service_name": service_name,
                "customer_name": record.get("customer_name") or "",
                "vehicle_label": record.get("vehicle_label") or "",
                "related_title": related_title,
                "total": record.get("estimate_total"),
                "approval_status": record.get("approval_status") or "",
                "invoice_number": record.get("invoice_number") or "",
                "invoice_url": (
                    f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/invoices/{record.get('invoice_id')}"
                    if record.get("invoice_id")
                    else ""
                ),
                "mileage": None,
                "url": f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/estimates/{record['id']}/pdf",
                "edit_url": estimate_document_edit_url(customer_id, vehicle_id, record),
                "action_label": "Open Estimate PDF",
            },
        )

    for record in invoice_records or []:
        total = format_currency(record.get("grand_total")) or "$0.00"
        repair_title = clean_service_quantity_title(record.get("repair_name") or "Completed Repair")
        add_record(
            "invoices",
            {
                "id": record["id"],
                "date": record.get("created_at") or "",
                "created_at": record.get("created_at") or "",
                "service_name": f"Final Invoice {record.get('invoice_number') or ''} | {repair_title} | {total}".strip(),
                "mileage": None,
                "url": f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/invoices/{record['id']}",
                "action_label": "Open Final Invoice",
            },
        )

    for record in findings_records:
        status = record.get("status") or "Open"
        repair_status = record.get("repair_work_status") or ("completed" if status == "Completed" else "ready")
        try:
            repair_status = normalize_repair_work_status(repair_status)
        except HTTPException:
            repair_status = "ready"
        is_completed = status == "Completed" or repair_status == "completed"
        title = (
            (record.get("labor_description") or record.get("finding"))
            if normalize_finding_request_type(record.get("request_type")) == "labor"
            else record.get("finding")
        ) or record.get("recommendation") or "Finding"
        linked_repair_id = optional_int_value(record.get("linked_repair_record_id"))
        linked_invoice = invoice_by_repair_id.get(linked_repair_id or 0, {})
        if is_completed and not linked_repair_id:
            add_record(
                "repaired",
                {
                    "id": record["id"],
                    "date": record.get("repair_work_updated_at") or record.get("finding_date") or record.get("created_at") or "",
                    "created_at": record.get("repair_work_updated_at") or record.get("created_at") or "",
                    "service_name": title,
                    "mileage": record.get("mileage"),
                    "url": f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/findings/{record['id']}",
                },
            )
        add_record(
            "findings",
            {
                "id": record["id"],
                "date": record.get("finding_date") or "",
                "created_at": record.get("created_at") or "",
                "service_name": title,
                "finding": record.get("finding") or "",
                "recommendation": record.get("recommendation") or record.get("labor_description") or "",
                "severity": record.get("severity") or "",
                "status_label": status,
                "estimate_url": record.get("estimate_document_url") or "",
                "estimate_total": record.get("estimate_total"),
                "repair_url": (
                    f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{linked_repair_id}"
                    if linked_repair_id
                    else ""
                ),
                "invoice_number": linked_invoice.get("invoice_number") or "",
                "invoice_url": (
                    f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/invoices/{linked_invoice.get('id')}"
                    if linked_invoice.get("id")
                    else ""
                ),
                "mileage": record.get("mileage"),
                "url": f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/findings/{record['id']}",
            },
        )

    finding_status_history_keys = {
        (int(record.get("finding_id") or 0), record.get("new_status") or "")
        for record in finding_history_records
        if record.get("event_type") == "status_changed"
    }
    for record in customer_decision_logs:
        if (
            int(record.get("finding_id") or 0),
            record.get("decision_status") or "",
        ) in finding_status_history_keys:
            continue
        add_record(
            "approvals",
            {
                "id": record["id"],
                "date": record.get("created_at") or "",
                "created_at": record.get("created_at") or "",
                "service_name": f"Customer Decision: {record.get('decision_status') or 'Decision'}",
                "mileage": None,
                "url": "#recommendations-findings",
            },
        )

    for record in approval_event_records or []:
        event_type = record.get("event_type") or ""
        event_label = record.get("event_label") or "Approval Event"
        new_status = (record.get("new_status") or "").strip().lower().replace("-", "_")
        if event_type == "repair_work_status_changed" and new_status == "completed":
            add_record(
                "repaired",
                {
                    "id": record["id"],
                    "date": record.get("created_at") or "",
                    "created_at": record.get("created_at") or "",
                    "service_name": event_label,
                    "mileage": None,
                    "url": f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/approvals/{record.get('approval_id')}",
                },
            )
            continue
        add_record(
            "approvals",
            {
                "id": record["id"],
                "date": record.get("created_at") or "",
                "created_at": record.get("created_at") or "",
                "service_name": event_label,
                "mileage": None,
                "url": f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/approvals/{record.get('approval_id')}",
            },
        )

    for record in repair_completion_events or []:
        if int(record.get("repair_record_id") or 0) in repaired_source_ids:
            continue
        repair_id = int(record.get("repair_record_id") or 0)
        invoice_record = invoice_by_repair_id.get(repair_id, {})
        repair_title = clean_service_quantity_title(record.get("repair_name") or "Repair")
        photo_urls = parse_stored_photo_paths(record.get("after_repair_photo_paths"))
        add_record(
            "repaired",
            {
                "id": f"completion-{record['id']}",
                "date": record.get("completion_date") or record.get("completed_at") or record.get("created_at") or "",
                "created_at": record.get("completed_at") or record.get("created_at") or "",
                "service_name": repair_title,
                "mileage": record.get("mileage"),
                "source_label": repair_workspace_source_label(record),
                "total": record.get("total_cost"),
                "invoice_number": invoice_record.get("invoice_number") or "",
                "invoice_url": (
                    f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/invoices/{invoice_record.get('id')}"
                    if invoice_record.get("id")
                    else ""
                ),
                "status_label": "Completed",
                "photo_count": len(photo_urls),
                "tracked_parts": record.get("tracked_parts") or [],
                "tracked_parts_total": record.get("tracked_parts_total") or 0,
                "tracked_parts_count": record.get("tracked_parts_count") or 0,
                "url": f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{record.get('repair_record_id')}",
                "action_label": "Open Repair Record",
            },
        )
        if str(record.get("override_reason") or "").strip():
            add_record(
                "repaired",
                {
                    "id": f"completion-override-{record['id']}",
                    "date": record.get("completed_at") or "",
                    "created_at": record.get("completed_at") or record.get("created_at") or "",
                    "service_name": f"Completion Override Used: {repair_title}",
                    "mileage": None,
                    "url": f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{record.get('repair_record_id')}",
                },
            )

    for group in groups.values():
        sort_records(group["records"])
        group["count"] = len(group["records"])
    return [
        groups["findings"],
        groups["estimates"],
        groups["repaired"],
        groups["invoices"],
        groups["maintenance"],
        groups["approvals"],
    ]


def build_vehicle_history_summary(
    maintenance_records: list[dict[str, Any]],
) -> dict[str, Any]:
    latest_record = maintenance_records[0] if maintenance_records else {}
    return {
        "total_services": len(maintenance_records),
        "last_service": latest_record.get("service_type") or "",
        "last_service_date": latest_record.get("date_performed") or "",
        "last_recorded_mileage": latest_record.get("mileage_performed"),
    }


def build_repair_history_summary(
    repair_records: list[dict[str, Any]],
) -> dict[str, Any]:
    latest_record = repair_records[0] if repair_records else {}
    return {
        "total_repairs": len(repair_records),
        "last_repair": latest_record.get("repair_name") or "",
        "last_repair_date": latest_record.get("repair_date") or "",
        "last_repair_mileage": latest_record.get("mileage"),
        "lifetime_repair_spend": sum(
            float(record.get("total_cost") or 0) for record in repair_records
        ),
    }


def build_findings_summary(findings_records: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"Approved": 0, "Open": 0, "Declined": 0, "Deferred": 0}
    for record in findings_records:
        status = record.get("status") or "Open"
        if status in counts:
            counts[status] += 1
    return {
        "approved": counts["Approved"],
        "open": counts["Open"],
        "declined": counts["Declined"] + counts["Deferred"],
    }


def is_active_inspection_finding(record: dict[str, Any]) -> bool:
    status = record.get("status") or "Open"
    repair_status = str(record.get("repair_work_status") or "").strip().lower().replace("-", "_")
    return status in {"Open", "Approved", "Declined", "Deferred"} and repair_status != "completed"


def dashboard_card(
    title: str,
    count: int,
    helper: str,
    href: str,
    action_label: str,
) -> dict[str, Any]:
    return {
        "title": title,
        "count": count,
        "helper": helper,
        "href": href,
        "action_label": action_label,
    }


def build_pro_dashboard_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    today = local_today()
    ensure_customer_status_schema(conn)
    ensure_repair_records_schema(conn)
    ensure_findings_records_schema(conn)
    ensure_discrepancy_approvals_schema(conn)
    ensure_maintenance_records_schema(conn)
    ensure_invoices_schema(conn)
    ensure_repair_estimate_documents_schema(conn)

    active_customers_clause = "COALESCE(NULLIF(c.customer_status, ''), 'active') = 'active'"
    repair_counts = {
        "open": 0,
        "approved": 0,
        "in_progress": 0,
        "ready_to_complete": 0,
        "not_invoiced": 0,
        "already_invoiced": 0,
        "recently_invoiced": 0,
    }
    repair_columns = {row[1] for row in conn.execute("PRAGMA table_info(repair_records)").fetchall()}
    repair_status_select = (
        "rr.repair_work_status"
        if "repair_work_status" in repair_columns
        else "'' AS repair_work_status"
    )
    repair_rows = [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT
              rr.id,
              rr.status,
              {repair_status_select},
              rr.completed_at,
              CASE WHEN invoice_hits.repair_record_id IS NULL THEN 0 ELSE 1 END AS is_invoiced
            FROM repair_records rr
            JOIN customers c ON c.id = rr.customer_id
            LEFT JOIN (
              SELECT DISTINCT repair_record_id
              FROM invoice_items
              WHERE repair_record_id IS NOT NULL
              UNION
              SELECT repair_record_id
              FROM invoices
              WHERE repair_record_id IS NOT NULL
            ) invoice_hits ON invoice_hits.repair_record_id = rr.id
            WHERE {active_customers_clause}
              AND COALESCE(rr.status, '') NOT IN ('Declined', 'Deleted', 'Denied')
            """
        ).fetchall()
    ]
    for repair in repair_rows:
        status = repair.get("status") or "Open"
        repair_status = str(repair.get("repair_work_status") or "").strip().lower().replace("-", "_")
        is_invoiced = bool(repair.get("is_invoiced"))
        if status == "Completed":
            if is_invoiced:
                repair_counts["already_invoiced"] += 1
            else:
                repair_counts["not_invoiced"] += 1
            continue
        repair_counts["open"] += 1
        if repair_status == "in_progress":
            repair_counts["in_progress"] += 1
            repair_counts["ready_to_complete"] += 1
        elif repair_status == "waiting_parts":
            repair_counts["ready_to_complete"] += 1
        else:
            repair_counts["approved"] += 1

    recent_invoice_cutoff = (today - timedelta(days=14)).isoformat()
    repair_counts["recently_invoiced"] = int(
        conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM invoices i
            JOIN customers c ON c.id = i.customer_id
            WHERE {active_customers_clause}
              AND COALESCE(i.created_at, '') >= ?
            """,
            (recent_invoice_cutoff,),
        ).fetchone()["count"]
        or 0
    )

    finding_counts = {"open": 0, "estimate_ready": 0, "approved_not_converted": 0, "deferred_declined": 0}
    finding_counts["open"] = int(
        conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM findings_records fr
            JOIN customers c ON c.id = fr.customer_id
            WHERE {active_customers_clause}
              AND COALESCE(fr.status, 'Open') = 'Open'
            """
        ).fetchone()["count"]
        or 0
    )
    finding_counts["estimate_ready"] = int(
        conn.execute(
            f"""
            SELECT COUNT(DISTINCT fr.id) AS count
            FROM findings_records fr
            JOIN customers c ON c.id = fr.customer_id
            JOIN repair_estimate_documents red
              ON red.finding_id = fr.id
             AND red.customer_id = fr.customer_id
             AND red.vehicle_id = fr.vehicle_id
            WHERE {active_customers_clause}
            """
        ).fetchone()["count"]
        or 0
    )
    finding_counts["approved_not_converted"] = int(
        conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM findings_records fr
            JOIN customers c ON c.id = fr.customer_id
            WHERE {active_customers_clause}
              AND COALESCE(fr.status, '') = 'Approved'
              AND (fr.linked_repair_record_id IS NULL OR fr.linked_repair_record_id = 0)
            """
        ).fetchone()["count"]
        or 0
    )
    finding_counts["deferred_declined"] = int(
        conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM findings_records fr
            JOIN customers c ON c.id = fr.customer_id
            WHERE {active_customers_clause}
              AND COALESCE(fr.status, '') IN ('Deferred', 'Declined')
            """
        ).fetchone()["count"]
        or 0
    )

    maintenance_counts = {"overdue": 0, "due_soon": 0, "candidate": 0}
    shop_name = load_shop_name(conn)
    maintenance_rows = conn.execute(
        f"""
        SELECT
          m.*,
          c.first_name,
          c.last_name,
          c.phone,
          c.email,
          c.customer_status,
          v.year AS vehicle_year,
          v.make AS vehicle_make,
          v.model AS vehicle_model,
          v.mileage AS current_mileage,
          ? AS shop_name
        FROM maintenance_records m
        JOIN customers c ON c.id = m.customer_id
        JOIN customer_vehicles v ON v.id = m.vehicle_id
        WHERE {active_customers_clause}
        """,
        (shop_name,),
    ).fetchall()
    for row in maintenance_rows:
        status_key = build_follow_up_record(row, today).get("status_key")
        if status_key in maintenance_counts:
            maintenance_counts[status_key] += 1

    sections = [
        {
            "title": "Active Work",
            "empty": "No active repairs need attention right now.",
            "cards": [
                dashboard_card("Open Repairs", repair_counts["open"], "Active jobs not completed yet.", "/pro/customers#customer-list", "Find Repair"),
                dashboard_card("Approved Repairs", repair_counts["approved"], "Approved work ready to start.", "/pro/approvals", "Review Approvals"),
                dashboard_card("In Progress Repairs", repair_counts["in_progress"], "Jobs currently being worked.", "/pro/customers#customer-list", "View Active Jobs"),
                dashboard_card("Ready to Complete", repair_counts["ready_to_complete"], "Started work ready for final checks.", "/pro/customers#customer-list", "Complete Repairs"),
            ],
        },
        {
            "title": "Estimates & Approvals",
            "empty": "No findings or approval follow-ups are waiting.",
            "cards": [
                dashboard_card("Open Findings", finding_counts["open"], "Findings still waiting on a decision.", "/pro/customers", "Review Findings"),
                dashboard_card("Estimates Ready", finding_counts["estimate_ready"], "Saved estimate PDFs are available.", "/pro/customers", "Open Estimates"),
                dashboard_card("Approved Not Converted", finding_counts["approved_not_converted"], "Approved findings without repair records.", "/pro/approvals", "Review Queue"),
                dashboard_card("Deferred / Declined", finding_counts["deferred_declined"], "Customer decisions to revisit later.", "/pro/approvals", "Review Decisions"),
            ],
        },
        {
            "title": "Invoice Follow-Up",
            "empty": "No invoice follow-up is needed right now.",
            "cards": [
                dashboard_card("Not Invoiced Repairs", repair_counts["not_invoiced"], "Completed repairs still need an invoice.", "/pro/customers#customer-list", "Create Invoice"),
                dashboard_card("Recently Invoiced", repair_counts["recently_invoiced"], "Invoices created in the last 14 days.", "/pro/customers#customer-list", "View Invoices"),
                dashboard_card("Already Invoiced", repair_counts["already_invoiced"], "Completed jobs with final invoices.", "/pro/customers#customer-list", "View Invoices"),
            ],
        },
        {
            "title": "Maintenance Follow-Up",
            "empty": "No maintenance follow-ups are due right now.",
            "cards": [
                dashboard_card("Overdue Maintenance", maintenance_counts["overdue"], "Past due by mileage or date.", "/pro/follow-ups", "View Maintenance"),
                dashboard_card("Due Soon Maintenance", maintenance_counts["due_soon"], "Due within 30 days or 1,000 miles.", "/pro/follow-ups", "View Maintenance"),
                dashboard_card("Candidate Maintenance", maintenance_counts["candidate"], "Possible outreach within 90 days or 3,000 miles.", "/pro/follow-ups", "View Maintenance"),
            ],
        },
    ]
    active_work_total = sum(card["count"] for card in sections[0]["cards"])
    estimate_approval_total = sum(card["count"] for card in sections[1]["cards"])
    invoice_total = sum(card["count"] for card in sections[2]["cards"])
    maintenance_total = sum(card["count"] for card in sections[3]["cards"])
    attention_total = active_work_total + estimate_approval_total + invoice_total + maintenance_total

    return {
        "sections": sections,
        "attention_total": attention_total,
        "quick_actions": [
            {"label": "Add Customer", "href": "/pro/customers?mode=add#add-customer"},
            {"label": "View Customers", "href": "/pro/customers"},
            {"label": "Create Estimate", "href": "/estimator"},
            {"label": "Shop Calendar", "href": "/pro/calendar"},
            {"label": "Shop Settings", "href": "/pro/shop-settings"},
            {"label": "View Active Jobs", "href": "/pro/customers#customer-list"},
        ],
    }


def load_customer_vehicle(
    conn: sqlite3.Connection, customer_id: int, vehicle_id: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    ensure_customer_status_schema(conn)
    customer = row_to_dict(
        conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    )
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    vehicle = row_to_dict(
        conn.execute(
            """
            SELECT *
            FROM customer_vehicles
            WHERE id = ? AND customer_id = ?
            """,
            (vehicle_id, customer_id),
        ).fetchone()
    )
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return customer, vehicle


@router.get("", response_class=HTMLResponse)
def pro_welcome(request: Request):
    return templates.TemplateResponse(
        "pro/welcome_intro.html",
        {
            "request": request,
        },
    )


@router.get("/dashboard", response_class=HTMLResponse)
def pro_dashboard(request: Request):
    conn = crm_db_conn()
    try:
        dashboard = build_pro_dashboard_summary(conn)
    finally:
        conn.close()

    return templates.TemplateResponse(
        "pro/dashboard.html",
        {
            "request": request,
            "dashboard": dashboard,
        },
    )


@router.get("/visual-references", response_class=HTMLResponse)
def pro_visual_references(request: Request):
    conn = crm_db_conn()
    try:
        seed_visual_references(conn)
        records = [
            dict(row)
            for row in conn.execute(
                """
                SELECT
                  r.*,
                  (SELECT COUNT(*) FROM visual_reference_images i WHERE i.visual_reference_id = r.id) AS image_count,
                  (SELECT COUNT(*) FROM visual_reference_specs s WHERE s.visual_reference_id = r.id) AS spec_count,
                  (SELECT COUNT(*) FROM visual_reference_oem_parts p WHERE p.visual_reference_id = r.id) AS oem_part_count
                FROM visual_reference_records r
                ORDER BY r.vehicle_identifier ASC, r.service_type ASC, r.id ASC
                """
            ).fetchall()
        ]
    finally:
        conn.close()

    return templates.TemplateResponse(
        "pro/visual_references.html",
        {
            "request": request,
            "records": records,
        },
    )


@router.post("/visual-references")
async def pro_visual_reference_create(request: Request):
    form = await read_form_data(request)
    vehicle_identifier = form.get("vehicle_identifier", "")
    service_type = normalize_visual_reference_service(form.get("service_type"))
    if not vehicle_identifier.strip() or not service_type:
        raise HTTPException(status_code=400, detail="Vehicle identifier and service type are required")
    now = datetime.now(timezone.utc).isoformat()
    conn = crm_db_conn()
    try:
        ensure_visual_reference_schema(conn)
        cur = conn.execute(
            """
            INSERT INTO visual_reference_records (
              vehicle_identifier, service_type, title, quick_reference, created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                vehicle_identifier.strip(),
                service_type,
                form.get("title", ""),
                form.get("quick_reference", ""),
                now,
            ),
        )
        visual_reference_id = int(cur.lastrowid)
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(f"/pro/visual-references/{visual_reference_id}", status_code=303)


@router.get("/visual-references/{visual_reference_id}", response_class=HTMLResponse)
def pro_visual_reference_detail(request: Request, visual_reference_id: int):
    conn = crm_db_conn()
    try:
        reference = load_visual_reference_record(conn, visual_reference_id)
    finally:
        conn.close()
    return templates.TemplateResponse(
        "pro/visual_reference_detail.html",
        {
            "request": request,
            "reference": reference,
            "image_types": public_visual_reference_image_types(),
        },
    )


@router.get("/visual-references/{visual_reference_id}/repair-map", response_class=HTMLResponse)
def pro_visual_reference_repair_map(request: Request, visual_reference_id: int):
    conn = crm_db_conn()
    try:
        reference = load_visual_reference_record(conn, visual_reference_id)
    finally:
        conn.close()
    main_image = next(
        (image for image in reference["images"] if image["image_type"] == "component_location"),
        reference["images"][0] if reference["images"] else None,
    )
    return templates.TemplateResponse(
        "pro/visual_reference_repair_map.html",
        {
            "request": request,
            "reference": reference,
            "main_image": main_image,
        },
    )


@router.post("/visual-references/{visual_reference_id}")
async def pro_visual_reference_update(request: Request, visual_reference_id: int):
    form = await read_form_data(request)
    vehicle_identifier = form.get("vehicle_identifier", "")
    service_type = normalize_visual_reference_service(form.get("service_type"))
    if not vehicle_identifier.strip() or not service_type:
        raise HTTPException(status_code=400, detail="Vehicle identifier and service type are required")
    conn = crm_db_conn()
    try:
        load_visual_reference_record(conn, visual_reference_id)
        conn.execute(
            """
            UPDATE visual_reference_records
            SET vehicle_identifier = ?, service_type = ?, title = ?, quick_reference = ?
            WHERE id = ?
            """,
            (
                vehicle_identifier.strip(),
                service_type,
                form.get("title", ""),
                form.get("quick_reference", ""),
                visual_reference_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(f"/pro/visual-references/{visual_reference_id}", status_code=303)


@router.post("/visual-references/{visual_reference_id}/delete")
async def pro_visual_reference_delete(visual_reference_id: int):
    conn = crm_db_conn()
    try:
        load_visual_reference_record(conn, visual_reference_id)
        conn.execute("DELETE FROM visual_reference_hotspots WHERE visual_reference_id = ?", (visual_reference_id,))
        conn.execute("DELETE FROM visual_reference_images WHERE visual_reference_id = ?", (visual_reference_id,))
        conn.execute("DELETE FROM visual_reference_specs WHERE visual_reference_id = ?", (visual_reference_id,))
        conn.execute("DELETE FROM visual_reference_oem_parts WHERE visual_reference_id = ?", (visual_reference_id,))
        conn.execute("DELETE FROM visual_reference_records WHERE id = ?", (visual_reference_id,))
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse("/pro/visual-references", status_code=303)


@router.post("/visual-references/{visual_reference_id}/images")
async def pro_visual_reference_image_create(request: Request, visual_reference_id: int):
    form, files = await read_multipart_form_data(request)
    image_type = normalize_visual_reference_image_type(form.get("image_type"))
    image_path = save_visual_reference_upload(files.get("image_file")) or form.get("image_path", "").strip()
    if not image_path:
        raise HTTPException(status_code=400, detail="Upload an image or provide an image path")
    conn = crm_db_conn()
    try:
        load_visual_reference_record(conn, visual_reference_id)
        conn.execute(
            """
            INSERT INTO visual_reference_images (
              visual_reference_id, image_type, image_path, caption
            )
            VALUES (?, ?, ?, ?)
            """,
            (visual_reference_id, image_type, image_path, form.get("caption", "")),
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(f"/pro/visual-references/{visual_reference_id}#images", status_code=303)


@router.post("/visual-references/{visual_reference_id}/images/{image_id}")
async def pro_visual_reference_image_update(request: Request, visual_reference_id: int, image_id: int):
    form, files = await read_multipart_form_data(request)
    image_type = normalize_visual_reference_image_type(form.get("image_type"))
    uploaded_path = save_visual_reference_upload(files.get("image_file"))
    conn = crm_db_conn()
    try:
        load_visual_reference_record(conn, visual_reference_id)
        image = load_visual_reference_child(conn, "visual_reference_images", visual_reference_id, image_id)
        image_path = uploaded_path or form.get("image_path", "").strip() or image.get("image_path") or ""
        if not image_path:
            raise HTTPException(status_code=400, detail="Image path is required")
        conn.execute(
            """
            UPDATE visual_reference_images
            SET image_type = ?, image_path = ?, caption = ?
            WHERE id = ? AND visual_reference_id = ?
            """,
            (image_type, image_path, form.get("caption", ""), image_id, visual_reference_id),
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(f"/pro/visual-references/{visual_reference_id}#images", status_code=303)


@router.post("/visual-references/{visual_reference_id}/images/{image_id}/delete")
async def pro_visual_reference_image_delete(visual_reference_id: int, image_id: int):
    conn = crm_db_conn()
    try:
        load_visual_reference_record(conn, visual_reference_id)
        load_visual_reference_child(conn, "visual_reference_images", visual_reference_id, image_id)
        conn.execute(
            "DELETE FROM visual_reference_images WHERE id = ? AND visual_reference_id = ?",
            (image_id, visual_reference_id),
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(f"/pro/visual-references/{visual_reference_id}#images", status_code=303)


@router.post("/visual-references/{visual_reference_id}/specs")
async def pro_visual_reference_spec_create(request: Request, visual_reference_id: int):
    form = await read_form_data(request)
    if not form.get("spec_name", "").strip() or not form.get("spec_value", "").strip():
        raise HTTPException(status_code=400, detail="Spec name and value are required")
    conn = crm_db_conn()
    try:
        load_visual_reference_record(conn, visual_reference_id)
        conn.execute(
            """
            INSERT INTO visual_reference_specs (
              visual_reference_id, spec_name, spec_value, spec_unit
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                visual_reference_id,
                form.get("spec_name", ""),
                form.get("spec_value", ""),
                form.get("spec_unit", ""),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(f"/pro/visual-references/{visual_reference_id}#specs", status_code=303)


@router.post("/visual-references/{visual_reference_id}/specs/{spec_id}")
async def pro_visual_reference_spec_update(request: Request, visual_reference_id: int, spec_id: int):
    form = await read_form_data(request)
    if not form.get("spec_name", "").strip() or not form.get("spec_value", "").strip():
        raise HTTPException(status_code=400, detail="Spec name and value are required")
    conn = crm_db_conn()
    try:
        load_visual_reference_record(conn, visual_reference_id)
        load_visual_reference_child(conn, "visual_reference_specs", visual_reference_id, spec_id)
        conn.execute(
            """
            UPDATE visual_reference_specs
            SET spec_name = ?, spec_value = ?, spec_unit = ?
            WHERE id = ? AND visual_reference_id = ?
            """,
            (
                form.get("spec_name", ""),
                form.get("spec_value", ""),
                form.get("spec_unit", ""),
                spec_id,
                visual_reference_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(f"/pro/visual-references/{visual_reference_id}#specs", status_code=303)


@router.post("/visual-references/{visual_reference_id}/specs/{spec_id}/delete")
async def pro_visual_reference_spec_delete(visual_reference_id: int, spec_id: int):
    conn = crm_db_conn()
    try:
        load_visual_reference_record(conn, visual_reference_id)
        load_visual_reference_child(conn, "visual_reference_specs", visual_reference_id, spec_id)
        conn.execute(
            "DELETE FROM visual_reference_specs WHERE id = ? AND visual_reference_id = ?",
            (spec_id, visual_reference_id),
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(f"/pro/visual-references/{visual_reference_id}#specs", status_code=303)


@router.post("/visual-references/{visual_reference_id}/oem-parts")
async def pro_visual_reference_oem_part_create(request: Request, visual_reference_id: int):
    form = await read_form_data(request)
    if not form.get("part_name", "").strip() or not form.get("oem_part_number", "").strip():
        raise HTTPException(status_code=400, detail="Part name and OEM part number are required")
    conn = crm_db_conn()
    try:
        load_visual_reference_record(conn, visual_reference_id)
        conn.execute(
            """
            INSERT INTO visual_reference_oem_parts (
              visual_reference_id, part_name, oem_part_number, future_parts_intelligence_id
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                visual_reference_id,
                form.get("part_name", ""),
                form.get("oem_part_number", ""),
                optional_int(form, "future_parts_intelligence_id"),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(f"/pro/visual-references/{visual_reference_id}#oem-parts", status_code=303)


@router.post("/visual-references/{visual_reference_id}/oem-parts/{part_id}")
async def pro_visual_reference_oem_part_update(request: Request, visual_reference_id: int, part_id: int):
    form = await read_form_data(request)
    if not form.get("part_name", "").strip() or not form.get("oem_part_number", "").strip():
        raise HTTPException(status_code=400, detail="Part name and OEM part number are required")
    conn = crm_db_conn()
    try:
        load_visual_reference_record(conn, visual_reference_id)
        load_visual_reference_child(conn, "visual_reference_oem_parts", visual_reference_id, part_id)
        conn.execute(
            """
            UPDATE visual_reference_oem_parts
            SET part_name = ?, oem_part_number = ?, future_parts_intelligence_id = ?
            WHERE id = ? AND visual_reference_id = ?
            """,
            (
                form.get("part_name", ""),
                form.get("oem_part_number", ""),
                optional_int(form, "future_parts_intelligence_id"),
                part_id,
                visual_reference_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(f"/pro/visual-references/{visual_reference_id}#oem-parts", status_code=303)


@router.post("/visual-references/{visual_reference_id}/oem-parts/{part_id}/delete")
async def pro_visual_reference_oem_part_delete(visual_reference_id: int, part_id: int):
    conn = crm_db_conn()
    try:
        load_visual_reference_record(conn, visual_reference_id)
        load_visual_reference_child(conn, "visual_reference_oem_parts", visual_reference_id, part_id)
        conn.execute(
            "DELETE FROM visual_reference_oem_parts WHERE id = ? AND visual_reference_id = ?",
            (part_id, visual_reference_id),
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(f"/pro/visual-references/{visual_reference_id}#oem-parts", status_code=303)


@router.get("/approvals", response_class=HTMLResponse)
def pro_approvals(request: Request):
    conn = crm_db_conn()
    try:
        ensure_customer_status_schema(conn)
        ensure_discrepancy_approvals_schema(conn)
        records = [
            dict(row)
            for row in conn.execute(
                """
                SELECT
                  a.*,
                  c.first_name,
                  c.last_name,
                  c.phone,
                  c.email,
                  c.customer_status,
                  v.year AS vehicle_year,
                  v.make AS vehicle_make,
                  v.model AS vehicle_model
                FROM discrepancy_approvals a
                JOIN customers c ON c.id = a.customer_id
                JOIN customer_vehicles v ON v.id = a.vehicle_id
                ORDER BY
                  CASE a.customer_decision
                    WHEN 'pending' THEN 0
                    WHEN 'approved' THEN 1
                    WHEN 'deferred' THEN 2
                    ELSE 3
                  END,
                  a.created_at DESC,
                  a.id DESC
                """
            ).fetchall()
        ]
        vehicle_options = [
            dict(row)
            for row in conn.execute(
                """
                SELECT
                  v.id AS vehicle_id,
                  v.customer_id,
                  v.year AS vehicle_year,
                  v.make AS vehicle_make,
                  v.model AS vehicle_model,
                  c.first_name,
                  c.last_name,
                  c.phone
                FROM customer_vehicles v
                JOIN customers c ON c.id = v.customer_id
                ORDER BY c.last_name, c.first_name, v.year DESC, v.make, v.model
                """
            ).fetchall()
        ]
    finally:
        conn.close()

    for record in records:
        record["customer_name"] = customer_name(record)
        record["vehicle_label"] = vehicle_label(record)
        record["request_type_label"] = approval_request_type_label(record.get("request_type"))
        record["vehicle_url"] = f"/pro/customers/{record['customer_id']}/vehicles/{record['vehicle_id']}"
        record["detail_url"] = (
            f"/pro/customers/{record['customer_id']}/vehicles/{record['vehicle_id']}"
            f"/approvals/{record['id']}"
        )
    for option in vehicle_options:
        option["customer_name"] = customer_name(option)
        option["vehicle_label"] = vehicle_label(option)

    grouped = group_approval_records(records)

    return templates.TemplateResponse(
        "pro/approvals.html",
        {
            "request": request,
            "groups": grouped,
            "summary": {key: len(items) for key, items in grouped.items()},
            "vehicle_options": vehicle_options,
        },
    )


@router.get("/follow-ups", response_class=HTMLResponse)
def pro_follow_ups(request: Request):
    today = local_today()
    conn = crm_db_conn()
    try:
        ensure_customer_status_schema(conn)
        ensure_maintenance_records_schema(conn)
        ensure_maintenance_reminder_events_schema(conn)
        ensure_service_history_records_schema(conn)
        maintenance_records = [
            dict(row)
            for row in conn.execute(
            """
            SELECT
              m.*,
              c.first_name,
              c.last_name,
              c.phone,
              c.email,
              c.customer_status,
              v.year AS vehicle_year,
              v.make AS vehicle_make,
              v.model AS vehicle_model,
              v.mileage AS current_mileage
            FROM maintenance_records m
            JOIN customers c ON c.id = m.customer_id
            JOIN customer_vehicles v ON v.id = m.vehicle_id
            WHERE COALESCE(NULLIF(c.customer_status, ''), 'active') = 'active'
            ORDER BY
              m.customer_id,
              m.vehicle_id,
              m.service_type,
              m.date_performed DESC,
              m.mileage_performed DESC,
              m.id DESC
            """,
            ).fetchall()
        ]
        service_history_records = [
            dict(row)
            for row in conn.execute(
                """
                SELECT shr.*
                FROM service_history_records shr
                JOIN customers c ON c.id = shr.customer_id
                WHERE COALESCE(NULLIF(c.customer_status, ''), 'active') = 'active'
                ORDER BY shr.vehicle_id, shr.service_date DESC, shr.mileage DESC, shr.id DESC
                """
            ).fetchall()
        ]
        maintenance_reminder_events = load_maintenance_reminder_events_map(
            conn,
            {int(record.get("id") or 0) for record in maintenance_records if record.get("id")},
        )
        sender_context = attach_shop_booking_context(load_shop_profile_context(conn), request)
    finally:
        conn.close()

    grouped = {
        "overdue": [],
        "due_soon": [],
        "sent_waiting": [],
    }
    service_history_by_vehicle: dict[int, list[dict[str, Any]]] = {}
    for record in service_history_records:
        vehicle_id = optional_int_value(record.get("vehicle_id"))
        if vehicle_id is not None:
            service_history_by_vehicle.setdefault(vehicle_id, []).append(record)

    maintenance_by_vehicle: dict[int, list[dict[str, Any]]] = {}
    for record in maintenance_records:
        vehicle_id = optional_int_value(record.get("vehicle_id"))
        if vehicle_id is not None:
            maintenance_by_vehicle.setdefault(vehicle_id, []).append(record)

    for vehicle_id, vehicle_maintenance_records in maintenance_by_vehicle.items():
        if not vehicle_maintenance_records:
            continue
        sample = vehicle_maintenance_records[0]
        vehicle = {
            "year": sample.get("vehicle_year"),
            "make": sample.get("vehicle_make"),
            "model": sample.get("vehicle_model"),
            "mileage": sample.get("current_mileage"),
        }
        customer = {
            "first_name": sample.get("first_name"),
            "last_name": sample.get("last_name"),
        }
        driving_rate = estimate_vehicle_driving_rate(
            vehicle_maintenance_records,
            service_history_by_vehicle.get(vehicle_id, []),
            vehicle,
            today,
        )
        latest_records = latest_maintenance_records_by_vehicle_service(vehicle_maintenance_records)
        annotated_records = annotate_vehicle_maintenance_records(
            latest_records,
            vehicle,
            customer,
            today,
            driving_rate,
            sender_context,
        )
        attach_maintenance_reminder_events(annotated_records, maintenance_reminder_events)
        for record in annotated_records:
            bucket = maintenance_reminder_follow_up_bucket(record, today)
            if bucket not in grouped:
                continue
            item = follow_up_item_from_maintenance_record(record)
            if bucket == "sent_waiting":
                latest_event = record.get("latest_automatic_reminder_event") or {}
                item["queue_status"] = "Reminder copied" if latest_event.get("status") == "copied" else "Reminder prepared"
                item["queue_status_key"] = "sent_waiting"
            grouped[bucket].append(
                item
            )

    for items in grouped.values():
        items.sort(
            key=lambda item: (
                str(item.get("customer_name") or ""),
                str(item.get("vehicle_label") or ""),
                str(item.get("service_type") or ""),
            )
        )

    summary = {key: len(items) for key, items in grouped.items()}

    return templates.TemplateResponse(
        "pro/follow_ups.html",
        {
            "request": request,
            "today": today.isoformat(),
            "groups": grouped,
            "summary": summary,
            "shop_profile": sender_context,
        },
    )


@router.get("/shop-settings", response_class=HTMLResponse)
def pro_shop_settings(request: Request, saved: str = ""):
    conn = crm_db_conn()
    try:
        profile = attach_shop_booking_context(load_shop_profile_context(conn), request)
    finally:
        conn.close()

    return templates.TemplateResponse(
        "pro/shop_settings.html",
        {
            "request": request,
            "profile": profile,
            "saved": saved == "1",
        },
    )


@router.post("/shop-settings")
async def pro_shop_settings_save(request: Request):
    form = await read_form_data(request)
    conn = crm_db_conn()
    try:
        save_shop_settings(conn, form)
    finally:
        conn.close()
    return RedirectResponse("/pro/shop-settings?saved=1", status_code=303)


@router.get("/shop-schedule", response_class=HTMLResponse)
def pro_shop_schedule(request: Request, saved: str = "", closed_saved: str = ""):
    conn = crm_db_conn()
    try:
        availability = load_shop_availability(conn)
        closed_days = load_closed_days(conn)
        profile = attach_shop_booking_context(load_shop_profile_context(conn), request)
    finally:
        conn.close()
    appointment_length = int(availability[0].get("appointment_length_minutes") or 60) if availability else 60
    buffer_minutes = int(availability[0].get("buffer_minutes") or 0) if availability else 0
    return templates.TemplateResponse(
        "pro/shop_schedule.html",
        {
            "request": request,
            "availability": availability,
            "closed_days": closed_days,
            "profile": profile,
            "appointment_length": appointment_length,
            "buffer_minutes": buffer_minutes,
            "appointment_length_options": APPOINTMENT_LENGTH_OPTIONS,
            "buffer_options": APPOINTMENT_BUFFER_OPTIONS,
            "saved": saved == "1",
            "closed_saved": closed_saved == "1",
        },
    )


@router.post("/shop-schedule")
async def pro_shop_schedule_save(request: Request):
    form = await read_form_data(request)
    appointment_length = optional_int_value(form.get("appointment_length_minutes")) or 60
    buffer_minutes = optional_int_value(form.get("buffer_minutes")) or 0
    availability = []
    for day in SHOP_SCHEDULE_DAYS:
        index = day["index"]
        availability.append(
            {
                "day_of_week": index,
                "is_open": form.get(f"is_open_{index}") == "1",
                "start_time": form.get(f"start_time_{index}", "09:00"),
                "end_time": form.get(f"end_time_{index}", "17:00"),
            }
        )
    conn = crm_db_conn()
    try:
        save_shop_availability(
            conn,
            availability,
            appointment_length_minutes=appointment_length,
            buffer_minutes=buffer_minutes,
        )
    finally:
        conn.close()
    return RedirectResponse("/pro/shop-schedule?saved=1", status_code=303)


@router.post("/shop-schedule/closed-days")
async def pro_shop_schedule_closed_day_add(request: Request):
    form = await read_form_data(request)
    conn = crm_db_conn()
    try:
        create_closed_day(conn, form.get("closed_date", ""), form.get("reason", ""))
    finally:
        conn.close()
    return RedirectResponse("/pro/shop-schedule?closed_saved=1", status_code=303)


@router.post("/shop-schedule/closed-days/{closed_day_id}/delete")
def pro_shop_schedule_closed_day_delete(closed_day_id: int):
    conn = crm_db_conn()
    try:
        delete_closed_day(conn, closed_day_id)
    finally:
        conn.close()
    return RedirectResponse("/pro/shop-schedule", status_code=303)


@router.get("/calendar", response_class=HTMLResponse)
def pro_calendar(request: Request, saved: str = "", notice: str = "", error: str = ""):
    conn = crm_db_conn()
    try:
        profile = load_shop_profile_context(conn)
        appointments = attach_appointment_customer_messages(
            load_service_appointments(conn),
            profile,
        )
    finally:
        conn.close()
    return templates.TemplateResponse(
        "pro/calendar.html",
        {
            "request": request,
            "groups": group_booking_review_appointments(appointments),
            "status_options": APPOINTMENT_STATUS_OPTIONS,
            "saved": saved == "1",
            "error": error,
            "notice": {
                "confirmed": "Appointment confirmed.",
                "handled": "Booking request marked as handled.",
                "declined": "Booking request declined.",
                "rescheduled": "Appointment rescheduled.",
                "cancelled": "Appointment canceled.",
            }.get(notice, ""),
        },
    )


@router.post("/calendar")
async def pro_calendar_add(request: Request):
    form = await read_form_data(request)
    conn = crm_db_conn()
    try:
        available, warning = is_booking_time_available(
            conn,
            form.get("requested_date", ""),
            form.get("requested_time", ""),
        )
        generated_times = available_booking_times(conn, form.get("requested_date", ""))
        if available:
            available = form.get("requested_time", "") in {
                slot["value"] for slot in generated_times.get("times", [])
            }
        if not available:
            notice = warning or generated_times.get("message") or (
                "No appointment times are available for this day. Please choose another day."
            )
            return RedirectResponse(f"/pro/calendar?{urlencode({'error': notice})}", status_code=303)
        create_service_appointment(
            conn,
            {
                "customer_name": form.get("customer_name", ""),
                "customer_phone": form.get("customer_phone", ""),
                "vehicle_label": form.get("vehicle_label", ""),
                "service_name": form.get("service_name", ""),
                "requested_date": form.get("requested_date", ""),
                "requested_time": form.get("requested_time", ""),
                "notes": form.get("notes", ""),
                "status": form.get("status", "Requested"),
                "source": "manual",
            },
        )
    finally:
        conn.close()
    return RedirectResponse("/pro/calendar?saved=1", status_code=303)


@router.post("/calendar/{appointment_id}/status")
async def pro_calendar_status_update(request: Request, appointment_id: int):
    form = await read_form_data(request)
    status = form.get("status", "Requested")
    conn = crm_db_conn()
    try:
        update_service_appointment_status(conn, appointment_id, status)
    finally:
        conn.close()
    notice = {"Confirmed": "confirmed", "Handled": "handled", "Declined": "declined"}.get(status, "")
    suffix = f"?notice={notice}" if notice else ""
    return RedirectResponse(f"/pro/calendar{suffix}", status_code=303)


@router.post("/calendar/{appointment_id}/reschedule")
async def pro_calendar_reschedule(request: Request, appointment_id: int):
    form = await read_form_data(request)
    conn = crm_db_conn()
    try:
        try:
            reschedule_service_appointment(
                conn,
                appointment_id,
                form.get("requested_date", ""),
                form.get("requested_time", ""),
            )
        except HTTPException as exc:
            return RedirectResponse(
                f"/pro/calendar?{urlencode({'error': str(exc.detail)})}",
                status_code=303,
            )
    finally:
        conn.close()
    return RedirectResponse("/pro/calendar?notice=rescheduled", status_code=303)


@router.post("/calendar/{appointment_id}/cancel")
def pro_calendar_cancel(appointment_id: int):
    conn = crm_db_conn()
    try:
        appointment = load_service_appointment(conn, appointment_id)
        if not appointment or appointment.get("status") != "Confirmed":
            return RedirectResponse(
                f"/pro/calendar?{urlencode({'error': 'Confirmed appointment not found.'})}",
                status_code=303,
            )
        update_service_appointment_status(conn, appointment_id, "Cancelled")
    finally:
        conn.close()
    return RedirectResponse("/pro/calendar?notice=cancelled", status_code=303)


@public_router.get("/book/{shop_slug}/available-times", response_class=JSONResponse)
def public_booking_available_times(
    shop_slug: str,
    date: str = "",
    exclude_appointment_id: int | None = None,
):
    conn = crm_db_conn()
    try:
        result = available_booking_times(conn, date, exclude_appointment_id)
    finally:
        conn.close()
    return JSONResponse(result)


@public_router.get("/book/{shop_slug}/available-dates", response_class=JSONResponse)
def public_booking_available_dates(
    shop_slug: str,
    month: str = "",
    exclude_appointment_id: int | None = None,
):
    conn = crm_db_conn()
    try:
        result = booking_availability_for_month(conn, month, exclude_appointment_id)
    finally:
        conn.close()
    return JSONResponse(result)


@public_router.get("/book/{shop_slug}", response_class=HTMLResponse)
def public_booking_page(request: Request, shop_slug: str, success: str = "", warning: str = ""):
    conn = crm_db_conn()
    try:
        profile = attach_shop_booking_context(load_shop_profile_context(conn), request)
        booking_schedule = public_booking_schedule(conn)
    finally:
        conn.close()
    return templates.TemplateResponse(
        "booking.html",
        {
            "request": request,
            "profile": profile,
            "shop_slug": profile["booking_slug"],
            "booking_schedule": booking_schedule,
            "success": success == "1",
            "warning": warning,
        },
    )


@public_router.post("/book/{shop_slug}", response_class=HTMLResponse)
async def public_booking_submit(request: Request, shop_slug: str):
    form = await read_form_data(request)
    warning = ""
    conn = crm_db_conn()
    try:
        profile = attach_shop_booking_context(load_shop_profile_context(conn), request)
        booking_schedule = public_booking_schedule(conn)
        required_fields = ("customer_name", "customer_phone", "vehicle_label", "service_name")
        if any(not str(form.get(field) or "").strip() for field in required_fields):
            return templates.TemplateResponse(
                "booking.html",
                {
                    "request": request,
                    "profile": profile,
                    "shop_slug": profile["booking_slug"],
                    "booking_schedule": booking_schedule,
                    "success": False,
                    "warning": "Please complete the required fields and try again.",
                    "form": form,
                },
                status_code=400,
            )
        closed, closed_reason = is_closed_booking_day(conn, form.get("requested_date", ""))
        if closed:
            warning = closed_reason or "The shop is closed on this day. Please choose another day."
            return templates.TemplateResponse(
                "booking.html",
                {
                    "request": request,
                    "profile": profile,
                    "shop_slug": profile["booking_slug"],
                    "booking_schedule": booking_schedule,
                    "success": False,
                    "warning": warning,
                    "form": form,
                },
                status_code=400,
            )
        available, availability_warning = is_booking_time_available(
            conn,
            form.get("requested_date", ""),
            form.get("requested_time", ""),
            form.get("appointment_length_minutes", ""),
        )
        if available:
            generated_times = available_booking_times(conn, form.get("requested_date", ""))
            available = form.get("requested_time", "") in {
                slot["value"] for slot in generated_times.get("times", [])
            }
            if not available:
                availability_warning = (
                    "This time is not available based on the shop's schedule. "
                    "Please choose another available time."
                )
        if not available:
            warning = availability_warning or "This time is not available based on the shop's schedule. Please choose another available time."
            return templates.TemplateResponse(
                "booking.html",
                {
                    "request": request,
                    "profile": profile,
                    "shop_slug": profile["booking_slug"],
                    "booking_schedule": booking_schedule,
                    "success": False,
                    "warning": warning,
                    "form": form,
                },
                status_code=400,
            )
        create_service_appointment(
            conn,
            {
                "customer_name": form.get("customer_name", ""),
                "customer_phone": form.get("customer_phone", ""),
                "customer_email": form.get("customer_email", ""),
                "vehicle_label": form.get("vehicle_label", ""),
                "service_name": form.get("service_name", ""),
                "requested_date": form.get("requested_date", ""),
                "requested_time": form.get("requested_time", ""),
                "notes": form.get("notes", ""),
                "source": "customer_booking",
                "status": "Requested",
            },
        )
    finally:
        conn.close()
    return RedirectResponse(f"/book/{profile['booking_slug']}?success=1", status_code=303)


@router.get("/customers", response_class=HTMLResponse)
def pro_customers(request: Request, q: str = "", status: str = "active"):
    search = q.strip()
    status_filter = normalize_customer_status(status)
    conn = crm_db_conn()
    try:
        ensure_customer_status_schema(conn)
        status_clause = ""
        params: list[Any] = []
        if status_filter != "all":
            status_clause = "COALESCE(NULLIF(c.customer_status, ''), 'active') = ?"
            params.append(status_filter)

        if search:
            like = f"%{search}%"
            search_clause = """
              (
                c.first_name LIKE ?
                OR c.last_name LIKE ?
                OR c.phone LIKE ?
                OR c.email LIKE ?
              )
            """
            params.extend([like, like, like, like])
            where_clause = (
                f"WHERE {status_clause} AND {search_clause}"
                if status_clause
                else f"WHERE {search_clause}"
            )
            rows = conn.execute(
                f"""
                SELECT
                  c.*,
                  COUNT(v.id) AS vehicle_count
                FROM customers c
                LEFT JOIN customer_vehicles v ON v.customer_id = c.id
                {where_clause}
                GROUP BY c.id
                ORDER BY c.updated_at DESC, c.created_at DESC, c.id DESC
                """,
                params,
            ).fetchall()
        else:
            where_clause = f"WHERE {status_clause}" if status_clause else ""
            rows = conn.execute(
                f"""
                SELECT
                  c.*,
                  COUNT(v.id) AS vehicle_count
                FROM customers c
                LEFT JOIN customer_vehicles v ON v.customer_id = c.id
                {where_clause}
                GROUP BY c.id
                ORDER BY c.updated_at DESC, c.created_at DESC, c.id DESC
                """,
                params,
            ).fetchall()
    finally:
        conn.close()

    return templates.TemplateResponse(
        "pro/customers.html",
        {
            "request": request,
            "customers": [dict(row) for row in rows],
            "q": search,
            "status_filter": status_filter,
        },
    )


@router.get("/estimate-conversion", response_class=HTMLResponse)
def pro_estimate_conversion_empty(request: Request):
    return templates.TemplateResponse(
        "pro/estimate_conversion.html",
        {
            "request": request,
            "payload": None,
            "payload_json": "",
            "customers": [],
            "vehicles_by_customer": {},
            "error": "Start from the Estimator after adding at least one service.",
        },
    )


@router.post("/estimate-conversion", response_class=HTMLResponse)
async def pro_estimate_conversion(request: Request):
    form = await read_form_data(request)
    payload_json = form.get("estimate_payload", "")
    payload = load_estimate_conversion_payload(payload_json)
    conn = crm_db_conn()
    try:
        ensure_customer_status_schema(conn)
        customers = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM customers
                WHERE customer_status = 'active'
                ORDER BY updated_at DESC, created_at DESC, id DESC
                """
            ).fetchall()
        ]
        vehicles_by_customer: dict[str, list[dict[str, Any]]] = {}
        for row in conn.execute(
            """
            SELECT *
            FROM customer_vehicles
            ORDER BY updated_at DESC, created_at DESC, id DESC
            """
        ).fetchall():
            vehicle = dict(row)
            vehicles_by_customer.setdefault(str(vehicle["customer_id"]), []).append(vehicle)
    finally:
        conn.close()
    return templates.TemplateResponse(
        "pro/estimate_conversion.html",
        {
            "request": request,
            "payload": payload,
            "payload_json": json.dumps(payload),
            "customers": customers,
            "vehicles_by_customer": vehicles_by_customer,
            "error": "",
        },
    )


@router.post("/estimate-conversion/create")
async def pro_estimate_conversion_create(request: Request):
    raw_body = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qs(raw_body, keep_blank_values=True)
    form = {key: values[0].strip() for key, values in parsed.items()}
    selected_indices = {
        int(value)
        for value in parsed.get("service_index", [])
        if str(value).strip().isdigit()
    }
    payload = load_estimate_conversion_payload(form.get("estimate_payload", ""))
    selected_items = [
        item for item in payload["lineItems"] if int(item["index"]) in selected_indices
    ]
    if not selected_items:
        raise HTTPException(status_code=400, detail="Select at least one service to import")

    now = datetime.utcnow().isoformat()
    repair_date = local_today().isoformat()
    customer_mode = form.get("customer_mode", "existing")
    vehicle_mode = form.get("vehicle_mode", "existing")

    conn = crm_db_conn()
    try:
        ensure_customer_status_schema(conn)
        ensure_repair_records_schema(conn)
        if customer_mode == "new":
            first_name, last_name = split_customer_name(form.get("new_customer_name", ""))
            cur = conn.execute(
                """
                INSERT INTO customers (
                  first_name, last_name, phone, email, customer_status, notes, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (
                    first_name,
                    last_name,
                    clean_phone(form.get("new_customer_phone", "")),
                    form.get("new_customer_email", ""),
                    "Created from estimator conversion.",
                    now,
                    now,
                ),
            )
            customer_id = int(cur.lastrowid)
            vehicle_mode = "new"
        else:
            customer_id = optional_int(form, "customer_id") or 0
            customer = conn.execute(
                "SELECT id FROM customers WHERE id = ?",
                (customer_id,),
            ).fetchone()
            if not customer:
                raise HTTPException(status_code=400, detail="Select a customer")

        if vehicle_mode == "new":
            vehicle_payload = payload.get("vehicle") or {}
            cur = conn.execute(
                """
                INSERT INTO customer_vehicles (
                  customer_id, year, make, model, engine, vin, license_plate,
                  mileage, notes, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    customer_id,
                    optional_int(form, "new_vehicle_year") or optional_int({"new_vehicle_year": vehicle_payload.get("year")}, "new_vehicle_year"),
                    form.get("new_vehicle_make") or vehicle_payload.get("make") or "",
                    form.get("new_vehicle_model") or vehicle_payload.get("model") or "",
                    form.get("new_vehicle_engine", ""),
                    form.get("new_vehicle_vin", ""),
                    form.get("new_vehicle_license_plate", ""),
                    optional_int(form, "new_vehicle_mileage"),
                    "Created from estimator conversion.",
                    now,
                    now,
                ),
            )
            vehicle_id = int(cur.lastrowid)
            vehicle_mileage = optional_int(form, "new_vehicle_mileage")
        else:
            vehicle_id = optional_int(form, "vehicle_id") or 0
            vehicle = conn.execute(
                """
                SELECT id, mileage
                FROM customer_vehicles
                WHERE id = ? AND customer_id = ?
                """,
                (vehicle_id, customer_id),
            ).fetchone()
            if not vehicle:
                raise HTTPException(status_code=400, detail="Select a vehicle for this customer")
            vehicle_mileage = vehicle["mileage"]

        source_type = "finding" if payload.get("source") == "finding" and payload.get("finding_id") else "estimate"
        source_id = int(payload["finding_id"]) if source_type == "finding" else None
        if source_type == "finding":
            ensure_findings_records_schema(conn)
            finding = conn.execute(
                f"""
                SELECT id
                FROM findings_records
                WHERE {finding_record_where_sql(conn)}
                """,
                finding_record_where_params(conn, source_id, customer_id, vehicle_id),
            ).fetchone()
            if not finding:
                raise HTTPException(status_code=400, detail="Finding context does not match selected customer and vehicle")

        created_count = 0
        for item in selected_items:
            if source_type == "finding" and source_id is not None:
                existing = conn.execute(
                    """
                    SELECT id
                    FROM repair_records
                    WHERE workflow_source_type = 'finding'
                      AND workflow_source_id = ?
                      AND vehicle_id = ?
                      AND LOWER(TRIM(COALESCE(repair_name, ''))) = LOWER(TRIM(?))
                      AND repair_date = ?
                    LIMIT 1
                    """,
                    (source_id, vehicle_id, item["service_name"], repair_date),
                ).fetchone()
            else:
                existing = conn.execute(
                    """
                    SELECT id
                    FROM repair_records
                    WHERE vehicle_id = ?
                      AND LOWER(TRIM(COALESCE(repair_name, ''))) = LOWER(TRIM(?))
                      AND repair_date = ?
                    LIMIT 1
                    """,
                    (vehicle_id, item["service_name"], repair_date),
                ).fetchone()
            if existing:
                continue
            labor_cost = item["labor_total"]
            if labor_cost is None and item["labor_hours"] is not None and item["labor_rate"] is not None:
                labor_cost = round(float(item["labor_hours"]) * float(item["labor_rate"]), 2)
            total_cost = item["grand_total"]
            if total_cost is None:
                total_cost = round(float(labor_cost or 0) + float(item["parts_total"] or 0), 2)
            notes = "\n".join(
                part
                for part in [
                    "Source: Finding" if source_type == "finding" else "Source: Estimate",
                    payload.get("notes") or "",
                    item.get("notes") or "",
                ]
                if str(part or "").strip()
            )
            cur = conn.execute(
                """
                INSERT INTO repair_records (
                  vehicle_id, customer_id, repair_name, repair_date, mileage,
                  labor_hours, labor_rate, parts_cost, labor_cost, total_cost,
                  track_as_maintenance, workflow_source_type, workflow_source_id, parts_search_term,
                  pricing_mode, flat_rate_price, approved_estimate_total,
                  notes, status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, 'Open', ?)
                """,
                (
                    vehicle_id,
                    customer_id,
                    item["service_name"],
                    repair_date,
                    vehicle_mileage,
                    item["labor_hours"],
                    item["labor_rate"],
                    item["parts_total"],
                    labor_cost,
                    total_cost,
                    source_type,
                    source_id,
                    item.get("parts_search_term") or "",
                    item.get("pricing_mode") or "hourly",
                    item.get("flat_rate_price"),
                    total_cost,
                    notes,
                    now,
                ),
            )
            repair_id = int(cur.lastrowid)
            if source_type == "finding" and source_id is not None:
                conn.execute(
                    f"""
                    UPDATE findings_records
                    SET status = 'Approved',
                        linked_repair_record_id = CASE
                            WHEN linked_repair_record_id IS NULL OR linked_repair_record_id = 0 THEN ?
                            ELSE linked_repair_record_id
                        END,
                        repair_record_created_at = COALESCE(NULLIF(repair_record_created_at, ''), ?),
                        repair_work_status = COALESCE(NULLIF(repair_work_status, ''), 'ready'),
                        repair_work_updated_at = COALESCE(NULLIF(repair_work_updated_at, ''), ?)
                    WHERE {finding_record_where_sql(conn)}
                    """,
                    (repair_id, now, now, *finding_record_where_params(conn, source_id, customer_id, vehicle_id)),
                )
            created_count += 1
        conn.commit()
    finally:
        conn.close()

    return RedirectResponse(
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}?converted=1&created={created_count}#repair-workspace",
        status_code=303,
    )


@router.post("/customers")
async def pro_customer_create(request: Request):
    form = await read_form_data(request)
    now = datetime.utcnow().isoformat()
    conn = crm_db_conn()
    try:
        ensure_customer_status_schema(conn)
        cur = conn.execute(
            """
            INSERT INTO customers (
              first_name, last_name, phone, email, customer_status, notes, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'active', ?, ?, ?)
            """,
            (
                form.get("first_name", ""),
                form.get("last_name", ""),
                clean_phone(form.get("phone", "")),
                form.get("email", ""),
                form.get("notes", ""),
                now,
                now,
            ),
        )
        conn.commit()
        customer_id = cur.lastrowid
    finally:
        conn.close()
    return RedirectResponse(f"/pro/customers/{customer_id}", status_code=303)


@router.post("/customers/{customer_id}")
async def pro_customer_update(request: Request, customer_id: int):
    form = await read_form_data(request)
    now = datetime.utcnow().isoformat()
    conn = crm_db_conn()
    try:
        ensure_customer_status_schema(conn)
        cur = conn.execute(
            """
            UPDATE customers
            SET
              first_name = ?,
              last_name = ?,
              phone = ?,
              email = ?,
              notes = ?,
              updated_at = ?
            WHERE id = ?
            """,
            (
                form.get("first_name", ""),
                form.get("last_name", ""),
                clean_phone(form.get("phone", "")),
                form.get("email", ""),
                form.get("notes", ""),
                now,
                customer_id,
            ),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Customer not found")
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(f"/pro/customers/{customer_id}", status_code=303)


@router.post("/customers/{customer_id}/deactivate")
async def pro_customer_deactivate(customer_id: int):
    now = datetime.utcnow().isoformat()
    conn = crm_db_conn()
    try:
        ensure_customer_status_schema(conn)
        cur = conn.execute(
            """
            UPDATE customers
            SET customer_status = 'inactive',
                updated_at = ?
            WHERE id = ?
            """,
            (now, customer_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Customer not found")
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(f"/pro/customers/{customer_id}", status_code=303)


@router.post("/customers/{customer_id}/reactivate")
async def pro_customer_reactivate(customer_id: int):
    now = datetime.utcnow().isoformat()
    conn = crm_db_conn()
    try:
        ensure_customer_status_schema(conn)
        cur = conn.execute(
            """
            UPDATE customers
            SET customer_status = 'active',
                updated_at = ?
            WHERE id = ?
            """,
            (now, customer_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Customer not found")
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(f"/pro/customers/{customer_id}", status_code=303)


@router.get("/customers/{customer_id}", response_class=HTMLResponse)
def pro_customer_detail(request: Request, customer_id: int):
    conn = crm_db_conn()
    try:
        ensure_customer_status_schema(conn)
        customer = row_to_dict(
            conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
        )
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")
        vehicles = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM customer_vehicles
                WHERE customer_id = ?
                ORDER BY updated_at DESC, created_at DESC, id DESC
                """,
                (customer_id,),
            ).fetchall()
        ]
    finally:
        conn.close()

    return templates.TemplateResponse(
        "pro/customer_detail.html",
        {
            "request": request,
            "customer": customer,
            "vehicles": vehicles,
        },
    )


@router.post("/customers/{customer_id}/vehicles")
async def pro_customer_vehicle_create(request: Request, customer_id: int):
    form = await read_form_data(request)
    conn = crm_db_conn()
    try:
        ensure_customer_status_schema(conn)
        customer = conn.execute("SELECT id FROM customers WHERE id = ?", (customer_id,)).fetchone()
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")

        now = datetime.utcnow().isoformat()
        conn.execute(
            """
            INSERT INTO customer_vehicles (
              customer_id, year, make, model, engine, vin, license_plate,
              mileage, notes, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                customer_id,
                optional_int(form, "year"),
                form.get("make", ""),
                form.get("model", ""),
                form.get("engine", ""),
                form.get("vin", ""),
                form.get("license_plate", ""),
                optional_int(form, "mileage"),
                form.get("notes", ""),
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(f"/pro/customers/{customer_id}", status_code=303)


@router.get("/customers/{customer_id}/vehicles/{vehicle_id}", response_class=HTMLResponse)
def pro_customer_vehicle_detail(
    request: Request,
    customer_id: int,
    vehicle_id: int,
    converted: str = "",
    created: int = 0,
):
    conn = crm_db_conn()
    try:
        customer, vehicle = load_customer_vehicle(conn, customer_id, vehicle_id)
        ensure_maintenance_records_schema(conn)
        ensure_maintenance_reminder_events_schema(conn)
        ensure_repair_records_schema(conn)
        ensure_repair_job_parts_schema(conn)
        ensure_repair_checklist_schema(conn)
        ensure_findings_records_schema(conn)
        ensure_finding_history_records_schema(conn)
        ensure_customer_decision_logs_schema(conn)
        ensure_service_history_schema(conn)
        ensure_service_history_records_schema(conn)
        ensure_invoices_schema(conn)
        ensure_visual_reference_schema(conn)
        ensure_repair_intelligence_schema(conn)
        service_history_records = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM service_history_records
                WHERE customer_id = ? AND vehicle_id = ?
                ORDER BY mileage DESC, service_date DESC, id DESC
                """,
                (customer_id, vehicle_id),
            ).fetchall()
        ]
        maintenance_records = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM maintenance_records
                WHERE customer_id = ? AND vehicle_id = ?
                ORDER BY
                  mileage_performed IS NULL ASC,
                  mileage_performed DESC,
                  CASE
                    WHEN date_performed IS NULL OR TRIM(date_performed) = '' THEN 1
                    ELSE 0
                  END ASC,
                  date_performed DESC,
                  id DESC
                """,
                (customer_id, vehicle_id),
            ).fetchall()
        ]
        maintenance_reminder_events = load_maintenance_reminder_events_map(
            conn,
            {int(record.get("id") or 0) for record in maintenance_records if record.get("id")},
        )
        sender_context = load_shop_profile_context(conn)
        repair_records = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM repair_records
                WHERE customer_id = ? AND vehicle_id = ?
                ORDER BY repair_date DESC, mileage DESC, id DESC
                """,
                (customer_id, vehicle_id),
            ).fetchall()
        ]
        repair_parts_map = load_repair_job_parts_map(
            conn,
            {int(record.get("id") or 0) for record in repair_records if record.get("id")},
        )
        attach_repair_job_parts(repair_records, repair_parts_map)
        for repair_record in repair_records:
            totals = repair_cost_totals(repair_record)
            repair_record["labor_rate"] = totals["labor_rate"]
            repair_record["labor_rate_is_legacy"] = totals["labor_rate_is_legacy"]
            repair_record["labor_cost"] = totals["labor_total"]
            repair_record["total_cost"] = totals["grand_total"]
            repair_record["parts_cost"] = totals["parts_total"]
            if (repair_record.get("status") or "") == "Completed":
                completion = load_repair_completion(conn, int(repair_record.get("id") or 0))
                repair_record["completion"] = completion
                repair_record["after_repair_photo_urls"] = completion.get("after_repair_photo_urls") or []
        findings_records = [
            dict(row)
            for row in conn.execute(
                """
                SELECT fr.*,
                       rr.status AS linked_repair_status
                FROM findings_records fr
                LEFT JOIN repair_records rr
                  ON rr.id = fr.linked_repair_record_id
                WHERE fr.vehicle_id = ?
                ORDER BY
                  CASE fr.status
                    WHEN 'Approved' THEN 1
                    WHEN 'Open' THEN 2
                    WHEN 'Completed' THEN 3
                    WHEN 'Deferred' THEN 4
                    WHEN 'Declined' THEN 5
                    ELSE 6
                  END ASC,
                  CASE fr.severity
                    WHEN 'Critical' THEN 1
                    WHEN 'High' THEN 2
                    WHEN 'Medium' THEN 3
                    WHEN 'Low' THEN 4
                    ELSE 5
                  END ASC,
                  fr.finding_date DESC,
                  fr.id DESC
                """,
                (vehicle_id,),
            ).fetchall()
        ]
        for record in findings_records:
            record.setdefault("customer_id", customer_id)
            attach_finding_photo_urls(record)
        finding_history_records = [
            dict(row)
            for row in conn.execute(
                """
                SELECT fhr.*, fr.finding, fr.request_type, fr.labor_description
                FROM finding_history_records fhr
                JOIN findings_records fr ON fr.id = fhr.finding_id
                WHERE fr.vehicle_id = ?
                ORDER BY fhr.created_at DESC, fhr.id DESC
                """,
                (vehicle_id,),
            ).fetchall()
        ]
        customer_decision_logs = [
            dict(row)
            for row in conn.execute(
                """
                SELECT cdl.*, fr.finding
                FROM customer_decision_logs cdl
                JOIN findings_records fr ON fr.id = cdl.finding_id
                WHERE fr.vehicle_id = ?
                ORDER BY cdl.created_at DESC, cdl.id DESC
                """,
                (vehicle_id,),
            ).fetchall()
        ]
        ensure_discrepancy_approvals_schema(conn)
        ensure_discrepancy_approval_events_schema(conn)
        approval_records = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM discrepancy_approvals
                WHERE customer_id = ? AND vehicle_id = ?
                ORDER BY
                  CASE customer_decision
                    WHEN 'pending' THEN 0
                    WHEN 'approved' THEN 1
                    ELSE 2
                  END,
                  created_at DESC,
                  id DESC
                """,
                (customer_id, vehicle_id),
            ).fetchall()
        ]
        approval_event_records = [
            dict(row)
            for row in conn.execute(
                """
                SELECT dae.*, da.request_type, da.part_name, da.part_number
                FROM discrepancy_approval_events dae
                JOIN discrepancy_approvals da ON da.id = dae.approval_id
                WHERE dae.customer_id = ? AND dae.vehicle_id = ?
                ORDER BY dae.created_at DESC, dae.id DESC
                """,
                (customer_id, vehicle_id),
            ).fetchall()
        ]
        repair_checklist_events = load_vehicle_repair_checklist_events(conn, customer_id, vehicle_id)
        repair_completion_events = load_vehicle_repair_completion_events(conn, customer_id, vehicle_id)
        invoice_records = load_vehicle_invoice_records(conn, customer_id, vehicle_id)
        estimate_document_records = load_vehicle_estimate_documents(conn, customer_id, vehicle_id)
        attach_estimate_documents_to_findings(
            findings_records,
            estimate_document_records,
            customer_id=customer_id,
            vehicle_id=vehicle_id,
        )
        repair_invoice_map = load_repair_invoice_map(conn, customer_id, vehicle_id)
        annotate_repairs_with_invoice_status(repair_records, repair_invoice_map)
        seed_visual_references(conn)
        visual_reference_records = load_visual_references_for_vehicle(conn, vehicle)
        seed_repair_intelligence(conn)
        repair_intelligence_records = load_repair_intelligence_for_vehicle(conn, vehicle)
        for repair_record in repair_records:
            repair_record["repair_intelligence_records"] = load_repair_intelligence_for_repair(
                conn,
                vehicle,
                repair_record.get("repair_name"),
            )
        linked_repair_record_ids = {
            int(record.get("linked_repair_record_id") or 0)
            for record in [*findings_records, *approval_records]
            if record.get("linked_repair_record_id")
        }
        linked_repair_record_ids.update(
            int(record.get("id") or 0)
            for record in repair_records
            if record.get("id") and (record.get("status") or "") != "Completed"
        )
        checklist_summaries = {
            repair_record_id: repair_checklist_summary(conn, repair_record_id)
            for repair_record_id in linked_repair_record_ids
        }
    finally:
        conn.close()

    grouped_approval_records = group_approval_records(approval_records)
    vehicle_timeline = build_vehicle_timeline(
        customer_id,
        vehicle_id,
        vehicle,
        service_history_records,
        invoice_records,
        findings_records,
        finding_history_records,
        customer_decision_logs,
        approval_event_records,
        repair_checklist_events,
        repair_completion_events,
        estimate_document_records,
    )
    vehicle_timeline_total = sum(int(group.get("count") or 0) for group in vehicle_timeline)
    today = date.today()
    maintenance_driving_rate = estimate_vehicle_driving_rate(
        maintenance_records,
        service_history_records,
        vehicle,
        today,
    )
    maintenance_records = annotate_vehicle_maintenance_records(
        maintenance_records,
        vehicle,
        customer,
        today,
        maintenance_driving_rate,
        sender_context,
    )
    mark_active_maintenance_baselines(maintenance_records)
    attach_maintenance_reminder_events(maintenance_records, maintenance_reminder_events)
    repair_history_summary = build_repair_history_summary(repair_records)
    inspection_findings_records = [
        record for record in findings_records if is_active_inspection_finding(record)
    ]
    findings_summary = build_findings_summary(inspection_findings_records)
    approval_summary = build_approval_summary(approval_records)
    repair_work_items = build_repair_work_items(
        vehicle,
        findings_records,
        approval_records,
        repair_records,
        estimate_document_records,
    )
    for item in repair_work_items:
        item["checklist_summary"] = checklist_summaries.get(
            int(item.get("linked_repair_record_id") or 0),
            {"completed": 0, "total": 0, "incomplete": 0, "percent": 0},
        )
    repair_workspace_groups = build_repair_workspace_groups(vehicle, repair_work_items, repair_records)

    return templates.TemplateResponse(
        "pro/vehicle_detail.html",
        {
            "request": request,
            "customer": customer,
            "vehicle": vehicle,
            "service_history_records": service_history_records,
            "maintenance_records": maintenance_records,
            "repair_records": repair_records,
            "repair_history_summary": repair_history_summary,
            "findings_records": inspection_findings_records,
            "all_findings_records": findings_records,
            "findings_summary": findings_summary,
            "approval_summary": approval_summary,
            "repair_work_items": repair_work_items,
            "repair_workspace_groups": repair_workspace_groups,
            "visual_reference_records": visual_reference_records,
            "repair_intelligence_records": repair_intelligence_records,
            "repair_work_status_options": [
                {"value": value, "label": REPAIR_WORK_STATUS_LABELS[value]}
                for value in REPAIR_WORK_STATUS_OPTIONS
            ],
            "repair_job_part_status_options": REPAIR_JOB_PART_STATUS_OPTIONS,
            "finding_history_records": finding_history_records,
            "customer_decision_logs": customer_decision_logs,
            "vehicle_timeline": vehicle_timeline,
            "vehicle_timeline_total": vehicle_timeline_total,
            "invoice_records": invoice_records,
            "approval_records": approval_records,
            "approval_groups": grouped_approval_records,
            "maintenance_driving_rate": maintenance_driving_rate,
            "maintenance_service_options": MAINTENANCE_SERVICE_OPTIONS,
            "maintenance_interval_presets": MAINTENANCE_INTERVAL_PRESETS,
            "maintenance_service_aliases": MAINTENANCE_SERVICE_ALIASES,
            "estimate_conversion_success": converted == "1",
            "estimate_conversion_created": created,
        },
    )


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}")
async def pro_customer_vehicle_update(request: Request, customer_id: int, vehicle_id: int):
    form = await read_form_data(request)
    now = datetime.utcnow().isoformat()
    conn = crm_db_conn()
    try:
        cur = conn.execute(
            """
            UPDATE customer_vehicles
            SET
              year = ?,
              make = ?,
              model = ?,
              engine = ?,
              vin = ?,
              license_plate = ?,
              mileage = ?,
              notes = ?,
              updated_at = ?
            WHERE id = ? AND customer_id = ?
            """,
            (
                optional_int(form, "year"),
                form.get("make", ""),
                form.get("model", ""),
                form.get("engine", ""),
                form.get("vin", ""),
                form.get("license_plate", ""),
                optional_int(form, "mileage"),
                form.get("notes", ""),
                now,
                vehicle_id,
                customer_id,
            ),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Vehicle not found")
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(f"/pro/customers/{customer_id}/vehicles/{vehicle_id}", status_code=303)


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/findings")
async def pro_finding_record_create(request: Request, customer_id: int, vehicle_id: int):
    content_type = request.headers.get("content-type", "")
    before_inspection_photo_paths: list[str] = []
    if "multipart/form-data" in content_type:
        form, files = await read_multipart_form_data(request)
        before_inspection_photo_paths = save_image_upload_paths(
            files.get("before_inspection_photos"),
            max_files=PHOTO_UPLOAD_MAX_FILES,
            allowed_extensions=PHOTO_UPLOAD_ALLOWED_EXTENSIONS,
        )
    else:
        form = await read_form_data(request)
    now = datetime.utcnow().isoformat()
    severity = normalize_finding_severity(form.get("severity", "Low"))
    status = normalize_finding_status(form.get("status", "Open"))
    request_type = normalize_finding_request_type(form.get("request_type"))
    labor_description = form.get("labor_description", "") if request_type == "labor" else ""
    labor_hours = optional_float(form, "labor_hours") if request_type == "labor" else None
    labor_rate = optional_float(form, "labor_rate") if request_type == "labor" else None
    labor_amount = finding_labor_amount(labor_hours, labor_rate)
    parts_cost = finding_parts_cost(form)
    labor_reason = form.get("labor_reason", "") if request_type == "labor" else ""

    conn = crm_db_conn()
    try:
        customer, vehicle = load_customer_vehicle(conn, customer_id, vehicle_id)
        ensure_findings_records_schema(conn)
        insert_columns = [
            "vehicle_id",
            "request_type",
            "finding",
            "recommendation",
            "labor_description",
            "labor_hours",
            "labor_rate",
            "labor_amount",
            "parts_cost",
            "labor_reason",
            "before_inspection_photo_paths",
            "severity",
            "status",
            "repair_work_status",
            "repair_work_updated_at",
            "mileage",
            "finding_date",
            "created_at",
        ]
        insert_values: list[Any] = [
            vehicle_id,
            request_type,
            form.get("finding", ""),
            form.get("recommendation", ""),
            labor_description,
            labor_hours,
            labor_rate,
            labor_amount,
            parts_cost,
            labor_reason,
            json.dumps(before_inspection_photo_paths),
            severity,
            status,
            "completed" if status == "Completed" else "ready" if status == "Approved" else "",
            now if status in {"Approved", "Completed"} else "",
            optional_int(form, "mileage"),
            local_today().isoformat(),
            now,
        ]
        if findings_records_has_customer_id(conn):
            insert_columns.insert(1, "customer_id")
            insert_values.insert(1, customer_id)
        cur = conn.execute(
            f"""
            INSERT INTO findings_records ({", ".join(insert_columns)})
            VALUES ({", ".join("?" for _ in insert_columns)})
            """,
            tuple(insert_values),
        )
        append_finding_history_record(
            conn,
            cur.lastrowid,
            None,
            status,
            "finding_created",
            now,
            notes="Labor Request Created" if request_type == "labor" else "Finding Created",
        )
        append_customer_decision_log_if_needed(
            conn,
            cur.lastrowid,
            status,
            customer_name(customer),
            now,
        )
        if status == "Approved":
            ensure_repair_record_for_approved_finding(
                conn,
                customer_id=customer_id,
                vehicle_id=vehicle_id,
                finding_id=int(cur.lastrowid),
                now=now,
            )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}#recommendations-findings",
        status_code=303,
    )


@router.get(
    "/customers/{customer_id}/vehicles/{vehicle_id}/findings/{finding_id}",
    response_class=HTMLResponse,
)
def pro_finding_record_detail(
    request: Request, customer_id: int, vehicle_id: int, finding_id: int
):
    conn = crm_db_conn()
    try:
        customer, vehicle = load_customer_vehicle(conn, customer_id, vehicle_id)
        finding = load_finding_record(conn, customer_id, vehicle_id, finding_id)
        finding_history_records = load_finding_history_records(conn, finding_id)
        checklist_summary = repair_checklist_summary(conn, finding.get("linked_repair_record_id"))
    finally:
        conn.close()

    return templates.TemplateResponse(
        "pro/finding_detail.html",
        {
            "request": request,
            "customer": customer,
            "vehicle": vehicle,
            "finding": finding,
            "finding_history_records": finding_history_records,
            "checklist_summary": checklist_summary,
            "repair_work_status_options": [
                {"value": value, "label": REPAIR_WORK_STATUS_LABELS[value]}
                for value in REPAIR_WORK_STATUS_OPTIONS
            ],
            "repair_work_status_label": repair_work_status_label(finding.get("repair_work_status") or ("completed" if finding.get("status") == "Completed" else "ready")),
        },
    )


@router.get(
    "/customers/{customer_id}/vehicles/{vehicle_id}/findings/{finding_id}/edit",
    response_class=HTMLResponse,
)
def pro_finding_record_edit(
    request: Request, customer_id: int, vehicle_id: int, finding_id: int
):
    conn = crm_db_conn()
    try:
        customer, vehicle = load_customer_vehicle(conn, customer_id, vehicle_id)
        finding = load_finding_record(conn, customer_id, vehicle_id, finding_id)
    finally:
        conn.close()

    return templates.TemplateResponse(
        "pro/finding_edit.html",
        {
            "request": request,
            "customer": customer,
            "vehicle": vehicle,
            "finding": finding,
        },
    )


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/findings/{finding_id}")
async def pro_finding_record_update(
    request: Request, customer_id: int, vehicle_id: int, finding_id: int
):
    form = await read_form_data(request)
    severity = normalize_finding_severity(form.get("severity"))
    status = normalize_finding_status(form.get("status"))
    request_type = normalize_finding_request_type(form.get("request_type"))
    labor_description = form.get("labor_description", "") if request_type == "labor" else ""
    labor_hours = optional_float(form, "labor_hours") if request_type == "labor" else None
    labor_rate = optional_float(form, "labor_rate") if request_type == "labor" else None
    labor_amount = finding_labor_amount(labor_hours, labor_rate)
    parts_cost = finding_parts_cost(form)
    labor_reason = form.get("labor_reason", "") if request_type == "labor" else ""

    conn = crm_db_conn()
    try:
        customer, vehicle = load_customer_vehicle(conn, customer_id, vehicle_id)
        existing = load_finding_record(conn, customer_id, vehicle_id, finding_id)
        cur = conn.execute(
            f"""
            UPDATE findings_records
            SET request_type = ?, finding = ?, recommendation = ?,
                labor_description = ?, labor_hours = ?, labor_rate = ?,
                labor_amount = ?, parts_cost = ?, labor_reason = ?, severity = ?, status = ?,
                repair_work_status = CASE
                  WHEN ? IN ('Approved', 'Completed')
                    AND (repair_work_status IS NULL OR TRIM(repair_work_status) = '')
                  THEN CASE WHEN ? = 'Completed' THEN 'completed' ELSE 'ready' END
                  ELSE repair_work_status
                END,
                repair_work_updated_at = CASE
                  WHEN ? IN ('Approved', 'Completed')
                    AND (repair_work_updated_at IS NULL OR TRIM(repair_work_updated_at) = '')
                  THEN ?
                  ELSE repair_work_updated_at
                END,
                mileage = ?, finding_date = ?
            WHERE {finding_record_where_sql(conn)}
            """,
            (
                request_type,
                form.get("finding", ""),
                form.get("recommendation", ""),
                labor_description,
                labor_hours,
                labor_rate,
                labor_amount,
                parts_cost,
                labor_reason,
                severity,
                status,
                status,
                status,
                status,
                datetime.utcnow().isoformat(),
                optional_int(form, "mileage"),
                form.get("finding_date", ""),
                *finding_record_where_params(conn, finding_id, customer_id, vehicle_id),
            ),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Finding record not found")
        if (existing.get("status") or "") != status:
            now = datetime.utcnow().isoformat()
            append_finding_history_record(
                conn,
                finding_id,
                existing.get("status") or None,
                status,
                "status_changed",
                now,
            )
            append_customer_decision_log_if_needed(
                conn,
                finding_id,
                status,
                customer_name(customer),
                now,
            )
        if status == "Approved":
            ensure_repair_record_for_approved_finding(
                conn,
                customer_id=customer_id,
                vehicle_id=vehicle_id,
                finding_id=finding_id,
                now=datetime.utcnow().isoformat(),
            )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}#recommendations-findings",
        status_code=303,
    )


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/findings/{finding_id}/notes")
async def pro_finding_notes_update(
    request: Request, customer_id: int, vehicle_id: int, finding_id: int
):
    form = await read_form_data(request)
    customer_notes = form.get("customer_notes", "")
    internal_notes = form.get("internal_notes", "")

    conn = crm_db_conn()
    try:
        load_customer_vehicle(conn, customer_id, vehicle_id)
        existing = load_finding_record(conn, customer_id, vehicle_id, finding_id)
        previous_customer_notes = existing.get("customer_notes") or ""
        previous_internal_notes = existing.get("internal_notes") or ""
        customer_notes_changed = previous_customer_notes != customer_notes
        internal_notes_changed = previous_internal_notes != internal_notes

        cur = conn.execute(
            f"""
            UPDATE findings_records
            SET customer_notes = ?, internal_notes = ?
            WHERE {finding_record_where_sql(conn)}
            """,
            (customer_notes, internal_notes, *finding_record_where_params(conn, finding_id, customer_id, vehicle_id)),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Finding record not found")

        if customer_notes_changed or internal_notes_changed:
            now = datetime.utcnow().isoformat()
            status = existing.get("status") or "Open"
            if customer_notes_changed:
                append_finding_history_record(
                    conn,
                    finding_id,
                    status,
                    status,
                    "customer_notes_updated",
                    now,
                    notes="Customer Notes updated",
                )
            if internal_notes_changed:
                append_finding_history_record(
                    conn,
                    finding_id,
                    status,
                    status,
                    "internal_notes_updated",
                    now,
                    notes="Internal Notes updated",
                )
        conn.commit()
    finally:
        conn.close()

    return RedirectResponse(
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/findings/{finding_id}",
        status_code=303,
    )


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/findings/{finding_id}/status")
async def pro_finding_record_status_update(
    request: Request, customer_id: int, vehicle_id: int, finding_id: int
):
    form = await read_form_data(request)
    status = normalize_finding_status(form.get("status"))

    conn = crm_db_conn()
    try:
        customer, vehicle = load_customer_vehicle(conn, customer_id, vehicle_id)
        existing = load_finding_record(conn, customer_id, vehicle_id, finding_id)
        previous_status = existing.get("status") or ""
        if previous_status == status:
            if status == "Approved":
                ensure_repair_record_for_approved_finding(
                    conn,
                    customer_id=customer_id,
                    vehicle_id=vehicle_id,
                    finding_id=finding_id,
                    now=datetime.utcnow().isoformat(),
                )
                conn.commit()
            if request.headers.get("x-requested-with") == "fetch":
                return JSONResponse(
                    {
                        "status": status,
                        "message": "Status Updated",
                        **vehicle_finding_activity_payload(conn, customer_id, vehicle_id),
                    }
                )
            return RedirectResponse(
                f"/pro/customers/{customer_id}/vehicles/{vehicle_id}#recommendations-findings",
                status_code=303,
            )
        cur = conn.execute(
            f"""
            UPDATE findings_records
            SET status = ?,
                repair_work_status = CASE
                  WHEN ? IN ('Approved', 'Completed')
                    AND (repair_work_status IS NULL OR TRIM(repair_work_status) = '')
                  THEN CASE WHEN ? = 'Completed' THEN 'completed' ELSE 'ready' END
                  ELSE repair_work_status
                END,
                repair_work_updated_at = CASE
                  WHEN ? IN ('Approved', 'Completed')
                    AND (repair_work_updated_at IS NULL OR TRIM(repair_work_updated_at) = '')
                  THEN ?
                  ELSE repair_work_updated_at
                END
            WHERE {finding_record_where_sql(conn)}
            """,
            (
                status,
                status,
                status,
                status,
                datetime.utcnow().isoformat(),
                *finding_record_where_params(conn, finding_id, customer_id, vehicle_id),
            ),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Finding record not found")
        now = datetime.utcnow().isoformat()
        append_finding_history_record(
            conn,
            finding_id,
            previous_status or None,
            status,
            "status_changed",
            now,
        )
        append_customer_decision_log_if_needed(
            conn,
            finding_id,
            status,
            customer_name(customer),
            now,
        )
        if status == "Approved":
            ensure_repair_record_for_approved_finding(
                conn,
                customer_id=customer_id,
                vehicle_id=vehicle_id,
                finding_id=finding_id,
                now=now,
            )
        conn.commit()
        activity_payload = vehicle_finding_activity_payload(conn, customer_id, vehicle_id)
    finally:
        conn.close()
    if request.headers.get("x-requested-with") == "fetch":
        return JSONResponse(
            {"status": status, "message": "Status Updated", **activity_payload}
        )
    return RedirectResponse(
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}#recommendations-findings",
        status_code=303,
    )


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/findings/{finding_id}/repair-work")
async def pro_finding_repair_work_status_update(
    request: Request, customer_id: int, vehicle_id: int, finding_id: int
):
    raise HTTPException(status_code=405, detail="Repair status is managed from the linked repair execution record")
    form = await read_form_data(request)
    repair_status = normalize_repair_work_status(form.get("repair_work_status"))
    now = datetime.utcnow().isoformat()

    conn = crm_db_conn()
    try:
        load_customer_vehicle(conn, customer_id, vehicle_id)
        existing = load_finding_record(conn, customer_id, vehicle_id, finding_id)
        if (existing.get("status") or "") not in {"Approved", "Completed"}:
            raise HTTPException(status_code=400, detail="Finding must be approved before repair work starts")
        previous_repair_status = existing.get("repair_work_status") or (
            "completed" if existing.get("status") == "Completed" else "ready"
        )
        if (
            repair_status == "completed"
            and repair_completion_requires_checklist_override(conn, existing.get("linked_repair_record_id"))
            and not str(form.get("checklist_override_notes") or "").strip()
        ):
            raise HTTPException(
                status_code=400,
                detail="Repair checklist contains incomplete items. Override notes are required.",
            )
        next_finding_status = "Completed" if repair_status == "completed" else "Approved"
        conn.execute(
            f"""
            UPDATE findings_records
            SET repair_work_status = ?,
                repair_work_updated_at = ?,
                status = ?
            WHERE {finding_record_where_sql(conn)}
            """,
            (
                repair_status,
                now,
                next_finding_status,
                *finding_record_where_params(conn, finding_id, customer_id, vehicle_id),
            ),
        )
        if previous_repair_status != repair_status:
            append_finding_history_record(
                conn,
                finding_id,
                previous_repair_status,
                repair_status,
                "repair_work_status_changed",
                now,
                notes=str(form.get("checklist_override_notes") or "").strip(),
            )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}#repair-workspace",
        status_code=303,
    )


def create_discrepancy_approval_record(
    conn: sqlite3.Connection,
    customer_id: int,
    vehicle_id: int,
    form: dict[str, str],
    now: str,
) -> int:
    decision = normalize_approval_decision(form.get("customer_decision"))
    decision_recorded_at = now if decision != "pending" else ""
    request_type = normalize_approval_request_type(form.get("request_type"))
    labor_hours = optional_float(form, "labor_hours") if request_type == "labor" else None
    labor_rate = optional_float(form, "labor_rate") if request_type == "labor" else None
    labor_amount = approval_labor_amount(labor_hours, labor_rate)
    labor_reason = form.get("labor_reason", "") if request_type == "labor" else ""
    part_name = (form.get("part_name") or form.get("part_description") or "") if request_type == "parts" else ""
    part_number = form.get("part_number", "") if request_type == "parts" else ""
    part_description = part_name
    quantity = optional_float(form, "quantity") if request_type == "parts" else None
    unit_cost = optional_float(form, "unit_cost") if request_type == "parts" else None
    parts_total = approval_parts_amount(quantity, unit_cost)
    parts_amount = parts_total
    ensure_discrepancy_approvals_schema(conn)
    cur = conn.execute(
        """
        INSERT INTO discrepancy_approvals (
          customer_id, vehicle_id, request_type, finding_title, finding_description,
          recommended_repair, estimated_cost, labor_hours, labor_rate,
          labor_amount, labor_reason, part_description, part_name, part_number,
          quantity, unit_cost, parts_amount, parts_total, customer_decision,
          repair_work_status, repair_work_updated_at, decision_notes,
          decision_recorded_at, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            customer_id,
            vehicle_id,
            request_type,
            form.get("finding_title", ""),
            form.get("finding_description", ""),
            form.get("recommended_repair", ""),
            optional_float(form, "estimated_cost"),
            labor_hours,
            labor_rate,
            labor_amount,
            labor_reason,
            part_description,
            part_name,
            part_number,
            quantity,
            unit_cost,
            parts_amount,
            parts_total,
            decision,
            "ready" if decision == "approved" else "",
            now if decision == "approved" else "",
            form.get("decision_notes", ""),
            decision_recorded_at,
            now,
            now,
        ),
    )
    approval_id = int(cur.lastrowid)
    if request_type == "parts":
        append_discrepancy_approval_event(
            conn,
            approval_id,
            customer_id,
            vehicle_id,
            "parts_requested",
            approval_parts_event_label(),
            now,
        )
        if decision != "pending":
            append_discrepancy_approval_event(
                conn,
                approval_id,
                customer_id,
                vehicle_id,
                f"parts_{decision}",
                approval_parts_event_label(decision),
                now,
            )
    return approval_id


@router.post("/approvals")
async def pro_approval_record_create_from_dashboard(request: Request):
    form = await read_form_data(request)
    vehicle_key = form.get("vehicle_key", "")
    try:
        customer_id, vehicle_id = [int(part) for part in vehicle_key.split(":", 1)]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Select a customer vehicle") from exc
    now = datetime.utcnow().isoformat()
    conn = crm_db_conn()
    try:
        load_customer_vehicle(conn, customer_id, vehicle_id)
        approval_id = create_discrepancy_approval_record(conn, customer_id, vehicle_id, form, now)
        conn.commit()
        activity_payload = vehicle_finding_activity_payload(conn, customer_id, vehicle_id)
    finally:
        conn.close()
    if request.headers.get("x-requested-with") == "fetch":
        return JSONResponse(
            {
                "message": "Approval request saved",
                "approval_id": approval_id,
                "redirect_url": f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/approvals/{approval_id}",
                **activity_payload,
            }
        )
    return RedirectResponse(
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/approvals/{approval_id}",
        status_code=303,
    )


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/approvals")
async def pro_approval_record_create(request: Request, customer_id: int, vehicle_id: int):
    form = await read_form_data(request)
    now = datetime.utcnow().isoformat()

    conn = crm_db_conn()
    try:
        load_customer_vehicle(conn, customer_id, vehicle_id)
        approval_id = create_discrepancy_approval_record(conn, customer_id, vehicle_id, form, now)
        conn.commit()
        activity_payload = vehicle_finding_activity_payload(conn, customer_id, vehicle_id)
    finally:
        conn.close()
    if request.headers.get("x-requested-with") == "fetch":
        return JSONResponse(
            {
                "message": "Approval request saved",
                "approval_id": approval_id,
                "redirect_url": f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/approvals/{approval_id}",
                **activity_payload,
            }
        )
    return RedirectResponse(f"/pro/customers/{customer_id}/vehicles/{vehicle_id}", status_code=303)


@router.get("/customers/{customer_id}/vehicles/{vehicle_id}/approvals/{approval_id}", response_class=HTMLResponse)
def pro_approval_record_detail(
    request: Request, customer_id: int, vehicle_id: int, approval_id: int
):
    conn = crm_db_conn()
    try:
        customer, vehicle = load_customer_vehicle(conn, customer_id, vehicle_id)
        approval = load_approval_record(conn, customer_id, vehicle_id, approval_id)
        checklist_summary = repair_checklist_summary(conn, approval.get("linked_repair_record_id"))
    finally:
        conn.close()

    return templates.TemplateResponse(
        "pro/approval_detail.html",
        {
            "request": request,
            "customer": customer,
            "vehicle": vehicle,
            "approval": approval,
            "checklist_summary": checklist_summary,
            "repair_work_status_options": [
                {"value": value, "label": REPAIR_WORK_STATUS_LABELS[value]}
                for value in REPAIR_WORK_STATUS_OPTIONS
            ],
            "repair_work_status_label": repair_work_status_label(approval.get("repair_work_status") or "ready"),
        },
    )


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/approvals/{approval_id}")
async def pro_approval_record_update(
    request: Request, customer_id: int, vehicle_id: int, approval_id: int
):
    form = await read_form_data(request)
    now = datetime.utcnow().isoformat()
    decision = normalize_approval_decision(form.get("customer_decision"))
    request_type = normalize_approval_request_type(form.get("request_type"))
    labor_hours = optional_float(form, "labor_hours") if request_type == "labor" else None
    labor_rate = optional_float(form, "labor_rate") if request_type == "labor" else None
    labor_amount = approval_labor_amount(labor_hours, labor_rate)
    labor_reason = form.get("labor_reason", "") if request_type == "labor" else ""
    part_name = (form.get("part_name") or form.get("part_description") or "") if request_type == "parts" else ""
    part_number = form.get("part_number", "") if request_type == "parts" else ""
    part_description = part_name
    quantity = optional_float(form, "quantity") if request_type == "parts" else None
    unit_cost = optional_float(form, "unit_cost") if request_type == "parts" else None
    parts_total = approval_parts_amount(quantity, unit_cost)
    parts_amount = parts_total

    conn = crm_db_conn()
    try:
        load_customer_vehicle(conn, customer_id, vehicle_id)
        ensure_discrepancy_approvals_schema(conn)
        existing = load_approval_record(conn, customer_id, vehicle_id, approval_id)
        if decision == "pending":
            decision_recorded_at = ""
        else:
            decision_recorded_at = existing.get("decision_recorded_at") or now
        cur = conn.execute(
            """
            UPDATE discrepancy_approvals
            SET
              request_type = ?,
              finding_title = ?,
              finding_description = ?,
              recommended_repair = ?,
              estimated_cost = ?,
              labor_hours = ?,
              labor_rate = ?,
              labor_amount = ?,
              labor_reason = ?,
              part_description = ?,
              part_name = ?,
              part_number = ?,
              quantity = ?,
              unit_cost = ?,
              parts_amount = ?,
              parts_total = ?,
              customer_decision = ?,
              repair_work_status = CASE
                WHEN ? = 'approved' AND (repair_work_status IS NULL OR TRIM(repair_work_status) = '')
                THEN 'ready'
                WHEN ? != 'approved'
                THEN ''
                ELSE repair_work_status
              END,
              repair_work_updated_at = CASE
                WHEN ? = 'approved' AND (repair_work_updated_at IS NULL OR TRIM(repair_work_updated_at) = '')
                THEN ?
                WHEN ? != 'approved'
                THEN ''
                ELSE repair_work_updated_at
              END,
              decision_notes = ?,
              decision_recorded_at = ?,
              updated_at = ?
            WHERE id = ? AND customer_id = ? AND vehicle_id = ?
            """,
            (
                request_type,
                form.get("finding_title", ""),
                form.get("finding_description", ""),
                form.get("recommended_repair", ""),
                optional_float(form, "estimated_cost"),
                labor_hours,
                labor_rate,
                labor_amount,
                labor_reason,
                part_description,
                part_name,
                part_number,
                quantity,
                unit_cost,
                parts_amount,
                parts_total,
                decision,
                decision,
                decision,
                decision,
                now,
                decision,
                form.get("decision_notes", ""),
                decision_recorded_at,
                now,
                approval_id,
                customer_id,
                vehicle_id,
            ),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Approval record not found")
        existing_request_type = normalize_approval_request_type(existing.get("request_type"))
        previous_decision = normalize_approval_decision(existing.get("customer_decision"))
        if request_type == "parts" and existing_request_type != "parts":
            append_discrepancy_approval_event(
                conn,
                approval_id,
                customer_id,
                vehicle_id,
                "parts_requested",
                approval_parts_event_label(),
                now,
            )
        if request_type == "parts" and decision != "pending" and previous_decision != decision:
            append_discrepancy_approval_event(
                conn,
                approval_id,
                customer_id,
                vehicle_id,
                f"parts_{decision}",
                approval_parts_event_label(decision),
                now,
            )
        conn.commit()
        activity_payload = vehicle_finding_activity_payload(conn, customer_id, vehicle_id)
    finally:
        conn.close()
    if request.headers.get("x-requested-with") == "fetch":
        return JSONResponse(
            {
                "message": "Approval request saved",
                "approval_id": approval_id,
                "redirect_url": f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/approvals/{approval_id}",
                **activity_payload,
            }
        )
    return RedirectResponse(
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/approvals/{approval_id}",
        status_code=303,
    )


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/approvals/{approval_id}/approve")
async def pro_approval_record_approve(
    request: Request, customer_id: int, vehicle_id: int, approval_id: int
):
    form = await read_form_data(request)
    now = datetime.utcnow().isoformat()
    conn = crm_db_conn()
    try:
        load_customer_vehicle(conn, customer_id, vehicle_id)
        ensure_discrepancy_approvals_schema(conn)
        existing = load_approval_record(conn, customer_id, vehicle_id, approval_id)
        previous_decision = normalize_approval_decision(existing.get("customer_decision"))
        cur = conn.execute(
            """
            UPDATE discrepancy_approvals
            SET customer_decision = 'approved',
                repair_work_status = COALESCE(NULLIF(repair_work_status, ''), 'ready'),
                repair_work_updated_at = COALESCE(NULLIF(repair_work_updated_at, ''), ?),
                decision_notes = ?,
                decision_recorded_at = ?,
                updated_at = ?
            WHERE id = ? AND customer_id = ? AND vehicle_id = ?
            """,
            (
                now,
                form.get("decision_notes", ""),
                now,
                now,
                approval_id,
                customer_id,
                vehicle_id,
            ),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Approval record not found")
        if normalize_approval_request_type(existing.get("request_type")) == "parts" and previous_decision != "approved":
            append_discrepancy_approval_event(
                conn,
                approval_id,
                customer_id,
                vehicle_id,
                "parts_approved",
                approval_parts_event_label("approved"),
                now,
            )
        conn.commit()
        activity_payload = vehicle_finding_activity_payload(conn, customer_id, vehicle_id)
    finally:
        conn.close()
    if request.headers.get("x-requested-with") == "fetch":
        return JSONResponse({"status": "approved", "message": "Approval Updated", **activity_payload})
    return RedirectResponse(
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/approvals/{approval_id}",
        status_code=303,
    )


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/approvals/{approval_id}/decline")
async def pro_approval_record_decline(
    request: Request, customer_id: int, vehicle_id: int, approval_id: int
):
    form = await read_form_data(request)
    now = datetime.utcnow().isoformat()
    conn = crm_db_conn()
    try:
        load_customer_vehicle(conn, customer_id, vehicle_id)
        ensure_discrepancy_approvals_schema(conn)
        existing = load_approval_record(conn, customer_id, vehicle_id, approval_id)
        previous_decision = normalize_approval_decision(existing.get("customer_decision"))
        cur = conn.execute(
            """
            UPDATE discrepancy_approvals
            SET customer_decision = 'declined',
                repair_work_status = '',
                repair_work_updated_at = '',
                decision_notes = ?,
                decision_recorded_at = ?,
                updated_at = ?
            WHERE id = ? AND customer_id = ? AND vehicle_id = ?
            """,
            (
                form.get("decision_notes", ""),
                now,
                now,
                approval_id,
                customer_id,
                vehicle_id,
            ),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Approval record not found")
        if normalize_approval_request_type(existing.get("request_type")) == "parts" and previous_decision != "declined":
            append_discrepancy_approval_event(
                conn,
                approval_id,
                customer_id,
                vehicle_id,
                "parts_declined",
                approval_parts_event_label("declined"),
                now,
            )
        conn.commit()
        activity_payload = vehicle_finding_activity_payload(conn, customer_id, vehicle_id)
    finally:
        conn.close()
    if request.headers.get("x-requested-with") == "fetch":
        return JSONResponse({"status": "declined", "message": "Approval Updated", **activity_payload})
    return RedirectResponse(
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/approvals/{approval_id}",
        status_code=303,
    )


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/approvals/{approval_id}/defer")
async def pro_approval_record_defer(
    request: Request, customer_id: int, vehicle_id: int, approval_id: int
):
    form = await read_form_data(request)
    now = datetime.utcnow().isoformat()
    conn = crm_db_conn()
    try:
        load_customer_vehicle(conn, customer_id, vehicle_id)
        ensure_discrepancy_approvals_schema(conn)
        existing = load_approval_record(conn, customer_id, vehicle_id, approval_id)
        previous_decision = normalize_approval_decision(existing.get("customer_decision"))
        cur = conn.execute(
            """
            UPDATE discrepancy_approvals
            SET customer_decision = 'deferred',
                repair_work_status = '',
                repair_work_updated_at = '',
                decision_notes = ?,
                decision_recorded_at = ?,
                updated_at = ?
            WHERE id = ? AND customer_id = ? AND vehicle_id = ?
            """,
            (
                form.get("decision_notes", ""),
                now,
                now,
                approval_id,
                customer_id,
                vehicle_id,
            ),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Approval record not found")
        if normalize_approval_request_type(existing.get("request_type")) == "parts" and previous_decision != "deferred":
            append_discrepancy_approval_event(
                conn,
                approval_id,
                customer_id,
                vehicle_id,
                "parts_deferred",
                approval_parts_event_label("deferred"),
                now,
            )
        conn.commit()
        activity_payload = vehicle_finding_activity_payload(conn, customer_id, vehicle_id)
    finally:
        conn.close()
    if request.headers.get("x-requested-with") == "fetch":
        return JSONResponse({"status": "deferred", "message": "Approval Updated", **activity_payload})
    return RedirectResponse(
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/approvals/{approval_id}",
        status_code=303,
    )


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/approvals/{approval_id}/repair-work")
async def pro_approval_repair_work_status_update(
    request: Request, customer_id: int, vehicle_id: int, approval_id: int
):
    raise HTTPException(status_code=405, detail="Repair status is managed from the linked repair execution record")
    form = await read_form_data(request)
    repair_status = normalize_repair_work_status(form.get("repair_work_status"))
    now = datetime.utcnow().isoformat()

    conn = crm_db_conn()
    try:
        load_customer_vehicle(conn, customer_id, vehicle_id)
        ensure_discrepancy_approvals_schema(conn)
        existing = load_approval_record(conn, customer_id, vehicle_id, approval_id)
        if normalize_approval_decision(existing.get("customer_decision")) != "approved":
            raise HTTPException(status_code=400, detail="Approval request must be approved before repair work starts")
        previous_repair_status = existing.get("repair_work_status") or "ready"
        if (
            repair_status == "completed"
            and repair_completion_requires_checklist_override(conn, existing.get("linked_repair_record_id"))
            and not str(form.get("checklist_override_notes") or "").strip()
        ):
            raise HTTPException(
                status_code=400,
                detail="Repair checklist contains incomplete items. Override notes are required.",
            )
        conn.execute(
            """
            UPDATE discrepancy_approvals
            SET repair_work_status = ?,
                repair_work_updated_at = ?,
                updated_at = ?
            WHERE id = ? AND customer_id = ? AND vehicle_id = ?
            """,
            (repair_status, now, now, approval_id, customer_id, vehicle_id),
        )
        if previous_repair_status != repair_status:
            checklist_override_notes = str(form.get("checklist_override_notes") or "").strip()
            append_discrepancy_approval_event(
                conn,
                approval_id,
                customer_id,
                vehicle_id,
                "repair_work_status_changed",
                (
                    f"Repair Workflow {repair_work_status_label(repair_status)}: {repair_work_title_from_approval(existing)}"
                    + (f" (Checklist override: {checklist_override_notes})" if checklist_override_notes else "")
                ),
                now,
            )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}#repair-workspace",
        status_code=303,
    )


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/history")
async def pro_service_history_create(request: Request, customer_id: int, vehicle_id: int):
    raise HTTPException(status_code=405, detail="Service history records are created automatically")


@router.get("/customers/{customer_id}/vehicles/{vehicle_id}/history/{history_id}", response_class=HTMLResponse)
def pro_service_history_detail(
    request: Request, customer_id: int, vehicle_id: int, history_id: int
):
    conn = crm_db_conn()
    try:
        customer, vehicle = load_customer_vehicle(conn, customer_id, vehicle_id)
        ensure_service_history_schema(conn)
        history = row_to_dict(
            conn.execute(
                """
                SELECT *
                FROM service_history
                WHERE id = ? AND customer_id = ? AND vehicle_id = ?
                """,
                (history_id, customer_id, vehicle_id),
            ).fetchone()
        )
        if not history:
            raise HTTPException(status_code=404, detail="Service history not found")
    finally:
        conn.close()

    return templates.TemplateResponse(
        "pro/service_history_detail.html",
        {
            "request": request,
            "customer": customer,
            "vehicle": vehicle,
            "history": history,
        },
    )


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/history/{history_id}")
async def pro_service_history_update(
    request: Request, customer_id: int, vehicle_id: int, history_id: int
):
    raise HTTPException(status_code=405, detail="Service history records are append-only")


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/history/{history_id}/delete")
async def pro_service_history_delete(
    customer_id: int, vehicle_id: int, history_id: int
):
    raise HTTPException(status_code=405, detail="Service history records are append-only")


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/maintenance")
async def pro_maintenance_record_create(request: Request, customer_id: int, vehicle_id: int):
    form = await read_form_data(request)
    now = datetime.utcnow().isoformat()
    service_type = form.get("service_type", "")
    date_performed = form.get("date_performed", "")
    mileage_performed = optional_int(form, "mileage_performed")
    interval_miles = maintenance_interval_value(form, service_type, "interval_miles")
    interval_months = maintenance_interval_value(form, service_type, "interval_months")
    due_mileage, due_date = maintenance_due_values(
        form,
        mileage_performed,
        date_performed,
        interval_miles,
        interval_months,
    )
    conn = crm_db_conn()
    try:
        load_customer_vehicle(conn, customer_id, vehicle_id)
        ensure_maintenance_records_schema(conn)
        ensure_repair_records_schema(conn)
        ensure_service_history_schema(conn)
        ensure_service_history_records_schema(conn)
        cur = conn.execute(
            """
            INSERT INTO maintenance_records (
              customer_id, vehicle_id, service_type, date_performed,
              mileage_performed, interval_miles, interval_months,
              due_mileage, due_date, notes, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                customer_id,
                vehicle_id,
                service_type,
                date_performed,
                mileage_performed,
                interval_miles,
                interval_months,
                due_mileage,
                due_date,
                form.get("notes", ""),
                now,
                now,
            ),
        )
        append_service_history_record(
            conn,
            customer_id=customer_id,
            vehicle_id=vehicle_id,
            source_type="maintenance",
            source_record_id=int(cur.lastrowid),
            service_name=service_type,
            service_date=date_performed,
            mileage=mileage_performed,
            notes=form.get("notes", ""),
            created_at=now,
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(f"/pro/customers/{customer_id}/vehicles/{vehicle_id}", status_code=303)


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/repairs")
async def pro_repair_record_create(request: Request, customer_id: int, vehicle_id: int):
    form = await read_form_data(request)
    now = datetime.utcnow().isoformat()
    parts_cost = optional_float(form, "parts_cost")
    labor_hours = optional_float(form, "labor_hours")
    labor_rate = optional_float(form, "labor_rate")
    legacy_labor_cost = optional_float(form, "labor_cost")
    labor_cost = (
        round(float(labor_hours or 0) * float(labor_rate or 0), 2)
        if labor_hours is not None and labor_rate is not None
        else legacy_labor_cost
    )
    total_cost = float(parts_cost or 0) + float(labor_cost or 0)
    repair_name = form.get("repair_name", "")
    parts_search_term = form.get("parts_search_term", "")
    repair_date = form.get("repair_date", "")
    mileage = optional_int(form, "mileage")
    notes = form.get("notes", "")
    update_maintenance = form.get("also_update_maintenance_tracking") == "1"
    workflow_source_type = normalize_workflow_source_type(form.get("workflow_source_type"))
    workflow_source_id = optional_int(form, "workflow_source_id") if workflow_source_type else None

    conn = crm_db_conn()
    try:
        load_customer_vehicle(conn, customer_id, vehicle_id)
        ensure_repair_records_schema(conn)
        ensure_findings_records_schema(conn)
        ensure_discrepancy_approvals_schema(conn)
        ensure_maintenance_records_schema(conn)
        ensure_service_history_schema(conn)
        ensure_service_history_records_schema(conn)
        if workflow_source_type and workflow_source_id is not None:
            existing_repair = conn.execute(
                """
                SELECT id
                FROM repair_records
                WHERE workflow_source_type = ? AND workflow_source_id = ?
                LIMIT 1
                """,
                (workflow_source_type, workflow_source_id),
            ).fetchone()
            if existing_repair:
                return RedirectResponse(
                    f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{existing_repair['id']}",
                    status_code=303,
                )
            if workflow_source_type == "finding":
                workflow_record = load_finding_record(conn, customer_id, vehicle_id, workflow_source_id)
                if (workflow_record.get("status") or "") not in {"Approved", "Completed"}:
                    raise HTTPException(status_code=400, detail="Finding must be approved before creating a repair record")
                if workflow_record.get("linked_repair_record_id"):
                    return RedirectResponse(
                        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{workflow_record['linked_repair_record_id']}",
                        status_code=303,
                    )
            else:
                workflow_record = load_approval_record(conn, customer_id, vehicle_id, workflow_source_id)
                if normalize_approval_decision(workflow_record.get("customer_decision")) != "approved":
                    raise HTTPException(status_code=400, detail="Approval request must be approved before creating a repair record")
                if workflow_record.get("linked_repair_record_id"):
                    return RedirectResponse(
                        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{workflow_record['linked_repair_record_id']}",
                        status_code=303,
                    )
        cur = conn.execute(
            """
            INSERT INTO repair_records (
              vehicle_id, customer_id, repair_name, repair_date, mileage,
              labor_hours, labor_rate, parts_cost, labor_cost, total_cost,
              track_as_maintenance, workflow_source_type, workflow_source_id, parts_search_term,
              notes, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                vehicle_id,
                customer_id,
                repair_name,
                repair_date,
                mileage,
                labor_hours,
                labor_rate,
                parts_cost,
                labor_cost,
                total_cost,
                1 if update_maintenance else 0,
                workflow_source_type or "",
                workflow_source_id,
                parts_search_term,
                notes,
                now,
            ),
        )
        repair_id = int(cur.lastrowid)
        if workflow_source_type == "finding" and workflow_source_id is not None:
            conn.execute(
                f"""
                UPDATE findings_records
                SET linked_repair_record_id = ?,
                    repair_record_created_at = ?
                WHERE {finding_record_where_sql(conn)}
                """,
                (
                    repair_id,
                    now,
                    *finding_record_where_params(conn, workflow_source_id, customer_id, vehicle_id),
                ),
            )
        elif workflow_source_type == "approval" and workflow_source_id is not None:
            conn.execute(
                """
                UPDATE discrepancy_approvals
                SET linked_repair_record_id = ?,
                    repair_record_created_at = ?,
                    updated_at = ?
                WHERE id = ? AND customer_id = ? AND vehicle_id = ?
                """,
                (repair_id, now, now, workflow_source_id, customer_id, vehicle_id),
            )
        if update_maintenance:
            upsert_maintenance_from_repair(
                conn,
                customer_id=customer_id,
                vehicle_id=vehicle_id,
                service_type=repair_name,
                date_performed=repair_date,
                mileage_performed=mileage,
                notes=notes,
                now=now,
            )
        append_service_history_record(
            conn,
            customer_id=customer_id,
            vehicle_id=vehicle_id,
            source_type="repair",
            source_record_id=repair_id,
            service_name=repair_name,
            service_date=repair_date,
            mileage=mileage,
            labor_hours=labor_hours,
            parts_cost=parts_cost,
            labor_cost=labor_cost,
            total_cost=total_cost,
            notes=notes,
            created_at=now,
        )
        conn.commit()
    finally:
        conn.close()
    if form.get("return_to") == "repair_detail":
        return RedirectResponse(
            f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{repair_id}",
            status_code=303,
        )
    return RedirectResponse(
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}#vehicle-timeline",
        status_code=303,
    )


@router.get(
    "/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{repair_id}",
    response_class=HTMLResponse,
)
def pro_repair_record_detail(
    request: Request, customer_id: int, vehicle_id: int, repair_id: int
):
    conn = crm_db_conn()
    try:
        customer, vehicle = load_customer_vehicle(conn, customer_id, vehicle_id)
        repair = load_repair_record(conn, customer_id, vehicle_id, repair_id)
        invoice = load_invoice_for_repair(conn, repair_id)
        repair_execution_status = repair_execution_status_context(conn, repair, customer_id, vehicle_id)
        invoice_warnings = repair_invoice_warnings(repair) if not invoice else []
        checklist_items = load_repair_checklist_items(conn, repair_id)
        checklist_progress = repair_checklist_progress(checklist_items)
        completion = load_repair_completion(conn, repair_id)
        completion_progress = repair_completion_progress(completion)
        source_finding = load_repair_source_finding_for_detail(conn, repair, customer_id, vehicle_id)
        seed_repair_intelligence(conn)
        repair_intelligence_records = load_repair_intelligence_for_repair(
            conn,
            vehicle,
            repair.get("repair_name"),
        )
        if not repair_intelligence_records:
            repair_intelligence_records = [
                record
                for record in load_repair_intelligence_seed_records()
                if repair_intelligence_matches_repair(record, repair.get("repair_name"))
            ]
    finally:
        conn.close()

    repair_display_mileage = repair.get("mileage")
    if repair_display_mileage is None:
        repair_display_mileage = vehicle.get("mileage")

    return templates.TemplateResponse(
        "pro/repair_detail.html",
        {
            "request": request,
            "customer": customer,
            "vehicle": vehicle,
            "repair": repair,
            "repair_display_mileage": repair_display_mileage,
            "invoice": invoice,
            "invoice_warnings": invoice_warnings,
            "repair_execution_status": repair_execution_status,
            "checklist_items": checklist_items,
            "checklist_progress": checklist_progress,
            "completion": completion,
            "source_finding": source_finding,
            "completion_checks": REPAIR_COMPLETION_CHECKS,
            "completion_progress": completion_progress,
            "completion_warnings": [],
            "repair_intelligence_records": repair_intelligence_records,
            "repair_job_part_status_options": REPAIR_JOB_PART_STATUS_OPTIONS,
        },
    )


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{repair_id}/parts")
async def pro_repair_job_part_create(request: Request, customer_id: int, vehicle_id: int, repair_id: int):
    form = await read_form_data(request)
    now = datetime.utcnow().isoformat()
    conn = crm_db_conn()
    try:
        load_customer_vehicle(conn, customer_id, vehicle_id)
        load_repair_record(conn, customer_id, vehicle_id, repair_id)
        create_repair_job_part(conn, repair_id, form, now)
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{repair_id}#parts-tracking",
        status_code=303,
    )


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{repair_id}/parts/{part_id}")
async def pro_repair_job_part_update(
    request: Request, customer_id: int, vehicle_id: int, repair_id: int, part_id: int
):
    form = await read_form_data(request)
    now = datetime.utcnow().isoformat()
    conn = crm_db_conn()
    try:
        load_customer_vehicle(conn, customer_id, vehicle_id)
        load_repair_record(conn, customer_id, vehicle_id, repair_id)
        update_repair_job_part(conn, repair_id, part_id, form, now)
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{repair_id}#parts-tracking",
        status_code=303,
    )


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{repair_id}/parts/{part_id}/delete")
async def pro_repair_job_part_delete(customer_id: int, vehicle_id: int, repair_id: int, part_id: int):
    conn = crm_db_conn()
    try:
        load_customer_vehicle(conn, customer_id, vehicle_id)
        load_repair_record(conn, customer_id, vehicle_id, repair_id)
        delete_repair_job_part(conn, repair_id, part_id)
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{repair_id}#parts-tracking",
        status_code=303,
    )


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{repair_id}/invoice")
async def pro_invoice_generate(request: Request, customer_id: int, vehicle_id: int, repair_id: int):
    now = datetime.utcnow().isoformat()
    conn = crm_db_conn()
    try:
        load_customer_vehicle(conn, customer_id, vehicle_id)
        repair = load_repair_record(conn, customer_id, vehicle_id, repair_id)
        warnings = repair_invoice_warnings(repair)
        if warnings and not load_invoice_for_repair(conn, repair_id):
            context = completion_detail_context(
                conn,
                request=request,
                customer_id=customer_id,
                vehicle_id=vehicle_id,
                repair_id=repair_id,
                completion_warnings=[],
            )
            context["invoice_warnings"] = warnings
            return templates.TemplateResponse(
                "pro/repair_detail.html",
                context,
                status_code=400,
            )
        invoice = create_invoice_for_repair(
            conn,
            repair=repair,
            customer_id=customer_id,
            vehicle_id=vehicle_id,
            now=now,
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/invoices/{invoice['id']}",
        status_code=303,
    )


@router.get("/customers/{customer_id}/vehicles/{vehicle_id}/invoices/new", response_class=HTMLResponse)
def pro_invoice_builder(request: Request, customer_id: int, vehicle_id: int):
    conn = crm_db_conn()
    try:
        customer, vehicle = load_customer_vehicle(conn, customer_id, vehicle_id)
        job_groups = load_invoice_builder_jobs(conn, customer_id, vehicle_id)
    finally:
        conn.close()
    return templates.TemplateResponse(
        "pro/invoice_builder.html",
        {
            "request": request,
            "customer": customer,
            "vehicle": vehicle,
            "job_groups": job_groups,
            "error": "",
        },
    )


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/invoices")
async def pro_invoice_create(request: Request, customer_id: int, vehicle_id: int):
    raw_body = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qs(raw_body, keep_blank_values=True)
    selected_ids = [
        int(value)
        for value in parsed.get("repair_record_id", [])
        if str(value).strip().isdigit()
    ]
    now = datetime.utcnow().isoformat()
    conn = crm_db_conn()
    try:
        customer, vehicle = load_customer_vehicle(conn, customer_id, vehicle_id)
        repairs = [
            load_repair_record(conn, customer_id, vehicle_id, repair_id)
            for repair_id in selected_ids
        ]
        try:
            invoice = create_invoice_for_repairs(
                conn,
                repairs=repairs,
                customer_id=customer_id,
                vehicle_id=vehicle_id,
                now=now,
            )
        except HTTPException as exc:
            job_groups = load_invoice_builder_jobs(conn, customer_id, vehicle_id)
            return templates.TemplateResponse(
                "pro/invoice_builder.html",
                {
                    "request": request,
                    "customer": customer,
                    "vehicle": vehicle,
                    "job_groups": job_groups,
                    "error": str(exc.detail),
                },
                status_code=400,
            )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/invoices/{invoice['id']}",
        status_code=303,
    )


@router.get("/customers/{customer_id}/vehicles/{vehicle_id}/invoices/{invoice_id}", response_class=HTMLResponse)
def pro_invoice_detail(
    request: Request, customer_id: int, vehicle_id: int, invoice_id: int
):
    conn = crm_db_conn()
    try:
        customer, vehicle = load_customer_vehicle(conn, customer_id, vehicle_id)
        invoice = load_invoice_record(conn, customer_id, vehicle_id, invoice_id)
        shop_name = load_shop_name(conn)
    finally:
        conn.close()

    return templates.TemplateResponse(
        "pro/invoice_detail.html",
        {
            "request": request,
            "customer": customer,
            "vehicle": vehicle,
            "invoice": invoice,
            "shop_name": shop_name,
        },
    )


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/invoices/{invoice_id}/recalculate")
async def pro_invoice_recalculate(
    customer_id: int, vehicle_id: int, invoice_id: int
):
    conn = crm_db_conn()
    try:
        load_customer_vehicle(conn, customer_id, vehicle_id)
        recalculate_invoice_from_repair(
            conn,
            invoice_id=invoice_id,
            customer_id=customer_id,
            vehicle_id=vehicle_id,
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/invoices/{invoice_id}",
        status_code=303,
    )


@router.get("/customers/{customer_id}/vehicles/{vehicle_id}/invoices/{invoice_id}/pdf")
def pro_invoice_pdf(request: Request, customer_id: int, vehicle_id: int, invoice_id: int):
    conn = crm_db_conn()
    try:
        customer, vehicle = load_customer_vehicle(conn, customer_id, vehicle_id)
        invoice = load_invoice_record(conn, customer_id, vehicle_id, invoice_id)
        shop_name = load_shop_name(conn)
    finally:
        conn.close()
    content = build_invoice_pdf_bytes(
        invoice=invoice,
        customer=customer,
        vehicle=vehicle,
        shop_name=shop_name,
        display_options=invoice_pdf_options_from_query(request.query_params),
    )
    return Response(
        content,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={invoice_filename(invoice)}"},
    )


@router.get("/customers/{customer_id}/vehicles/{vehicle_id}/estimates/{estimate_id}/pdf")
def pro_estimate_document_pdf(customer_id: int, vehicle_id: int, estimate_id: int):
    conn = crm_db_conn()
    try:
        ensure_repair_estimate_documents_schema(conn)
        record = row_to_dict(
            conn.execute(
                """
                SELECT *
                FROM repair_estimate_documents
                WHERE id = ?
                  AND customer_id = ?
                  AND vehicle_id = ?
                """,
                (estimate_id, customer_id, vehicle_id),
            ).fetchone()
        )
        if not record:
            raise HTTPException(status_code=404, detail="Estimate PDF not found")
    finally:
        conn.close()

    pdf_path = Path(record.get("pdf_path") or "").resolve()
    try:
        pdf_path.relative_to(ESTIMATE_PDF_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=404, detail="Estimate PDF not found")
    if not pdf_path.exists() or not pdf_path.is_file():
        raise HTTPException(status_code=404, detail="Estimate PDF file not found")

    title = re.sub(r"[^A-Za-z0-9_-]+", "-", str(record.get("related_title") or "repair-estimate")).strip("-")
    filename = f"{title or 'repair-estimate'}-{estimate_id}.pdf"
    return Response(
        pdf_path.read_bytes(),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def completion_detail_context(
    conn: sqlite3.Connection,
    *,
    request: Request,
    customer_id: int,
    vehicle_id: int,
    repair_id: int,
    completion_warnings: list[str] | None = None,
) -> dict[str, Any]:
    customer, vehicle = load_customer_vehicle(conn, customer_id, vehicle_id)
    repair = load_repair_record(conn, customer_id, vehicle_id, repair_id)
    invoice = load_invoice_for_repair(conn, repair_id)
    repair_execution_status = repair_execution_status_context(conn, repair, customer_id, vehicle_id)
    invoice_warnings = repair_invoice_warnings(repair) if not invoice else []
    checklist_items = load_repair_checklist_items(conn, repair_id)
    checklist_progress = repair_checklist_progress(checklist_items)
    completion = load_repair_completion(conn, repair_id)
    completion_progress = repair_completion_progress(completion)
    source_finding = load_repair_source_finding_for_detail(conn, repair, customer_id, vehicle_id)
    seed_repair_intelligence(conn)
    repair_intelligence_records = load_repair_intelligence_for_repair(
        conn,
        vehicle,
        repair.get("repair_name"),
    )
    if not repair_intelligence_records:
        repair_intelligence_records = [
            record
            for record in load_repair_intelligence_seed_records()
            if repair_intelligence_matches_repair(record, repair.get("repair_name"))
        ]
    return {
        "request": request,
        "customer": customer,
        "vehicle": vehicle,
        "repair": repair,
        "repair_display_mileage": repair.get("mileage") if repair.get("mileage") is not None else vehicle.get("mileage"),
        "invoice": invoice,
        "invoice_warnings": invoice_warnings,
        "repair_execution_status": repair_execution_status,
        "checklist_items": checklist_items,
        "checklist_progress": checklist_progress,
        "completion": completion,
        "source_finding": source_finding,
        "completion_checks": REPAIR_COMPLETION_CHECKS,
        "completion_progress": completion_progress,
        "completion_warnings": completion_warnings or [],
        "repair_intelligence_records": repair_intelligence_records,
        "repair_job_part_status_options": REPAIR_JOB_PART_STATUS_OPTIONS,
    }


def sync_repair_completion_source(
    conn: sqlite3.Connection,
    *,
    repair: dict[str, Any],
    customer_id: int,
    vehicle_id: int,
    now: str,
) -> None:
    source_type = normalize_workflow_source_type(repair.get("workflow_source_type"))
    source_id = repair.get("workflow_source_id")
    if not source_type or source_id is None:
        return
    source_id = int(source_id)
    if source_type == "finding":
        existing = load_finding_record(conn, customer_id, vehicle_id, source_id)
        previous_status = existing.get("status") or ""
        previous_repair_status = existing.get("repair_work_status") or (
            "completed" if previous_status == "Completed" else "ready"
        )
        conn.execute(
            f"""
            UPDATE findings_records
            SET status = 'Completed',
                repair_work_status = 'completed',
                repair_work_updated_at = ?
            WHERE {finding_record_where_sql(conn)}
            """,
            (now, *finding_record_where_params(conn, source_id, customer_id, vehicle_id)),
        )
        if previous_status != "Completed":
            append_finding_history_record(
                conn,
                source_id,
                previous_status or None,
                "Completed",
                "status_changed",
                now,
            )
        if previous_repair_status != "completed":
            append_finding_history_record(
                conn,
                source_id,
                previous_repair_status,
                "completed",
                "repair_work_status_changed",
                now,
            )
        return

    existing = load_approval_record(conn, customer_id, vehicle_id, source_id)
    previous_repair_status = existing.get("repair_work_status") or "ready"
    conn.execute(
        """
        UPDATE discrepancy_approvals
        SET repair_work_status = 'completed',
            repair_work_updated_at = ?,
            updated_at = ?
        WHERE id = ? AND customer_id = ? AND vehicle_id = ?
        """,
        (now, now, source_id, customer_id, vehicle_id),
    )
    if previous_repair_status != "completed":
        append_discrepancy_approval_event(
            conn,
            source_id,
            customer_id,
            vehicle_id,
            "repair_work_status_changed",
            f"Repair Workflow {repair_work_status_label('completed')}: {repair_work_title_from_approval(existing)}",
            now,
        )


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{repair_id}/workflow-status")
async def pro_repair_execution_status_update(
    request: Request, customer_id: int, vehicle_id: int, repair_id: int
):
    form = await read_form_data(request)
    repair_status = normalize_repair_work_status(form.get("repair_work_status"))
    if repair_status == "completed":
        raise HTTPException(status_code=400, detail="Use Repair Completion to complete repair work")
    now = datetime.utcnow().isoformat()

    conn = crm_db_conn()
    try:
        load_customer_vehicle(conn, customer_id, vehicle_id)
        repair = load_repair_record(conn, customer_id, vehicle_id, repair_id)
        if (repair.get("status") or "") == "Completed":
            raise HTTPException(status_code=400, detail="Completed repairs cannot be moved back from Repair Execution")
        source_type = normalize_workflow_source_type(repair.get("workflow_source_type"))
        source_id = repair.get("workflow_source_id")
        if not source_type or source_id is None:
            raise HTTPException(status_code=400, detail="Repair is not linked to a repair workflow source")
        source_id = int(source_id)
        if source_type == "finding":
            existing = load_finding_record(conn, customer_id, vehicle_id, source_id)
            previous_repair_status = existing.get("repair_work_status") or (
                "completed" if existing.get("status") == "Completed" else "ready"
            )
            if (existing.get("status") or "") not in {"Approved", "Completed"}:
                raise HTTPException(status_code=400, detail="Finding must be approved before repair work starts")
            conn.execute(
                f"""
                UPDATE findings_records
                SET repair_work_status = ?,
                    repair_work_updated_at = ?,
                    status = 'Approved'
                WHERE {finding_record_where_sql(conn)}
                """,
                (
                    repair_status,
                    now,
                    *finding_record_where_params(conn, source_id, customer_id, vehicle_id),
                ),
            )
            if previous_repair_status != repair_status:
                append_finding_history_record(
                    conn,
                    source_id,
                    previous_repair_status,
                    repair_status,
                    "repair_work_status_changed",
                    now,
                )
        else:
            existing = load_approval_record(conn, customer_id, vehicle_id, source_id)
            previous_repair_status = existing.get("repair_work_status") or "ready"
            conn.execute(
                """
                UPDATE discrepancy_approvals
                SET repair_work_status = ?,
                    repair_work_updated_at = ?,
                    updated_at = ?
                WHERE id = ? AND customer_id = ? AND vehicle_id = ?
                """,
                (repair_status, now, now, source_id, customer_id, vehicle_id),
            )
            if previous_repair_status != repair_status:
                append_discrepancy_approval_event(
                    conn,
                    source_id,
                    customer_id,
                    vehicle_id,
                    "repair_work_status_changed",
                    f"Repair Workflow {repair_work_status_label(repair_status)}: {repair_work_title_from_approval(existing)}",
                    now,
                )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{repair_id}#repair-execution-status",
        status_code=303,
    )


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{repair_id}/completion")
async def pro_repair_completion_update(
    request: Request, customer_id: int, vehicle_id: int, repair_id: int
):
    content_type = request.headers.get("content-type", "")
    after_repair_photo_paths: list[str] = []
    if "multipart/form-data" in content_type:
        form, files = await read_multipart_form_data(request)
        after_repair_photo_paths = save_image_upload_paths(
            files.get("after_repair_photos"),
            max_files=PHOTO_UPLOAD_MAX_FILES,
            allowed_extensions=PHOTO_UPLOAD_ALLOWED_EXTENSIONS,
        )
    else:
        form = await read_form_data(request)
    now = datetime.utcnow().isoformat()
    conn = crm_db_conn()
    try:
        load_customer_vehicle(conn, customer_id, vehicle_id)
        repair = load_repair_record(conn, customer_id, vehicle_id, repair_id)
        completion_date = str(form.get("completion_date") or "").strip()
        completion_mileage = optional_int(form, "completion_mileage")
        completed_at = repair.get("completed_at") or (f"{completion_date}T{now.split('T', 1)[1]}" if completion_date else now)
        posted_completion = upsert_repair_completion(
            conn,
            repair_record_id=repair_id,
            form=form,
            after_repair_photo_paths=after_repair_photo_paths,
            completed_at=completed_at,
            now=now,
        )
        conn.execute(
            """
            UPDATE repair_records
            SET status = 'Completed',
                completed_at = COALESCE(NULLIF(completed_at, ''), ?),
                mileage = COALESCE(?, mileage)
            WHERE id = ? AND customer_id = ? AND vehicle_id = ?
            """,
            (completed_at, completion_mileage, repair_id, customer_id, vehicle_id),
        )
        refreshed_repair = load_repair_record(conn, customer_id, vehicle_id, repair_id)
        upsert_service_history_record(
            conn,
            customer_id=customer_id,
            vehicle_id=vehicle_id,
            source_type="repair",
            source_record_id=repair_id,
            service_name=refreshed_repair.get("repair_name") or "Repair",
            service_date=posted_completion.get("completion_date") or completed_at[:10],
            mileage=refreshed_repair.get("mileage"),
            labor_hours=refreshed_repair.get("labor_hours"),
            parts_cost=refreshed_repair.get("parts_cost"),
            labor_cost=refreshed_repair.get("labor_cost"),
            total_cost=refreshed_repair.get("total_cost"),
            notes=posted_completion.get("completion_notes") or posted_completion.get("technician_notes") or refreshed_repair.get("notes") or "",
            created_at=completed_at,
        )
        if refreshed_repair.get("track_as_maintenance"):
            upsert_maintenance_from_repair(
                conn,
                customer_id=customer_id,
                vehicle_id=vehicle_id,
                service_type=refreshed_repair.get("repair_name") or "Repair",
                date_performed=posted_completion.get("completion_date") or completed_at[:10],
                mileage_performed=refreshed_repair.get("mileage"),
                notes=posted_completion.get("completion_notes") or posted_completion.get("technician_notes") or refreshed_repair.get("notes") or "",
                now=now,
            )
        sync_repair_completion_source(
            conn,
            repair=refreshed_repair,
            customer_id=customer_id,
            vehicle_id=vehicle_id,
            now=now,
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}#repair-workspace",
        status_code=303,
    )


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{repair_id}/checklist")
async def pro_repair_checklist_item_create(
    request: Request, customer_id: int, vehicle_id: int, repair_id: int
):
    form = await read_form_data(request)
    task_name = str(form.get("task_name") or "").strip()
    if not task_name:
        raise HTTPException(status_code=400, detail="Checklist task name is required")
    now = datetime.utcnow().isoformat()
    conn = crm_db_conn()
    try:
        load_customer_vehicle(conn, customer_id, vehicle_id)
        load_repair_record(conn, customer_id, vehicle_id, repair_id)
        ensure_repair_checklist_schema(conn)
        next_order = conn.execute(
            "SELECT COALESCE(MAX(task_order), 0) + 1 AS next_order FROM repair_checklist_items WHERE repair_record_id = ?",
            (repair_id,),
        ).fetchone()["next_order"]
        conn.execute(
            """
            INSERT INTO repair_checklist_items (
              repair_record_id, task_name, task_order, completed, completed_at, notes, created_at
            )
            VALUES (?, ?, ?, 0, NULL, ?, ?)
            """,
            (repair_id, task_name, next_order, form.get("notes", ""), now),
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{repair_id}#repair-checklist",
        status_code=303,
    )


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{repair_id}/checklist/{item_id}")
async def pro_repair_checklist_item_update(
    request: Request, customer_id: int, vehicle_id: int, repair_id: int, item_id: int
):
    form = await read_form_data(request)
    task_name = str(form.get("task_name") or "").strip()
    if not task_name:
        raise HTTPException(status_code=400, detail="Checklist task name is required")
    conn = crm_db_conn()
    try:
        load_customer_vehicle(conn, customer_id, vehicle_id)
        load_repair_record(conn, customer_id, vehicle_id, repair_id)
        ensure_repair_checklist_schema(conn)
        cur = conn.execute(
            """
            UPDATE repair_checklist_items
            SET task_name = ?, notes = ?
            WHERE id = ? AND repair_record_id = ?
            """,
            (task_name, form.get("notes", ""), item_id, repair_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Checklist item not found")
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{repair_id}#repair-checklist",
        status_code=303,
    )


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{repair_id}/checklist/{item_id}/toggle")
async def pro_repair_checklist_item_toggle(
    request: Request, customer_id: int, vehicle_id: int, repair_id: int, item_id: int
):
    form = await read_form_data(request)
    completed = form.get("completed") == "1"
    now = datetime.utcnow().isoformat()
    conn = crm_db_conn()
    try:
        load_customer_vehicle(conn, customer_id, vehicle_id)
        load_repair_record(conn, customer_id, vehicle_id, repair_id)
        ensure_repair_checklist_schema(conn)
        cur = conn.execute(
            """
            UPDATE repair_checklist_items
            SET completed = ?, completed_at = ?
            WHERE id = ? AND repair_record_id = ?
            """,
            (1 if completed else 0, now if completed else None, item_id, repair_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Checklist item not found")
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{repair_id}#repair-checklist",
        status_code=303,
    )


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{repair_id}/checklist/{item_id}/move")
async def pro_repair_checklist_item_move(
    request: Request, customer_id: int, vehicle_id: int, repair_id: int, item_id: int
):
    form = await read_form_data(request)
    direction = str(form.get("direction") or "").strip()
    if direction not in {"up", "down"}:
        raise HTTPException(status_code=400, detail="Checklist move direction is invalid")
    conn = crm_db_conn()
    try:
        load_customer_vehicle(conn, customer_id, vehicle_id)
        load_repair_record(conn, customer_id, vehicle_id, repair_id)
        items = load_repair_checklist_items(conn, repair_id)
        index = next((idx for idx, item in enumerate(items) if int(item["id"]) == item_id), None)
        if index is None:
            raise HTTPException(status_code=404, detail="Checklist item not found")
        swap_index = index - 1 if direction == "up" else index + 1
        if 0 <= swap_index < len(items):
            current = items[index]
            other = items[swap_index]
            conn.execute(
                "UPDATE repair_checklist_items SET task_order = ? WHERE id = ?",
                (other["task_order"], current["id"]),
            )
            conn.execute(
                "UPDATE repair_checklist_items SET task_order = ? WHERE id = ?",
                (current["task_order"], other["id"]),
            )
            conn.commit()
    finally:
        conn.close()
    return RedirectResponse(
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{repair_id}#repair-checklist",
        status_code=303,
    )


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{repair_id}/checklist/{item_id}/delete")
async def pro_repair_checklist_item_delete(
    customer_id: int, vehicle_id: int, repair_id: int, item_id: int
):
    conn = crm_db_conn()
    try:
        load_customer_vehicle(conn, customer_id, vehicle_id)
        load_repair_record(conn, customer_id, vehicle_id, repair_id)
        ensure_repair_checklist_schema(conn)
        cur = conn.execute(
            "DELETE FROM repair_checklist_items WHERE id = ? AND repair_record_id = ?",
            (item_id, repair_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Checklist item not found")
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{repair_id}#repair-checklist",
        status_code=303,
    )


@router.get(
    "/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{repair_id}/edit",
    response_class=HTMLResponse,
)
def pro_repair_record_edit(
    request: Request, customer_id: int, vehicle_id: int, repair_id: int
):
    conn = crm_db_conn()
    try:
        customer, vehicle = load_customer_vehicle(conn, customer_id, vehicle_id)
        repair = load_repair_record(conn, customer_id, vehicle_id, repair_id)
        completion = load_repair_completion(conn, repair_id)
    finally:
        conn.close()

    return templates.TemplateResponse(
        "pro/repair_edit.html",
        {
            "request": request,
            "customer": customer,
            "vehicle": vehicle,
            "repair": repair,
            "completion": completion,
        },
    )


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{repair_id}")
async def pro_repair_record_update(
    request: Request, customer_id: int, vehicle_id: int, repair_id: int
):
    form = await read_form_data(request)
    parts_cost = optional_float(form, "parts_cost")
    labor_hours = optional_float(form, "labor_hours")
    labor_rate = optional_float(form, "labor_rate")
    track_as_maintenance = form.get("also_update_maintenance_tracking") == "1"

    conn = crm_db_conn()
    try:
        load_customer_vehicle(conn, customer_id, vehicle_id)
        existing_repair = load_repair_record(conn, customer_id, vehicle_id, repair_id)
        if labor_hours is not None and labor_rate is not None:
            labor_cost = round(float(labor_hours or 0) * float(labor_rate or 0), 2)
        elif "labor_cost" in form:
            labor_cost = optional_float(form, "labor_cost")
        else:
            labor_cost = existing_repair.get("labor_cost")
        total_cost = float(parts_cost or 0) + float(labor_cost or 0)
        cur = conn.execute(
            """
            UPDATE repair_records
            SET repair_name = ?, repair_date = ?, mileage = ?, labor_hours = ?,
                labor_rate = ?, parts_cost = ?, labor_cost = ?, total_cost = ?,
                track_as_maintenance = ?, notes = ?
            WHERE id = ? AND customer_id = ? AND vehicle_id = ?
            """,
            (
                form.get("repair_name", ""),
                form.get("repair_date", ""),
                optional_int(form, "mileage"),
                labor_hours,
                labor_rate,
                parts_cost,
                labor_cost,
                total_cost,
                1 if track_as_maintenance else 0,
                form.get("notes", ""),
                repair_id,
                customer_id,
                vehicle_id,
            ),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Repair record not found")
        existing_completion = load_repair_completion(conn, repair_id)
        upsert_repair_completion(
            conn,
            repair_record_id=repair_id,
            form=form,
            completed_at=existing_completion.get("completed_at") or None,
            now=datetime.utcnow().isoformat(),
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}#vehicle-timeline",
        status_code=303,
    )


@router.get("/customers/{customer_id}/vehicles/{vehicle_id}/maintenance/{maintenance_id}", response_class=HTMLResponse)
def pro_maintenance_record_detail(
    request: Request, customer_id: int, vehicle_id: int, maintenance_id: int
):
    conn = crm_db_conn()
    try:
        customer, vehicle = load_customer_vehicle(conn, customer_id, vehicle_id)
        ensure_maintenance_records_schema(conn)
        maintenance = row_to_dict(
            conn.execute(
                """
                SELECT *
                FROM maintenance_records
                WHERE id = ? AND customer_id = ? AND vehicle_id = ?
                """,
                (maintenance_id, customer_id, vehicle_id),
            ).fetchone()
        )
        if not maintenance:
            raise HTTPException(status_code=404, detail="Maintenance record not found")
    finally:
        conn.close()

    return templates.TemplateResponse(
        "pro/maintenance_detail.html",
        {
            "request": request,
            "customer": customer,
            "vehicle": vehicle,
            "maintenance": maintenance,
            "maintenance_service_options": MAINTENANCE_SERVICE_OPTIONS,
        },
    )


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/maintenance/{maintenance_id}/reminders/{action}")
async def pro_maintenance_reminder_event_action(
    request: Request, customer_id: int, vehicle_id: int, maintenance_id: int, action: str
):
    form = await read_form_data(request)
    action_key = action.strip().lower()
    status = {
        "draft": "drafted",
        "copy": "copied",
    }.get(action_key)
    if not status:
        raise HTTPException(status_code=400, detail="Reminder action is invalid")

    now = datetime.utcnow().isoformat()

    conn = crm_db_conn()
    try:
        customer, vehicle = load_customer_vehicle(conn, customer_id, vehicle_id)
        ensure_maintenance_records_schema(conn)
        maintenance = row_to_dict(
            conn.execute(
                """
                SELECT *
                FROM maintenance_records
                WHERE id = ? AND customer_id = ? AND vehicle_id = ?
                """,
                (maintenance_id, customer_id, vehicle_id),
            ).fetchone()
        )
        if not maintenance:
            raise HTTPException(status_code=404, detail="Maintenance record not found")
        sender_context = load_shop_profile_context(conn)

        annotated = annotate_vehicle_maintenance_records(
            [maintenance],
            vehicle,
            customer,
            local_today(),
            None,
            sender_context,
        )[0]
        message = str(annotated.get("reminder_message") or form.get("message") or "").strip()
        event_id = create_maintenance_reminder_event(
            conn,
            customer_id=customer_id,
            vehicle_id=vehicle_id,
            maintenance_record_id=maintenance_id,
            service_type=str(maintenance.get("service_type") or ""),
            status=status,
            method="manual",
            message=message,
            notes=str(form.get("notes") or ""),
            created_at=now,
        )
        conn.commit()
    finally:
        conn.close()

    if request.headers.get("x-requested-with") == "fetch":
        return JSONResponse(
            {
                "ok": True,
                "event_id": event_id,
                "status": status,
                "status_label": maintenance_reminder_status_label(status),
            }
        )

    return_to = str(form.get("return_to") or "").strip()
    if return_to.startswith("/pro/"):
        return RedirectResponse(return_to, status_code=303)
    return RedirectResponse(
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}#maintenance",
        status_code=303,
    )


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/maintenance/{maintenance_id}")
async def pro_maintenance_record_update(
    request: Request, customer_id: int, vehicle_id: int, maintenance_id: int
):
    form = await read_form_data(request)
    now = datetime.utcnow().isoformat()
    service_type = form.get("service_type", "")
    date_performed = form.get("date_performed", "")
    mileage_performed = optional_int(form, "mileage_performed")
    interval_miles = maintenance_interval_value(form, service_type, "interval_miles")
    interval_months = maintenance_interval_value(form, service_type, "interval_months")
    due_mileage, due_date = maintenance_due_values(
        form,
        mileage_performed,
        date_performed,
        interval_miles,
        interval_months,
    )
    conn = crm_db_conn()
    try:
        load_customer_vehicle(conn, customer_id, vehicle_id)
        ensure_maintenance_records_schema(conn)
        cur = conn.execute(
            """
            UPDATE maintenance_records
            SET
              service_type = ?,
              date_performed = ?,
              mileage_performed = ?,
              interval_miles = ?,
              interval_months = ?,
              due_mileage = ?,
              due_date = ?,
              notes = ?,
              updated_at = ?
            WHERE id = ? AND customer_id = ? AND vehicle_id = ?
            """,
            (
                service_type,
                date_performed,
                mileage_performed,
                interval_miles,
                interval_months,
                due_mileage,
                due_date,
                form.get("notes", ""),
                now,
                maintenance_id,
                customer_id,
                vehicle_id,
            ),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Maintenance record not found")
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/maintenance/{maintenance_id}",
        status_code=303,
    )


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/maintenance/{maintenance_id}/delete")
async def pro_maintenance_record_delete(
    customer_id: int, vehicle_id: int, maintenance_id: int
):
    conn = crm_db_conn()
    try:
        load_customer_vehicle(conn, customer_id, vehicle_id)
        ensure_maintenance_records_schema(conn)
        cur = conn.execute(
            """
            DELETE FROM maintenance_records
            WHERE id = ? AND customer_id = ? AND vehicle_id = ?
            """,
            (maintenance_id, customer_id, vehicle_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Maintenance record not found")
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}",
        status_code=303,
    )
