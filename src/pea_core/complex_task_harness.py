"""PEA image-local mirror of the OPC complex-task contract.

PEA images intentionally contain only ``pea_core`` and therefore cannot
import the Agent image package.  This small implementation keeps the same
schema and state semantics as ``agents.complex_task_harness``: objective,
deduplicated context, Artifact hash, issue ledger, provider terminal state and
bounded fail-closed status.  The public fields are kept deliberately aligned
so PEA templates and A2A Agents exchange the same projection.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from .quality_contract import (
    QUALITY_CONTRACT_ID,
    QUALITY_CONTRACT_VERSION,
    quality_contract_binding,
    quality_contract_manifest,
)


COMPLEX_TASK_HARNESS_SCHEMA = "opc.complex_task_harness.v1"


def _text(value: Any, limit: int = 16_000) -> str:
    return str(value or "").strip()[:limit]


@dataclass(frozen=True)
class HarnessObjective:
    goal: str
    success_evidence: tuple[str, ...] = ()
    stop_conditions: tuple[str, ...] = ()
    side_effect_class: str = "read_only"
    max_rounds: int = 3
    context_budget_chars: int = 240_000
    quality_contract_id: str = QUALITY_CONTRACT_ID
    quality_contract_version: str = ""
    quality_contract_fingerprint: str = ""

    def normalized(self) -> "HarnessObjective":
        manifest = quality_contract_manifest()
        requested_id = _text(self.quality_contract_id, 160) or QUALITY_CONTRACT_ID
        requested_version = _text(self.quality_contract_version, 160)
        requested_fingerprint = _text(self.quality_contract_fingerprint, 128)
        if requested_id != QUALITY_CONTRACT_ID:
            raise ValueError(f"unsupported PEA quality contract: {requested_id!r}")
        if requested_version and requested_version != QUALITY_CONTRACT_VERSION:
            raise ValueError("PEA quality contract version mismatch")
        if requested_fingerprint and requested_fingerprint != manifest["contract_fingerprint"]:
            raise ValueError("PEA quality contract fingerprint mismatch")
        return HarnessObjective(
            goal=_text(self.goal, 8_000),
            success_evidence=tuple(_text(item, 2_000) for item in self.success_evidence if _text(item, 2_000)),
            stop_conditions=tuple(_text(item, 2_000) for item in self.stop_conditions if _text(item, 2_000)),
            side_effect_class=_text(self.side_effect_class, 80).lower() or "read_only",
            max_rounds=max(1, min(8, int(self.max_rounds or 3))),
            context_budget_chars=max(8_000, min(1_000_000, int(self.context_budget_chars or 240_000))),
            quality_contract_id=QUALITY_CONTRACT_ID,
            quality_contract_version=QUALITY_CONTRACT_VERSION,
            quality_contract_fingerprint=str(manifest["contract_fingerprint"]),
        )


@dataclass
class HarnessIssue:
    issue_id: str
    fingerprint: str
    category: str
    severity: str
    message: str
    first_seen_round: int = 0
    last_seen_round: int = 0
    status: str = "open"

    def projection(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _Context:
    source_id: str
    text: str
    kind: str
    priority: int
    authoritative: bool
    created_at: float = field(default_factory=time.time)


@dataclass
class _Checkpoint:
    run_id: str
    objective: HarnessObjective
    status: str = "running"
    round_index: int = 0
    current_artifact: dict[str, Any] | None = None
    issue_ledger: list[HarnessIssue] = field(default_factory=list)
    context_entries: list[_Context] = field(default_factory=list)
    compacted_context_count: int = 0
    pending_action: str | None = None
    provider_status: str = "not_started"
    provider_attempts: int = 0
    terminal_event_seen: bool = False
    last_error: str = ""
    updated_at: float = field(default_factory=time.time)


class ComplexTaskSession:
    def __init__(self, objective: HarnessObjective, *, run_id: str | None = None, checkpoint_store: Any = None) -> None:
        self.checkpoint = _Checkpoint(run_id or f"pea_harness_{uuid.uuid4().hex}", objective.normalized())
        self._checkpoint_store = checkpoint_store
        self._seen: set[str] = set()

    def add_context(self, source_id: str, text: str, *, kind: str = "fact", priority: int = 50, authoritative: bool = False) -> bool:
        body = _text(text, 80_000)
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if not body or digest in self._seen:
            return False
        self._seen.add(digest)
        self.checkpoint.context_entries.append(_Context(_text(source_id, 160), body, _text(kind, 80), int(priority), bool(authoritative)))
        self._compact()
        return True

    def _compact(self) -> None:
        entries = self.checkpoint.context_entries
        if sum(len(entry.text) for entry in entries) <= self.checkpoint.objective.context_budget_chars:
            return
        ranked = sorted(entries, key=lambda item: (item.authoritative, item.kind in {"objective", "artifact_ref", "issue"}, item.priority, item.created_at), reverse=True)
        kept: list[_Context] = []
        used = 0
        for entry in ranked:
            if kept and used + len(entry.text) > self.checkpoint.objective.context_budget_chars:
                continue
            kept.append(entry)
            used += len(entry.text)
        self.checkpoint.compacted_context_count += len(entries) - len(kept)
        self.checkpoint.context_entries = sorted(kept, key=lambda item: item.created_at)

    def record_artifact(self, content: str, *, artifact_type: str = "pea_artifact", source: str = "pea") -> dict[str, Any]:
        body = str(content or "")
        ref = {
            "artifact_id": f"artifact_{uuid.uuid4().hex}",
            "artifact_type": _text(artifact_type, 120),
            "content_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "source": _text(source, 160),
            "round_index": self.checkpoint.round_index,
            "quality_contract": quality_contract_binding("generator"),
        }
        self.checkpoint.current_artifact = ref
        self.add_context(ref["artifact_id"], f"artifact_ref={ref['artifact_id']} sha256={ref['content_sha256']}", kind="artifact_ref", priority=100, authoritative=True)
        return ref

    def record_review(self, review: Mapping[str, Any] | None) -> list[HarnessIssue]:
        """Record a domain quality tool result in the shared issue ledger."""

        if not isinstance(review, Mapping):
            return []
        binding = review.get("quality_contract")
        if isinstance(binding, Mapping):
            expected = quality_contract_binding("reviewer")
            actual_tuple = (
                binding.get("contract_id"),
                binding.get("contract_version"),
                binding.get("contract_fingerprint"),
            )
            expected_tuple = (
                expected.get("contract_id"),
                expected.get("contract_version"),
                expected.get("contract_fingerprint"),
            )
            if actual_tuple != expected_tuple:
                raise ValueError("PEA reviewer quality contract mismatch")
        raw_issues = review.get("issues") if isinstance(review.get("issues"), list) else []
        if review.get("pass") is False and not raw_issues:
            raw_issues = [review.get("reason") or "PEA quality gate did not pass"]
        current: list[HarnessIssue] = []
        current_fingerprints: set[str] = set()
        for raw in raw_issues:
            message = _text(
                raw.get("message") or raw.get("issue") or raw.get("description")
                if isinstance(raw, Mapping)
                else raw,
                2_000,
            )
            if not message:
                continue
            fingerprint = hashlib.sha256(message.lower().encode("utf-8")).hexdigest()[:24]
            current_fingerprints.add(fingerprint)
            existing = next(
                (item for item in self.checkpoint.issue_ledger if item.fingerprint == fingerprint),
                None,
            )
            if existing is None:
                existing = HarnessIssue(
                    issue_id=f"issue_{fingerprint}",
                    fingerprint=fingerprint,
                    category="quality",
                    severity="blocker" if review.get("pass") is False else "warning",
                    message=message,
                    first_seen_round=self.checkpoint.round_index,
                    last_seen_round=self.checkpoint.round_index,
                )
                self.checkpoint.issue_ledger.append(existing)
            else:
                existing.last_seen_round = self.checkpoint.round_index
                existing.status = "open"
            current.append(existing)
        for issue in self.checkpoint.issue_ledger:
            if issue.status == "open" and issue.fingerprint not in current_fingerprints:
                issue.status = "resolved"
        if current:
            self.add_context(
                f"quality-review-{self.checkpoint.round_index}",
                "; ".join(issue.message for issue in current),
                kind="issue",
                priority=100,
                authoritative=True,
            )
        return current

    def open_issues(self) -> list[HarnessIssue]:
        return [item for item in self.checkpoint.issue_ledger if item.status == "open"]

    def quality_blocked(self, reason: str = "open quality issues remain") -> None:
        self.checkpoint.status = "quality_blocked"
        self.checkpoint.pending_action = "remediate_open_issues"
        self.checkpoint.last_error = _text(reason, 2_000)

    def begin_round(self, *, reason: str = "continue") -> None:
        self.checkpoint.round_index += 1
        self.checkpoint.pending_action = _text(reason, 1_000)

    def mark_provider(self, status: str, *, terminal: bool = False) -> None:
        self.checkpoint.provider_status = _text(status, 80).lower() or "unknown"
        self.checkpoint.provider_attempts += 1
        self.checkpoint.terminal_event_seen = bool(terminal)

    def fail_closed(self, reason: str, *, provider_status: str = "unknown") -> None:
        self.checkpoint.status = "needs_reconciliation" if provider_status in {"unknown", "transport_unknown", "response_stream_waiting"} else "failed"
        self.checkpoint.last_error = _text(reason, 2_000)
        self.checkpoint.provider_status = provider_status
        self.checkpoint.terminal_event_seen = False

    def complete(self) -> None:
        self.checkpoint.status = "completed"
        self.checkpoint.pending_action = None

    def to_projection(self) -> dict[str, Any]:
        return {
            "schema": COMPLEX_TASK_HARNESS_SCHEMA,
            "run_id": self.checkpoint.run_id,
            "status": self.checkpoint.status,
            "round_index": self.checkpoint.round_index,
            "objective": asdict(self.checkpoint.objective),
            "current_artifact": self.checkpoint.current_artifact,
            "issue_ledger": [item.projection() for item in self.checkpoint.issue_ledger],
            "context": {
                "entry_count": len(self.checkpoint.context_entries),
                "compacted_count": self.checkpoint.compacted_context_count,
                "source_ids": [item.source_id for item in self.checkpoint.context_entries[-32:]],
            },
            "pending_action": self.checkpoint.pending_action,
            "provider": {
                "status": self.checkpoint.provider_status,
                "attempts": self.checkpoint.provider_attempts,
                "terminal_event_seen": self.checkpoint.terminal_event_seen,
            },
            "last_error": self.checkpoint.last_error,
            "updated_at": self.checkpoint.updated_at,
        }
