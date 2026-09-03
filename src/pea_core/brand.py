"""Typed brand overlay loader used by template and custom PEA releases."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SETTING_MAP = {
    "brand_name": "app_name",
    "api_public_url": "public_base_url",
    "consultant_name": "consultant_name",
    "consultant_wechat": "consultant_wechat",
    "customer_service_phone": "consultant_phone",
}


def load_brand_values(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    if not source.is_file():
        return {}
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    values = payload.get("values", payload)
    return values if isinstance(values, dict) else {}


def apply_brand_settings(settings: Any, path: str | Path) -> Any:
    """Apply only explicitly mapped public values; credentials are never brand fields."""
    values = load_brand_values(path)
    updates = {
        setting_key: values[brand_key]
        for brand_key, setting_key in SETTING_MAP.items()
        if values.get(brand_key) not in (None, "") and hasattr(settings, setting_key)
    }
    return settings.model_copy(update=updates) if updates and hasattr(settings, "model_copy") else settings


__all__ = ["apply_brand_settings", "load_brand_values"]
