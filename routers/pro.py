from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

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