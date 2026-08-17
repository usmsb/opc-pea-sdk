"""Artifact-first semantic quality review for standalone PEA services.

PEA images do not ship the OPC backend, so this helper uses the image-local
mirror of the shared complex-task contract.  It deliberately performs two
provider calls: a Markdown semantic review over the complete Artifact, then a
small JSON projection over that review only.  Projection failure fails closed
without asking the reviewer to repeat the expensive semantic work.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
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


_ISSUE_TERMINAL_STATUSES = frozenset({"resolved", "not_applicable"})
_ISSUE_OPEN_STATUSES = frozenset(
    {
        "open",
        "advisory",
        "pending_execution",
        "pending_human_confirmation",
    }
)
_ISSUE_STATUSES = _ISSUE_TERMINAL_STATUSES | _ISSUE_OPEN_STATUSES


def _text(value: Any, limit: int = 2_000) -> str:
    return str(value or "").strip()[:limit]


def _evidence(value: Any) -> list[str]:
    source = value if isinstance(value, (list, tuple)) else [value]
    return list(
        dict.fromkeys(
            item
            for item in (_text(raw, 1_000) for raw in source)
            if item
        )
    )[:8]


def _bounded_previous_ledger(
    rows: Iterable[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    bounded: list[dict[str, Any]] = []
    for raw in rows or ():
        if not isinstance(raw, Mapping):
            continue
        issue_id = _text(raw.get("issue_id") or raw.get("id"), 160)
        status = _text(raw.get("status"), 80).lower() or "open"
        if not issue_id or status in _ISSUE_TERMINAL_STATUSES:
            continue
        bounded.append(
            {
                "issue_id": issue_id,
                "criterion_id": _text(raw.get("criterion_id"), 160),
                "status": status,
                "message": _text(
                    raw.get("message")
                    or raw.get("issue")
                    or raw.get("description"),
                    1_000,
                ),
                "evidence": _evidence(raw.get("evidence")),
            }
        )
        if len(bounded) >= 32:
            break
    return bounded


def _normalize_projected_issues(
    raw_issues: Any,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate a semantic issue ledger without interpreting its prose.

    Stable identities are part of the Harness protocol.  The projection LLM
    may copy them from the Markdown review, but it may not mint a replacement
    ID merely because its wording changed.  Existing issues must be carried
    forward explicitly until the reviewer closes the same identity with
    evidence.  This keeps convergence evidence stable while leaving the
    semantic judgment entirely with the reviewer.
    """

    if not isinstance(raw_issues, list):
        return [], ["issues must be an array"]
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    errors: list[str] = []
    for index, raw in enumerate(raw_issues[:40]):
        if not isinstance(raw, Mapping):
            errors.append(f"issues[{index}] must be an object with stable identity")
            continue
        issue_id = _text(raw.get("issue_id") or raw.get("id"), 160)
        criterion_id = _text(raw.get("criterion_id"), 160)
        status = _text(raw.get("status"), 80).lower() or "open"
        message = _text(
            raw.get("message") or raw.get("issue") or raw.get("description"),
            2_000,
        )
        evidence = _evidence(raw.get("evidence"))
        if not issue_id or not criterion_id:
            errors.append(
                f"issues[{index}] must preserve issue_id and criterion_id"
            )
            continue
        if issue_id in seen_ids:
            errors.append(f"duplicate issue_id: {issue_id}")
            continue
        if status not in _ISSUE_STATUSES:
            errors.append(f"{issue_id}: unsupported status {status!r}")
            continue
        if not message:
            errors.append(f"{issue_id}: message is required")
            continue
        if status in _ISSUE_TERMINAL_STATUSES and not evidence:
            errors.append(
                f"{issue_id}: a resolved/not_applicable issue needs explicit evidence"
            )
            continue
        seen_ids.add(issue_id)
        normalized.append(
            {
                "issue_id": issue_id,
                "criterion_id": criterion_id,
                "status": status,
                "severity": _text(raw.get("severity"), 80).lower()
                or ("advisory" if status == "advisory" else "blocker"),
                "message": message,
                "evidence": evidence,
                "required_change": _text(raw.get("required_change"), 2_000),
                "acceptance_test": _text(raw.get("acceptance_test"), 2_000),
            }
        )
    # Do not infer resolution from omission.  The durable session keeps an
    # omitted prior issue open.  Requiring every compact projection to repeat
    # all old rows would turn a harmless omission into a protocol outage; only
    # an explicit same-ID closure with evidence is allowed to change state.
    return normalized, errors


def _control_issue(
    *,
    issue_id: str,
    criterion_id: str,
    message: str,
    evidence: str,
) -> dict[str, Any]:
    return {
        "issue_id": issue_id,
        "criterion_id": criterion_id,
        "status": "open",
        "severity": "blocker",
        "message": message,
        "evidence": [evidence],
        "required_change": "恢复该阶段并重新执行同一无副作用审查/投影事务。",
        "acceptance_test": "该阶段形成可追踪的完整结果且不丢失既有问题身份。",
    }


