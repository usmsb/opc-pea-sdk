"""Artifact-first semantic quality review for standalone PEA services.

PEA images do not ship the OPC backend, so this helper uses the image-local
mirror of the shared complex-task contract.  It deliberately performs two
provider calls: a Markdown semantic review over the complete Artifact, then a
small JSON projection over that review only.  Projection failure fails closed
without asking the reviewer to repeat the expensive semantic work.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from .quality_contract import quality_contract_binding, render_quality_contract


def _first_object(raw: str) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


async def review_complex_artifact(
    *,
    chat: Any,
    before_external_io: Callable[[], Awaitable[None]],
    artifact: str,
    objective: str,
    domain_requirements: str,
) -> dict[str, Any]:
    body = str(artifact or "").strip()
    if not body:
        return {
            "pass": False,
            "score": 0.0,
            "reason": "canonical Artifact is empty",
            "issues": ["缺少可审查的完整成品"],
            "quality_contract": quality_contract_binding("reviewer"),
        }

    reviewer_prompt = "\n\n".join(
        (
            render_quality_contract(role="reviewer"),
            "【PEA 领域验收要求】",
            str(domain_requirements or "按用户目标判断成品是否完整、可用、可验收。"),
            "直接审查附加的完整 canonical Artifact。输出精简 Markdown：结论、证据锚点、"
            "blocking/non-blocking 问题、定向修复要求和验收测试。不要输出 JSON，不要复制整篇成品。",
        )
    )
    await before_external_io()
    review_artifact = await chat.complete(
        [
            {"role": "system", "content": reviewer_prompt},
            {"role": "user", "content": f"目标：{objective}\n\ncanonical Artifact：\n{body}"},
        ],
        temperature=0,
    )
    review_artifact = str(review_artifact or "").strip()
    if not review_artifact:
        return {
            "pass": False,
            "score": 0.0,
            "reason": "semantic quality review returned no Artifact",
            "issues": ["质量审查没有形成明确结论"],
            "quality_contract": quality_contract_binding("reviewer"),
        }

    projection_prompt = "\n\n".join(
        (
            render_quality_contract(role="projection"),
            "只把附加的 Markdown 审查 Artifact 投影成一个小型 JSON object，不重新审查、不新增问题。",
            '字段：{"pass":true|false,"score":0-1,"reason":"...","issues":["..."]}。',
        )
    )
    await before_external_io()
    raw_projection = await chat.complete(
        [
            {"role": "system", "content": projection_prompt},
            {"role": "user", "content": review_artifact},
        ],
        temperature=0,
    )
    projection = _first_object(raw_projection)
    if not isinstance(projection, dict) or not isinstance(projection.get("pass"), bool):
        return {
            "pass": False,
            "score": 0.0,
            "reason": "quality projection is invalid and can be retried independently",
            "issues": ["质量审查已保留，但机器投影暂未完成"],
            "review_artifact": review_artifact,
            "projection_status": "invalid",
            "quality_contract": quality_contract_binding("reviewer"),
        }
    try:
        score = float(projection.get("score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    if score > 1:
        score /= 100
    issues = projection.get("issues") if isinstance(projection.get("issues"), list) else []
    return {
        "pass": bool(projection["pass"]),
        "score": max(0.0, min(1.0, score)),
        "reason": str(projection.get("reason") or "").strip(),
        "issues": [str(item) for item in issues if str(item).strip()][:20],
        "review_artifact": review_artifact,
        "projection_status": "ready",
        "quality_contract": quality_contract_binding("reviewer"),
        "projection_contract": quality_contract_binding("projection"),
    }
