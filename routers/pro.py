import sqlite3
from datetime import datetime
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


@router.get("", response_class=HTMLResponse)
def pro_dashboard(request: Request):
    return templates.TemplateResponse(
        "pro/dashboard.html",
        {"request": request},
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
    finally:
        conn.close()

    return templates.TemplateResponse(
        "pro/vehicle_detail.html",
        {
            "request": request,
            "customer": customer,
            "vehicle": vehicle,
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
