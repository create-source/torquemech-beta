import sqlite3
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

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
    raw = form.get(name, "")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def optional_float(form: dict[str, str], name: str) -> float | None:
    raw = form.get(name, "")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def format_phone(value: Any) -> str:
    raw = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(raw) == 11 and raw.startswith("1"):
        raw = raw[1:]
    if len(raw) == 10:
        return f"{raw[:3]}-{raw[3:6]}-{raw[6:]}"
    return str(value or "").strip()


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


templates.env.filters["pro_phone"] = format_phone
templates.env.filters["pro_miles"] = format_mileage
templates.env.filters["pro_currency"] = format_currency
templates.env.filters["pro_date"] = format_pro_date
templates.env.filters["pro_datetime"] = format_pro_datetime
templates.env.filters["service_total"] = service_total_value


def parse_date_value(raw: Any) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


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
    performed_date = parse_date_value(record.get("date_performed"))

    due_mileage = None
    if mileage_performed is not None and interval_miles:
        due_mileage = int(mileage_performed) + int(interval_miles)

    due_date = None
    if performed_date and interval_months:
        due_date = add_months(performed_date, int(interval_months))

    needs_mileage = current_mileage is None
    missing_interval = not interval_miles and not interval_months

    status = "Candidate"
    status_key = "candidate"
    reason = "Interval data recorded for future follow-up."

    if needs_mileage or missing_interval:
        status = "Unknown"
        status_key = "unknown"
        if needs_mileage and missing_interval:
            reason = "Current mileage and interval data are missing."
        elif needs_mileage:
            reason = "Current mileage is missing."
        else:
            reason = "Interval data is missing."
    else:
        overdue_by_mileage = due_mileage is not None and int(current_mileage) > due_mileage
        overdue_by_date = due_date is not None and today > due_date
        due_soon_by_mileage = (
            due_mileage is not None
            and int(current_mileage) <= due_mileage
            and due_mileage - int(current_mileage) <= 500
        )
        due_soon_by_date = (
            due_date is not None
            and today <= due_date
            and due_date - today <= timedelta(days=30)
        )

        if overdue_by_mileage or overdue_by_date:
            status = "Overdue"
            status_key = "overdue"
            if overdue_by_mileage and overdue_by_date:
                reason = "Past due by mileage and date."
            elif overdue_by_mileage:
                reason = "Current mileage is past the due mileage."
            else:
                reason = "Today is past the due date."
        elif due_soon_by_mileage or due_soon_by_date:
            status = "Due Soon"
            status_key = "due_soon"
            if due_soon_by_mileage and due_soon_by_date:
                reason = "Within 500 miles and 30 days of the follow-up point."
            elif due_soon_by_mileage:
                reason = "Within 500 miles of the due mileage."
            else:
                reason = "Within 30 days of the due date."

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
          finding_title TEXT,
          finding_description TEXT,
          recommended_repair TEXT,
          estimated_cost REAL,
          customer_decision TEXT NOT NULL CHECK (customer_decision IN ('pending', 'approved', 'declined')),
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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_discrepancy_approvals_customer_id ON discrepancy_approvals (customer_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_discrepancy_approvals_vehicle_id ON discrepancy_approvals (vehicle_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_discrepancy_approvals_service_history_id ON discrepancy_approvals (service_history_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_discrepancy_approvals_decision ON discrepancy_approvals (customer_decision)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_discrepancy_approvals_created_at ON discrepancy_approvals (created_at)")


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
    grouped = {"pending": [], "approved": [], "declined": []}
    for record in records:
        key = str(record.get("customer_decision") or "pending").lower()
        grouped.setdefault(key, []).append(record)
    return grouped


def load_customer_vehicle(
    conn: sqlite3.Connection, customer_id: int, vehicle_id: int
) -> tuple[dict[str, Any], dict[str, Any]]:
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
                    ELSE 2
                  END,
                  a.created_at DESC,
                  a.id DESC
                """
            ).fetchall()
        ]
    finally:
        conn.close()

    for record in records:
        record["customer_name"] = customer_name(record)
        record["vehicle_label"] = vehicle_label(record)
        record["vehicle_url"] = f"/pro/customers/{record['customer_id']}/vehicles/{record['vehicle_id']}"
        record["detail_url"] = (
            f"/pro/customers/{record['customer_id']}/vehicles/{record['vehicle_id']}"
            f"/approvals/{record['id']}"
        )

    grouped = group_approval_records(records)

    return templates.TemplateResponse(
        "pro/approvals.html",
        {
            "request": request,
            "groups": grouped,
            "summary": {key: len(items) for key, items in grouped.items()},
        },
    )


@router.get("/follow-ups", response_class=HTMLResponse)
def pro_follow_ups(request: Request):
    today = date.today()
    conn = crm_db_conn()
    try:
        shop_name = load_shop_name(conn)
        rows = conn.execute(
            """
            SELECT
              m.*,
              c.first_name,
              c.last_name,
              c.phone,
              c.email,
              v.year AS vehicle_year,
              v.make AS vehicle_make,
              v.model AS vehicle_model,
              v.mileage AS current_mileage,
              ? AS shop_name
            FROM maintenance_records m
            JOIN customers c ON c.id = m.customer_id
            JOIN customer_vehicles v ON v.id = m.vehicle_id
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
        "unknown": [],
    }
    for row in rows:
        follow_up = build_follow_up_record(row, today)
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
def pro_customers(request: Request, q: str = ""):
    search = q.strip()
    conn = crm_db_conn()
    try:
        if search:
            like = f"%{search}%"
            rows = conn.execute(
                """
                SELECT
                  c.*,
                  COUNT(v.id) AS vehicle_count
                FROM customers c
                LEFT JOIN customer_vehicles v ON v.customer_id = c.id
                WHERE
                  c.first_name LIKE ?
                  OR c.last_name LIKE ?
                  OR c.phone LIKE ?
                  OR c.email LIKE ?
                GROUP BY c.id
                ORDER BY c.updated_at DESC, c.created_at DESC, c.id DESC
                """,
                (like, like, like, like),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT
                  c.*,
                  COUNT(v.id) AS vehicle_count
                FROM customers c
                LEFT JOIN customer_vehicles v ON v.customer_id = c.id
                GROUP BY c.id
                ORDER BY c.updated_at DESC, c.created_at DESC, c.id DESC
                """
            ).fetchall()
    finally:
        conn.close()

    return templates.TemplateResponse(
        "pro/customers.html",
        {
            "request": request,
            "customers": [dict(row) for row in rows],
            "q": search,
        },
    )


@router.post("/customers")
async def pro_customer_create(request: Request):
    form = await read_form_data(request)
    now = datetime.utcnow().isoformat()
    conn = crm_db_conn()
    try:
        cur = conn.execute(
            """
            INSERT INTO customers (
              first_name, last_name, phone, email, notes, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                form.get("first_name", ""),
                form.get("last_name", ""),
                form.get("phone", ""),
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
                form.get("phone", ""),
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


@router.get("/customers/{customer_id}", response_class=HTMLResponse)
def pro_customer_detail(request: Request, customer_id: int):
    conn = crm_db_conn()
    try:
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
        service_history = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM service_history
                WHERE customer_id = ? AND vehicle_id = ?
                ORDER BY service_date DESC, id DESC
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
                ORDER BY date_performed DESC, id DESC
                """,
                (customer_id, vehicle_id),
            ).fetchall()
        ]
        ensure_discrepancy_approvals_schema(conn)
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
    finally:
        conn.close()

    grouped_approval_records = group_approval_records(approval_records)

    return templates.TemplateResponse(
        "pro/vehicle_detail.html",
        {
            "request": request,
            "customer": customer,
            "vehicle": vehicle,
            "service_history": service_history,
            "maintenance_records": maintenance_records,
            "approval_records": approval_records,
            "approval_groups": grouped_approval_records,
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


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/approvals")
async def pro_approval_record_create(request: Request, customer_id: int, vehicle_id: int):
    form = await read_form_data(request)
    now = datetime.utcnow().isoformat()
    decision = (form.get("customer_decision") or "pending").lower()
    if decision not in {"pending", "approved", "declined"}:
        decision = "pending"
    decision_recorded_at = now if decision in {"approved", "declined"} else ""

    conn = crm_db_conn()
    try:
        load_customer_vehicle(conn, customer_id, vehicle_id)
        ensure_discrepancy_approvals_schema(conn)
        conn.execute(
            """
            INSERT INTO discrepancy_approvals (
              customer_id, vehicle_id, finding_title, finding_description,
              recommended_repair, estimated_cost, customer_decision,
              decision_notes, decision_recorded_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                customer_id,
                vehicle_id,
                form.get("finding_title", ""),
                form.get("finding_description", ""),
                form.get("recommended_repair", ""),
                optional_float(form, "estimated_cost"),
                decision,
                form.get("decision_notes", ""),
                decision_recorded_at,
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
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
        },
    )


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/approvals/{approval_id}")
async def pro_approval_record_update(
    request: Request, customer_id: int, vehicle_id: int, approval_id: int
):
    form = await read_form_data(request)
    now = datetime.utcnow().isoformat()
    decision = (form.get("customer_decision") or "pending").lower()
    if decision not in {"pending", "approved", "declined"}:
        decision = "pending"

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
              finding_title = ?,
              finding_description = ?,
              recommended_repair = ?,
              estimated_cost = ?,
              customer_decision = ?,
              decision_notes = ?,
              decision_recorded_at = ?,
              updated_at = ?
            WHERE id = ? AND customer_id = ? AND vehicle_id = ?
            """,
            (
                form.get("finding_title", ""),
                form.get("finding_description", ""),
                form.get("recommended_repair", ""),
                optional_float(form, "estimated_cost"),
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
        conn.commit()
    finally:
        conn.close()
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
        cur = conn.execute(
            """
            UPDATE discrepancy_approvals
            SET customer_decision = 'approved',
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
        conn.commit()
    finally:
        conn.close()
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
        cur = conn.execute(
            """
            UPDATE discrepancy_approvals
            SET customer_decision = 'declined',
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
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/approvals/{approval_id}",
        status_code=303,
    )


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/history")
async def pro_service_history_create(request: Request, customer_id: int, vehicle_id: int):
    form = await read_form_data(request)
    now = datetime.utcnow().isoformat()
    service_total = service_total_from_form(form)
    conn = crm_db_conn()
    try:
        load_customer_vehicle(conn, customer_id, vehicle_id)
        conn.execute(
            """
            INSERT INTO service_history (
              customer_id, vehicle_id, service_title, service_notes,
              mileage_at_service, service_date, estimate_total, actual_total,
              status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?, ?)
            """,
            (
                customer_id,
                vehicle_id,
                form.get("service_title", ""),
                form.get("service_notes", ""),
                optional_int(form, "mileage_at_service"),
                form.get("service_date", ""),
                None,
                service_total,
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(f"/pro/customers/{customer_id}/vehicles/{vehicle_id}", status_code=303)


@router.get("/customers/{customer_id}/vehicles/{vehicle_id}/history/{history_id}", response_class=HTMLResponse)
def pro_service_history_detail(
    request: Request, customer_id: int, vehicle_id: int, history_id: int
):
    conn = crm_db_conn()
    try:
        customer, vehicle = load_customer_vehicle(conn, customer_id, vehicle_id)
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
    form = await read_form_data(request)
    now = datetime.utcnow().isoformat()
    service_total = service_total_from_form(form)
    conn = crm_db_conn()
    try:
        load_customer_vehicle(conn, customer_id, vehicle_id)
        cur = conn.execute(
            """
            UPDATE service_history
            SET
              service_title = ?,
              service_notes = ?,
              mileage_at_service = ?,
              service_date = ?,
              estimate_total = ?,
              actual_total = ?,
              updated_at = ?
            WHERE id = ? AND customer_id = ? AND vehicle_id = ?
            """,
            (
                form.get("service_title", ""),
                form.get("service_notes", ""),
                optional_int(form, "mileage_at_service"),
                form.get("service_date", ""),
                None,
                service_total,
                now,
                history_id,
                customer_id,
                vehicle_id,
            ),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Service history not found")
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(
        f"/pro/customers/{customer_id}/vehicles/{vehicle_id}/history/{history_id}",
        status_code=303,
    )


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/maintenance")
async def pro_maintenance_record_create(request: Request, customer_id: int, vehicle_id: int):
    form = await read_form_data(request)
    now = datetime.utcnow().isoformat()
    conn = crm_db_conn()
    try:
        load_customer_vehicle(conn, customer_id, vehicle_id)
        conn.execute(
            """
            INSERT INTO maintenance_records (
              customer_id, vehicle_id, service_type, date_performed,
              mileage_performed, interval_miles, interval_months,
              notes, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                customer_id,
                vehicle_id,
                form.get("service_type", ""),
                form.get("date_performed", ""),
                optional_int(form, "mileage_performed"),
                optional_int(form, "interval_miles"),
                optional_int(form, "interval_months"),
                form.get("notes", ""),
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(f"/pro/customers/{customer_id}/vehicles/{vehicle_id}", status_code=303)


@router.get("/customers/{customer_id}/vehicles/{vehicle_id}/maintenance/{maintenance_id}", response_class=HTMLResponse)
def pro_maintenance_record_detail(
    request: Request, customer_id: int, vehicle_id: int, maintenance_id: int
):
    conn = crm_db_conn()
    try:
        customer, vehicle = load_customer_vehicle(conn, customer_id, vehicle_id)
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
        },
    )


@router.post("/customers/{customer_id}/vehicles/{vehicle_id}/maintenance/{maintenance_id}")
async def pro_maintenance_record_update(
    request: Request, customer_id: int, vehicle_id: int, maintenance_id: int
):
    form = await read_form_data(request)
    now = datetime.utcnow().isoformat()
    conn = crm_db_conn()
    try:
        load_customer_vehicle(conn, customer_id, vehicle_id)
        cur = conn.execute(
            """
            UPDATE maintenance_records
            SET
              service_type = ?,
              date_performed = ?,
              mileage_performed = ?,
              interval_miles = ?,
              interval_months = ?,
              notes = ?,
              updated_at = ?
            WHERE id = ? AND customer_id = ? AND vehicle_id = ?
            """,
            (
                form.get("service_type", ""),
                form.get("date_performed", ""),
                optional_int(form, "mileage_performed"),
                optional_int(form, "interval_miles"),
                optional_int(form, "interval_months"),
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
