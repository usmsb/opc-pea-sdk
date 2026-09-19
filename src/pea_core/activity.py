"""Safe, compact UI projection of a PEA Harness turn.

The canonical Harness projection remains internal.  Never send raw prompts,
tool arguments/results, provider output, or private context to the chat UI.
"""

from __future__ import annotations

from typing import Any, Mapping


def present_turn_activity(
    *,
    steps: list[dict[str, Any]] | None,
    harness: Mapping[str, Any] | None,
    pending_interactions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    projection = harness if isinstance(harness, Mapping) else {}
    executed = steps if isinstance(steps, list) else []
    pending = pending_interactions if isinstance(pending_interactions, list) else []
    status = str(projection.get("status") or "completed").strip().lower()
    needs_input = status in {"needs_human_input", "waiting_for_human"} or any(
        item.get("status") == "pending" for item in pending if isinstance(item, dict)
    )
    if needs_input:
        state, title = "waiting", "等待你的确认"
    elif status in {"failed", "quality_blocked", "needs_reconciliation", "semantic_stalled"}:
        state, title = "attention", "本轮需要继续处理"
    else:
        state, title = "completed", "本轮已完成"

    events: list[dict[str, str]] = [{"label": "整理本轮需求与上下文", "state": "completed"}]
    for step in executed[:8]:
        if not isinstance(step, dict):
            continue
        name = str(step.get("tool") or "调用能力").strip()[:32]
        result = step.get("result")
        has_issue = isinstance(result, Mapping) and (
            result.get("pass") is False
            or any(result.get(key) for key in ("error", "blocked", "failed"))
        )
        events.append({"label": name or "调用能力", "state": "attention" if has_issue else "completed"})
    if len(executed) > 8:
        events.append({"label": f"另有 {len(executed) - 8} 项处理", "state": "completed"})
    if needs_input:
        events.append({"label": "等待确认后继续", "state": "waiting"})
    elif state == "completed":
        events.append({"label": "形成本轮回复", "state": "completed"})
    for index, event in enumerate(events):
        event["id"] = str(index)

    context = projection.get("context")
    raw_compacted = context.get("compacted_count") if isinstance(context, Mapping) else 0
    try:
        compacted = int(raw_compacted or 0)
    except (TypeError, ValueError):
        compacted = 0
    summary = (
        f"已调用 {len(executed)} 项能力"
        if executed
        else "已结合当前对话形成回复"
    )
    if compacted:
        summary += "，已整理较早的对话"
    return {
        "schema": "opc.pea_harness_activity.v1",
        "state": state,
        "title": title,
        "summary": summary,
        "events": events,
    }
