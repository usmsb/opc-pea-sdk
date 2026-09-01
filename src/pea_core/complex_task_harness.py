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
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from copy import deepcopy
from typing import Any, Iterable, Mapping

from .quality_contract import (
    QUALITY_CONTRACT_ID,
    QUALITY_CONTRACT_VERSION,
    quality_contract_binding,
    quality_contract_manifest,
)


COMPLEX_TASK_HARNESS_SCHEMA = "opc.complex_task_harness.v1"
_HARNESS_TERMINAL_STATES = frozenset(
    {
        "accepted",
        "needs_human_input",
        "semantic_stalled",
        "budget_exhausted",
        "provider_unknown",
        "failed",
    }
)
_HARNESS_TRANSIENT_PROVIDER_KINDS = frozenset(
    {
        "rate_limited",
        "provider_unavailable",
        "gateway_unavailable",
        "network_unavailable",
        "timeout_before_provider_acceptance",
    }
)
_HARNESS_UNKNOWN_PROVIDER_KINDS = frozenset(
    {
        "provider_unknown",
        "transport_unknown",
        "response_stream_waiting",
    }
)

HARNESS_FAILURE_CLASSES = frozenset(
    {
        "transport_failure",
        "provider_unknown",
        "provider_incomplete",
        "syntax_invalid",
        "schema_invalid",
        "output_budget_insufficient",
        "context_budget_exceeded",
        "semantic_contract_failed",
        "artifact_regression",
        "unknown_output_failure",
    }
)

HARNESS_RECOVERY_ACTIONS = frozenset(
    {
        "syntax_repair",
        "schema_projection_or_replan",
        "shrink_output_transaction",
        "shard_context",
        "diagnose_and_patch_local_scope",
        "retry_side_effect_free_call",
        "reconcile_existing_call",
        "llm_diagnose_and_select_recovery",
        "human_handoff",
    }
)


def _text(value: Any, limit: int = 16_000) -> str:
    return str(value or "").strip()[:limit]


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def classify_harness_output_failure(value: Any) -> str:
    """Stable provider-neutral failure vocabulary for slim PEA images."""

    typed = str(
        (value.get("failure_class") if isinstance(value, Mapping) else None)
        or getattr(value, "failure_class", None)
        or getattr(value, "classification", None)
        or ""
    ).strip().lower()
    if typed in HARNESS_FAILURE_CLASSES:
        return typed
    text = json.dumps(dict(value), ensure_ascii=False, default=str) if isinstance(value, Mapping) else f"{type(value).__name__}: {value}"
    normalized = text.lower()
    if any(token in normalized for token in ("jsondecode", "invalid json", "malformed json")):
        return "syntax_invalid"
    if any(token in normalized for token in ("schema_invalid", "schema validation", "invalid enum")):
        return "schema_invalid"
    if any(token in normalized for token in ("max_output", "output limit", "incomplete_details")):
        return "output_budget_insufficient"
    if any(token in normalized for token in ("provider_incomplete", "response.incomplete")):
        return "provider_incomplete"
    if any(token in normalized for token in ("context budget", "context_window", "max_input")):
        return "context_budget_exceeded"
    if any(token in normalized for token in _HARNESS_UNKNOWN_PROVIDER_KINDS):
        return "provider_unknown"
    if any(token in normalized for token in _HARNESS_TRANSIENT_PROVIDER_KINDS):
        return "transport_failure"
    if any(token in normalized for token in ("quality gate", "semantic", "regression")):
        return "semantic_contract_failed"
    return "unknown_output_failure"


def recovery_action_for_failure(failure_class: str) -> str:
    return {
        "syntax_invalid": "syntax_repair",
        "schema_invalid": "schema_projection_or_replan",
        "output_budget_insufficient": "shrink_output_transaction",
        "provider_incomplete": "shrink_output_transaction",
        "context_budget_exceeded": "shard_context",
        "semantic_contract_failed": "diagnose_and_patch_local_scope",
        "artifact_regression": "diagnose_and_patch_local_scope",
        "transport_failure": "retry_side_effect_free_call",
        "provider_unknown": "reconcile_existing_call",
        "unknown_output_failure": "llm_diagnose_and_select_recovery",
    }.get(_text(failure_class, 120).lower(), "llm_diagnose_and_select_recovery")


