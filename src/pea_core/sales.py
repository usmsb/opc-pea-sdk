"""One explicit switch for new customer sales, independent of provider access."""
from typing import Any

SALES_PAUSED_MESSAGE = "当前开放免费体验，付费服务暂未开放。"


def sales_paused(settings: Any) -> dict[str, Any] | None:
    if getattr(settings, "payments_enabled", True):
        return None
    return {"paid": False, "blocked": True, "code": "SALES_PAUSED", "reason": SALES_PAUSED_MESSAGE}


def sales_prompt(settings: Any) -> str:
    if getattr(settings, "payments_enabled", True):
        return ""
    return (
        "\n# 当前营业范围（优先于上文的报价、升单与收款说明）\n"
        "当前仅开放既有免费体验、咨询和需求沟通，付费服务暂未开放。"
        "不要引导下单、购买、付款、支付确认或线下转账；不要创建新订单、发券促购或转包付费服务。"
        "不能把未付款标为已付款，也不能绕过原有次数、交付质量及购买权限。"
        "如果客户要求购买，说明暂未开放并引导免费体验或记录需求。"
        "已购客户仍可查看、继续其有权获得的交付并申请售后。\n"
    )
