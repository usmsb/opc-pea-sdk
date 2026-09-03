"""Authentication boundary for public PEA A2A endpoints."""
from __future__ import annotations

import secrets
from typing import Any

from fastapi import HTTPException, Request


def require_a2a_request(request: Request, settings: Any) -> None:
    expected = str(getattr(settings, "a2a_bearer_token", "") or "")
    production = str(getattr(settings, "env", "dev")).lower() in {"prod", "production"}
    if not expected and not production:
        return
    supplied = str(request.headers.get("Authorization") or "")
    if not expected or not secrets.compare_digest(supplied, f"Bearer {expected}"):
        raise HTTPException(status_code=401, detail="A2A authorization failed")


__all__ = ["require_a2a_request"]