async def review_complex_artifact(
    *,
    chat: Any,
    before_external_io: Callable[[], Awaitable[None]],
    artifact: str,
    objective: str,
    domain_requirements: str,
    previous_issue_ledger: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    body = str(artifact or "").strip()
    previous_open = _bounded_previous_ledger(previous_issue_ledger)
    if not body:
        return {
            "pass": False,
            "can_submit": False,
            "score": 0.0,
            "reason": "canonical Artifact is empty",
            "issues": [
                _control_issue(
                    issue_id="issue:artifact:canonical-empty",
                    criterion_id="artifact",
                    message="缺少可审查的完整 canonical Artifact",
                    evidence="canonical Artifact length=0",
                )
            ],
            "quality_contract": quality_contract_binding("reviewer"),
        }

    reviewer_prompt = "\n\n".join(
        (
            render_quality_contract(role="reviewer"),
            "【PEA 领域验收要求】",
            str(domain_requirements or "按用户目标判断成品是否完整、可用、可验收。"),
            "直接审查附加的完整 canonical Artifact。输出精简 Markdown：结论、证据锚点、"
            "blocking/non-blocking 问题、定向修复要求和验收测试。不要输出 JSON，不要复制整篇成品。",
            "每个问题必须逐行写出稳定的 issue_id、criterion_id、status、severity、"
            "evidence、required_change、acceptance_test。criterion_id 应对应上方统一义务"
            "或领域合同中的稳定验收项；同一语义问题跨轮次必须复用同一 issue_id。",
            "如果下方存在既有 open issue ledger，必须逐项沿用原 issue_id：仍有问题就保持"
            "open；确已修复或不适用时写 resolved/not_applicable 并给出当前 Artifact 的证据。"
            "不得靠省略旧问题宣称通过，也不得因为换一种措辞就生成新身份。",
            "【既有 open issue ledger（仅控制证据，不是待复制正文）】\n"
            + json.dumps(previous_open, ensure_ascii=False, separators=(",", ":")),
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
            "can_submit": False,
            "score": 0.0,
            "reason": "semantic quality review returned no Artifact",
            "issues": [
                _control_issue(
                    issue_id="issue:convergence:semantic-review-empty",
                    criterion_id="convergence",
                    message="质量审查没有形成明确结论",
                    evidence="semantic review response length=0",
                )
            ],
            "quality_contract": quality_contract_binding("reviewer"),
        }

    projection_prompt = "\n\n".join(
        (
            render_quality_contract(role="projection"),
            "只把附加的 Markdown 审查 Artifact 投影成一个小型 JSON object，不重新审查、不新增问题。",
            "必须逐字保留 Markdown 中的 issue_id、criterion_id 和证据，不得根据措辞另造身份。",
            "既有 open issue 不得省略；只有 Markdown 明确给出同一 ID 的关闭证据才可标为 resolved。",
            '字段：{"pass":true|false,"score":0-1,"reason":"...","issues":['
            '{"issue_id":"...","criterion_id":"...","status":"open|resolved|not_applicable|advisory|pending_execution|pending_human_confirmation",'
            '"severity":"...","message":"...","evidence":["..."],"required_change":"...","acceptance_test":"..."}]}。',
            "只输出一个 JSON object。",
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
            "can_submit": False,
            "score": 0.0,
            "reason": "quality projection is invalid and can be retried independently",
            "issues": [
                _control_issue(
                    issue_id="issue:convergence:review-projection-invalid",
                    criterion_id="convergence",
                    message="质量审查已保留，但机器投影暂未完成",
                    evidence="projection did not contain a valid pass boolean",
                )
            ],
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
    issues, issue_errors = _normalize_projected_issues(
        projection.get("issues"),
    )
    blocking_statuses = {"open", "pending_human_confirmation"}
    projected_pass = bool(projection["pass"])
    if projected_pass and any(
        item["status"] in blocking_statuses
        and item["severity"] not in {"advisory", "info", "warning"}
        for item in issues
    ):
        issue_errors.append("pass=true conflicts with an open blocking issue")
    if not projected_pass and not any(
        item["status"] in blocking_statuses for item in issues
    ):
        issue_errors.append("pass=false requires at least one open evidenced issue")
    if issue_errors:
        return {
            "pass": False,
            "can_submit": False,
            "score": 0.0,
            "reason": "quality issue projection violated the stable-ledger contract",
            "issues": [
                _control_issue(
                    issue_id="issue:convergence:issue-ledger-invalid",
                    criterion_id="convergence",
                    message="质量问题投影没有保持稳定身份或完整状态",
                    evidence="; ".join(issue_errors[:8]),
                )
            ],
            "review_artifact": review_artifact,
            "projection_status": "invalid",
            "projection_errors": issue_errors,
            "quality_contract": quality_contract_binding("reviewer"),
        }
    return {
        "pass": projected_pass,
        "can_submit": projected_pass,
        "score": max(0.0, min(1.0, score)),
        "reason": str(projection.get("reason") or "").strip(),
        "issues": issues[:32],
        "review_artifact": review_artifact,
        "projection_status": "ready",
        "quality_contract": quality_contract_binding("reviewer"),
        "projection_contract": quality_contract_binding("projection"),
    }
