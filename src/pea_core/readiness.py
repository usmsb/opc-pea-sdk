"""Fail-closed production readiness shared by all first-party PEAs."""
from __future__ import annotations

import os
from typing import Any, Iterable


def production_readiness(settings: Any, *, required_setting_names: Iterable[str] = ()) -> dict[str, Any]:
    production = str(getattr(settings, "env", "dev")).lower() in {"prod", "production"}
    issues: list[str] = []
    if production:
        jwt_secret = str(getattr(settings, "jwt_secret", "") or "")
        admin_password = str(getattr(settings, "admin_password", "") or "")
        if len(jwt_secret) < 32 or jwt_secret == "dev-secret-change-me":
            issues.append("JWT_SECRET_MISSING_OR_DEVELOPMENT_VALUE")
        if len(admin_password) < 12 or admin_password.endswith("-admin-2026"):
            issues.append("ADMIN_PASSWORD_MISSING_OR_DEVELOPMENT_VALUE")
        for name in ("OPC_LLM_GATEWAY_URL", "OPC_PEA_ID", "OPC_SERVICE_TOKEN", "OPC_OWNER_ID"):
            if not os.getenv(name, "").strip():
                issues.append(f"{name}_MISSING")
        if os.getenv("OPC_LLM_MODE", "").strip().lower() != "central":
            issues.append("OPC_LLM_MODE_MUST_BE_CENTRAL")
        for name in required_setting_names:
            if not str(getattr(settings, name, "") or "").strip():
                issues.append(f"{name.upper()}_MISSING")
    return {
        "status": "ready" if not issues else "not_ready",
        "production": production,
        "issues": issues,
    }


__all__ = ["production_readiness"]
