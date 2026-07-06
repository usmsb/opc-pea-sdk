"""分层上下文装配：系统提示 → 滚动摘要 → RAG 召回 → 近窗口原文 → MEMORY+STATE → 当前话。

关键：把"裸 JSON 工具结果/大段交付物正文"清洗成简短行，正文不进每轮上下文（控 token）；
交付物以 role=artifact 的简短摘要进历史，完整正文落库 + 进向量记忆，按需召回。
"""
from __future__ import annotations

import json
from typing import Any

# 交付物正文字段（不进每轮上下文，只留简短摘要）
_BODY_KEYS = ("report", "plan", "memorial", "preview", "body", "lyrics", "summary")


def _brief_tool(tool_name: str, payload: str | None) -> str:
    try:
        d = json.loads(payload or "{}")
    except json.JSONDecodeError:
        d = {}
    flags = {k: v for k, v in d.items()
             if k not in _BODY_KEYS and not (isinstance(v, str) and len(v) > 80)}
    note = json.dumps(flags, ensure_ascii=False) if flags else "ok"
    # 交付物正文不进上下文，只留一句提示（让脑知道已交付什么；完整正文在向量记忆里按需召回）
    hint = next((str(d[k]).replace("\n", " ")[:90] for k in _BODY_KEYS if d.get(k)), "")
    if hint:
        note += f" ｜已交付摘要：{hint}…"
    return f"[工具 {tool_name}] {note[:300]}"


def to_llm_messages(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    """rows: [{role, content, tool_name, tool_payload}] → 干净 LLM 消息（无大 blob）。"""
    out: list[dict[str, str]] = []
    for m in rows:
        role = m.get("role")
        if role in ("user", "assistant", "artifact"):
            content = (m.get("content") or "").strip()
            if content:
                out.append({"role": "assistant" if role == "artifact" else role, "content": content})
        elif role == "tool":
            out.append({"role": "system", "content": _brief_tool(m.get("tool_name") or "?", m.get("tool_payload"))})
    return out


def split_window(msgs: list[dict[str, str]], keep: int = 12) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """近窗口保留最后 keep 条；更早的作为 overflow 交给滚动摘要。"""
    if len(msgs) <= keep:
        return msgs, []
    return msgs[-keep:], msgs[:-keep]


def build(system_prompt: str, summary: str, recalls: list[dict[str, Any]],
          recent: list[dict[str, str]], memory: dict[str, Any], state: dict[str, Any]) -> list[dict[str, str]]:
    msgs: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    if summary:
        msgs.append({"role": "system", "content": "SUMMARY(早先对话摘要)：" + summary})
    if recalls:
        lines = "\n".join(f"- [{r.get('kind','记忆')}] {(r.get('text') or '')[:240]}" for r in recalls)
        msgs.append({"role": "system", "content": "RECALL(相关历史，可能跨会话)：\n" + lines})
    msgs.extend(recent)
    if memory:
        msgs.append({"role": "system", "content": "MEMORY:" + json.dumps(memory, ensure_ascii=False)})
    # STATE 去掉 memory（已单独作 MEMORY 注入）；保留 last_user/last_tool 等事实（脑判断要用）
    facts = {k: v for k, v in state.items() if k != "memory"}
    msgs.append({"role": "system", "content": "STATE:" + json.dumps(facts, ensure_ascii=False)})
    return msgs
