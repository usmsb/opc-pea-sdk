"""PEA-local projections for OPC-controlled original-route refunds."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .opc_client import CentralCommerce, CentralCommerceError, central_runtime_enabled


async def request_order_refund(
    db: AsyncSession, *, order_model: Any, refund_model: Any, customer_id: str,
    order_id: str, reason: str,
) -> tuple[Any, Any]:
    order = await db.scalar(select(order_model).where(order_model.id == order_id, order_model.customer_id == customer_id))
    if order is None:
        raise CentralCommerceError("订单不存在。", code="ORDER_NOT_FOUND", status_code=404)
    existing = await db.scalar(select(refund_model).where(refund_model.order_id == order.id))
    if existing is not None:
        return order, existing
    if order.status != "paid" or not order.idempotency_key:
        raise CentralCommerceError("只有已确认支付的订单可以退款。", code="ORDER_NOT_REFUNDABLE", status_code=409)
    if not central_runtime_enabled():
        raise CentralCommerceError("OPC 支付中枢尚未配置。", code="CENTRAL_COMMERCE_RUNTIME_UNAVAILABLE", status_code=503)
    row = refund_model(customer_id=customer_id, order_id=order.id, amount_cents=order.amount_cents,
                       reason=str(reason or "用户申请退款")[:80], status="created")
    db.add(row)
    await db.flush()
    response = await CentralCommerce().refund(
        intent_id=order.idempotency_key, amount_cents=order.amount_cents, reason=row.reason,
        idempotency_key=f"pea-refund-{order.id}",
    )
    central = response["refund"]
    row.central_refund_id = str(central["id"])
    row.status = str(central.get("status") or "processing")
    order.status = "refund_processing" if row.status != "success" else "refunded"
    await db.commit()
    return order, row


async def sync_order_refund(
    db: AsyncSession, *, order: Any, refund: Any,
    on_success: Callable[[], Awaitable[Any]] | None = None,
) -> Any:
    if refund.status not in {"success", "closed"} and refund.central_refund_id:
        response = await CentralCommerce().refund_status(refund.central_refund_id)
        refund.status = str(response["refund"].get("status") or refund.status)
    if refund.status == "success" and order.status != "refunded":
        order.status = "refunded"
        if on_success:
            await on_success()
    await db.commit()
    return refund


__all__ = ["request_order_refund", "sync_order_refund"]
