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
        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            category TEXT DEFAULT '',
            labor_min REAL,
            labor_max REAL
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

    cur.execute("""
        CREATE TABLE IF NOT EXISTS symptom_search_terms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symptom_id INTEGER NOT NULL,
            term TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (symptom_id) REFERENCES symptoms(id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS symptom_related_repairs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symptom_id INTEGER NOT NULL,
            repair_slug TEXT NOT NULL,
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
            symptom_id = existing["id"]

            cur.execute("""
                UPDATE symptoms
                SET
                    title = ?,
                    summary = ?,
                    intro = ?,
                    is_published = 1
                WHERE id = ?
            """, (
                symptom["title"],
                symptom.get("summary", ""),
                symptom.get("intro", ""),
                symptom_id,
            ))
        else:
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

        # Refresh child tables so seed data stays in sync
        cur.execute("DELETE FROM symptom_causes WHERE symptom_id = ?", (symptom_id,))
        cur.execute("DELETE FROM symptom_codes WHERE symptom_id = ?", (symptom_id,))
        cur.execute("DELETE FROM symptom_repairs WHERE symptom_id = ?", (symptom_id,))
        cur.execute("DELETE FROM symptom_search_terms WHERE symptom_id = ?", (symptom_id,))
        cur.execute("DELETE FROM symptom_related_repairs WHERE symptom_id = ?", (symptom_id,))

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

        for i, term in enumerate(symptom.get("search_terms", []), start=1):
            cur.execute("""
                INSERT INTO symptom_search_terms (symptom_id, term, sort_order)
                VALUES (?, ?, ?)
            """, (symptom_id, term, i))

        for i, repair_slug in enumerate(symptom.get("related_repair_slugs", []), start=1):
            cur.execute("""
                INSERT INTO symptom_related_repairs (symptom_id, repair_slug, sort_order)
                VALUES (?, ?, ?)
            """, (symptom_id, repair_slug, i))

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
        "related_repair_slugs": [
        "spark-plug-replacement",
        "ignition-coil-replacement",
        "fuel-pump-replacement"
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
        "related_repair_slugs": [
            "battery-replacement",
            "starter-replacement",
            "fuel-pump-replacement",
            "alternator-replacement"
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
        "related_repair_slugs": [
            "spark-plug-replacement",
            "ignition-coil-replacement",
            "throttle-body-cleaning",
            "fuel-injector-cleaning",
            "maf-sensor-replacement",
            "pcv-valve-replacement"
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
        "related_repair_slugs": [
            "thermostat-replacement",
            "radiator-replacement",
            "water-pump-replacement",
            "serpentine-belt-replacement"
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
        "related_repair_slugs": [
            "battery-replacement",
            "fuel-pump-replacement",
            "fuel-injector-cleaning",
            "ignition-coil-replacement",
            "spark-plug-replacement"
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
    {
        "slug": "brake-grinding",
        "title": "Brake Grinding Noise",
        "search_terms": [
            "brakes grinding",
            "grinding noise braking",
            "metal scraping brakes",
            "brake grinding sound"
        ],
        "related_repair_slugs": [
            "brake-pad-replacement",
            "brake-rotor-replacement"
        ],
        "summary": "Grinding or scraping noise when braking, often caused by worn brake pads or damaged rotors.",
        "intro": "A grinding noise when braking usually indicates that the brake pads are worn down to the metal backing plate or that debris has become trapped between the rotor and brake pad.",
        "common_causes": [
            "Worn brake pads",
            "Brake pads worn down to metal backing plate",
            "Damaged or scored brake rotors",
            "Debris caught between rotor and pad",
            "Brake caliper hardware issues"
        ],
        "related_codes": [
            {"code": "C1234", "label": "Brake System Warning (example)"},
            {"code": "C0040", "label": "Brake System Fault"}
        ],
        "common_repairs": [
            {"name": "Replace brake pads"},
            {"name": "Replace brake rotors"},
            {"name": "Inspect and service brake calipers"},
            {"name": "Remove debris from brake assembly"}
        ],
    },
    {
        "slug": "battery-keeps-dying",
        "title": "Battery Keeps Dying",
        "search_terms": [
            "battery keeps dying",
            "dead battery overnight",
            "car battery drains",
            "battery going dead"
        ],
        "related_repair_slugs": [
            "battery-replacement",
            "alternator-replacement"
        ],
        "summary": "Frequent dead battery caused by charging system problems or electrical drains.",
        "intro": "A car battery that repeatedly dies may be caused by a weak battery, alternator failure, parasitic electrical drain, or poor battery connections.",
        "common_causes": [
            "Old or failing battery",
            "Faulty alternator not charging battery",
            "Parasitic electrical drain",
            "Loose or corroded battery terminals",
            "Vehicle left with lights or accessories on"
        ],
        "related_codes": [
            {"code": "P0562", "label": "System Voltage Low"},
            {"code": "P0620", "label": "Generator Control Circuit Malfunction"}
        ],
        "common_repairs": [
            {"name": "Replace battery"},
            {"name": "Replace alternator"},
            {"name": "Clean battery terminals"},
            {"name": "Perform parasitic drain test"}
        ],
    },
    {
        "slug": "car-vibrating",
        "title": "Car Vibrating While Driving",
        "search_terms": [
            "car vibrating",
            "car shaking while driving",
            "vehicle vibration",
            "steering wheel vibration"
        ],
        "summary": "Vehicle vibration caused by tire, wheel, suspension, or drivetrain issues.",
        "intro": "A car vibrating while driving can be caused by wheel imbalance, worn suspension components, damaged tires, or drivetrain issues.",
        "common_causes": [
            "Unbalanced wheels",
            "Bent wheel or damaged rim",
            "Worn suspension components",
            "Damaged or uneven tires",
            "Drivetrain or axle issues"
        ],
        "related_codes": [
            {"code": "C1235", "label": "Wheel Speed Sensor Fault"},
            {"code": "C1145", "label": "Chassis System Fault"}
        ],
        "common_repairs": [
            {"name": "Balance wheels"},
            {"name": "Replace damaged tire"},
            {"name": "Inspect suspension components"},
            {"name": "Repair or replace axle"}
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

    cur.execute("""
        SELECT s.slug, s.name, s.category, s.labor_min, s.labor_max
        FROM symptom_related_repairs rr
        JOIN services s
            ON s.slug = rr.repair_slug
        WHERE rr.symptom_id = ?
        ORDER BY rr.sort_order ASC, rr.id ASC
    """, (symptom_id,))
    symptom["related_repair_guides"] = [dict(row) for row in cur.fetchall()]

    symptom.setdefault("related_repair_guides", [])

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

def init_service_tables():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            category TEXT DEFAULT '',
            labor_min REAL,
            labor_max REAL
        )
    """)

    conn.commit()
    conn.close()


