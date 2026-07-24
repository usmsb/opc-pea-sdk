"""Test-only SQLite session helpers with deterministic connection cleanup."""
from __future__ import annotations

import asyncio
import os
import tempfile
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Coroutine

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


_resources: ContextVar[list[tuple[AsyncSession, AsyncEngine, Path]] | None] = ContextVar(
    "pea_test_sqlite_resources",
    default=None,
)


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
