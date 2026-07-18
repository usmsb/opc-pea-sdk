"""PEA 通用 Harness 父类：perceive → think(LLM) → act(tools) → observe → 迭代。

三体 PEA 继承它，只实现少量"接线"钩子（自己的 ORM/providers/state/tools）。
核心逻辑（分层上下文 + RAG 召回 + 滚动摘要 + 交付物上下文感知生成）只此一份。
无业务 if-else：判断全来自 LLM（沙箱用测试替身）。
"""
from __future__ import annotations

import abc
import json
from dataclasses import dataclass, field
from typing import Any

from . import context as ctxmod
from .memory import RollingSummary, VectorMemory

WINDOW = 12       # 近窗口保留消息数
RAG_K = 4         # 每轮 RAG 召回条数
MAX_STEPS = 14    # 单轮 harness 最多工具步数


@dataclass
class TurnResult:
    reply: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)


def parse_action(raw: str) -> dict[str, Any]:
    """从模型输出里取出**第一个**合法 action 对象（action=say|tool）。

    真实大模型常在一轮里吐多个 JSON（如 {tool}\\n\\n{say}）或带前后文/```json 包裹——
    旧写法取"首 { 到尾 }"整段会 json 解析失败→把原始 JSON 当 say 泄露、工具不执行。
    这里用 raw_decode 从每个 { 处尝试解析单个对象，命中第一个 action 即返回（忽略后续），稳。
    """
    s = (raw or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        s = s[4:] if s.startswith("json") else s
        s = s.strip()
    dec = json.JSONDecoder()
    i, n = 0, len(s)
    while True:
        st = s.find("{", i)
        if st < 0:
            break
        try:
            obj, end = dec.raw_decode(s, st)  # 解析 st 处的单个对象，忽略其后内容
        except json.JSONDecodeError:
            i = st + 1
            continue
        if isinstance(obj, dict) and obj.get("action") in ("say", "tool"):
            return obj
        i = end if end > st else st + 1
    # 实在没有 action 对象：当作纯文本回复（但别把疑似 action 的裸 JSON 泄露给用户）
    if s.startswith("{") and '"action"' in s:
        return {"action": "say", "text": "我在的，您慢慢说～"}
    return {"action": "say", "text": s or "我在的，您慢慢说～"}


class BaseHarness(abc.ABC):
    """子类需提供：system_prompt + 下列钩子。其余（run_turn/generate/上下文装配）由本类完成。"""

    # ---- 子类接线钩子 ----
    @property
    @abc.abstractmethod
    def system_prompt(self) -> str: ...

    @property
    @abc.abstractmethod
    def chat(self) -> Any: ...           # ChatProvider（含 .complete）

    @property
    @abc.abstractmethod
    def vmem(self) -> VectorMemory: ...   # 向量记忆（embedding + store）

    @abc.abstractmethod
    async def load_history(self, conv: Any) -> list[dict[str, Any]]:
        """返回 [{role, content, tool_name, tool_payload}]，按时间升序。"""

    @abc.abstractmethod
    async def save_message(self, conv: Any, role: str, content: str,
                           tool_name: str | None = None, tool_payload: str | None = None) -> None: ...

    @abc.abstractmethod
    async def compute_state(self, customer: Any, conv: Any) -> dict[str, Any]: ...

    @abc.abstractmethod
    def make_ctx(self, customer: Any, conv: Any) -> Any:
        """构造 ToolContext（须带 .generate = self.generate 的偏函数，供交付工具上下文感知生成）。"""

    @abc.abstractmethod
    def known_tool(self, name: str) -> bool: ...

    @abc.abstractmethod
    async def dispatch(self, name: str, ctx: Any, args: dict[str, Any]) -> dict[str, Any]: ...

    @abc.abstractmethod
    async def get_summary(self, conv: Any) -> str: ...

    @abc.abstractmethod
    async def set_summary(self, conv: Any, summary: str) -> None: ...

    # ---- 核心逻辑（共享）----
    async def _assemble(self, customer: Any, conv: Any, query: str) -> list[dict[str, str]]:
        rows = await self.load_history(conv)
        clean = ctxmod.to_llm_messages(rows)
        recent, overflow = ctxmod.split_window(clean, WINDOW)
        summary = await self.get_summary(conv)
        if overflow:
            summary = await RollingSummary.update(self.chat, summary, overflow)
            await self.set_summary(conv, summary)
        recalls = await self.vmem.recall(customer.id, query, k=RAG_K)
        state = await self.compute_state(customer, conv)
        return ctxmod.build(self.system_prompt, summary, recalls, recent, state.get("memory", {}), state)

    async def run_turn(self, customer: Any, conv: Any, user_text: str) -> TurnResult:
        await self.save_message(conv, "user", user_text)
        await self.vmem.remember(customer.id, "user", user_text)
        steps: list[dict[str, Any]] = []
        reply = ""
        for _ in range(MAX_STEPS):
            msgs = await self._assemble(customer, conv, user_text)
            raw = await self.chat.complete(msgs)
            action = parse_action(raw)
            if action.get("action") == "say":
                reply = str(action.get("text", "")).strip()
                await self.save_message(conv, "assistant", reply)
                await self.vmem.remember(customer.id, "assistant", reply)
                break
            name = str(action.get("name", ""))
            args = action.get("args") or {}
            if not self.known_tool(name):
                reply = "（内部）未知工具，已跳过。"
                await self.save_message(conv, "assistant", reply)
                break
            result = await self.dispatch(name, self.make_ctx(customer, conv), args)
            await self.save_message(conv, "tool", "", tool_name=name,
                                    tool_payload=json.dumps(result, ensure_ascii=False))
            steps.append({"tool": name, "args": args, "result": result})
        else:
            reply = "（已尽力，稍后再为您继续）"
            await self.save_message(conv, "assistant", reply)
        state = await self.compute_state(customer, conv)
        return TurnResult(reply=reply, steps=steps, state=state)

    async def generate(self, customer: Any, conv: Any, system: str, ask: str,
                       kind: str, title: str, temperature: float = 0.6, max_tokens: int = 2000) -> str:
        """交付物的上下文感知生成：近窗口 + RAG 召回 + 当前诉求 → 正文。

        正文落库由调用方负责；这里把正文存进向量记忆（供日后召回），不塞进每轮上下文。
        """
        rows = await self.load_history(conv)
        recent, _ = ctxmod.split_window(ctxmod.to_llm_messages(rows), WINDOW)
        recalls = await self.vmem.recall(customer.id, ask or title, k=RAG_K)
        msgs: list[dict[str, str]] = [{"role": "system", "content": system}]
        if recalls:
            msgs.append({"role": "system", "content": "参考历史：\n" + "\n".join(
                f"- {(r.get('text') or '')[:200]}" for r in recalls)})
        msgs.extend(recent)
        msgs.append({"role": "user", "content": ask or f"请生成「{title}」。"})
        try:
            body = await self.chat.complete(msgs, temperature=temperature, max_tokens=max_tokens)
        except Exception:
            body = ""
        body = (body or "").strip()
        if body and not body.startswith("{"):
            await self.vmem.remember(customer.id, kind, f"{title}\n{body}", ref=kind)
        return body
