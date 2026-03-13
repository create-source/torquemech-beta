from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse

router = APIRouter()

BASE = Path("data/knowledge")
DB_PATH = Path("app.db")

ALLOWED_CATEGORIES = {
    "obd": "OBD Code",
    "cost-guides": "Cost Guide",
    "repair-guides": "Repair Guide",
}

def load_article(category: str, slug: str) -> dict:
    if category not in ALLOWED_CATEGORIES:
        raise HTTPException(status_code=404, detail="Category not found")

    # basic hardening: keep it file-safe
    safe_slug = "".join([c for c in slug.lower() if c.isalnum() or c in ("-", "_")]).strip("-_")
    if not safe_slug:
        raise HTTPException(status_code=404, detail="Not found")

    path = BASE / category / f"{safe_slug}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Not found")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(status_code=500, detail="Article file invalid JSON")

    # fill defaults
    data.setdefault("slug", safe_slug)
    data.setdefault("category", category)
    data.setdefault("category_label", ALLOWED_CATEGORIES[category])

    data.setdefault("title", "Knowledge Article")
    data.setdefault("subtitle", "")
    data.setdefault("badge", "")
    data.setdefault("updated", "")
    data.setdefault("disclaimer", "")

    data.setdefault("blocks", [])
    return data

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_symptom_tables():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS symptoms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            summary TEXT DEFAULT '',
            intro TEXT DEFAULT '',
            system TEXT DEFAULT '',
            severity TEXT DEFAULT '',
            driveability TEXT DEFAULT '',
            repair_cost_min INTEGER,
            repair_cost_max INTEGER,
            difficulty TEXT DEFAULT '',
            repair_time TEXT DEFAULT '',
            is_published INTEGER NOT NULL DEFAULT 1
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS symptom_causes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symptom_id INTEGER NOT NULL,
            cause_text TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (symptom_id) REFERENCES symptoms(id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS symptom_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symptom_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            label TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (symptom_id) REFERENCES symptoms(id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS symptom_repairs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symptom_id INTEGER NOT NULL,
            repair_name TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (symptom_id) REFERENCES symptoms(id) ON DELETE CASCADE
        )
    """)

    conn.commit()
    conn.close()


def seed_symptoms_from_python():
    conn = get_db()
    cur = conn.cursor()

    for symptom in SYMPTOM_PAGES:
        cur.execute("SELECT id FROM symptoms WHERE slug = ?", (symptom["slug"],))
        existing = cur.fetchone()
        if existing:
            continue

        cur.execute("""
            INSERT INTO symptoms (
                slug, title, summary, intro, system, severity, driveability,
                repair_cost_min, repair_cost_max, difficulty, repair_time, is_published
            )
            VALUES (?, ?, ?, ?, '', '', '', NULL, NULL, '', '', 1)
        """, (
            symptom["slug"],
            symptom["title"],
            symptom.get("summary", ""),
            symptom.get("intro", ""),
        ))

        symptom_id = cur.lastrowid

        for i, cause in enumerate(symptom.get("common_causes", []), start=1):
            cur.execute("""
                INSERT INTO symptom_causes (symptom_id, cause_text, sort_order)
                VALUES (?, ?, ?)
            """, (symptom_id, cause, i))

        for i, code in enumerate(symptom.get("related_codes", []), start=1):
            cur.execute("""
                INSERT INTO symptom_codes (symptom_id, code, label, sort_order)
                VALUES (?, ?, ?, ?)
            """, (
                symptom_id,
                code.get("code", ""),
                code.get("label", ""),
                i,
            ))

        for i, repair in enumerate(symptom.get("common_repairs", []), start=1):
            cur.execute("""
                INSERT INTO symptom_repairs (symptom_id, repair_name, sort_order)
                VALUES (?, ?, ?)
            """, (
                symptom_id,
                repair.get("name", ""),
                i,
            ))

    conn.commit()
    conn.close()

@router.get("/knowledge/{category}/{slug}", response_class=HTMLResponse)
def knowledge_article(request: Request, category: str, slug: str):
    article = load_article(category, slug)
    return request.app.state.templates.TemplateResponse(
        "article.html",
        {
            "request": request,
            "title": f"{article.get('title','Knowledge')} — TorqueMech",
            "article": article,
        },
    )

SYMPTOM_PAGES = [
    {
    "slug": "engine-misfire",
    "title": "Engine Misfire",
    "search_terms": [
        "car shaking",
        "engine shaking",
        "car jerking",
        "rough running",
        "misfire",
        "shaking while driving"
    ],
        "summary": "Rough idle, hesitation, loss of power, and flashing check engine light.",
        "intro": "An engine misfire happens when one or more cylinders fail to burn the air-fuel mixture correctly. This can cause shaking, hesitation, poor fuel economy, and reduced power.",
        "common_causes": [
            "Worn or fouled spark plugs",
            "Failing ignition coils",
            "Vacuum leaks",
            "Fuel injector problems",
            "Low engine compression",
            "Dirty or faulty mass airflow sensor",
        ],
        "related_codes": [
            {"code": "P0300", "label": "Random/Multiple Cylinder Misfire Detected"},
            {"code": "P0301", "label": "Cylinder 1 Misfire Detected"},
            {"code": "P0302", "label": "Cylinder 2 Misfire Detected"},
            {"code": "P0303", "label": "Cylinder 3 Misfire Detected"},
            {"code": "P0304", "label": "Cylinder 4 Misfire Detected"},
        ],
        "common_repairs": [
            {"name": "Replace spark plugs"},
            {"name": "Replace ignition coil"},
            {"name": "Repair vacuum leak"},
            {"name": "Clean or replace fuel injector"},
            {"name": "Perform compression test and mechanical diagnosis"},
        ],
    },
    {
        "slug": "check-engine-light",
        "title": "Check Engine Light",
        "summary": "Common causes, related OBD codes, and likely repair paths.",
        "intro": "A check engine light can be triggered by emissions faults, ignition problems, fuel system issues, sensor failures, or engine performance problems. A scan tool is the first step.",
        "common_causes": [
            "Loose or faulty gas cap",
            "Oxygen sensor failure",
            "Catalytic converter efficiency issues",
            "Ignition misfires",
            "EVAP system leaks",
            "Mass airflow sensor problems",
        ],
        "related_codes": [
            {"code": "P0420", "label": "Catalyst System Efficiency Below Threshold"},
            {"code": "P0171", "label": "System Too Lean"},
            {"code": "P0300", "label": "Random/Multiple Cylinder Misfire"},
            {"code": "P0442", "label": "EVAP System Small Leak Detected"},
            {"code": "P0101", "label": "Mass Air Flow Sensor Range/Performance"},
        ],
        "common_repairs": [
            {"name": "Scan and diagnose stored trouble codes"},
            {"name": "Replace faulty oxygen sensor"},
            {"name": "Repair EVAP leak"},
            {"name": "Replace spark plugs or ignition components"},
            {"name": "Clean or replace mass airflow sensor"},
        ],
    },
    {
        "slug": "car-wont-start",
        "title": "Car Won’t Start",
        "search_terms": [
            "car wont start",
            "no start",
            "engine wont crank",
            "car not starting"
        ],
        "summary": "Battery, starter, fuel, ignition, and sensor-related no-start issues.",
        "intro": "A vehicle that won’t start may have a battery issue, starter failure, ignition fault, fuel delivery problem, or sensor-related no-start condition.",
        "common_causes": [
            "Weak or dead battery",
            "Bad starter motor or starter solenoid",
            "Poor battery terminal connection",
            "No fuel pressure",
            "Ignition switch issues",
            "Crankshaft position sensor failure",
        ],
        "related_codes": [
            {"code": "P0335", "label": "Crankshaft Position Sensor A Circuit"},
            {"code": "P0562", "label": "System Voltage Low"},
            {"code": "P0230", "label": "Fuel Pump Primary Circuit"},
            {"code": "P0685", "label": "ECM/PCM Power Relay Control Circuit"},
        ],
        "common_repairs": [
            {"name": "Charge or replace battery"},
            {"name": "Replace starter motor"},
            {"name": "Clean and tighten battery terminals"},
            {"name": "Test fuel pressure and replace fuel pump if needed"},
            {"name": "Replace crankshaft position sensor"},
        ],
    },
    {
        "slug": "rough-idle",
        "title": "Rough Idle",
        "search_terms": [
            "car shaking at idle",
            "engine vibration",
            "rough idle",
            "shaking when stopped"
        ],
        "summary": "Uneven RPM, shaking at idle, lean/rich conditions, and ignition faults.",
        "intro": "A rough idle usually means the engine is struggling to maintain a smooth idle speed. This can be caused by air leaks, ignition issues, fuel delivery problems, or sensor faults.",
        "common_causes": [
            "Vacuum leaks",
            "Dirty throttle body",
            "Worn spark plugs",
            "Bad ignition coil",
            "Dirty fuel injectors",
            "Mass airflow or idle control issues",
        ],
        "related_codes": [
            {"code": "P0505", "label": "Idle Control System Malfunction"},
            {"code": "P0171", "label": "System Too Lean Bank 1"},
            {"code": "P0300", "label": "Random/Multiple Cylinder Misfire"},
            {"code": "P0101", "label": "Mass Air Flow Sensor Range/Performance"},
        ],
        "common_repairs": [
            {"name": "Repair vacuum leak"},
            {"name": "Clean throttle body"},
            {"name": "Replace spark plugs"},
            {"name": "Replace ignition coil"},
            {"name": "Clean fuel injectors"},
        ],
    },
    {
        "slug": "engine-overheating",
        "title": "Engine Overheating",
        "search_terms": [
            "car overheating",
            "running hot",
            "engine too hot",
            "temperature gauge high"
        ],
        "summary": "High engine temperature, steam, or coolant loss.",
        "intro": "Engine overheating occurs when the cooling system cannot regulate engine temperature. This can cause severe engine damage if not addressed quickly.",
        "common_causes": [
            "Low coolant level",
            "Cooling system leaks",
            "Faulty thermostat",
            "Radiator blockage",
            "Failed water pump",
            "Cooling fan malfunction"
        ],
        "related_codes": [
            {"code": "P0217", "label": "Engine Over Temperature Condition"},
            {"code": "P0128", "label": "Coolant Thermostat Below Regulating Temperature"},
            {"code": "P0117", "label": "Engine Coolant Temperature Circuit Low"},
            {"code": "P0480", "label": "Cooling Fan Control Circuit"}
        ],
        "common_repairs": [
            {"name": "Replace thermostat"},
            {"name": "Repair coolant leak"},
            {"name": "Replace radiator"},
            {"name": "Replace water pump"},
            {"name": "Repair cooling fan system"}
        ],
    },
    {
        "slug": "engine-stalling",
        "title": "Engine Stalling",
        "summary": "Vehicle suddenly shuts off while driving or idling.",
        "intro": "Engine stalling usually occurs when the engine cannot maintain combustion due to fuel, ignition, or airflow problems.",
        "common_causes": [
            "Fuel pump failure",
            "Dirty throttle body",
            "Faulty idle air control valve",
            "Crankshaft position sensor failure",
            "Vacuum leaks"
        ],
        "related_codes": [
            {"code": "P0335", "label": "Crankshaft Position Sensor Circuit"},
            {"code": "P0505", "label": "Idle Control System Malfunction"},
            {"code": "P0171", "label": "System Too Lean"},
            {"code": "P0230", "label": "Fuel Pump Primary Circuit"}
        ],
        "common_repairs": [
            {"name": "Replace fuel pump"},
            {"name": "Clean throttle body"},
            {"name": "Replace crankshaft position sensor"},
            {"name": "Repair vacuum leak"},
            {"name": "Service fuel system"}
        ],
    },
    {
        "slug": "hard-starting",
        "title": "Hard Starting",
        "summary": "Engine takes longer than normal to start.",
        "intro": "Hard starting can occur due to weak fuel pressure, ignition problems, sensor faults, or poor battery performance.",
        "common_causes": [
            "Weak battery",
            "Fuel pressure loss",
            "Faulty crankshaft position sensor",
            "Dirty fuel injectors",
            "Ignition coil problems"
        ],
        "related_codes": [
            {"code": "P0335", "label": "Crankshaft Position Sensor Circuit"},
            {"code": "P0562", "label": "System Voltage Low"},
            {"code": "P0171", "label": "System Too Lean"}
        ],
        "common_repairs": [
            {"name": "Replace battery"},
            {"name": "Repair fuel pressure issue"},
            {"name": "Replace crankshaft sensor"},
            {"name": "Clean fuel injectors"}
        ],
    },

]

init_symptom_tables()
seed_symptoms_from_python()

def get_all_symptoms():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            s.slug,
            s.title,
            s.summary,
            COALESCE(GROUP_CONCAT(t.term, ' '), '') AS search_terms
        FROM symptoms s
        LEFT JOIN symptom_search_terms t
            ON s.id = t.symptom_id
        WHERE s.is_published = 1
        GROUP BY s.id, s.slug, s.title, s.summary
        ORDER BY s.title ASC
    """)

    rows = cur.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def get_symptom_by_slug(slug: str):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM symptoms
        WHERE slug = ? AND is_published = 1
        LIMIT 1
    """, (slug,))
    symptom_row = cur.fetchone()

    if not symptom_row:
        conn.close()
        return None

    symptom = dict(symptom_row)
    symptom_id = symptom["id"]

    cur.execute("""
        SELECT cause_text
        FROM symptom_causes
        WHERE symptom_id = ?
        ORDER BY sort_order ASC, id ASC
    """, (symptom_id,))
    symptom["common_causes"] = [row["cause_text"] for row in cur.fetchall()]

    cur.execute("""
        SELECT code, label
        FROM symptom_codes
        WHERE symptom_id = ?
        ORDER BY sort_order ASC, id ASC
    """, (symptom_id,))
    symptom["related_codes"] = [
        {"code": row["code"], "label": row["label"]}
        for row in cur.fetchall()
    ]

    cur.execute("""
        SELECT repair_name
        FROM symptom_repairs
        WHERE symptom_id = ?
        ORDER BY sort_order ASC, id ASC
    """, (symptom_id,))
    symptom["common_repairs"] = [
        {"name": row["repair_name"]}
        for row in cur.fetchall()
    ]

    conn.close()
    return symptom

@router.get("/symptoms", response_class=HTMLResponse)
def symptoms_index(request: Request):
    return request.app.state.templates.TemplateResponse(
        "symptoms_index.html",
        {
            "request": request,
            "symptom_pages": get_all_symptoms(),
        },
    )


@router.get("/symptoms/{slug}", response_class=HTMLResponse)
def symptom_page(request: Request, slug: str):
    symptom = get_symptom_by_slug(slug)
    if not symptom:
        raise HTTPException(status_code=404, detail="Symptom guide not found")

    return request.app.state.templates.TemplateResponse(
        "symptom_page.html",
        {
            "request": request,
            "symptom": symptom,
        },
    )