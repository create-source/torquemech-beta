import sqlite3
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

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
USE_LOCAL_SQLITE_COMPAT = not Path("/data").exists()

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

router = APIRouter(prefix="/pro", tags=["pro"])

FINDING_STATUS_OPTIONS = ("Approved", "Open", "Completed", "Deferred", "Declined")
FINDING_SEVERITY_OPTIONS = ("Low", "Medium", "High", "Critical")
FINDING_REQUEST_TYPES = ("finding", "labor")
CUSTOMER_DECISION_LOG_STATUSES = {"Approved", "Deferred", "Declined"}
APPROVAL_REQUEST_TYPES = ("finding", "labor", "parts")
APPROVAL_DECISION_OPTIONS = ("pending", "approved", "declined", "deferred")
REPAIR_WORK_STATUS_OPTIONS = ("ready", "in_progress", "waiting_parts", "completed")
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


def crm_db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    if USE_LOCAL_SQLITE_COMPAT:
        conn.execute("PRAGMA journal_mode=MEMORY")
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
        return f"{raw[:3]}-{raw[3:6]}-{raw[6:]}"
    return str(value or "").strip()


def clean_phone(value: Any) -> str:
    raw = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(raw) == 11 and raw.startswith("1"):
        raw = raw[1:]
    return raw if len(raw) == 10 else str(value or "").strip()


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


def format_pro_date(value: Any) -> str:
    parsed = parse_date_value(value)
    if not parsed:
        return str(value or "")
    return parsed.strftime("%m/%d/%Y")


def format_pro_datetime(value: Any) -> str:
    if not value:
        return ""
    raw = str(value).strip()
    try:
        parsed = datetime.fromisoformat(raw)
        return parsed.strftime("%m/%d/%Y")
    except ValueError:
        return format_pro_date(raw)


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


templates.env.filters["pro_phone"] = format_phone
templates.env.filters["pro_miles"] = format_mileage
templates.env.filters["pro_currency"] = format_currency
templates.env.filters["pro_date"] = format_pro_date
templates.env.filters["pro_datetime"] = format_pro_datetime
templates.env.filters["pro_quantity"] = format_quantity
templates.env.filters["pro_engine_badge"] = format_engine_badge
templates.env.filters["service_total"] = service_total_value


def parse_date_value(raw: Any) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
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
    shop_name = (record.get("shop_name") or "").strip() or "our shop"
    service_type = (record.get("service_type") or "maintenance").strip()

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
            "suggested_message": (
                f"Hi {customer}, this is {shop_name}. According to our records, "
                f"your {vehicle} may be due for {service_type}. Let me know if "
                "you'd like to schedule service."
            ),
        }
    )
    return record


def load_shop_name(conn: sqlite3.Connection) -> str:
    try:
        row = conn.execute("SELECT shop_name FROM shop_profile WHERE id = 1").fetchone()
    except sqlite3.OperationalError:
        return ""
    return str(row["shop_name"] or "").strip() if row else ""


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
            files[name] = {
                "filename": filename,
                "content_type": headers.get("content-type", ""),
                "content": payload,
            }
        else:
            fields[name] = payload.decode("utf-8", errors="replace").strip()
    return fields, files


def save_visual_reference_upload(upload: dict[str, Any] | None) -> str:
    if not upload:
        return ""
    content = upload.get("content") or b""
    filename = str(upload.get("filename") or "").strip()
    if not content or not filename:
        return ""
    suffix = Path(filename).suffix.lower()
    if suffix not in VISUAL_REFERENCE_ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported image upload type")
    VISUAL_REFERENCE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid4().hex}{suffix}"
    target = (VISUAL_REFERENCE_UPLOAD_DIR / stored_name).resolve()
    target.relative_to(VISUAL_REFERENCE_UPLOAD_DIR.resolve())
    target.write_bytes(content)
    return f"{VISUAL_REFERENCE_UPLOAD_URL_PREFIX}/{stored_name}"


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
          parts_cost REAL,
          labor_cost REAL,
          total_cost REAL,
          track_as_maintenance INTEGER NOT NULL DEFAULT 0,
          workflow_source_type TEXT,
          workflow_source_id INTEGER,
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
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_repair_records_workflow_source
        ON repair_records (workflow_source_type, workflow_source_id)
        WHERE workflow_source_type IS NOT NULL
          AND TRIM(workflow_source_type) != ''
          AND workflow_source_id IS NOT NULL
        """
    )
    conn.commit()


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
          completion_notes TEXT,
          override_reason TEXT,
          completed_at TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY (repair_record_id) REFERENCES repair_records(id)
        )
        """
    )
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
        "completion_notes": "",
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
    return completion


