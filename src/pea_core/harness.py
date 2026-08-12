"""PEA 通用 Harness 父类：perceive → think(LLM) → act(tools) → observe → 迭代。

三体 PEA 继承它，只实现少量"接线"钩子（自己的 ORM/providers/state/tools）。
核心逻辑（分层上下文 + RAG 召回 + 滚动摘要 + 交付物上下文感知生成）只此一份。
无业务 if-else：判断全来自 LLM（沙箱用测试替身）。
"""
from __future__ import annotations

import abc
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

try:
    # The full Agent image exposes the canonical implementation.
    from agents.complex_task_harness import ComplexTaskSession, HarnessObjective
except ModuleNotFoundError:  # pragma: no cover - exercised in the slim PEA image
    # PEA images copy only pea_core; the package-local mirror preserves the
    # exact public projection without adding the Agent runtime dependency.
    from .complex_task_harness import ComplexTaskSession, HarnessObjective

from . import context as ctxmod
from .context_manifest import build_manifest
from .interaction import INTERACTION_ACTIONS, interaction_from_action, interaction_from_payload
from .memory import RollingSummary, VectorMemory
from .quality_contract import render_quality_contract

WINDOW = 12       # 近窗口保留消息数
RAG_K = 4         # 每轮 RAG 召回条数
MAX_STEPS = 14    # 单轮 harness 最多工具步数


@dataclass
class TurnResult:
    reply: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)
    harness: dict[str, Any] = field(default_factory=dict)
    turn_id: str = field(default_factory=lambda: f"turn_{uuid.uuid4().hex}")
    pending_interactions: list[dict[str, Any]] = field(default_factory=list)
    context_manifest: dict[str, Any] = field(default_factory=dict)


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
        if isinstance(obj, dict) and obj.get("action") in ({"say", "tool"} | INTERACTION_ACTIONS):
            return obj
        i = end if end > st else st + 1
    # 实在没有 action 对象：当作纯文本回复（但别把疑似 action 的裸 JSON 泄露给用户）
    if s.startswith("{") and '"action"' in s:
        return {"action": "say", "text": "我在的，您慢慢说～"}
    return {"action": "say", "text": s or "我在的，您慢慢说～"}


def _action_object(raw: str) -> dict[str, Any] | None:
    """Return a real action object without converting prose into ``say``."""

    parsed = parse_action(raw)
    source = str(raw or "").strip()
    if parsed.get("action") == "say" and parsed.get("text") == source:
        return None
    if (
        parsed.get("action") == "say"
        and source.startswith("{")
        and '"action"' in source
        and parsed.get("text") == "我在的，您慢慢说～"
    ):
        return None
    return parsed


