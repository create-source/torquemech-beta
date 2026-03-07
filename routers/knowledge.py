from __future__ import annotations

import json
from pathlib import Path
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse

router = APIRouter()

BASE = Path("data/knowledge")

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