from pydantic import BaseModel
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi import FastAPI, HTTPException
import json
import httpx
from datetime import datetime
import math

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
CATALOG_PATH = BASE_DIR / "services_catalog.json"

app = FastAPI(title="Personal Repair Estimate API", version="0.1")

# Serve static
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def home():
    static_index = STATIC_DIR / "index.html"
    root_index = BASE_DIR / "index.html"

    if static_index.exists():
        return FileResponse(str(static_index))
    if root_index.exists():
        return FileResponse(str(root_index))

    raise HTTPException(status_code=404, detail="index.html not found")



@app.get("/favicon.ico")
def favicon():
    ico = STATIC_DIR / "favicon.ico"
    if ico.exists():
        return FileResponse(str(ico))
    raise HTTPException(status_code=404, detail="favicon.ico not found")


# ---------------- Catalog loading ----------------
def load_catalog() -> dict:
    if not CATALOG_PATH.exists():
        raise RuntimeError(f"Missing {CATALOG_PATH.name} next to app.py")

    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    cats = data.get("categories", [])
    catalog = {}

    for c in cats:
        key = c.get("key")
        name = c.get("name")
        services = c.get("services", [])
        if not key or not name:
            continue

        norm_services = []
        for s in services:
            code = s.get("code")
            sname = s.get("name")
            if not code or not sname:
                continue
            norm_services.append(s)

        catalog[key] = {"name": name, "services": norm_services}

    return catalog


def get_catalog() -> dict:
    # Reload every request so edits to JSON show up immediately while developing
    try:
        return load_catalog()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def find_service(catalog: dict, category_key: str, service_code: str) -> dict | None:
    cat = catalog.get(category_key)
    if not cat:
        return None
    for s in cat.get("services", []):
        if s.get("code") == service_code:
            return s
    return None


# ---------------- Pricing + multipliers ----------------
DEFAULT_LABOR_RATE = 90.0  # your requested $90/hr
LABOR_RATE_BY_ZIP: dict[str, float] = {
    # optional overrides later:
    # "92646": 95.0,
}
PARTS_TAX_RATE = 0.0775


def money(x: float) -> float:
    return round(float(x) + 1e-9, 2)


def get_labor_rate(zip_code: str | None) -> float:
    if not zip_code:
        return DEFAULT_LABOR_RATE
    return float(LABOR_RATE_BY_ZIP.get(str(zip_code), DEFAULT_LABOR_RATE))


def vehicle_multiplier_from_inputs(vehicle_type: str | None, year: int | None, model: str | None) -> tuple[str, float]:
    vt = (vehicle_type or "auto").lower().strip()
    m = (model or "").upper()

    # base by type
    if vt in ("sedan", "car"):
        base = 1.00
        label = "sedan"
    elif vt in ("suv", "cuv"):
        base = 1.12
        label = "suv"
    elif vt in ("truck", "pickup"):
        base = 1.20
        label = "truck"
    else:
        # auto-detect
        TRUCK_HINTS = ["F-150", "F150", "SILVERADO", "SIERRA", "RAM", "TUNDRA", "TACOMA", "RANGER", "FRONTIER", "TITAN"]
        SUV_HINTS = ["4RUNNER", "RAV4", "HIGHLANDER", "PILOT", "CR-V", "CRV", "HR-V", "HRV", "EXPLORER",
                     "TAHOE", "SUBURBAN", "YUKON", "PATHFINDER", "ROGUE"]
        if any(h in m for h in TRUCK_HINTS):
            base = 1.20
            label = "truck"
        elif any(h in m for h in SUV_HINTS):
            base = 1.12
            label = "suv"
        else:
            base = 1.00
            label = "sedan"

    # age tweak (small)
    if year is not None:
        if year <= 2005:
            base *= 1.10
        elif year >= 2020:
            base *= 1.05

    return label, round(base, 4)


# ---------------- API: categories/services ----------------
@app.get("/categories")
def categories():
    catalog = get_catalog()
    out = []
    for key, cat in catalog.items():
        out.append(
            {"key": key, "name": cat.get("name"), "count": len(cat.get("services", []))}
        )
    out.sort(key=lambda x: (x["name"] or "").lower())
    return out


@app.get("/services/{category_key}")
def services_for_category(category_key: str):
    catalog = get_catalog()
    cat = catalog.get(category_key)
    if not cat:
        raise HTTPException(status_code=404, detail=f"Unknown category '{category_key}'")

    svcs = cat.get("services", [])
    svcs_sorted = sorted(svcs, key=lambda s: (s.get("name") or "").lower())

    # return full service objects (frontend can use hours + flat)
    return svcs_sorted


# ---------------- Estimate ----------------
class EstimateIn(BaseModel):
    zip_code: str | None = None
    parts_price: float | None = 0.0

    labor_pricing: str | None = "hourly"  # "hourly" or "flat"
    vehicle_type: str | None = "auto"

    year: int | None = None
    make: str | None = None
    model: str | None = None

    category: str
    service: str


