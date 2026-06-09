import sqlite3
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs

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

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
STATE_DIR = Path("/data") if Path("/data").exists() else BASE_DIR / ".localstate"
DB_PATH = str((STATE_DIR / "app.db").resolve())
USE_LOCAL_SQLITE_COMPAT = not Path("/data").exists()

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
    )
    finding_history_count = len(finding_history_records)
    customer_decision_log_count = len(customer_decision_logs)
    return {
        "finding_history_count": finding_history_count,
        "customer_decision_log_count": customer_decision_log_count,
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
        items.append(
            {
                "source_type": "finding",
                "source_id": record.get("id"),
                "title": repair_work_title_from_finding(record),
                "detail": record.get("finding") or record.get("labor_reason") or record.get("recommendation") or "",
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
                "url": f"#recommendations-findings",
            }
        )
    for record in approval_records:
        if normalize_approval_decision(record.get("customer_decision")) != "approved":
            continue
        status = record.get("repair_work_status") or "ready"
        try:
            status = normalize_repair_work_status(status)
        except HTTPException:
            status = "ready"
        items.append(
            {
                "source_type": "approval",
                "source_id": record.get("id"),
                "title": repair_work_title_from_approval(record),
                "detail": record.get("finding_description") or record.get("recommended_repair") or "",
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
        )
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
) -> list[dict[str, Any]]:
    finding_ids_with_created_history = {
        int(record.get("finding_id") or 0)
        for record in finding_history_records
        if record.get("event_type") == "finding_created"
    }
    finding_status_history_keys = {
        (int(record.get("finding_id") or 0), record.get("new_status") or "")
        for record in finding_history_records
        if record.get("event_type") == "status_changed"
    }
    timeline = [
        {
            "id": record["id"],
            "record_type": "Repair" if record.get("source_type") == "repair" else "Maintenance",
            "record_type_key": "repair" if record.get("source_type") == "repair" else "maintenance",
            "date": record.get("service_date") or "",
            "created_at": record.get("created_at") or "",
            "service_name": (
                f"Repair Record Created: {record.get('service_name') or 'Repair'}"
                if record.get("source_type") == "repair"
                else record.get("service_name") or "Service"
            ),
            "mileage": record.get("mileage"),
            "url": "#repair-history" if record.get("source_type") == "repair" else "#maintenance-tracking",
        }
        for record in service_history_records
    ]
    timeline.extend(
        {
            "id": record["id"],
            "record_type": "Finding",
            "record_type_key": "finding",
            "date": record.get("finding_date") or "",
            "created_at": record.get("created_at") or "",
            "service_name": (
                f"Labor Request Created: {record.get('labor_description') or record.get('finding') or 'Labor Request'}"
                if normalize_finding_request_type(record.get("request_type")) == "labor"
                else f"Finding Created: {record.get('finding') or 'Finding'}"
            ),
            "mileage": record.get("mileage"),
            "url": "#recommendations-findings",
        }
        for record in findings_records
        if int(record.get("id") or 0) not in finding_ids_with_created_history
    )
    timeline.extend(
        {
            "id": record["id"],
            "record_type": "Finding",
            "record_type_key": "finding",
            "date": record.get("created_at") or "",
            "created_at": record.get("created_at") or "",
            "service_name": finding_history_timeline_label(record),
            "mileage": None,
            "url": "#recommendations-findings",
        }
        for record in finding_history_records
    )
    timeline.extend(
        {
            "id": record["id"],
            "record_type": "Finding",
            "record_type_key": "finding",
            "date": record.get("created_at") or "",
            "created_at": record.get("created_at") or "",
            "service_name": f"Customer Decision: {record.get('decision_status') or 'Decision'}",
            "mileage": None,
            "url": "#recommendations-findings",
        }
        for record in customer_decision_logs
        if (
            int(record.get("finding_id") or 0),
            record.get("decision_status") or "",
        )
        not in finding_status_history_keys
    )
    timeline.extend(
        {
            "id": record["id"],
            "record_type": "Repair Workflow" if record.get("event_type") == "repair_work_status_changed" else "Approval",
            "record_type_key": "repair" if record.get("event_type") == "repair_work_status_changed" else "finding",
            "date": record.get("created_at") or "",
            "created_at": record.get("created_at") or "",
            "service_name": record.get("event_label") or "Approval Event",
            "mileage": None,
            "url": f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/approvals/{record.get('approval_id')}",
        }
        for record in (approval_event_records or [])
    )
    if vehicle.get("mileage") is not None and vehicle.get("updated_at"):
        timeline.append(
            {
                "id": vehicle_id,
                "record_type": "Vehicle",
                "record_type_key": "vehicle",
                "date": "",
                "created_at": vehicle.get("updated_at") or "",
                "service_name": "Mileage Updated",
                "mileage": vehicle.get("mileage"),
                "url": "#vehicle-information",
            }
        )
    timeline.sort(
        key=lambda record: (
            parse_date_value(record.get("date")) is not None,
            parse_date_value(record.get("date")) or date.min,
            parse_datetime_value(record.get("created_at")) or datetime.min,
            int(record.get("id") or 0),
        ),
        reverse=True,
    )
    return timeline


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
        pending_approvals_count = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM discrepancy_approvals
            WHERE customer_decision = 'pending'
            """
        ).fetchone()["count"]
    finally:
        conn.close()

    return templates.TemplateResponse(
        "pro/dashboard.html",
        {
            "request": request,
            "pending_approvals_count": pending_approvals_count,
        },
    )


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
        ensure_findings_records_schema(conn)
        ensure_finding_history_records_schema(conn)
        ensure_customer_decision_logs_schema(conn)
        ensure_service_history_schema(conn)
        ensure_service_history_records_schema(conn)
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
    )
    repair_history_summary = build_repair_history_summary(repair_records)
    findings_summary = build_findings_summary(findings_records)
    approval_summary = build_approval_summary(approval_records)
    repair_work_items = build_repair_work_items(vehicle, findings_records, approval_records)

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
            "repair_work_status_options": [
                {"value": value, "label": REPAIR_WORK_STATUS_LABELS[value]}
                for value in REPAIR_WORK_STATUS_OPTIONS
            ],
            "finding_history_records": finding_history_records,
            "customer_decision_logs": customer_decision_logs,
            "vehicle_timeline": vehicle_timeline,
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
            )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}#repair-workflow",
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
    finally:
        conn.close()

    return templates.TemplateResponse(
        "pro/approval_detail.html",
        {
            "request": request,
            "customer": customer,
            "vehicle": vehicle,
            "approval": approval,
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
            append_discrepancy_approval_event(
                conn,
                approval_id,
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
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}#repair-workflow",
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
                if (workflow_record.get("repair_work_status") or "") != "completed":
                    raise HTTPException(status_code=400, detail="Workflow item must be done before creating a repair record")
                if workflow_record.get("linked_repair_record_id"):
                    return RedirectResponse(
                        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/repairs/{workflow_record['linked_repair_record_id']}",
                        status_code=303,
                    )
            else:
                workflow_record = load_approval_record(conn, customer_id, vehicle_id, workflow_source_id)
                if (workflow_record.get("repair_work_status") or "") != "completed":
                    raise HTTPException(status_code=400, detail="Workflow item must be done before creating a repair record")
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
    return RedirectResponse(
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}#repair-history",
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
    finally:
        conn.close()

    return templates.TemplateResponse(
        "pro/repair_detail.html",
        {
            "request": request,
            "customer": customer,
            "vehicle": vehicle,
            "repair": repair,
        },
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
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}#repair-history",
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