def repair_completion_progress(completion: dict[str, Any]) -> dict[str, int]:
    total = len(REPAIR_COMPLETION_CHECKS)
    completed = sum(1 for key, _label in REPAIR_COMPLETION_CHECKS if int(completion.get(key) or 0))
    percent = int(round((completed / total) * 100)) if total else 0
    return {"completed": completed, "total": total, "incomplete": total - completed, "percent": percent}


def upsert_repair_completion(
    conn: sqlite3.Connection,
    *,
    repair_record_id: int,
    form: dict[str, str],
    completed_at: str | None,
    now: str,
) -> dict[str, Any]:
    ensure_repair_completion_schema(conn)
    values = {key: 1 if form.get(key) == "1" else 0 for key, _label in REPAIR_COMPLETION_CHECKS}
    completion_notes = str(form.get("completion_notes") or "").strip()
    override_reason = str(form.get("override_reason") or "").strip()
    existing = load_repair_completion(conn, repair_record_id)
    effective_completed_at = completed_at or existing.get("completed_at") or ""
    conn.execute(
        """
        INSERT INTO repair_completions (
          repair_record_id, torque_verified, fluids_verified, leaks_checked,
          codes_cleared, road_test_completed, customer_concern_resolved,
          completion_notes, override_reason, completed_at, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(repair_record_id) DO UPDATE SET
          torque_verified = excluded.torque_verified,
          fluids_verified = excluded.fluids_verified,
          leaks_checked = excluded.leaks_checked,
          codes_cleared = excluded.codes_cleared,
          road_test_completed = excluded.road_test_completed,
          customer_concern_resolved = excluded.customer_concern_resolved,
          completion_notes = excluded.completion_notes,
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
            completion_notes,
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
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT
              rc.id,
              rc.repair_record_id,
              rc.completed_at,
              rc.override_reason,
              rc.created_at,
              rc.updated_at,
              rr.customer_id,
              rr.vehicle_id,
              rr.repair_name
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
    if "labor_reason" not in columns:
        conn.execute("ALTER TABLE findings_records ADD COLUMN labor_reason TEXT")
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
            WHERE fr.customer_id = ? AND fr.vehicle_id = ?
            ORDER BY fhr.created_at DESC, fhr.id DESC
            """,
            (customer_id, vehicle_id),
        ).fetchall()
    ]
    customer_decision_logs = [
        dict(row)
        for row in conn.execute(
            """
            SELECT cdl.*, fr.finding
            FROM customer_decision_logs cdl
            JOIN findings_records fr ON fr.id = cdl.finding_id
            WHERE fr.customer_id = ? AND fr.vehicle_id = ?
            ORDER BY cdl.created_at DESC, cdl.id DESC
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
    _customer, vehicle = load_customer_vehicle(conn, customer_id, vehicle_id)
    ensure_maintenance_records_schema(conn)
    ensure_repair_records_schema(conn)
    ensure_repair_checklist_schema(conn)
    ensure_repair_completion_schema(conn)
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
    findings_records = [
        dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM findings_records
            WHERE customer_id = ? AND vehicle_id = ?
            """,
            (customer_id, vehicle_id),
        ).fetchall()
    ]
    vehicle_timeline = build_vehicle_timeline(
        customer_id,
        vehicle_id,
        vehicle,
        service_history_records,
        findings_records,
        finding_history_records,
        customer_decision_logs,
        approval_event_records,
        load_vehicle_repair_checklist_events(conn, customer_id, vehicle_id),
        load_vehicle_repair_completion_events(conn, customer_id, vehicle_id),
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
    return repair


def load_finding_record(
    conn: sqlite3.Connection,
    customer_id: int,
    vehicle_id: int,
    finding_id: int,
) -> dict[str, Any]:
    ensure_findings_records_schema(conn)
    finding = row_to_dict(
        conn.execute(
            """
            SELECT *
            FROM findings_records
            WHERE id = ? AND customer_id = ? AND vehicle_id = ?
            """,
            (finding_id, customer_id, vehicle_id),
        ).fetchone()
    )
    if not finding:
        raise HTTPException(status_code=404, detail="Finding record not found")
    return finding


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


def repair_work_title_from_approval(record: dict[str, Any]) -> str:
    request_type = normalize_approval_request_type(record.get("request_type"))
    if request_type == "parts":
        return record.get("part_name") or record.get("part_description") or record.get("finding_title") or "Parts Request"
    if request_type == "labor":
        return record.get("finding_title") or record.get("recommended_repair") or "Labor Request"
    return record.get("recommended_repair") or record.get("finding_title") or "Approved Request"


def build_repair_work_items(
    vehicle: dict[str, Any],
    findings_records: list[dict[str, Any]],
    approval_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    current_vehicle_mileage = vehicle.get("mileage")
    for record in findings_records:
        if (record.get("status") or "") not in {"Approved", "Completed"}:
            continue
        status = record.get("repair_work_status") or ("completed" if record.get("status") == "Completed" else "ready")
        try:
            status = normalize_repair_work_status(status)
        except HTTPException:
            status = "ready"
        if status == "completed" or (record.get("status") or "") == "Completed":
            continue
        title = repair_work_title_from_finding(record)
        detail = record.get("finding") or record.get("labor_reason") or record.get("recommendation") or ""
        blueprint = get_repair_blueprint_for_work_item(title, detail, vehicle)
        item = {
            "source_type": "finding",
            "source_id": record.get("id"),
            "title": title,
            "detail": detail,
            "request_type_label": "Labor Request" if normalize_finding_request_type(record.get("request_type")) == "labor" else "Finding",
            "approval_label": record.get("status") or "Approved",
            "repair_work_status": status,
            "repair_work_status_label": repair_work_status_label(status),
            "linked_repair_record_id": record.get("linked_repair_record_id"),
            "repair_record_created_at": record.get("repair_record_created_at") or "",
            "repair_record_url": (
                f"/pro/customers/{record['customer_id']}/vehicles/{record['vehicle_id']}"
                f"/repairs/{record.get('linked_repair_record_id')}"
                if record.get("linked_repair_record_id")
                else ""
            ),
            "repair_prefill": {
                "repair_name": repair_work_title_from_finding(record),
                "repair_date": date.today().isoformat(),
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
        }
        if blueprint:
            item["blueprint"] = blueprint
            item["blueprint_summary"] = blueprint_summary(blueprint)
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
        title = repair_work_title_from_approval(record)
        detail = record.get("finding_description") or record.get("recommended_repair") or ""
        blueprint = get_repair_blueprint_for_work_item(title, detail, vehicle)
        item = {
            "source_type": "approval",
            "source_id": record.get("id"),
            "title": title,
            "detail": detail,
            "request_type_label": approval_request_type_label(record.get("request_type")),
            "approval_label": "Approved",
            "repair_work_status": status,
            "repair_work_status_label": repair_work_status_label(status),
            "linked_repair_record_id": record.get("linked_repair_record_id"),
            "repair_record_created_at": record.get("repair_record_created_at") or "",
            "repair_record_url": (
                f"/pro/customers/{record['customer_id']}/vehicles/{record['vehicle_id']}"
                f"/repairs/{record.get('linked_repair_record_id')}"
                if record.get("linked_repair_record_id")
                else ""
            ),
            "repair_prefill": {
                "repair_name": repair_work_title_from_approval(record),
                "repair_date": date.today().isoformat(),
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
        }
        if blueprint:
            item["blueprint"] = blueprint
            item["blueprint_summary"] = blueprint_summary(blueprint)
        items.append(item)
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
    findings_records: list[dict[str, Any]],
    finding_history_records: list[dict[str, Any]],
    customer_decision_logs: list[dict[str, Any]],
    approval_event_records: list[dict[str, Any]] | None = None,
    repair_checklist_events: list[dict[str, Any]] | None = None,
    repair_completion_events: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {
        "repaired": {"key": "repaired", "title": "Repaired Services", "records": []},
        "maintenance": {"key": "maintenance", "title": "Maintenance Services", "records": []},
        "findings": {"key": "findings", "title": "Findings", "records": []},
        "approvals": {"key": "approvals", "title": "Approvals", "records": []},
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

    for record in service_history_records:
        source_type = record.get("source_type") or ""
        if source_type == "repair":
            add_record(
                "repaired",
                {
                    "id": record["id"],
                    "date": record.get("service_date") or "",
                    "created_at": record.get("created_at") or "",
                    "service_name": record.get("service_name") or "Repair",
                    "mileage": record.get("mileage"),
                    "url": f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{record.get('source_record_id')}",
                },
            )
        else:
            add_record(
                "maintenance",
                {
                    "id": record["id"],
                    "date": record.get("service_date") or "",
                    "created_at": record.get("created_at") or "",
                    "service_name": record.get("service_name") or "Service",
                    "mileage": record.get("mileage"),
                    "url": (
                        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/maintenance/{record.get('source_record_id')}"
                        if source_type == "maintenance"
                        else "#vehicle-timeline"
                    ),
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
        if is_completed:
            if not record.get("linked_repair_record_id"):
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
            continue
        add_record(
            "findings",
            {
                "id": record["id"],
                "date": record.get("finding_date") or "",
                "created_at": record.get("created_at") or "",
                "service_name": title,
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

    for record in repair_checklist_events or []:
        add_record(
            "repaired",
            {
                "id": record["id"],
                "date": record.get("completed_at") or "",
                "created_at": record.get("completed_at") or record.get("created_at") or "",
                "service_name": f"Checklist Completed: {record.get('task_name') or 'Checklist Item'}",
                "mileage": None,
                "url": f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{record.get('repair_record_id')}",
            },
        )

    for record in repair_completion_events or []:
        repair_title = record.get("repair_name") or "Repair"
        add_record(
            "repaired",
            {
                "id": f"completion-{record['id']}",
                "date": record.get("completed_at") or "",
                "created_at": record.get("completed_at") or record.get("created_at") or "",
                "service_name": f"Repair Completed: {repair_title}",
                "mileage": None,
                "url": f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{record.get('repair_record_id')}",
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
    return [groups["repaired"], groups["maintenance"], groups["findings"], groups["approvals"]]


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
    counts = {"Approved": 0, "Open": 0, "Completed": 0, "Deferred": 0, "Declined": 0}
    for record in findings_records:
        status = record.get("status") or "Open"
        if status in counts:
            counts[status] += 1
    return {
        "approved": counts["Approved"],
        "open": counts["Open"],
        "completed": counts["Completed"],
        "deferred": counts["Deferred"],
        "declined": counts["Declined"],
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
def pro_dashboard(request: Request):
    conn = crm_db_conn()
    try:
        ensure_customer_status_schema(conn)
        ensure_discrepancy_approvals_schema(conn)
        ensure_visual_reference_schema(conn)
        pending_approvals_count = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM discrepancy_approvals
            WHERE customer_decision = 'pending'
            """
        ).fetchone()["count"]
        visual_reference_count = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM visual_reference_records
            """
        ).fetchone()["count"]
    finally:
        conn.close()

    return templates.TemplateResponse(
        "pro/dashboard.html",
        {
            "request": request,
            "pending_approvals_count": pending_approvals_count,
            "visual_reference_count": visual_reference_count,
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
    today = date.today()
    conn = crm_db_conn()
    try:
        ensure_customer_status_schema(conn)
        ensure_maintenance_records_schema(conn)
        shop_name = load_shop_name(conn)
        rows = conn.execute(
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
              v.mileage AS current_mileage,
              ? AS shop_name
            FROM maintenance_records m
            JOIN customers c ON c.id = m.customer_id
            JOIN customer_vehicles v ON v.id = m.vehicle_id
            WHERE COALESCE(NULLIF(c.customer_status, ''), 'active') = 'active'
            ORDER BY c.last_name, c.first_name, v.year DESC, v.make, v.model, m.service_type
            """,
            (shop_name,),
        ).fetchall()
    finally:
        conn.close()

    grouped = {
        "overdue": [],
        "due_soon": [],
        "candidate": [],
    }
    for row in rows:
        follow_up = build_follow_up_record(row, today)
        if follow_up["status_key"] in grouped:
            grouped[follow_up["status_key"]].append(follow_up)

    summary = {key: len(items) for key, items in grouped.items()}

    return templates.TemplateResponse(
        "pro/follow_ups.html",
        {
            "request": request,
            "today": today.isoformat(),
            "groups": grouped,
            "summary": summary,
        },
    )


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
def pro_customer_vehicle_detail(request: Request, customer_id: int, vehicle_id: int):
    conn = crm_db_conn()
    try:
        customer, vehicle = load_customer_vehicle(conn, customer_id, vehicle_id)
        ensure_maintenance_records_schema(conn)
        ensure_repair_records_schema(conn)
        ensure_repair_checklist_schema(conn)
        ensure_findings_records_schema(conn)
        ensure_finding_history_records_schema(conn)
        ensure_customer_decision_logs_schema(conn)
        ensure_service_history_schema(conn)
        ensure_service_history_records_schema(conn)
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
        findings_records = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM findings_records
                WHERE customer_id = ? AND vehicle_id = ?
                ORDER BY
                  CASE status
                    WHEN 'Approved' THEN 1
                    WHEN 'Open' THEN 2
                    WHEN 'Completed' THEN 3
                    WHEN 'Deferred' THEN 4
                    WHEN 'Declined' THEN 5
                    ELSE 6
                  END ASC,
                  CASE severity
                    WHEN 'Critical' THEN 1
                    WHEN 'High' THEN 2
                    WHEN 'Medium' THEN 3
                    WHEN 'Low' THEN 4
                    ELSE 5
                  END ASC,
                  finding_date DESC,
                  id DESC
                """,
                (customer_id, vehicle_id),
            ).fetchall()
        ]
        finding_history_records = [
            dict(row)
            for row in conn.execute(
                """
                SELECT fhr.*, fr.finding, fr.request_type, fr.labor_description
                FROM finding_history_records fhr
                JOIN findings_records fr ON fr.id = fhr.finding_id
                WHERE fr.customer_id = ? AND fr.vehicle_id = ?
                ORDER BY fhr.created_at DESC, fhr.id DESC
                """,
                (customer_id, vehicle_id),
            ).fetchall()
        ]
        customer_decision_logs = [
            dict(row)
            for row in conn.execute(
                """
                SELECT cdl.*, fr.finding
                FROM customer_decision_logs cdl
                JOIN findings_records fr ON fr.id = cdl.finding_id
                WHERE fr.customer_id = ? AND fr.vehicle_id = ?
                ORDER BY cdl.created_at DESC, cdl.id DESC
                """,
                (customer_id, vehicle_id),
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
        findings_records,
        finding_history_records,
        customer_decision_logs,
        approval_event_records,
        repair_checklist_events,
        repair_completion_events,
    )
    vehicle_timeline_total = sum(int(group.get("count") or 0) for group in vehicle_timeline)
    repair_history_summary = build_repair_history_summary(repair_records)
    findings_summary = build_findings_summary(findings_records)
    approval_summary = build_approval_summary(approval_records)
    repair_work_items = build_repair_work_items(vehicle, findings_records, approval_records)
    for item in repair_work_items:
        item["checklist_summary"] = checklist_summaries.get(
            int(item.get("linked_repair_record_id") or 0),
            {"completed": 0, "total": 0, "incomplete": 0, "percent": 0},
        )

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
            "findings_records": findings_records,
            "findings_summary": findings_summary,
            "approval_summary": approval_summary,
            "repair_work_items": repair_work_items,
            "visual_reference_records": visual_reference_records,
            "repair_intelligence_records": repair_intelligence_records,
            "repair_work_status_options": [
                {"value": value, "label": REPAIR_WORK_STATUS_LABELS[value]}
                for value in REPAIR_WORK_STATUS_OPTIONS
            ],
            "finding_history_records": finding_history_records,
            "customer_decision_logs": customer_decision_logs,
            "vehicle_timeline": vehicle_timeline,
            "vehicle_timeline_total": vehicle_timeline_total,
            "approval_records": approval_records,
            "approval_groups": grouped_approval_records,
            "maintenance_service_options": MAINTENANCE_SERVICE_OPTIONS,
            "maintenance_interval_presets": MAINTENANCE_INTERVAL_PRESETS,
            "maintenance_service_aliases": MAINTENANCE_SERVICE_ALIASES,
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
    form = await read_form_data(request)
    now = datetime.utcnow().isoformat()
    severity = normalize_finding_severity(form.get("severity", "Low"))
    status = normalize_finding_status(form.get("status", "Open"))
    request_type = normalize_finding_request_type(form.get("request_type"))
    labor_description = form.get("labor_description", "") if request_type == "labor" else ""
    labor_hours = optional_float(form, "labor_hours") if request_type == "labor" else None
    labor_rate = optional_float(form, "labor_rate") if request_type == "labor" else None
    labor_amount = finding_labor_amount(labor_hours, labor_rate)
    labor_reason = form.get("labor_reason", "") if request_type == "labor" else ""

    conn = crm_db_conn()
    try:
        customer, vehicle = load_customer_vehicle(conn, customer_id, vehicle_id)
        ensure_findings_records_schema(conn)
        cur = conn.execute(
            """
            INSERT INTO findings_records (
              vehicle_id, customer_id, request_type, finding, recommendation,
              labor_description, labor_hours, labor_rate, labor_amount, labor_reason,
              severity, status, repair_work_status, repair_work_updated_at,
              mileage, finding_date, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                vehicle_id,
                customer_id,
                request_type,
                form.get("finding", ""),
                form.get("recommendation", ""),
                labor_description,
                labor_hours,
                labor_rate,
                labor_amount,
                labor_reason,
                severity,
                status,
                "completed" if status == "Completed" else "ready" if status == "Approved" else "",
                now if status in {"Approved", "Completed"} else "",
                optional_int(form, "mileage"),
                date.today().isoformat(),
                now,
            ),
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
    labor_reason = form.get("labor_reason", "") if request_type == "labor" else ""

    conn = crm_db_conn()
    try:
        customer, vehicle = load_customer_vehicle(conn, customer_id, vehicle_id)
        existing = load_finding_record(conn, customer_id, vehicle_id, finding_id)
        cur = conn.execute(
            """
            UPDATE findings_records
            SET request_type = ?, finding = ?, recommendation = ?,
                labor_description = ?, labor_hours = ?, labor_rate = ?,
                labor_amount = ?, labor_reason = ?, severity = ?, status = ?,
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
            WHERE id = ? AND customer_id = ? AND vehicle_id = ?
            """,
            (
                request_type,
                form.get("finding", ""),
                form.get("recommendation", ""),
                labor_description,
                labor_hours,
                labor_rate,
                labor_amount,
                labor_reason,
                severity,
                status,
                status,
                status,
                status,
                datetime.utcnow().isoformat(),
                optional_int(form, "mileage"),
                form.get("finding_date", ""),
                finding_id,
                customer_id,
                vehicle_id,
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
            """
            UPDATE findings_records
            SET customer_notes = ?, internal_notes = ?
            WHERE id = ? AND customer_id = ? AND vehicle_id = ?
            """,
            (customer_notes, internal_notes, finding_id, customer_id, vehicle_id),
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
            """
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
            WHERE id = ? AND customer_id = ? AND vehicle_id = ?
            """,
            (status, status, status, status, datetime.utcnow().isoformat(), finding_id, customer_id, vehicle_id),
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
            """
            UPDATE findings_records
            SET repair_work_status = ?,
                repair_work_updated_at = ?,
                status = ?
            WHERE id = ? AND customer_id = ? AND vehicle_id = ?
            """,
            (repair_status, now, next_finding_status, finding_id, customer_id, vehicle_id),
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
    labor_cost = optional_float(form, "labor_cost")
    total_cost = float(parts_cost or 0) + float(labor_cost or 0)
    repair_name = form.get("repair_name", "")
    repair_date = form.get("repair_date", "")
    mileage = optional_int(form, "mileage")
    labor_hours = optional_float(form, "labor_hours")
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
              labor_hours, parts_cost, labor_cost, total_cost,
              track_as_maintenance, workflow_source_type, workflow_source_id,
              notes, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                vehicle_id,
                customer_id,
                repair_name,
                repair_date,
                mileage,
                labor_hours,
                parts_cost,
                labor_cost,
                total_cost,
                1 if update_maintenance else 0,
                workflow_source_type or "",
                workflow_source_id,
                notes,
                now,
            ),
        )
        repair_id = int(cur.lastrowid)
        if workflow_source_type == "finding" and workflow_source_id is not None:
            conn.execute(
                """
                UPDATE findings_records
                SET linked_repair_record_id = ?,
                    repair_record_created_at = ?
                WHERE id = ? AND customer_id = ? AND vehicle_id = ?
                """,
                (repair_id, now, workflow_source_id, customer_id, vehicle_id),
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
        checklist_items = load_repair_checklist_items(conn, repair_id)
        checklist_progress = repair_checklist_progress(checklist_items)
        completion = load_repair_completion(conn, repair_id)
        completion_progress = repair_completion_progress(completion)
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

    return templates.TemplateResponse(
        "pro/repair_detail.html",
        {
            "request": request,
            "customer": customer,
            "vehicle": vehicle,
            "repair": repair,
            "checklist_items": checklist_items,
            "checklist_progress": checklist_progress,
            "completion": completion,
            "completion_checks": REPAIR_COMPLETION_CHECKS,
            "completion_progress": completion_progress,
            "completion_warnings": [],
            "repair_intelligence_records": repair_intelligence_records,
        },
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
    checklist_items = load_repair_checklist_items(conn, repair_id)
    checklist_progress = repair_checklist_progress(checklist_items)
    completion = load_repair_completion(conn, repair_id)
    completion_progress = repair_completion_progress(completion)
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
        "checklist_items": checklist_items,
        "checklist_progress": checklist_progress,
        "completion": completion,
        "completion_checks": REPAIR_COMPLETION_CHECKS,
        "completion_progress": completion_progress,
        "completion_warnings": completion_warnings or [],
        "repair_intelligence_records": repair_intelligence_records,
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
            """
            UPDATE findings_records
            SET status = 'Completed',
                repair_work_status = 'completed',
                repair_work_updated_at = ?
            WHERE id = ? AND customer_id = ? AND vehicle_id = ?
            """,
            (now, source_id, customer_id, vehicle_id),
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


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{repair_id}/completion")
async def pro_repair_completion_update(
    request: Request, customer_id: int, vehicle_id: int, repair_id: int
):
    form = await read_form_data(request)
    now = datetime.utcnow().isoformat()
    override_reason = str(form.get("override_reason") or "").strip()
    conn = crm_db_conn()
    try:
        load_customer_vehicle(conn, customer_id, vehicle_id)
        repair = load_repair_record(conn, customer_id, vehicle_id, repair_id)
        checklist_summary = repair_checklist_summary(conn, repair_id)
        posted_completion = upsert_repair_completion(
            conn,
            repair_record_id=repair_id,
            form=form,
            completed_at=None,
            now=now,
        )
        completion_progress = repair_completion_progress(posted_completion)
        warnings: list[str] = []
        if checklist_summary["total"] > 0 and checklist_summary["incomplete"] > 0:
            warnings.append("Repair checklist contains incomplete items.")
        if completion_progress["incomplete"] > 0:
            warnings.append("Repair verification is incomplete.")
        if warnings and not override_reason:
            conn.commit()
            context = completion_detail_context(
                conn,
                request=request,
                customer_id=customer_id,
                vehicle_id=vehicle_id,
                repair_id=repair_id,
                completion_warnings=warnings,
            )
            return templates.TemplateResponse(
                "pro/repair_detail.html",
                context,
                status_code=400,
            )

        completed_at = repair.get("completed_at") or now
        upsert_repair_completion(
            conn,
            repair_record_id=repair_id,
            form=form,
            completed_at=completed_at,
            now=now,
        )
        conn.execute(
            """
            UPDATE repair_records
            SET status = 'Completed',
                completed_at = COALESCE(NULLIF(completed_at, ''), ?)
            WHERE id = ? AND customer_id = ? AND vehicle_id = ?
            """,
            (completed_at, repair_id, customer_id, vehicle_id),
        )
        refreshed_repair = load_repair_record(conn, customer_id, vehicle_id, repair_id)
        upsert_service_history_record(
            conn,
            customer_id=customer_id,
            vehicle_id=vehicle_id,
            source_type="repair",
            source_record_id=repair_id,
            service_name=refreshed_repair.get("repair_name") or "Repair",
            service_date=refreshed_repair.get("repair_date") or completed_at[:10],
            mileage=refreshed_repair.get("mileage"),
            labor_hours=refreshed_repair.get("labor_hours"),
            parts_cost=refreshed_repair.get("parts_cost"),
            labor_cost=refreshed_repair.get("labor_cost"),
            total_cost=refreshed_repair.get("total_cost"),
            notes=posted_completion.get("completion_notes") or refreshed_repair.get("notes") or "",
            created_at=completed_at,
        )
        if refreshed_repair.get("track_as_maintenance"):
            upsert_maintenance_from_repair(
                conn,
                customer_id=customer_id,
                vehicle_id=vehicle_id,
                service_type=refreshed_repair.get("repair_name") or "Repair",
                date_performed=refreshed_repair.get("repair_date") or completed_at[:10],
                mileage_performed=refreshed_repair.get("mileage"),
                notes=posted_completion.get("completion_notes") or refreshed_repair.get("notes") or "",
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
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{repair_id}#repair-completion",
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
    finally:
        conn.close()

    return templates.TemplateResponse(
        "pro/repair_edit.html",
        {
            "request": request,
            "customer": customer,
            "vehicle": vehicle,
            "repair": repair,
        },
    )


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{repair_id}")
async def pro_repair_record_update(
    request: Request, customer_id: int, vehicle_id: int, repair_id: int
):
    form = await read_form_data(request)
    parts_cost = optional_float(form, "parts_cost")
    labor_cost = optional_float(form, "labor_cost")
    total_cost = float(parts_cost or 0) + float(labor_cost or 0)
    track_as_maintenance = form.get("also_update_maintenance_tracking") == "1"

    conn = crm_db_conn()
    try:
        load_customer_vehicle(conn, customer_id, vehicle_id)
        load_repair_record(conn, customer_id, vehicle_id, repair_id)
        cur = conn.execute(
            """
            UPDATE repair_records
            SET repair_name = ?, repair_date = ?, mileage = ?, labor_hours = ?,
                parts_cost = ?, labor_cost = ?, total_cost = ?,
                track_as_maintenance = ?, notes = ?
            WHERE id = ? AND customer_id = ? AND vehicle_id = ?
            """,
            (
                form.get("repair_name", ""),
                form.get("repair_date", ""),
                optional_int(form, "mileage"),
                optional_float(form, "labor_hours"),
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