REPAIR_COST_GUIDES = [
    {
        "slug": "brake-pad-replacement",
        "name": "Brake Pad Replacement",
        "category": "Brakes",
        "labor_min": 1.0,
        "labor_max": 1.8,
    },
    {
        "slug": "alternator-replacement",
        "name": "Alternator Replacement",
        "category": "Electrical",
        "labor_min": 1.2,
        "labor_max": 2.5,
    },
    {
        "slug": "starter-replacement",
        "name": "Starter Replacement",
        "category": "Electrical",
        "labor_min": 1.0,
        "labor_max": 2.5,
    },
    {
        "slug": "spark-plug-replacement",
        "name": "Spark Plug Replacement",
        "category": "Ignition",
        "labor_min": 1.0,
        "labor_max": 3.0,
    },
    {
        "slug": "battery-replacement",
        "name": "Battery Replacement",
        "category": "Electrical",
        "labor_min": 0.2,
        "labor_max": 0.5,
    },
    {
        "slug": "radiator-replacement",
        "name": "Radiator Replacement",
        "category": "Cooling System",
        "labor_min": 1.5,
        "labor_max": 3.5,
    },
    {
        "slug": "water-pump-replacement",
        "name": "Water Pump Replacement",
        "category": "Cooling System",
        "labor_min": 2.0,
        "labor_max": 5.0,
    },
    {
        "slug": "wheel-bearing-replacement",
        "name": "Wheel Bearing Replacement",
        "category": "Suspension / Wheel End",
        "labor_min": 1.5,
        "labor_max": 3.0,
    },
    
    {
        "slug": "ignition-coil-replacement",
        "name": "Ignition Coil Replacement",
        "category": "Ignition",
        "labor_min": 0.5,
        "labor_max": 1.5,
    },
    {
        "slug": "brake-rotor-replacement",
        "name": "Brake Rotor Replacement",
        "category": "Brakes",
        "labor_min": 1.0,
        "labor_max": 2.2,
    },
    {
        "slug": "fuel-pump-replacement",
        "name": "Fuel Pump Replacement",
        "category": "Fuel System",
        "labor_min": 1.5,
        "labor_max": 4.0,
    },
    {
        "slug": "oxygen-sensor-replacement",
        "name": "Oxygen Sensor Replacement",
        "category": "Emissions / Engine",
        "labor_min": 0.5,
        "labor_max": 1.5,
    },
    {
        "slug": "thermostat-replacement",
        "name": "Thermostat Replacement",
        "category": "Cooling System",
        "labor_min": 1.0,
        "labor_max": 2.5,
    },
    {
        "slug": "maf-sensor-replacement",
        "name": "MAF Sensor Replacement",
        "category": "Air Intake / Engine",
        "labor_min": 0.3,
        "labor_max": 1.0,
    },
    {
        "slug": "throttle-body-cleaning",
        "name": "Throttle Body Cleaning",
        "category": "Air Intake / Engine",
        "labor_min": 0.5,
        "labor_max": 1.2,
    },
    {
        "slug": "fuel-injector-cleaning",
        "name": "Fuel Injector Cleaning",
        "category": "Fuel System",
        "labor_min": 1.0,
        "labor_max": 2.0,
    },
    {
        "slug": "pcv-valve-replacement",
        "name": "PCV Valve Replacement",
        "category": "Emissions / Engine",
        "labor_min": 0.3,
        "labor_max": 1.0,
    },
    {
        "slug": "serpentine-belt-replacement",
        "name": "Serpentine Belt Replacement",
        "category": "Drive Belt System",
        "labor_min": 0.5,
        "labor_max": 1.5,
    },
]

