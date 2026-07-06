"""长期记忆：向量 RAG（跨会话语义召回）+ 滚动摘要（压缩早先对话，控 token）。

- VectorMemory：embedding provider + 注入的持久化 store（各 PEA 用自己的 ORM 实现 MemoryChunk 表）。
- RollingSummary：把超出近窗口的老对话让 LLM 压成一段摘要；沙箱/失败时启发式降级（不阻塞）。
"""
from __future__ import annotations

import json
from typing import Any, Protocol

from .embeddings import EmbeddingProvider, cosine

SUMMARY_SYSTEM = (
    "TASK:SUMMARY 你是对话记忆压缩器。把【已有摘要】和【新增对话】合并成一段简洁中文摘要，"
    "只保留对后续对话有用的事实：用户是谁/诉求/关键信息/已做的事/已交付物/未决事项。"
    "150 字以内，纯文本，不要寒暄，不要 JSON。")


class ChunkStore(Protocol):
    """各 PEA 用自己的 ORM 实现（MemoryChunk 表，customer 维度）。"""
    async def add(self, customer_id: str, kind: str, text: str, vec: list[float], ref: str | None) -> None: ...
    async def all_for(self, customer_id: str) -> list[dict[str, Any]]: ...  # [{text,vec,kind,ref}]


class VectorMemory:
    def __init__(self, embedder: EmbeddingProvider, store: ChunkStore):
        self.embedder = embedder
        self.store = store

    async def remember(self, customer_id: str, kind: str, text: str, ref: str | None = None) -> None:
        text = (text or "").strip()
        if not text:
            return
        try:
            vec = (await self.embedder.embed([text[:2000]], kind="db"))[0]
        except Exception:
            return  # 记忆失败不阻塞主流程
        await self.store.add(customer_id, kind, text[:4000], vec, ref)

    async def recall(self, customer_id: str, query: str, k: int = 4, min_score: float = 0.15) -> list[dict[str, Any]]:
        query = (query or "").strip()
        if not query:
            return []
        rows = await self.store.all_for(customer_id)
        if not rows:
            return []
        try:
            qv = (await self.embedder.embed([query[:2000]], kind="query"))[0]
        except Exception:
            return []
        scored = [(cosine(qv, r["vec"]), r) for r in rows if r.get("vec")]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{**r, "score": round(s, 3)} for s, r in scored[:k] if s >= min_score]


class RollingSummary:
    @staticmethod
    async def update(chat: Any, prev_summary: str, dropped_msgs: list[dict[str, str]]) -> str:
        """合并旧摘要 + 被挤出近窗口的对话 → 新摘要。沙箱/异常时启发式降级。"""
        convo = "\n".join(f"{m.get('role')}: {m.get('content','')[:300]}" for m in dropped_msgs if m.get("content"))
        if not convo.strip():
            return prev_summary
        user = f"【已有摘要】{prev_summary or '（无）'}\n【新增对话】\n{convo}"
        try:
            out = await chat.complete(
                [{"role": "system", "content": SUMMARY_SYSTEM}, {"role": "user", "content": user}],
                temperature=0.3, max_tokens=300)
            out = (out or "").strip()
            # 测试替身可能返回 action JSON / 空 → 降级
            if out and not out.startswith("{") and not out.startswith("```"):
                return out[:600]
        except Exception:
            pass
        # 启发式降级：保留旧摘要 + 新对话里的 user 话
        users = [m.get("content", "") for m in dropped_msgs if m.get("role") == "user"]
        heur = (prev_summary + " ｜ " if prev_summary else "") + "；".join(u[:60] for u in users[-4:])
        return heur[:600]
