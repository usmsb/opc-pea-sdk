"""PEA access to the generic OPC complex-task quality contract.

Production PEA images include ``llm_contracts`` and use it as their only
contract source.  The compact local definition below is a source-only fallback
for legacy development images; it must never become a second production policy
surface.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

try:
    # Production PEA images now carry the same ``llm_contracts`` package as
    # the Agent runtime.  Prefer that registry so generator, reviewer, patch
    # and E2E roles cannot silently drift by maintaining two prompt manifests.
    from llm_contracts.quality import (
        build_stage_quality_prompt as _canonical_build_stage_quality_prompt,
        get_quality_contract as _canonical_get_quality_contract,
        quality_contract_binding as _canonical_quality_contract_binding,
    )
except ModuleNotFoundError:  # pragma: no cover - legacy/source-only fallback
    _canonical_build_stage_quality_prompt = None
    _canonical_get_quality_contract = None
    _canonical_quality_contract_binding = None


QUALITY_CONTRACT_SCHEMA = "opc.quality_contract_manifest.v1"
QUALITY_BINDING_SCHEMA = "opc.quality_contract_binding.v1"
QUALITY_CONTRACT_ID = "complex_task_artifact"
QUALITY_CONTRACT_VERSION = "opc.quality_contract.complex_task_artifact.v1"

QUALITY_OBLIGATIONS: tuple[dict[str, str], ...] = (
    {
        "key": "objective",
        "label": "目标对齐",
        "description": "成果直接回答目标并保留硬约束和停止条件。",
    },
    {
        "key": "artifact",
        "label": "Artifact完整",
        "description": "完整原始成果是事实源，JSON投影仅用于控制和索引。",
    },
    {
        "key": "evidence",
        "label": "证据充分",
        "description": "关键判断有可访问来源、文件、数据或执行回执。",
    },
    {
        "key": "usability",
        "label": "可直接使用",
        "description": "交付不是占位符、提示词、过程状态或不可访问路径。",
    },
    {
        "key": "safety",
        "label": "授权与副作用",
        "description": "人类批准、付费、发布、隐私和不可逆动作边界明确。",
    },
    {
        "key": "convergence",
        "label": "收敛与停止",
        "description": "问题账本稳定，定向修复有预算，无进展时停止而非盲重试。",
    },
)


def quality_contract_manifest() -> dict[str, Any]:
    if _canonical_get_quality_contract is not None:
        return _canonical_get_quality_contract(QUALITY_CONTRACT_ID).manifest()
    payload: dict[str, Any] = {
        "schema": QUALITY_CONTRACT_SCHEMA,
        "contract_id": QUALITY_CONTRACT_ID,
        "contract_version": QUALITY_CONTRACT_VERSION,
        "stage": "complex_task",
        "artifact_kind": "generic_artifact",
        "minimum_score": 85,
        "obligations": [dict(item) for item in QUALITY_OBLIGATIONS],
        "stage_semantics": "以任务目标和成功证据验收复杂任务 Artifact；具体领域要求由调用方合同扩展。",
        "future_evidence_policy": "未来结果不得冒充当前事实；当前成果必须定义责任、触发、证据、验收与停止条件。",
        "repair_scope": "targeted_patch",
        "canonical_artifact_required": True,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        **payload,
        "contract_fingerprint": hashlib.sha256(encoded).hexdigest(),
    }


def quality_contract_binding(role: str) -> dict[str, str]:
    normalized_role = str(role or "").strip().lower()
    if normalized_role not in {"generator", "reviewer", "projection", "patch", "controller"}:
        raise ValueError(f"unsupported PEA quality contract role: {role!r}")
    if _canonical_quality_contract_binding is not None:
        return _canonical_quality_contract_binding(QUALITY_CONTRACT_ID, normalized_role)
    manifest = quality_contract_manifest()
    return {
        "schema": QUALITY_BINDING_SCHEMA,
        "contract_id": QUALITY_CONTRACT_ID,
        "contract_version": QUALITY_CONTRACT_VERSION,
        "contract_fingerprint": str(manifest["contract_fingerprint"]),
        "stage": "complex_task",
        "role": normalized_role,
    }


def render_quality_contract(*, role: str = "generator") -> str:
    if _canonical_build_stage_quality_prompt is not None:
        return _canonical_build_stage_quality_prompt(QUALITY_CONTRACT_ID, role)
    binding = quality_contract_binding(role)
    manifest = quality_contract_manifest()
    obligations = "\n".join(
        f"- {item['key']}: {item['label']}；{item['description']}"
        for item in QUALITY_OBLIGATIONS
    )
    role_text = {
        "generator": "【生成器职责】产出完整 canonical Artifact；逐项自检统一验收义务；正文不放进JSON。",
        "reviewer": "【详细审查器职责】直接审查完整 canonical Artifact；输出Markdown问题账本和证据，不输出JSON。",
        "projection": "【结构化投影职责】只把已有Artifact或审查结论提取为小型JSON；不重新审查、不新增问题。",
        "patch": "【定向 Patch 职责】只修复问题账本授权的目标；保留未授权内容，不输出整篇Artifact。",
        "controller": "【质量控制器职责】只选择问题与修订目标；不生成正文，无进展时停止。",
    }[binding["role"]]
    return "\n".join(
        (
            "【OPC 版本化质量合同】",
            f"contract_id={QUALITY_CONTRACT_ID}",
            f"contract_version={QUALITY_CONTRACT_VERSION}",
            f"contract_fingerprint={manifest['contract_fingerprint']}",
            f"stage_semantics={manifest['stage_semantics']}",
            "canonical_artifact=required",
            f"minimum_score={manifest['minimum_score']}",
            f"repair_scope={manifest['repair_scope']}",
            f"future_evidence_policy={manifest['future_evidence_policy']}",
            "【统一验收义务】",
            obligations,
            role_text,
        )
    )
