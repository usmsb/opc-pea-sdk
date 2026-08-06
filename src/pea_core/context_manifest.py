"""Reproducible, task-aware context manifest for PEA/Agent calls.

The manifest describes what was selected; it is not a second source of truth.
Artifact bodies stay in artifact storage and are referenced by hash/id.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable


@dataclass(slots=True)
class ContextSource:
    source_id: str
    kind: str
    authority: str
    content_hash: str
    token_estimate: int
    required: bool
    include_mode: str = "full"
    freshness: str | None = None
    provenance: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ContextManifest:
    schema: str = "opc.context_manifest.v1"
    budget_tokens: int = 0
    used_tokens: int = 0
    sources: list[ContextSource] = field(default_factory=list)
    dropped_optional: list[str] = field(default_factory=list)
    blocked_reason: str | None = None

    @property
    def required_source_ids(self) -> list[str]:
        return [item.source_id for item in self.sources if item.required]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "budget_tokens": self.budget_tokens,
            "used_tokens": self.used_tokens,
            "sources": [item.to_dict() for item in self.sources],
            "dropped_optional": self.dropped_optional,
            "blocked_reason": self.blocked_reason,
        }


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _estimate(value: str) -> int:
    return max(1, len(value or "") // 4)


def build_manifest(
    *,
    goal: str,
    summary: str,
    recent: Iterable[dict[str, Any]],
    recalls: Iterable[dict[str, Any]],
    state: dict[str, Any],
    budget_tokens: int = 6000,
) -> ContextManifest:
    """Select required objective/state/recent context before optional recall.

    Selection is deterministic by source lineage and hash. Required sources are
    never dropped; optional recall is clipped or dropped when the budget is low.
    """
    manifest = ContextManifest(budget_tokens=budget_tokens)
    candidates: list[tuple[str, str, str, bool, str, str | None]] = [
        ("objective", goal, "goal_contract", True, "full", "user"),
        ("state", str(state or {}), "run_state", True, "full", "system"),
    ]
    if summary:
        candidates.append(("summary", summary, "conversation_summary", True, "summary", "harness"))
    recent_text = "\n".join(str(row.get("content") or "") for row in recent)
    if recent_text:
        candidates.append(("recent_turns", recent_text, "conversation", True, "full", "user"))
    for index, item in enumerate(recalls):
        text = str(item.get("text") or "")
        if text:
            candidates.append((
                f"recall:{item.get('ref') or index}",
                text,
                str(item.get("kind") or "memory"),
                False,
                "excerpt",
                "vector_memory",
            ))

    seen: set[str] = set()
    for source_id, text, authority, required, mode, provenance in candidates:
        digest = _hash(text)
        lineage = f"{source_id}:{digest}"
        if lineage in seen:
            continue
        seen.add(lineage)
        estimate = _estimate(text)
        if not required and manifest.used_tokens + estimate > budget_tokens:
            manifest.dropped_optional.append(source_id)
            continue
        if not required and estimate > max(256, budget_tokens // 3):
            text = text[:max(1024, budget_tokens * 2)]
            estimate = _estimate(text)
            mode = "excerpt"
        manifest.sources.append(ContextSource(
            source_id=source_id,
            kind=authority,
            authority=authority,
            content_hash=digest,
            token_estimate=estimate,
            required=required,
            include_mode=mode,
            provenance=provenance,
        ))
        manifest.used_tokens += estimate

    if manifest.used_tokens > budget_tokens and any(item.required for item in manifest.sources):
        manifest.blocked_reason = "required_context_exceeds_budget"
    return manifest