@dataclass(frozen=True)
class HarnessFailureObservation:
    phase: str
    failure_class: str
    recovery_action: str
    error: str = ""
    raw_output_sha256: str = ""
    raw_output_preview: str = ""
    raw_output_chars: int = 0
    provider_response_id: str = ""
    gateway_request_id: str = ""
    attempt: int = 0

    @classmethod
    def from_failure(
        cls,
        failure: Any,
        *,
        phase: str,
        raw_output: Any = None,
        attempt: int = 0,
    ) -> "HarnessFailureObservation":
        raw = str(raw_output or "")
        failure_class = classify_harness_output_failure(failure)
        return cls(
            phase=_text(phase, 160) or "unknown",
            failure_class=failure_class,
            recovery_action=recovery_action_for_failure(failure_class),
            error=str(failure or "")[:4_000],
            raw_output_sha256=_sha256_text(raw) if raw else "",
            raw_output_preview=raw[:4_000],
            raw_output_chars=len(raw),
            provider_response_id=str(getattr(failure, "response_id", "") or "").strip(),
            gateway_request_id=str(getattr(failure, "gateway_request_id", "") or "").strip(),
            attempt=max(0, int(attempt or 0)),
        )

    def projection(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HarnessLoopBudget:
    """Image-local mirror of the common finite convergence budget."""

    max_semantic_candidates: int = 8
    max_replans: int = 3
    max_local_transactions: int = 24
    max_protocol_retries: int = 2
    max_provider_calls: int = 64
    stagnant_observations_before_replan: int = 2

    def normalized(self) -> "HarnessLoopBudget":
        return HarnessLoopBudget(
            max_semantic_candidates=max(
                1, min(32, int(self.max_semantic_candidates or 8))
            ),
            max_replans=max(0, min(8, int(self.max_replans or 0))),
            max_local_transactions=max(
                1, min(128, int(self.max_local_transactions or 24))
            ),
            max_protocol_retries=max(
                0, min(12, int(self.max_protocol_retries or 0))
            ),
            max_provider_calls=max(
                1, min(512, int(self.max_provider_calls or 64))
            ),
            stagnant_observations_before_replan=max(
                1,
                min(
                    4,
                    int(self.stagnant_observations_before_replan or 2),
                ),
            ),
        )


@dataclass(frozen=True)
class HarnessLoopDecision:
    action: str
    reason: str
    terminal: bool = False
    material_progress: bool = False


class HarnessConvergenceController:
    """Finite evidence controller available inside every slim PEA image.

    PEA domain code still lets an LLM diagnose and plan semantic repairs. This
    mirror owns only budgets, stable evidence identities and absorbing terminal
    states, so a long Artifact task cannot rely on the outer 14-step chat loop
    as an accidental convergence policy.
    """

    def __init__(self, budget: HarnessLoopBudget | None = None) -> None:
        self.budget = (budget or HarnessLoopBudget()).normalized()
        self.semantic_candidates_used = 0
        self.replans_used = 0
        self.local_transactions_used = 0
        self.protocol_retries_used = 0
        self.provider_calls_used = 0
        self.stagnant_observations = 0
        self.state = "running"
        self.reason = ""
        self._strategy_fingerprints: set[str] = set()
        self._replacement_authorized = False
        self._progress_advance_authorized = False
        self._stagnation_counts: dict[str, int] = {}
        self._stagnation_identity: dict[str, dict[str, Any]] = {}
        self._last_failure_signature = ""
        self.history: list[dict[str, Any]] = []

    @property
    def terminal(self) -> bool:
        return self.state in _HARNESS_TERMINAL_STATES

    def terminate(
        self,
        state: str,
        reason: str,
        *,
        diagnostic: Any = None,
    ) -> HarnessLoopDecision:
        if state not in _HARNESS_TERMINAL_STATES:
            raise ValueError(f"unsupported Harness terminal state: {state}")
        if self.terminal:
            return self._terminal_noop()
        return self._finish(state, reason, diagnostic=diagnostic)

    def has_strategy(self, fingerprint: str) -> bool:
        token = _text(fingerprint, 128)
        return bool(token and token in self._strategy_fingerprints)

    def register_strategy(self, fingerprint: str) -> HarnessLoopDecision:
        if self.terminal:
            return self._terminal_noop()
        if self._replacement_authorized or self._progress_advance_authorized:
            return self._finish(
                "semantic_stalled",
                "a pending strategy transition must use its explicit state-machine action",
            )
        token = _text(fingerprint, 128)
        if not token:
            return self._finish(
                "semantic_stalled",
                "repair planner produced no stable strategy fingerprint",
            )
        if token in self._strategy_fingerprints:
            return self._finish(
                "semantic_stalled",
                "repair planner repeated an already observed strategy outside an authorized replan",
            )
        if self._strategy_fingerprints:
            return self._finish(
                "semantic_stalled",
                "repair planner changed strategy without an evidence-authorized replan",
            )
        self._strategy_fingerprints.add(token)
        return self._record("continue", "new repair strategy registered")

    def replace_strategy(self, fingerprint: str) -> HarnessLoopDecision:
        if self.terminal:
            return self._terminal_noop()
        if not self._replacement_authorized:
            return self._finish(
                "semantic_stalled",
                "replacement strategy was supplied without an evidence-authorized replan",
            )
        token = _text(fingerprint, 128)
        if not token:
            return self._finish(
                "semantic_stalled",
                "replacement planner produced no stable strategy fingerprint",
            )
        if token in self._strategy_fingerprints:
            return self._finish(
                "semantic_stalled",
                "replacement planner repeated an already failed strategy",
            )
        self._replacement_authorized = False
        self._progress_advance_authorized = False
        self._strategy_fingerprints.add(token)
        self._clear_stagnation()
        return self._record(
            "continue",
            "materially different repair strategy registered after replan",
        )

    def advance_strategy(self, fingerprint: str) -> HarnessLoopDecision:
        if self.terminal:
            return self._terminal_noop()
        if not self._progress_advance_authorized:
            return self._finish(
                "semantic_stalled",
                "next repair strategy was supplied without a proved canonical improvement",
            )
        token = _text(fingerprint, 128)
        if not token:
            return self._finish(
                "semantic_stalled",
                "progressive repair planner produced no stable strategy fingerprint",
            )
        if token in self._strategy_fingerprints:
            return self._finish(
                "semantic_stalled",
                "progressive repair planner repeated an already observed strategy",
            )
        self._progress_advance_authorized = False
        self._strategy_fingerprints.add(token)
        self._clear_stagnation()
        return self._record(
            "continue",
            "next repair strategy registered after a proved canonical improvement",
            material_progress=True,
        )

    def request_replan(
        self, reason: str, *, diagnostic: Any = None
    ) -> HarnessLoopDecision:
        if self.terminal:
            return self._terminal_noop()
        if self._replacement_authorized:
            return self._finish(
                "semantic_stalled",
                "a replan is already awaiting one replacement strategy",
                diagnostic=diagnostic,
            )
        self._progress_advance_authorized = False
        if self.replans_used >= self.budget.max_replans:
            return self._finish(
                "semantic_stalled",
                "no materially different repair strategy converged within the replan budget",
                diagnostic=diagnostic,
            )
        self.replans_used += 1
        self._replacement_authorized = True
        self._clear_stagnation()
        return self._record("replan", reason, diagnostic=diagnostic)

    def record_protocol_failure(self, diagnostic: Any) -> HarnessLoopDecision:
        if self.terminal:
            return self._terminal_noop()
        if self.protocol_retries_used >= self.budget.max_protocol_retries:
            return self._finish(
                "budget_exhausted", "patch protocol retry budget exhausted"
            )
        self.protocol_retries_used += 1
        return self._record(
            "retry_same_transaction",
            "observed protocol failure may be retried without consuming semantic budget",
            diagnostic=diagnostic,
        )

    def record_provider_failure(
        self,
        *,
        kind: str,
        diagnostic: Any = None,
    ) -> HarnessLoopDecision:
        if self.terminal:
            return self._terminal_noop()
        normalized = _text(kind, 120).lower()
        if normalized in _HARNESS_UNKNOWN_PROVIDER_KINDS:
            return self._finish(
                "provider_unknown",
                "provider accepted state is unknown; reconcile the existing call instead of retrying",
                diagnostic=diagnostic,
            )
        if normalized in _HARNESS_TRANSIENT_PROVIDER_KINDS:
            return self.record_protocol_failure(
                {"kind": normalized, "diagnostic": diagnostic}
            )
        if normalized in {
            "max_output_tokens",
            "candidate_output_budget_insufficient",
            "context_budget_exceeded",
            "max_input_tokens",
        }:
            return self.request_replan(
                "provider context/output budget requires a smaller or different semantic transaction plan",
                diagnostic={"kind": normalized, "diagnostic": diagnostic},
            )
        return self._finish(
            "failed",
            "provider failure is not safely retryable by the Harness",
            diagnostic={"kind": normalized, "diagnostic": diagnostic},
        )

    @property
    def remaining_provider_calls(self) -> int:
        return max(0, self.budget.max_provider_calls - self.provider_calls_used)

    def require_provider_capacity(
        self,
        count: int,
        *,
        phase: str,
        diagnostic: Any = None,
    ) -> HarnessLoopDecision:
        required = max(0, int(count or 0))
        if self.terminal:
            return self._terminal_noop()
        if required > self.remaining_provider_calls:
            return self._finish(
                "budget_exhausted",
                "remaining provider-call budget cannot cover the complete repair strategy",
                diagnostic={
                    "phase": _text(phase, 160),
                    "required_provider_calls": required,
                    "remaining_provider_calls": self.remaining_provider_calls,
                    "detail": diagnostic,
                },
            )
        return HarnessLoopDecision(
            action="continue",
            reason="provider-call capacity available",
        )

    def reserve_provider_call(
        self,
        *,
        phase: str,
        diagnostic: Any = None,
    ) -> HarnessLoopDecision:
        capacity = self.require_provider_capacity(
            1, phase=phase, diagnostic=diagnostic
        )
        if capacity.terminal:
            return capacity
        self.provider_calls_used += 1
        return self._record(
            "continue",
            "provider call reserved before dispatch",
            diagnostic={
                "phase": _text(phase, 160),
                "remaining_provider_calls": self.remaining_provider_calls,
                "detail": diagnostic,
            },
        )

    def record_local_transaction(
        self,
        *,
        passed: bool,
        artifact_changed: bool,
        transaction_key: str = "",
        failure_class: str = "",
        failure_evidence_ids: Iterable[str] = (),
        diagnostic: Any = None,
        human_input_required: bool = False,
        missing_fact: str = "",
        human_question: str = "",
    ) -> HarnessLoopDecision:
        if self.terminal:
            return self._terminal_noop()
        if self.local_transactions_used >= self.budget.max_local_transactions:
            return self._finish(
                "budget_exhausted", "local repair transaction budget exhausted"
            )
        self.local_transactions_used += 1
        if human_input_required:
            if _text(missing_fact, 2_000) and _text(human_question, 2_000):
                return self._finish(
                    "needs_human_input",
                    "the reviewer proved that an external fact or decision is required",
                    diagnostic={
                        "missing_fact": _text(missing_fact, 2_000),
                        "human_question": _text(human_question, 2_000),
                    },
                )
            passed = False
            diagnostic = {
                "kind": "unproved_human_handoff",
                "observed": diagnostic,
            }
        if passed and artifact_changed:
            self._clear_stagnation(
                scope="local_transaction",
                transaction_key=_text(transaction_key, 240) or None,
            )
            return self._record(
                "continue",
                "local transaction passed its acceptance test",
                material_progress=True,
            )
        normalized_failure_class = _text(failure_class, 160)
        if not normalized_failure_class and isinstance(diagnostic, Mapping):
            normalized_failure_class = _text(
                diagnostic.get("kind")
                or diagnostic.get("failure_class")
                or diagnostic.get("unresolved_reason"),
                160,
            )
        return self._observe_stagnation(
            signature_evidence={
                "scope": "local_transaction",
                "transaction_key": _text(transaction_key, 240),
                "failure_class": normalized_failure_class,
                "failure_evidence_ids": sorted(
                    {
                        _text(value, 200)
                        for value in failure_evidence_ids
                        if _text(value, 200)
                    }
                ),
            },
            diagnostic={
                "scope": "local_transaction",
                "transaction_key": _text(transaction_key, 240),
                "passed": bool(passed),
                "artifact_changed": bool(artifact_changed),
                "diagnostic": diagnostic,
            },
        )

    def record_semantic_candidate(
        self,
        *,
        can_submit: bool,
        artifact_changed: bool,
        resolved_issue_ids: Iterable[str] = (),
        remaining_open_issue_ids: Iterable[str] = (),
        introduced_issue_ids: Iterable[str] = (),
        structural_regressions: Iterable[str] = (),
        diagnostic: Any = None,
    ) -> HarnessLoopDecision:
        if self.terminal:
            return self._terminal_noop()
        if self.semantic_candidates_used >= self.budget.max_semantic_candidates:
            return self._finish(
                "budget_exhausted", "semantic candidate budget exhausted"
            )
        self.semantic_candidates_used += 1
        remaining = tuple(
            sorted(
                {
                    _text(value, 160)
                    for value in remaining_open_issue_ids
                    if _text(value, 160)
                }
            )
        )
        introduced = tuple(
            sorted(
                {
                    _text(value, 160)
                    for value in introduced_issue_ids
                    if _text(value, 160)
                }
            )
        )
        regressions = tuple(
            _text(value, 1_000)
            for value in structural_regressions
            if _text(value, 1_000)
        )
        resolved = tuple(
            sorted(
                {
                    _text(value, 160)
                    for value in resolved_issue_ids
                    if _text(value, 160)
                }
            )
        )
        if (
            can_submit
            and artifact_changed
            and not remaining
            and not introduced
            and not regressions
        ):
            return self._finish(
                "accepted",
                "full quality gate returned can_submit=true",
                material_progress=True,
            )
        material_progress = bool(
            artifact_changed and resolved and not introduced and not regressions
        )
        if material_progress:
            self._progress_advance_authorized = True
            self._clear_stagnation(scope="semantic_candidate")
            return self._record(
                "continue",
                "candidate resolved evidenced issues without introducing a regression",
                material_progress=True,
                diagnostic={
                    "resolved_issue_ids": resolved,
                    "remaining_open_issue_ids": remaining,
                },
            )
        return self._observe_stagnation(
            signature_evidence={
                "scope": "semantic_candidate",
                "remaining_open_issue_ids": remaining,
                "introduced_issue_ids": introduced,
                "structural_regressions": regressions,
            },
            diagnostic={
                "scope": "semantic_candidate",
                "artifact_changed": bool(artifact_changed),
                "resolved_issue_ids": resolved,
                "remaining_open_issue_ids": remaining,
                "introduced_issue_ids": introduced,
                "structural_regressions": regressions,
                "diagnostic": diagnostic,
            },
        )

    def record_initial_gate(
        self,
        *,
        can_submit: bool,
        remaining_open_issue_ids: Iterable[str] = (),
        introduced_issue_ids: Iterable[str] = (),
        structural_regressions: Iterable[str] = (),
        diagnostic: Any = None,
    ) -> HarnessLoopDecision:
        if self.terminal:
            return self._terminal_noop()
        remaining = tuple(
            sorted(
                {
                    _text(value, 160)
                    for value in remaining_open_issue_ids
                    if _text(value, 160)
                }
            )
        )
        introduced = tuple(
            sorted(
                {
                    _text(value, 160)
                    for value in introduced_issue_ids
                    if _text(value, 160)
                }
            )
        )
        regressions = tuple(
            _text(value, 1_000)
            for value in structural_regressions
            if _text(value, 1_000)
        )
        if can_submit and not remaining and not introduced and not regressions:
            return self._finish(
                "accepted",
                "initial full quality gate returned can_submit=true",
                material_progress=True,
            )
        return self._record(
            "diagnose_and_plan",
            "initial full quality gate observed actionable defects",
            diagnostic={
                "remaining_open_issue_ids": remaining,
                "introduced_issue_ids": introduced,
                "structural_regressions": regressions,
                "diagnostic": diagnostic,
            },
        )

    def _observe_stagnation(
        self,
        *,
        signature_evidence: Any,
        diagnostic: Any,
    ) -> HarnessLoopDecision:
        signature = _sha256_text(
            json.dumps(
                signature_evidence,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        )
        identity = (
            dict(signature_evidence)
            if isinstance(signature_evidence, Mapping)
            else {"scope": "unknown", "value": signature_evidence}
        )
        self._stagnation_identity[signature] = identity
        self._stagnation_counts[signature] = (
            self._stagnation_counts.get(signature, 0) + 1
        )
        self._last_failure_signature = signature
        self.stagnant_observations = self._stagnation_counts[signature]
        if (
            self.stagnant_observations
            >= self.budget.stagnant_observations_before_replan
        ):
            return self.request_replan(
                "the same evidenced failure repeated without material progress",
                diagnostic=diagnostic,
            )
        return self._record(
            "revise_current_transaction",
            "candidate failed, but one evidence-guided correction remains before replanning",
            diagnostic=diagnostic,
        )

    def _clear_stagnation(
        self,
        *,
        scope: str | None = None,
        transaction_key: str | None = None,
    ) -> None:
        if scope is None:
            self._stagnation_counts.clear()
            self._stagnation_identity.clear()
            self._last_failure_signature = ""
            self.stagnant_observations = 0
            return
        removable = []
        for signature, identity in self._stagnation_identity.items():
            if _text(identity.get("scope"), 120) != scope:
                continue
            if transaction_key is not None and _text(
                identity.get("transaction_key"), 240
            ) != transaction_key:
                continue
            removable.append(signature)
        for signature in removable:
            self._stagnation_counts.pop(signature, None)
            self._stagnation_identity.pop(signature, None)
        if self._last_failure_signature in removable:
            self._last_failure_signature = ""
            self.stagnant_observations = 0

    def _finish(
        self,
        state: str,
        reason: str,
        *,
        material_progress: bool = False,
        diagnostic: Any = None,
    ) -> HarnessLoopDecision:
        if self.terminal:
            return self._terminal_noop()
        self.state = state
        self.reason = reason
        return self._record(
            state,
            reason,
            terminal=True,
            material_progress=material_progress,
            diagnostic=diagnostic,
        )

    def _record(
        self,
        action: str,
        reason: str,
        *,
        terminal: bool = False,
        material_progress: bool = False,
        diagnostic: Any = None,
    ) -> HarnessLoopDecision:
        row = {
            "action": action,
            "reason": reason,
            "terminal": terminal,
            "material_progress": material_progress,
            "semantic_candidates_used": self.semantic_candidates_used,
            "replans_used": self.replans_used,
            "local_transactions_used": self.local_transactions_used,
            "protocol_retries_used": self.protocol_retries_used,
            "provider_calls_used": self.provider_calls_used,
            "stagnant_observations": self.stagnant_observations,
        }
        if diagnostic not in (None, "", [], {}):
            row["diagnostic"] = diagnostic
        self.history.append(row)
        return HarnessLoopDecision(
            action=action,
            reason=reason,
            terminal=terminal,
            material_progress=material_progress,
        )

    def _terminal_noop(self) -> HarnessLoopDecision:
        return HarnessLoopDecision(
            action=self.state,
            reason=self.reason,
            terminal=True,
        )

    def projection(self) -> dict[str, Any]:
        return {
            "schema": "opc.harness_convergence.v2",
            "state": self.state,
            "reason": self.reason,
            "terminal": self.terminal,
            "budget": asdict(self.budget),
            "usage": {
                "semantic_candidates": self.semantic_candidates_used,
                "replans": self.replans_used,
                "local_transactions": self.local_transactions_used,
                "protocol_retries": self.protocol_retries_used,
                "provider_calls": self.provider_calls_used,
                "remaining_provider_calls": self.remaining_provider_calls,
            },
            "strategy_fingerprints": sorted(self._strategy_fingerprints),
            "replacement_strategy_pending": self._replacement_authorized,
            "progress_strategy_advance_pending": self._progress_advance_authorized,
            "stagnation": {
                "last_failure_signature": self._last_failure_signature,
                "current_count": self.stagnant_observations,
                "counts": dict(self._stagnation_counts),
                "identity": deepcopy(self._stagnation_identity),
            },
            "history": self.history[-32:],
        }


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
    criterion_id: str
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
    failure_evidence: list[dict[str, Any]] = field(default_factory=list)
    last_error: str = ""
    updated_at: float = field(default_factory=time.time)


def _stable_issue_identity_matches(
    previous: HarnessIssue,
    *,
    issue_id: str,
    criterion_id: str,
    fingerprint: str,
) -> bool:
    if issue_id and issue_id == previous.issue_id:
        return True
    incoming_has_stable_id = bool(
        criterion_id or (issue_id and not issue_id.startswith("issue_"))
    )
    previous_has_stable_id = bool(
        previous.criterion_id or not previous.issue_id.startswith("issue_")
    )
    return (
        not incoming_has_stable_id
        and not previous_has_stable_id
        and fingerprint == previous.fingerprint
    )


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
        for raw in raw_issues:
            raw_mapping = raw if isinstance(raw, Mapping) else {}
            message = _text(
                raw_mapping.get("message")
                or raw_mapping.get("issue")
                or raw_mapping.get("description")
                or raw,
                2_000,
            )
            if not message:
                continue
            requested_issue_id = _text(
                raw_mapping.get("issue_id") or raw_mapping.get("id"), 160
            )
            requested_criterion_id = _text(
                raw_mapping.get("criterion_id"), 160
            )
            fingerprint = hashlib.sha256(
                message.lower().encode("utf-8")
            ).hexdigest()[:24]
            existing = next(
                (
                    item
                    for item in self.checkpoint.issue_ledger
                    if _stable_issue_identity_matches(
                        item,
                        issue_id=requested_issue_id,
                        criterion_id=requested_criterion_id,
                        fingerprint=fingerprint,
                    )
                ),
                None,
            )
            requested_status = _text(raw_mapping.get("status"), 80).lower()
            evidence = raw_mapping.get("evidence")
            has_evidence = bool(
                _text(evidence, 2_000)
                if not isinstance(evidence, (list, tuple))
                else any(_text(item, 2_000) for item in evidence)
            )
            if requested_status in {
                "resolved",
                "not_applicable",
                "passed",
                "fixed",
                "satisfied",
            }:
                # Omission is not resolution and neither is a newly invented
                # resolved row.  The reviewer must close the same stable issue
                # identity with explicit evidence.
                if existing is not None and requested_issue_id and has_evidence:
                    existing.status = (
                        "not_applicable"
                        if requested_status == "not_applicable"
                        else "resolved"
                    )
                    existing.last_seen_round = self.checkpoint.round_index
                continue
            if existing is None:
                normalized_status = (
                    requested_status
                    if requested_status
                    in {
                        "open",
                        "advisory",
                        "pending_execution",
                        "pending_human_confirmation",
                    }
                    else "open"
                )
                existing = HarnessIssue(
                    issue_id=requested_issue_id or f"issue_{fingerprint}",
                    criterion_id=requested_criterion_id,
                    fingerprint=fingerprint,
                    category="quality",
                    severity=_text(raw_mapping.get("severity"), 80).lower()
                    or (
                        "blocker"
                        if review.get("pass") is False
                        else "warning"
                    ),
                    message=message,
                    first_seen_round=self.checkpoint.round_index,
                    last_seen_round=self.checkpoint.round_index,
                    status=normalized_status,
                )
                self.checkpoint.issue_ledger.append(existing)
            else:
                existing.last_seen_round = self.checkpoint.round_index
                existing.status = (
                    requested_status
                    if requested_status
                    in {
                        "open",
                        "advisory",
                        "pending_execution",
                        "pending_human_confirmation",
                    }
                    else "open"
                )
                if requested_criterion_id:
                    existing.criterion_id = requested_criterion_id
            current.append(existing)
        # A truncated/drifting reviewer can omit a previously observed row.
        # Keep it open until the same stable identity is explicitly resolved
        # with evidence; never infer semantic progress from omission.
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

    def record_failure_evidence(
        self,
        failure: Any,
        *,
        phase: str,
        raw_output: Any = None,
        attempt: int = 0,
    ) -> HarnessFailureObservation:
        observation = HarnessFailureObservation.from_failure(
            failure,
            phase=phase,
            raw_output=raw_output,
            attempt=attempt,
        )
        self.checkpoint.failure_evidence.append(observation.projection())
        self.checkpoint.failure_evidence = self.checkpoint.failure_evidence[-64:]
        self.checkpoint.pending_action = observation.recovery_action
        self.checkpoint.last_error = observation.error[:2_000]
        return observation

    def fail_closed(self, reason: str, *, provider_status: str = "unknown") -> None:
        self.checkpoint.status = "needs_reconciliation" if provider_status in {"unknown", "transport_unknown", "response_stream_waiting"} else "failed"
        self.checkpoint.last_error = _text(reason, 2_000)
        self.checkpoint.provider_status = provider_status
        self.checkpoint.terminal_event_seen = False

    def complete(self) -> None:
        self.checkpoint.status = "completed"
        self.checkpoint.pending_action = None

    def apply_convergence(self, controller: HarnessConvergenceController) -> None:
        """Project one v2 controller terminal into the legacy PEA checkpoint."""

        if not controller.terminal:
            return
        self.checkpoint.status = controller.state
        self.checkpoint.pending_action = None
        self.checkpoint.last_error = controller.reason

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
            "failure_evidence": self.checkpoint.failure_evidence[-32:],
            "last_error": self.checkpoint.last_error,
            "updated_at": self.checkpoint.updated_at,
        }
