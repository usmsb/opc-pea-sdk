"""共享管理后台：管理员鉴权 + 通用实体 CRUD + 分析。三体 PEA 各注册自己的实体清单。

无额外依赖：token 用 HMAC 自签（不引 jose）。各 PEA 在 routes/admin.py 调 make_admin_router 注册。
后台账号（admin_user/password）与签名密钥（admin_secret）由各 PEA config 提供——部署期配置项。

设计要点：
- 序列化按模型实际列自省（_row），对三体 schema 漂移（喵星球多 Reward、Work/Order 列不同）天然鲁棒。
- core_entities(models) 给出三体共有实体的 EntitySpec，并按"列是否真实存在"过滤可编辑/搜索字段。
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy import func, inspect, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

# 上传白名单与上限（老板传商品图/纪念照片/视频；C 端晒图复用同逻辑）
_UPLOAD_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".mov", ".webm", ".m4v"}
_UPLOAD_LIMIT = 50 * 1024 * 1024  # 50MB


async def save_upload(file: UploadFile, media_dir: str) -> dict:
    """把上传文件落盘到 media_dir，分块写并限大小/类型，返回 {url,name,size}。/media 静态已挂载。"""
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _UPLOAD_EXT:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型 {ext or '(无扩展名)'}")
    media_dir = os.path.abspath(media_dir)
    os.makedirs(media_dir, exist_ok=True)
    name = uuid.uuid4().hex + ext
    dest = os.path.join(media_dir, name)
    size = 0
    with open(dest, "wb") as f:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > _UPLOAD_LIMIT:
                f.close()
                os.remove(dest)
                raise HTTPException(status_code=400, detail="文件过大（>50MB）")
            f.write(chunk)
    return {"url": f"/media/{name}", "name": name, "size": size}


@dataclass
class EntitySpec:
    model: Any
    label: str
    columns: list[str] = field(default_factory=list)    # 表格展示列（顺序提示；空=前端用全部列）
    editable: list[str] = field(default_factory=list)   # PATCH 可改字段
    creatable: list[str] = field(default_factory=list)  # POST 可建字段（空=不可建）
    deletable: bool = False
    search: list[str] = field(default_factory=list)     # 模糊搜索字段
    order_by: str = "created_at"
    order_desc: bool = True


# ---------- HMAC 自签 token ----------
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _ub64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(user: str, secret: str, ttl_seconds: int = 86400) -> str:
    payload = _b64(json.dumps({"u": user, "exp": int(time.time()) + ttl_seconds}).encode())
    sig = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def verify_token(token: str, secret: str) -> str | None:
    try:
        payload, sig = token.split(".")
        good = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return None
        data = json.loads(_ub64(payload))
        if int(data.get("exp", 0)) < int(time.time()):
            return None
        return str(data.get("u"))
    except Exception:
        return None


# ---------- 模型自省辅助 ----------
def _cols(model: Any) -> list[str]:
    return list(inspect(model).columns.keys())


def _pk(model: Any) -> str:
    return inspect(model).primary_key[0].name


def _present(model: Any, names) -> list[str]:
    have = set(_cols(model))
    return [n for n in names if n in have]


def _row(model: Any, obj: Any) -> dict[str, Any]:
    """按模型实际列自省序列化，附带 _id（主键值）方便前端定位。"""
    out: dict[str, Any] = {c: getattr(obj, c, None) for c in _cols(model)}
    out["_id"] = getattr(obj, _pk(model))
    return out


def spec_for(model: Any, label: str, *, columns=(), editable=(), creatable=(),
             deletable=False, search=(), order_by="created_at") -> EntitySpec:
    """构造 EntitySpec，并把列名按"模型上是否真实存在"过滤——三体 schema 漂移也不会引用到不存在的列。"""
    have = _cols(model)
    ob = order_by if order_by in have else ("updated_at" if "updated_at" in have else have[-1])
    return EntitySpec(
        model=model, label=label,
        columns=_present(model, columns),
        editable=_present(model, editable),
        creatable=_present(model, creatable),
        deletable=deletable,
        search=_present(model, search),
        order_by=ob,
    )


def core_entities(m: Any) -> dict[str, EntitySpec]:
    """三体共有实体的后台清单（传入各 PEA 的 models 模块）。喵星球另在自己的 routes 里补 Reward。"""
    ents: dict[str, EntitySpec] = {
        "customers": spec_for(
            m.Customer, "客户", columns=["id", "openid", "nickname", "channel", "free_trials_used", "created_at"],
            editable=["nickname", "channel", "free_trials_used", "bonus_free_trials"],
            search=["openid", "nickname"], deletable=True),
        "conversations": spec_for(
            m.Conversation, "会话", columns=["id", "customer_id", "surface", "summary", "created_at"],
            search=["customer_id"], deletable=True),
        "messages": spec_for(
            m.Message, "对话消息", columns=["id", "conversation_id", "role", "content", "tool_name", "created_at"],
            search=["content", "role", "tool_name"], deletable=True),
        "works": spec_for(
            m.Work, "交付作品", columns=["id", "customer_id", "kind", "title", "status", "review_score", "created_at"],
            editable=["title", "status", "body", "lyrics", "review_score"],
            search=["title", "kind", "pet_name"], order_by="updated_at", deletable=True),
        "orders": spec_for(
            m.Order, "订单", columns=["id", "customer_id", "sku", "amount_cents", "status", "pay_provider", "paid_at", "created_at"],
            editable=["status", "pay_ref"], search=["sku", "pay_ref", "customer_id"], deletable=True),
        "memory_items": spec_for(
            m.MemoryItem, "客户记忆", columns=["id", "customer_id", "key", "value", "updated_at"],
            editable=["value"], search=["key", "customer_id"], order_by="updated_at", deletable=True),
        "coupons": spec_for(
            m.Coupon, "优惠券", columns=["id", "customer_id", "kind", "value", "status", "created_at"],
            editable=["status", "value"], creatable=["customer_id", "kind", "value", "status"],
            search=["customer_id", "kind"], deletable=True),
        "settings": spec_for(
            m.Setting, "运营配置", columns=["key", "value", "updated_at"],
            editable=["value"], creatable=["key", "value"], search=["key"], order_by="updated_at", deletable=True),
        "shares": spec_for(
            m.Share, "分享卡", columns=["id", "token", "work_id", "sharer_id", "created_at"],
            search=["token", "sharer_id"], deletable=True),
        "referrals": spec_for(
            m.Referral, "裂变归因", columns=["id", "token", "new_customer_id", "rewarded", "created_at"],
            editable=["rewarded"], search=["token"], deletable=True),
        "a2a_jobs": spec_for(
            m.A2AJob, "A2A 工单", columns=["id", "from_agent", "task", "status", "budget_credits", "created_at"],
            editable=["status", "result"], search=["from_agent", "task"], deletable=True),
        "a2a_escrows": spec_for(
            m.A2AEscrow, "A2A 托管", columns=["id", "direction", "counterparty", "amount_credits", "status", "created_at"],
            editable=["status", "note"], search=["counterparty"], deletable=True),
        "credit_entries": spec_for(
            m.CreditEntry, "积分流水", columns=["id", "reason", "amount", "balance_after", "created_at"],
            search=["reason"]),
        "memory_chunks": spec_for(
            m.MemoryChunk, "向量记忆", columns=["id", "customer_id", "kind", "text", "created_at"],
            search=["kind", "customer_id"], deletable=True),
    }
    return ents


def make_admin_router(*, entities: dict[str, EntitySpec], get_settings: Callable[[], Any],
                      get_db: Callable[..., Any], overview: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/api/admin", tags=["admin"])

    def _secret(s: Any) -> str:
        # admin_secret 未配则回退 jwt_secret（仍是 PEA 私有密钥，不影响安全）
        return getattr(s, "admin_secret", "") or getattr(s, "jwt_secret", "") or "pea-admin-dev"

    def _ttl(s: Any) -> int:
        return int(getattr(s, "admin_ttl_seconds", 86400))

    async def require_admin(request: Request) -> str:
        s = get_settings()
        auth = request.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        u = verify_token(token, _secret(s))
        if not u:
            raise HTTPException(status_code=401, detail="需要管理员登录")
        return u

    def _spec(name: str) -> EntitySpec:
        e = entities.get(name)
        if e is None:
            raise HTTPException(status_code=404, detail=f"未知实体 {name}")
        return e

    @router.post("/login")
    async def login(body: dict) -> dict:
        s = get_settings()
        admin_user = getattr(s, "admin_user", "admin")
        admin_pw = getattr(s, "admin_password", "")
        if admin_pw and (body or {}).get("user") == admin_user and (body or {}).get("password") == admin_pw:
            return {"token": make_token(admin_user, _secret(s), _ttl(s)), "user": admin_user, "app": s.app_name}
        raise HTTPException(status_code=401, detail="账号或密码错误")

    @router.get("/me")
    async def me(u: str = Depends(require_admin)) -> dict:
        s = get_settings()
        return {"user": u, "app": s.app_name, "slug": s.pea_slug}

    @router.get("/entities")
    async def list_entities(u: str = Depends(require_admin)) -> dict:
        return {"entities": [
            {"name": k, "label": e.label, "columns": e.columns or _cols(e.model), "editable": e.editable,
             "creatable": e.creatable, "deletable": e.deletable, "search": e.search, "pk": _pk(e.model)}
            for k, e in entities.items()]}

    @router.get("/overview")
    async def admin_overview(db: AsyncSession = Depends(get_db), u: str = Depends(require_admin)) -> dict:
        return await overview(db, get_settings())

    @router.get("/stats/timeseries")
    async def timeseries(days: int = Query(14, ge=1, le=90), db: AsyncSession = Depends(get_db),
                         u: str = Depends(require_admin)) -> dict:
        e = entities.get("orders")
        if e is None:
            return {"series": []}
        rows = (await db.execute(select(e.model))).scalars().all()
        today = dt.date.today()
        buckets = {(today - dt.timedelta(days=i)).isoformat(): {"orders": 0, "revenue": 0.0} for i in range(days)}
        for o in rows:
            if getattr(o, "status", "") != "paid":
                continue
            ts = getattr(o, "paid_at", None) or getattr(o, "created_at", None)
            if not ts:
                continue
            day = dt.date.fromtimestamp(ts).isoformat()
            if day in buckets:
                buckets[day]["orders"] += 1
                buckets[day]["revenue"] += getattr(o, "amount_cents", 0) / 100
        return {"series": [{"day": d, **buckets[d]} for d in sorted(buckets)]}

    @router.post("/upload")
    async def upload(file: UploadFile = File(...), u: str = Depends(require_admin)) -> dict:
        # 落盘到本 PEA 的 media_dir（部署期用 PVC 持久化，否则 pod 重启丢）
        media_dir = getattr(get_settings(), "media_dir", "./data/media")
        return await save_upload(file, media_dir)

    @router.get("/{name}")
    async def list_rows(name: str, q: str = Query("", description="模糊搜索"),
                        limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
                        db: AsyncSession = Depends(get_db), u: str = Depends(require_admin)) -> dict:
        e = _spec(name)
        stmt = select(e.model)
        if q and e.search:
            stmt = stmt.where(or_(*[getattr(e.model, f).ilike(f"%{q}%") for f in e.search]))
        total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
        col = getattr(e.model, e.order_by, None)
        if col is not None:
            stmt = stmt.order_by(col.desc() if e.order_desc else col.asc())
        rows = (await db.execute(stmt.limit(limit).offset(offset))).scalars().all()
        return {"total": total, "rows": [_row(e.model, r) for r in rows]}

    @router.get("/{name}/{rid}")
    async def get_row(name: str, rid: str, db: AsyncSession = Depends(get_db),
                      u: str = Depends(require_admin)) -> dict:
        e = _spec(name)
        obj = (await db.execute(select(e.model).where(getattr(e.model, _pk(e.model)) == rid))).scalar_one_or_none()
        if obj is None:
            raise HTTPException(status_code=404, detail="记录不存在")
        return _row(e.model, obj)

    @router.post("/{name}")
    async def create_row(name: str, body: dict, db: AsyncSession = Depends(get_db),
                         u: str = Depends(require_admin)) -> dict:
        e = _spec(name)
        if not e.creatable:
            raise HTTPException(status_code=400, detail=f"{e.label} 不支持新建")
        data = {k: body.get(k) for k in e.creatable if k in (body or {})}
        obj = e.model(**data)
        db.add(obj)
        await db.flush()
        await db.commit()
        return _row(e.model, obj)

    @router.patch("/{name}/{rid}")
    async def update_row(name: str, rid: str, body: dict, db: AsyncSession = Depends(get_db),
                         u: str = Depends(require_admin)) -> dict:
        e = _spec(name)
        obj = (await db.execute(select(e.model).where(getattr(e.model, _pk(e.model)) == rid))).scalar_one_or_none()
        if obj is None:
            raise HTTPException(status_code=404, detail="记录不存在")
        for k in e.editable:
            if k in (body or {}):
                setattr(obj, k, body[k])
        await db.flush()
        await db.commit()
        return _row(e.model, obj)

    @router.delete("/{name}/{rid}")
    async def delete_row(name: str, rid: str, db: AsyncSession = Depends(get_db),
                         u: str = Depends(require_admin)) -> dict:
        e = _spec(name)
        if not e.deletable:
            raise HTTPException(status_code=400, detail=f"{e.label} 不支持删除")
        obj = (await db.execute(select(e.model).where(getattr(e.model, _pk(e.model)) == rid))).scalar_one_or_none()
        if obj is None:
            raise HTTPException(status_code=404, detail="记录不存在")
        await db.delete(obj)
        await db.commit()
        return {"deleted": rid}

    return router
