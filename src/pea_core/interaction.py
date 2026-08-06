"""Unified task/interaction protocol shared by PEA adapters.

The module is deliberately domain-neutral: business services declare the interaction
metadata and the kernel only validates, serializes and resumes it. No UI or tool
name is encoded here.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

INTERACTION_ACTIONS = {"interaction", "needs_input", "needs_approval", "needs_payment", "needs_login"}
INTERACTION_TYPES = {
    "confirm", "choose", "provide_input", "review", "pay", "login", "publish",
    "human_handoff", "open_artifact", "cancel",
}
INTERACTION_STATUSES = {"pending", "accepted", "processing", "completed", "failed", "expired", "cancelled"}
TASK_STATUSES = {
    "created", "planning", "executing", "awaiting_input", "awaiting_approval",
    "awaiting_payment", "awaiting_login", "quality_review", "ready_for_delivery",
    "delivered", "completed", "blocked", "failed", "needs_reconciliation",
    "cancelled", "expired",
}


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def idempotency_key(*parts: Any) -> str:
    value = "|".join(str(item or "") for item in parts)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:64]


def now_ts() -> float:
    return time.time()


@dataclass(slots=True)
class GoalContract:
    goal: str
    audience: str | None = None
    purpose: str | None = None
    scope: list[str] = field(default_factory=list)
    non_goals: list[str] = field(default_factory=list)
    success_evidence: list[str] = field(default_factory=list)
    stop_conditions: list[str] = field(default_factory=list)
    output_contract: dict[str, Any] = field(default_factory=dict)
    quality_plan: dict[str, Any] = field(default_factory=dict)
    side_effect_class: str = "read_only"
    human_gates: list[str] = field(default_factory=list)
    budget: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class InteractionRequest:
    interaction_id: str
    run_id: str
    type: str
    title: str
    description: str = ""
    input_schema: dict[str, Any] | None = None
    options: list[dict[str, Any]] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    idempotency_key: str = ""
    expires_at: float | None = None
    security_scope: str = "run:resume"
    resume_prompt: str | None = None
    created_at: float = field(default_factory=now_ts)

    @classmethod
    def create(
        cls,
        run_id: str,
        interaction_type: str,
        title: str,
        description: str = "",
        *,
        input_schema: dict[str, Any] | None = None,
        options: list[dict[str, Any]] | None = None,
        payload: dict[str, Any] | None = None,
        expires_at: float | None = None,
        security_scope: str = "run:resume",
        resume_prompt: str | None = None,
        interaction_id: str | None = None,
        idem: str | None = None,
    ) -> "InteractionRequest":
        if interaction_type not in INTERACTION_TYPES:
            raise ValueError(f"unsupported interaction type: {interaction_type}")
        interaction_id = interaction_id or _id("ix")
        return cls(
            interaction_id=interaction_id,
            run_id=run_id,
            type=interaction_type,
            title=(title or "需要你的确认").strip(),
            description=(description or "").strip(),
            input_schema=input_schema,
            options=options or [],
            payload=payload or {},
            idempotency_key=idem or idempotency_key(run_id, interaction_id),
            expires_at=expires_at,
            security_scope=security_scope,
            resume_prompt=resume_prompt,
        )

    def is_expired(self, at: float | None = None) -> bool:
        return bool(self.expires_at and (at or now_ts()) >= self.expires_at)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["schema"] = "opc.interaction.v1"
        return data


@dataclass(slots=True)
class TaskEnvelope:
    task_id: str
    run_id: str
    turn_id: str
    status: str
    goal_contract: dict[str, Any]
    result_package: dict[str, Any] | None = None
    pending_interactions: list[dict[str, Any]] = field(default_factory=list)
    resume: dict[str, Any] = field(default_factory=dict)
    trace: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"schema": "opc.task_envelope.v1", **asdict(self)}


def interaction_from_payload(payload: Mapping[str, Any], run_id: str) -> InteractionRequest | None:
    """Build an interaction only from explicit declarative metadata.

    This is intentionally not a result-name heuristic. Tools/Agents add the
    '_interaction' extension; the kernel validates and serializes it.
    """
    raw = payload.get("_interaction") if isinstance(payload, Mapping) else None
    if not isinstance(raw, Mapping):
        return None
    interaction_type = str(raw.get("type") or "confirm")
    try:
        return InteractionRequest.create(
            run_id=run_id,
            interaction_type=interaction_type,
            title=str(raw.get("title") or "需要你的确认"),
            description=str(raw.get("description") or payload.get("message") or ""),
            input_schema=dict(raw["input_schema"]) if isinstance(raw.get("input_schema"), Mapping) else None,
            options=[dict(x) for x in raw.get("options", []) if isinstance(x, Mapping)],
            payload=dict(raw.get("payload") or {}),
            expires_at=float(raw["expires_at"]) if raw.get("expires_at") is not None else None,
            security_scope=str(raw.get("security_scope") or "run:resume"),
            resume_prompt=str(raw["resume_prompt"]) if raw.get("resume_prompt") else None,
            interaction_id=str(raw["interaction_id"]) if raw.get("interaction_id") else None,
            idem=str(raw["idempotency_key"]) if raw.get("idempotency_key") else None,
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def interaction_from_action(action: Mapping[str, Any], run_id: str) -> InteractionRequest | None:
    """Build an interaction from an LLM action envelope.

    The LLM may propose the interaction, but persistence and authorization still
    belong to the Harness/OPC layer.
    """
    if str(action.get("action") or "") not in INTERACTION_ACTIONS:
        return None
    raw = action.get("interaction") if isinstance(action.get("interaction"), Mapping) else action
    raw = dict(raw)
    action_name = str(action.get("action") or "")
    raw.setdefault("type", {
        "needs_input": "provide_input",
        "needs_approval": "confirm",
        "needs_payment": "pay",
        "needs_login": "login",
        "interaction": "confirm",
    }.get(action_name, "confirm"))
    raw.setdefault("title", action.get("text") or action.get("message") or "需要你的确认")
    payload = {"_interaction": raw}
    return interaction_from_payload(payload, run_id)
