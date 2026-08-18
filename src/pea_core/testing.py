"""Test-only SQLite session helpers with deterministic connection cleanup."""
from __future__ import annotations

import asyncio
import os
import tempfile
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Coroutine

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


_resources: ContextVar[list[tuple[AsyncSession, AsyncEngine, Path]] | None] = ContextVar(
    "pea_test_sqlite_resources",
    default=None,
)


class FakeCentralCommerce:
    """Deterministic two-phase payment double for PEA tests only.

    It mirrors the OPC contract without touching WeChat: ``prepay`` creates a
    ``requires_payment`` intent, ``mark_paid`` simulates the client callback,
    and ``payment_intent`` exposes the resulting receipt for polling tests.
    """

    intents: dict[str, dict[str, object]] = {}
    order_to_intent: dict[str, str] = {}

    @classmethod
    def reset(cls) -> None:
        cls.intents.clear()
        cls.order_to_intent.clear()

    @classmethod
    def mark_paid(cls, order_id: str | None = None, intent_id: str | None = None) -> dict[str, object]:
        resolved = intent_id or cls.order_to_intent.get(str(order_id or ""))
        if not resolved or resolved not in cls.intents:
            raise KeyError("fake payment intent not found")
        cls.intents[resolved]["status"] = "paid"
        cls.intents[resolved]["provider_transaction_id"] = f"fake-tx-{resolved[-12:]}"
        return dict(cls.intents[resolved])

    async def prepay(self, *, order_id: str, channel: str, amount_cents: int,
                     description: str, openid: str | None, client_ip: str | None,
                     idempotency_key: str) -> dict[str, object]:
        existing = self.order_to_intent.get(order_id)
        if existing:
            return {"intent": dict(self.intents[existing])}
        intent_id = f"fake-intent-{uuid.uuid4().hex[:20]}"
        intent: dict[str, object] = {
            "id": intent_id, "order_id": order_id, "status": "requires_payment",
            "amount_cents": amount_cents, "channel": channel,
            "description": description,
            "client": {"mode": "fake", "request_payment": {"provider": "fake"}},
        }
        self.intents[intent_id] = intent
        self.order_to_intent[order_id] = intent_id
        return {"intent": dict(intent)}

    async def payment_intent(self, intent_id: str) -> dict[str, object]:
        if intent_id not in self.intents:
            raise RuntimeError("fake payment intent not found")
        return {"intent": dict(self.intents[intent_id])}


async def isolated_sqlite_session(base: Any) -> AsyncSession:
    """Create a temp SQLite session tracked by :func:`run_isolated`."""
    resources = _resources.get()
    if resources is None:
        raise RuntimeError("isolated_sqlite_session must run inside run_isolated")
    fd, raw_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    path = Path(raw_path)
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}", poolclass=NullPool)
    async with engine.begin() as connection:
        await connection.run_sync(base.metadata.create_all)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    resources.append((session, engine, path))
    return session


def run_isolated(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run one async test and close every session/engine it created."""

    async def managed() -> Any:
        resources: list[tuple[AsyncSession, AsyncEngine, Path]] = []
        token = _resources.set(resources)
        try:
            return await coro
        finally:
            for session, _engine, _path in reversed(resources):
                try:
                    if session.in_transaction():
                        await session.rollback()
                finally:
                    await session.close()
            for _session, engine, path in reversed(resources):
                await engine.dispose()
                for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
                    candidate.unlink(missing_ok=True)
            _resources.reset(token)

    return asyncio.run(managed())
