from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

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

@router.get("", response_class=HTMLResponse)
def pro_dashboard(request: Request):
    return templates.TemplateResponse(
        "pro/dashboard.html",
        {"request": request},
    )

@router.get("/customers", response_class=HTMLResponse)
def pro_customers(request: Request):
    return templates.TemplateResponse(
        "pro/customers.html",
        {"request": request},
    )

@router.get("/vehicles", response_class=HTMLResponse)
def pro_vehicles(request: Request):
    return templates.TemplateResponse(
        "pro/vehicles.html",
        {"request": request},
    )

@router.get("/estimates", response_class=HTMLResponse)
def pro_estimates(request: Request):
    return templates.TemplateResponse(
        "pro/estimates.html",
        {"request": request},
    )

@router.get("/reports", response_class=HTMLResponse)
def pro_reports(request: Request):
    return templates.TemplateResponse(
        "pro/reports.html",
        {"request": request},
    )