@app.post("/estimate")
def estimate(payload: EstimateIn):
    catalog = get_catalog()
    svc = find_service(catalog, payload.category, payload.service)
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found for that category")

    parts_price = float(payload.parts_price or 0.0)
    rate = get_labor_rate(payload.zip_code)

    vehicle_label, mult = vehicle_multiplier_from_inputs(payload.vehicle_type, payload.year, payload.model)

    # pull labor ranges
    lh_min = float(svc.get("labor_hours_min", 0) or 0)
    lh_max = float(svc.get("labor_hours_max", lh_min) or lh_min)

    # fallback if missing
    if lh_min <= 0 and lh_max <= 0:
        lh_min = lh_max = 1.0
    elif lh_min <= 0:
        lh_min = lh_max
    elif lh_max <= 0:
        lh_max = lh_min

    # apply multiplier to labor-hours
    lh_min_eff = lh_min * mult
    lh_max_eff = lh_max * mult

    hourly_low = (rate * lh_min_eff) + parts_price
    hourly_high = (rate * lh_max_eff) + parts_price

    # flat-rate ranges (from JSON), also scale with multiplier
    fr_min = float(svc.get("flat_rate_min") or 0.0)
    fr_max = float(svc.get("flat_rate_max") or 0.0)

    if fr_min <= 0 and fr_max <= 0:
        fr_min = rate * lh_min
        fr_max = rate * lh_max

    fr_min_eff = fr_min * mult
    fr_max_eff = fr_max * mult

    flat_low = fr_min_eff + parts_price
    flat_high = fr_max_eff + parts_price

    mode = (payload.labor_pricing or "hourly").lower().strip()
    if mode not in ("hourly", "flat"):
        mode = "hourly"

    est_low, est_high = (flat_low, flat_high) if mode == "flat" else (hourly_low, hourly_high)

    # (optional) parts tax if you want it included:
    # parts_tax = parts_price * PARTS_TAX_RATE
    # est_low += parts_tax
    # est_high += parts_tax

    return {
        "service_name": svc.get("name", payload.service),
        "zip_code": payload.zip_code,
        "labor_pricing": mode,

        "labor_rate": money(rate),
        "vehicle_type": vehicle_label,
        "vehicle_multiplier": money(mult),

        "labor_hours_min": money(lh_min_eff),
        "labor_hours_max": money(lh_max_eff),

        "flat_rate_min": money(fr_min_eff),
        "flat_rate_max": money(fr_max_eff),

        "parts_price_used": money(parts_price),

        "estimate_low": money(est_low),
        "estimate_high": money(est_high),
    }


# ---------------- Vehicle endpoints (vPIC) ----------------
VPIC_BASE = "https://vpic.nhtsa.dot.gov/api/vehicles"

POPULAR_MAKES = {
    "ACURA","AUDI","BMW","BUICK","CADILLAC","CHEVROLET","CHRYSLER","DODGE","FORD","GMC",
    "HONDA","HYUNDAI","INFINITI","JEEP","KIA","LEXUS","LINCOLN","MAZDA","MERCEDES-BENZ",
    "MINI","MITSUBISHI","NISSAN","RAM","SUBARU","TESLA","TOYOTA","VOLKSWAGEN","VOLVO",
    "PORSCHE","LAND ROVER","JAGUAR"
}

@app.get("/vehicle/years")
def vehicle_years():
    current = datetime.now().year
    return list(range(current, current - 30, -1))


@app.get("/vehicle/makes")
async def vehicle_makes(year: int):
    # year is for your UI flow; vPIC "makes by type" doesn't require it
    urls = [
        f"{VPIC_BASE}/GetMakesForVehicleType/car?format=json",
        f"{VPIC_BASE}/GetMakesForVehicleType/truck?format=json",
        f"{VPIC_BASE}/GetMakesForVehicleType/multipurposepassengervehicle?format=json",
    ]
    makes_set = set()

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            for url in urls:
                r = await client.get(url)
                r.raise_for_status()
                data = r.json()
                for item in data.get("Results", []):
                    name = item.get("MakeName")
                    if not name:
                        continue
                    n = name.strip()
                    if n.upper() in POPULAR_MAKES:
                        makes_set.add(n.upper())
        return sorted(makes_set)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"/vehicle/makes failed: {type(e).__name__}: {e}")


@app.get("/vehicle/models")
async def vehicle_models(year: int, make: str):
    # vPIC endpoint
    url = f"{VPIC_BASE}/GetModelsForMakeYear/make/{make}/modelyear/{year}?format=json"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(url)
            r.raise_for_status()
            data = r.json()
            models = []
            seen = set()
            for item in data.get("Results", []):
                mn = item.get("Model_Name")
                if not mn:
                    continue
                mn = mn.strip()
                if mn.upper() in seen:
                    continue
                seen.add(mn.upper())
                models.append(mn.upper())
            return sorted(models)

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"/vehicle/models failed: {type(e).__name__}: {e}")
