"""Reusable PEA privacy, consent, export and erasure router."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, inspect as sa_inspect, or_, select
from sqlalchemy.ext.asyncio import AsyncSession


class ConsentIn(BaseModel):
    scopes: list[str] = Field(default_factory=list, min_length=1, max_length=20)


class EraseIn(BaseModel):
    confirmation: str


def _row_dict(row: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for column in sa_inspect(type(row)).columns:
        value = getattr(row, column.key)
        if column.key in {"openid"} and value:
            value = "<redacted>"
        result[column.key] = value
    return result


def _model_map(customer: Any) -> dict[str, Any]:
    return {mapper.class_.__name__: mapper.class_ for mapper in customer.__class__.registry.mappers}


async def _related_ids(db: AsyncSession, model: Any | None, field: str, value: str) -> list[str]:
    if model is None or not hasattr(model, field):
        return []
    return list((await db.execute(select(model.id).where(getattr(model, field) == value))).scalars().all())


async def _graph_context(db: AsyncSession, customer: Any) -> dict[str, Any]:
    models = _model_map(customer)
    customer_id = customer.id
    conversation_ids = await _related_ids(db, models.get("Conversation"), "customer_id", customer_id)
    work_ids = await _related_ids(db, models.get("Work"), "customer_id", customer_id)
    order_ids = await _related_ids(db, models.get("Order"), "customer_id", customer_id)
    run_ids = await _related_ids(db, models.get("TaskRun"), "customer_id", customer_id)
    share_rows = []
    if models.get("Share") is not None:
        share_rows = (
            await db.execute(select(models["Share"]).where(models["Share"].sharer_id == customer_id))
        ).scalars().all()
    return {
        "customer_id": customer_id,
        "conversation_ids": conversation_ids,
        "work_ids": work_ids,
        "order_ids": order_ids,
        "run_ids": run_ids,
        "share_tokens": [row.token for row in share_rows],
    }


def _graph_clauses(model: Any, context: dict[str, Any]) -> list[Any]:
    """Return ownership predicates for direct and indirectly-owned PEA rows."""
    name = model.__name__
    customer_id = context["customer_id"]
    clauses: list[Any] = []
    if hasattr(model, "customer_id"):
        clauses.append(model.customer_id == customer_id)
    if name == "TaskEvent" and context["run_ids"]:
        clauses.append(model.run_id.in_(context["run_ids"]))
    if name == "Message" and context["conversation_ids"]:
        clauses.append(model.conversation_id.in_(context["conversation_ids"]))
    if hasattr(model, "work_id") and context["work_ids"]:
        clauses.append(model.work_id.in_(context["work_ids"]))
    if hasattr(model, "order_id") and context["order_ids"]:
        clauses.append(model.order_id.in_(context["order_ids"]))
    if name == "Referral":
        if hasattr(model, "new_customer_id"):
            clauses.append(model.new_customer_id == customer_id)
        if context["share_tokens"] and hasattr(model, "token"):
            clauses.append(model.token.in_(context["share_tokens"]))
    if name == "Share" and hasattr(model, "sharer_id"):
        clauses.append(model.sharer_id == customer_id)
    return clauses


async def export_customer_graph(
    db: AsyncSession, customer: Any, model_classes: list[Any],
) -> dict[str, list[dict[str, Any]]]:
    """Export the same complete ownership graph that account erasure removes."""
    context = await _graph_context(db, customer)
    items: dict[str, list[dict[str, Any]]] = {}
    for model in model_classes:
        clauses = _graph_clauses(model, context)
        if not clauses:
            continue
        rows = (await db.execute(select(model).where(or_(*clauses)))).scalars().all()
        if rows:
            items[model.__tablename__] = [_row_dict(row) for row in rows]
    items[customer.__tablename__] = [_row_dict(customer)]
    return items


def remove_exported_media(items: dict[str, list[dict[str, Any]]], media_dir: str | None) -> int:
    """Remove explicit /media files from an exported graph without path traversal."""
    if not media_dir:
        return 0
    root = Path(media_dir).resolve()
    candidates: set[Path] = set()

    def inspect_value(value: Any) -> None:
        if isinstance(value, dict):
            for nested in value.values():
                inspect_value(nested)
            return
        if isinstance(value, list):
            for nested in value:
                inspect_value(nested)
            return
        if not isinstance(value, str):
            return
        stripped = value.strip()
        if stripped[:1] in {"[", "{"}:
            try:
                inspect_value(json.loads(stripped))
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        path = unquote(urlparse(stripped).path).replace("\\", "/")
        marker = "/media/"
        if marker not in path:
            return
        relative = path.split(marker, 1)[1].lstrip("/")
        if not relative:
            return
        candidate = (root / relative).resolve()
        if candidate.is_relative_to(root) and candidate != root:
            candidates.add(candidate)

    inspect_value(items)
    removed = 0
    for candidate in candidates:
        try:
            if candidate.is_file():
                candidate.unlink()
                removed += 1
        except OSError:
            # Database erasure remains authoritative; storage lifecycle jobs may
            # retry an OS-level lock without leaking a filesystem path to users.
            continue
    return removed


async def erase_customer_graph(db: AsyncSession, customer: Any) -> None:
    """Delete the PEA-local customer graph in foreign-key-safe order."""
    models = _model_map(customer)
    context = await _graph_context(db, customer)
    priority = [
        "TaskEvent", "TaskInteraction", "Message", "MemorialAsset", "MemorialTribute",
        "PaymentRefund", "ReferralReward", "WithdrawalRequest", "Reward", "FulfillmentOrder",
        "ServiceCase", "BusinessIntake", "CartItem", "ShippingAddress", "ConsentRecord", "MemoryChunk", "MemoryItem",
        "Coupon", "Referral", "Share", "Order", "Work", "TaskRun", "Conversation", "PetProfile",
    ]
    for name in priority:
        model = models.get(name)
        if model is None:
            continue
        clauses = _graph_clauses(model, context)
        if clauses:
            await db.execute(delete(model).where(or_(*clauses)))
    await db.delete(customer)


def make_legal_router(
    *, models: Any, get_db: Callable, get_current_customer: Callable,
    app_name: str, retention_days: int, delete_order: list[Any], media_dir: str | None = None,
    policy_version: str = "2026-09-03",
) -> APIRouter:
    router = APIRouter(prefix="/api/legal", tags=["legal"])

    @router.get("/policies")
    async def policies() -> dict:
        return {"app_name": app_name, "version": policy_version, "retention_days": retention_days,
                "privacy": "仅为下单、生成、交付、售后和用户主动分享处理必要数据；密钥由 OPC 中枢保管。",
                "content_rights": "用户应拥有上传文字、照片、音视频和肖像的使用权；平台不取得未明确授予的商业使用权。",
                "refund": "符合商品或服务退款规则时原路退款；退款完成后相关奖励同步冲销。",
                "deletion": "用户可导出并删除账户数据；依法必须保存的财务凭证由 OPC 中枢按法定期限隔离保留。"}

    @router.post("/consents")
    async def accept(req: ConsentIn, customer: Any = Depends(get_current_customer), db: AsyncSession = Depends(get_db)) -> dict:
        allowed = {"privacy", "terms", "content_rights", "marketing"}
        scopes = sorted({scope for scope in req.scopes if scope in allowed})
        if not {"privacy", "terms"}.issubset(scopes):
            raise HTTPException(422, "privacy and terms consent are required")
        row = models.ConsentRecord(customer_id=customer.id, policy_version=policy_version,
                                   scopes_json=json.dumps(scopes, ensure_ascii=False))
        db.add(row)
        await db.commit()
        return {"consent_id": row.id, "version": policy_version, "scopes": scopes}

    @router.get("/export")
    async def export(customer: Any = Depends(get_current_customer), db: AsyncSession = Depends(get_db)) -> dict:
        items = await export_customer_graph(db, customer, delete_order)
        return {"exported_at": time.time(), "policy_version": policy_version, "data": items}

    @router.delete("/account")
    async def erase(req: EraseIn, customer: Any = Depends(get_current_customer), db: AsyncSession = Depends(get_db)) -> dict:
        if req.confirmation != "DELETE":
            raise HTTPException(422, "confirmation must be DELETE")
        exported = await export_customer_graph(db, customer, delete_order)
        await erase_customer_graph(db, customer)
        await db.commit()
        return {"deleted": True, "deleted_media_files": remove_exported_media(exported, media_dir)}

    return router


__all__ = ["export_customer_graph", "make_legal_router", "remove_exported_media"]
