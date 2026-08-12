"""Artifact-first content-safety review shared by all PEA templates."""

from __future__ import annotations

import json
import re
from typing import Any


def _parse_safety_projection(raw: str) -> tuple[bool, str] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    # Compatibility with historical/sandbox providers; new prompts do not
    # require JSON and production correctness does not depend on it.
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(text[start : end + 1])
        except (TypeError, ValueError):
            value = None
        if isinstance(value, dict) and type(value.get("safe")) is bool:
            return value["safe"], str(value.get("reason") or "").strip()
    safe_match = re.fullmatch(
        r"\s*<safe>\s*(true|false)\s*</safe>\s*<reason>\s*(.*?)\s*</reason>\s*",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if safe_match:
        return safe_match.group(1).lower() == "true", safe_match.group(2).strip()
    return None


async def review_safety_artifact(chat: Any, text: str) -> tuple[bool, str]:
    """Review semantics first and project only the two control fields later."""

    if not str(text or "").strip():
        return True, ""
    try:
        semantic = await chat.complete(
            [
                {
                    "role": "system",
                    "content": (
                        "TASK:SAFETY 你是内容安全审核。直接审查附加文本是否涉及违法、侵权、"
                        "辱骂、政治敏感、色情等红线。输出简短自然语言审查 Artifact，说明结论、"
                        "证据和原因；不要输出 JSON。"
                    ),
                },
                {"role": "user", "content": text},
            ],
            temperature=0,
        )
        compatible = _parse_safety_projection(semantic)
        if compatible is not None:
            return compatible
        projected = await chat.complete(
            [
                {
                    "role": "system",
                    "content": (
                        "只把附加的安全审查 Artifact 投影为两个标签："
                        "<safe>true或false</safe><reason>原结论的简短原因</reason>。"
                        "不重新审核、不改变结论、不输出 JSON。"
                    ),
                },
                {"role": "user", "content": semantic},
            ],
            temperature=0,
        )
        parsed = _parse_safety_projection(projected)
        if parsed is not None:
            return parsed
    except Exception:
        pass
    # An enabled safety gate cannot silently turn Provider/format failure into
    # approval.  The caller may surface this as a manual-review requirement.
    return False, "内容安全审核暂不可用，已安全阻断并等待复核"
