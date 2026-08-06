"""Thin PEA client for the OPC central runtime gateway.

The client intentionally knows only the runtime env contract.  Provider keys,
owner identity and credit settlement remain in OPC; a PEA can send correlation
ids but cannot override billing authority.
"""
from __future__ import annotations

import os
import uuid
from typing import Any

import httpx


def central_runtime_enabled() -> bool:
    return os.getenv("OPC_LLM_MODE", "").strip().lower() == "central"


def _runtime() -> tuple[str, str, str]:
    url = os.getenv("OPC_LLM_GATEWAY_URL", "").strip().rstrip("/")
    token = os.getenv("OPC_SERVICE_TOKEN", "").strip()
    pea_id = os.getenv("OPC_PEA_ID", "").strip()
    if not url or not token or not pea_id:
        raise RuntimeError("central PEA runtime requires OPC_LLM_GATEWAY_URL, OPC_PEA_ID and OPC_SERVICE_TOKEN")
    return url, token, pea_id


class _CentralHTTP:
    async def _post(self, path: str, payload: dict[str, Any], timeout: float = 180.0,
                    idempotency_key: str | None = None) -> dict[str, Any]:
        url, token, _ = _runtime()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
            payload = {**payload, "idempotency_key": idempotency_key}
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"{url}{path}", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        return data if isinstance(data, dict) else {"data": data}


async def attest_runtime(*, deployment_revision: str | None = None,
                         capabilities: list[str] | None = None) -> dict[str, Any] | None:
    """Register/attest the running PEA without making startup brittle.

    A deployment can still expose health while OPC is temporarily restarting;
    the first central invocation remains authenticated and will fail closed if
    the token is invalid.  Successful attest transitions the control-plane
    instance to ``registered``.
    """
    if not central_runtime_enabled():
        return None
    client = _CentralHTTP()
    _, _, pea_id = _runtime()
    try:
        return await client._post(
            "/api/internal/pea/v1/attest",
            {
                "pea_id": pea_id,
                "deployment_revision": deployment_revision or os.getenv("PEA_IMAGE_REVISION", "unknown"),
                "capabilities": capabilities or [],
            },
            timeout=15,
        )
    except Exception:
        # The PEA process remains observable; central calls are still the only
        # allowed provider path and will surface the control-plane outage.
        return None


class CentralChat(_CentralHTTP):
    sandbox = False

    async def complete(self, messages: list[dict[str, Any]], *, temperature: float = 0.7,
                       max_tokens: int = 1600, **kwargs: Any) -> str:
        idempotency_key = str(kwargs.get("idempotency_key") or f"pea-chat-{uuid.uuid4()}")
        response = await self._post(
            "/api/internal/pea/v1/llm/chat",
            {
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "trace_id": kwargs.get("trace_id") or str(uuid.uuid4()),
                "task_scope_id": kwargs.get("task_scope_id"),
                "metadata": {"pea_client": "pea_core"},
            },
            timeout=180,
            idempotency_key=idempotency_key,
        )
        return str(response.get("content") or response.get("reply") or "")


class CentralLyrics(_CentralHTTP):
    sandbox = False

    async def generate(self, *, prompt: str, mode: str = "write_full_song",
                       lyrics: str | None = None, title: str | None = None) -> dict[str, Any]:
        idempotency_key = f"pea-lyrics-{uuid.uuid4()}"
        response = await self._post("/api/internal/pea/v1/llm/lyrics", {
            "prompt": prompt, "mode": mode, "lyrics": lyrics, "title": title,
            "trace_id": str(uuid.uuid4()),
        }, timeout=120, idempotency_key=idempotency_key)
        return response


class CentralMusic(_CentralHTTP):
    sandbox = False

    async def generate(self, *, prompt: str, lyrics: str | None = None,
                       instrumental: bool = False, duration_hint: str | None = None) -> dict[str, Any]:
        idempotency_key = f"pea-song-{uuid.uuid4()}"
        return await self._post("/api/internal/pea/v1/media/song", {
            "prompt": prompt, "lyrics": lyrics, "instrumental": instrumental,
            "duration_hint": duration_hint, "trace_id": str(uuid.uuid4()),
        }, timeout=240, idempotency_key=idempotency_key)


class CentralEmbedding(_CentralHTTP):
    sandbox = False

    async def embed(self, texts: list[str], kind: str = "db") -> list[list[float]]:
        idempotency_key = f"pea-embedding-{uuid.uuid4()}"
        response = await self._post("/api/internal/pea/v1/llm/embeddings", {
            "inputs": texts, "trace_id": str(uuid.uuid4()),
        }, timeout=90, idempotency_key=idempotency_key)
        rows = response.get("data") or response.get("embeddings") or []
        if not isinstance(rows, list) or len(rows) != len(texts):
            raise ValueError("central embedding response shape mismatch")
        return [[float(value) for value in (row.get("embedding") if isinstance(row, dict) else row)] for row in rows]


__all__ = ["central_runtime_enabled", "attest_runtime", "CentralChat", "CentralLyrics", "CentralMusic", "CentralEmbedding"]
