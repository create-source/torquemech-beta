from __future__ import annotations

from typing import Any


def tm_home_href(request: Any) -> str:
    user = getattr(getattr(request, "state", None), "current_user", None)
    return "/pro/dashboard" if user else "/"