def _artifact_first_domain_prompt(prompt: str) -> str:
    """Remove legacy JSON transport directives from a PEA domain prompt."""

    lines: list[str] = []
    for line in str(prompt or "").splitlines():
        if '{"action"' in line:
            continue
        if "输出格式" in line:
            lines.append("# 下一步决策 Artifact")
            continue
        lines.append(line.replace("JSON action", "下一步决策 Artifact"))
    lines.extend(
        [
            "",
            "请输出一份简短、完整的自然语言下一步决策 Artifact，不输出 JSON。",
            "必须写清 decision_type（say/tool/needs_input/needs_authorization）、给用户的话；",
            "需要工具时还要写清准确 tool_name 和每个参数。一步只决定一件事。",
            "Harness 会在下一次独立 LLM 调用中把这份 Artifact 投影成机器 action。",
        ]
    )
    return "\n".join(lines)


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

    @abc.abstractmethod
    async def checkpoint(self) -> None:
        """Durably finish the current short transaction before external I/O."""

    # ---- 核心逻辑（共享）----
    async def _assemble(self, customer: Any, conv: Any, query: str) -> list[dict[str, str]]:
        rows = await self.load_history(conv)
        clean = ctxmod.to_llm_messages(rows)
        recent, overflow = ctxmod.split_window(clean, WINDOW)
        summary = await self.get_summary(conv)
        if overflow:
            await self.checkpoint()
            summary = await RollingSummary.update(self.chat, summary, overflow)
            await self.set_summary(conv, summary)
            await self.checkpoint()
        recalls = await self.vmem.recall(customer.id, query, k=RAG_K)
        state = await self.compute_state(customer, conv)
        self._last_context_manifest = build_manifest(
            goal=query,
            summary=summary,
            recent=recent,
            recalls=recalls,
            state=state,
        ).to_dict()
        producer_prompt = "\n\n".join(
            (
                render_quality_contract(role="generator"),
                "【PEA 领域与工具合同】",
                _artifact_first_domain_prompt(self.system_prompt),
            )
        )
        return ctxmod.build(producer_prompt, summary, recalls, recent, state.get("memory", {}), state)

    async def run_turn(self, customer: Any, conv: Any, user_text: str,
                       run_id: str | None = None) -> TurnResult:
        # PEA conversations are complex tasks by default.  The common session
        # provides the same objective/context/artifact/checkpoint contract as
        # A2A Agents while keeping PEA-specific tools and memory local.
        harness_session = ComplexTaskSession(
            HarnessObjective(
                goal=user_text,
                success_evidence=("respond to the user's request",),
                stop_conditions=("human approval or tool safety gate required",),
                side_effect_class="read_only",
                max_rounds=1,
            ),
            run_id=run_id or f"pea_{getattr(conv, 'id', None) or id(conv)}",
        )
        harness_session.add_context("user_request", user_text, kind="objective", priority=100, authoritative=True)
        await self.save_message(conv, "user", user_text)
        await self.checkpoint()
        await self.vmem.remember(customer.id, "user", user_text)
        await self.checkpoint()
        steps: list[dict[str, Any]] = []
        pending_interactions: list[dict[str, Any]] = []
        turn_id = f"turn_{uuid.uuid4().hex}"
        reply = ""
        for _ in range(MAX_STEPS):
            harness_session.begin_round(reason="tool_or_reply_decision")
            msgs = await self._assemble(customer, conv, user_text)
            # compute_state/history reads and any summary writes must be closed
            # before waiting on a provider. SQLite has one writer, and a WAL
            # reader should not pin a checkpoint for the duration of an LLM call.
            await self.checkpoint()
            raw = await self.chat.complete(msgs)
            action = _action_object(raw)
            if action is None:
                # Semantic planning and JSON transport are separate model
                # calls.  The formatter receives only the completed decision
                # Artifact, never the original conversation/RAG context.
                projection_messages = [
                    {
                        "role": "system",
                        "content": "\n\n".join(
                            (
                                render_quality_contract(role="projection"),
                                "只把附加的下一步决策 Artifact 投影为一个小型 action JSON。",
                                "允许 action=say|tool|needs_input|needs_authorization。",
                                "say 使用 text；tool 使用 name 和 args；交互动作保留 text/message/interaction。",
                                "不重新决策、不新增工具、参数、事实或授权。只输出一个 JSON object。",
                            )
                        ),
                    },
                    {"role": "user", "content": raw},
                ]
                try:
                    projected_raw = await self.chat.complete(
                        projection_messages,
                        temperature=0,
                        max_tokens=2_000,
                    )
                    action = _action_object(projected_raw)
                    if action is None and str(projected_raw or "").strip():
                        repaired_raw = await self.chat.complete(
                            [
                                {
                                    "role": "system",
                                    "content": (
                                        "只修复附加 action 投影的 JSON 语法，保留所有可恢复字段和值；"
                                        "不得读取对话、重新决策或新增工具参数。只输出一个 JSON object。"
                                    ),
                                },
                                {"role": "user", "content": projected_raw},
                            ],
                            temperature=0,
                            max_tokens=2_000,
                        )
                        action = _action_object(repaired_raw)
                except Exception:
                    action = None
            if action is None:
                reply = "这一步的执行决策没有形成可用动作，我已安全停止；请再说一次你当前最想完成的事。"
                harness_session.quality_blocked("PEA action projection did not converge")
                await self.save_message(conv, "assistant", reply)
                await self.checkpoint()
                break
            if action.get("action") in INTERACTION_ACTIONS:
                interaction = interaction_from_action(action, run_id or f"run_local_{conv.id}")
                if interaction is None:
                    reply = "我需要你的确认，但这次交互信息不完整，请重新说一下你的目标。"
                else:
                    pending_interactions.append(interaction.to_dict())
                    reply = str(action.get("text") or action.get("message") or interaction.description).strip()
                    reply = reply or interaction.title
                await self.save_message(conv, "assistant", reply)
                await self.checkpoint()
                break
            if action.get("action") == "say":
                reply = str(action.get("text", "")).strip()
                harness_session.record_artifact(reply, artifact_type="pea_reply", source=self.__class__.__name__)
                harness_session.mark_provider("terminal", terminal=True)
                if harness_session.open_issues():
                    harness_session.quality_blocked(
                        "PEA attempted to finish while domain quality blockers remain"
                    )
                await self.save_message(conv, "assistant", reply)
                await self.checkpoint()
                await self.vmem.remember(customer.id, "assistant", reply)
                await self.checkpoint()
                break
            name = str(action.get("name", ""))
            args = action.get("args") or {}
            if not self.known_tool(name):
                reply = "（内部）未知工具，已跳过。"
                await self.save_message(conv, "assistant", reply)
                await self.checkpoint()
                break
            await self.checkpoint()
            result = await self.dispatch(name, self.make_ctx(customer, conv), args)
            if name == "质检" or (
                isinstance(result, dict)
                and "pass" in result
                and ("score" in result or "quality_contract" in result)
            ):
                harness_session.record_review(result)
            harness_session.add_context(
                f"tool-{len(steps)}",
                f"tool={name} result={json.dumps(result, ensure_ascii=False)[:8_000]}",
                kind="tool_observation",
                priority=70,
                authoritative=True,
            )
            # Tool implementations may write. Never carry those writes into the
            # next LLM decision round.
            await self.checkpoint()
            await self.save_message(conv, "tool", "", tool_name=name,
                                    tool_payload=json.dumps(result, ensure_ascii=False))
            await self.checkpoint()
            steps.append({"tool": name, "args": args, "result": result})
            interaction = interaction_from_payload(result, run_id or f"run_local_{conv.id}")
            if interaction is not None:
                pending_interactions.append(interaction.to_dict())
                reply = str(result.get("message") or interaction.description or interaction.title).strip()
                await self.save_message(conv, "assistant", reply)
                await self.checkpoint()
                break
        else:
            reply = "（已尽力，稍后再为您继续）"
            harness_session.fail_closed("PEA bounded tool loop exhausted", provider_status="loop_exhausted")
            await self.save_message(conv, "assistant", reply)
            await self.checkpoint()
        state = await self.compute_state(customer, conv)
        if harness_session.checkpoint.status == "running":
            harness_session.complete()
        harness_projection = harness_session.to_projection()
        state = {**state, "harness": harness_projection}
        await self.checkpoint()
        return TurnResult(reply=reply, steps=steps, state=state, harness=harness_projection,
                          turn_id=turn_id, pending_interactions=pending_interactions,
                          context_manifest=getattr(self, "_last_context_manifest", {}))

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
            await self.checkpoint()
            body = await self.chat.complete(msgs, temperature=temperature, max_tokens=max_tokens)
        except Exception:
            body = ""
        body = (body or "").strip()
        if body and not body.startswith("{"):
            await self.vmem.remember(customer.id, kind, f"{title}\n{body}", ref=kind)
            await self.checkpoint()
        return body
