"""Small, side-effect free helpers for PEA payment clients.

The commerce control plane returns the provider-native ``code_url`` for PC
payments.  Browser clients cannot render that URI directly, so PEA APIs add a
data-URL QR image while keeping the original provider value available for
auditing.  No merchant secret is involved in this conversion.
"""
from __future__ import annotations

import base64
import io
from typing import Any


def enrich_payment_client(client: dict[str, Any] | None) -> dict[str, Any]:
    result = dict(client or {})
    code_url = str(result.get("code_url") or "").strip()
    if not code_url or result.get("qr_data_url"):
        return result
    import qrcode

    image = qrcode.make(code_url)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    result["qr_data_url"] = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    return result


__all__ = ["enrich_payment_client"]