def seed_services_from_python():
    conn = get_db()
    cur = conn.cursor()

    for service in REPAIR_COST_GUIDES:
        cur.execute("SELECT id FROM services WHERE slug = ?", (service["slug"],))
        existing = cur.fetchone()
        if existing:
            continue

        cur.execute("""
            INSERT INTO services (
                slug, name, category, labor_min, labor_max
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
                service["slug"],
                service["name"],
                service.get("category", ""),
                service.get("labor_min"),
                service.get("labor_max"),
            )
        )

    conn.commit()
    conn.close()


def get_all_repair_guides():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT slug, name, category, labor_min, labor_max
        FROM services
        ORDER BY name ASC
    """)

    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_repair_guide_by_slug(slug: str):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT slug, name, category, labor_min, labor_max
        FROM services
        WHERE slug = ?
        LIMIT 1
    """, (slug,))

    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    return dict(row)


init_service_tables()
seed_services_from_python()


@router.get("/repair-cost", response_class=HTMLResponse)
def repair_cost_index(request: Request):
    return request.app.state.templates.TemplateResponse(
        "repair_cost_index.html",
        {
            "request": request,
            "repair_pages": get_all_repair_guides(),
        },
    )


@router.get("/repair-cost/{slug}", response_class=HTMLResponse)
def repair_cost_page(request: Request, slug: str):
    service = get_repair_guide_by_slug(slug)
    if not service:
        raise HTTPException(status_code=404, detail="Repair guide not found")

    labor_min = service["labor_min"]
    labor_max = service["labor_max"]

    labor_rate = 120
    labor_low = int(labor_min * labor_rate)
    labor_high = int(labor_max * labor_rate)

    return request.app.state.templates.TemplateResponse(
        "repair_cost.html",
        {
            "request": request,
            "service": service,
            "labor_min": labor_min,
            "labor_max": labor_max,
            "labor_low": labor_low,
            "labor_high": labor_high,
        },
    )

@router.get("/knowledge", response_class=HTMLResponse)
def knowledge_hub(request: Request):

    sections = [
        {
            "title": "Symptoms Diagnostic Guides",
            "summary": "Diagnose vehicle symptoms and common causes.",
            "href": "/symptoms"
        },
        {
            "title": "Repair Cost Guides",
            "summary": "Typical repair labor times and cost ranges for common vehicle repairs.",
            "href": "/repair-cost"
        },
        {
            "title": "OBD Diagnostic Codes",
            "summary": "Look up OBD-II diagnostic trouble codes.",
            "href": "/obd"
        },
        {
            "title": "Electrical & Wiring",
            "summary": "Relay wiring, fuse protection, voltage drop testing, ground circuits, and electrical fundamentals.",
            "href": "/electrical"
        }
    ]

    return request.app.state.templates.TemplateResponse(
        "knowledge_hub.html",
        {
            "request": request,
            "sections": sections
        },
    )