"""Protocol-parity tests for the standalone PEA convergence mirror."""

from __future__ import annotations

from dataclasses import asdict

from agents.complex_task_harness import (
    HarnessConvergenceController as AgentController,
    HarnessLoopBudget as AgentBudget,
)
from peas.pea_core.complex_task_harness import (
    HarnessConvergenceController as PeaController,
    HarnessLoopBudget as PeaBudget,
)


def _exercise(controller):
    decisions = [controller.register_strategy("strategy-a")]
    decisions.append(
        controller.record_local_transaction(
            passed=False,
            artifact_changed=True,
            transaction_key="unit-capacity",
            failure_class="acceptance_not_met",
            failure_evidence_ids=["CRITERION-CAPACITY"],
            diagnostic={"prose": "第一次措辞"},
        )
    )
    decisions.append(
        controller.record_local_transaction(
            passed=False,
            artifact_changed=True,
            transaction_key="unit-capacity",
            failure_class="acceptance_not_met",
            failure_evidence_ids=["CRITERION-CAPACITY"],
            diagnostic={"prose": "第二次措辞不同"},
        )
    )
    decisions.append(controller.replace_strategy("strategy-b"))
    decisions.append(
        controller.record_semantic_candidate(
            can_submit=True,
            artifact_changed=True,
            resolved_issue_ids=["CRITERION-CAPACITY"],
            remaining_open_issue_ids=[],
        )
    )
    return [asdict(item) for item in decisions], controller.projection()


def test_pea_and_agent_controllers_follow_the_same_finite_transition_trace():
    agent_decisions, agent_projection = _exercise(
        AgentController(
            AgentBudget(
                max_semantic_candidates=4,
                max_replans=2,
                max_local_transactions=4,
                max_protocol_retries=1,
                stagnant_observations_before_replan=2,
            )
        )
    )
    pea_decisions, pea_projection = _exercise(
        PeaController(
            PeaBudget(
                max_semantic_candidates=4,
                max_replans=2,
                max_local_transactions=4,
                max_protocol_retries=1,
                stagnant_observations_before_replan=2,
            )
        )
    )

    assert pea_decisions == agent_decisions
    assert pea_projection == agent_projection


def test_repeated_unapproved_strategy_is_absorbing_in_both_runtimes():
    for controller in (AgentController(), PeaController()):
        assert controller.register_strategy("strategy-a").action == "continue"
        repeated = controller.register_strategy("strategy-a")
        late = controller.request_replan("late event")
        assert repeated.action == "semantic_stalled"
        assert repeated.terminal is True
        assert late.action == "semantic_stalled"
        assert controller.replans_used == 0


def test_different_unapproved_strategy_is_absorbing_in_both_runtimes():
    for controller in (AgentController(), PeaController()):
        assert controller.register_strategy("strategy-a").action == "continue"
        unapproved = controller.register_strategy("strategy-b")
        assert unapproved.action == "semantic_stalled"
        assert unapproved.terminal is True
        assert controller.replans_used == 0


def test_direct_replacement_without_replan_is_absorbing_in_both_runtimes():
    for controller in (AgentController(), PeaController()):
        assert controller.register_strategy("strategy-a").action == "continue"
        unapproved = controller.replace_strategy("strategy-b")
        assert unapproved.action == "semantic_stalled"
        assert unapproved.terminal is True
        assert controller.replans_used == 0


def test_proved_progress_strategy_advance_has_runtime_parity():
    for controller in (AgentController(), PeaController()):
        assert controller.register_strategy("strategy-a").action == "continue"
        progress = controller.record_semantic_candidate(
            can_submit=False,
            artifact_changed=True,
            resolved_issue_ids=["ISSUE-A"],
            remaining_open_issue_ids=["ISSUE-B"],
        )
        advanced = controller.advance_strategy("strategy-b")
        assert progress.material_progress is True
        assert advanced.action == "continue"
        assert controller.replans_used == 0


def test_context_budget_failure_requests_replan_in_both_runtimes():
    """Context admission is a semantic planning failure, never a blind retry."""
    for controller in (AgentController(), PeaController()):
        decision = controller.record_provider_failure(
            kind="context_budget_exceeded",
            diagnostic={"required_tokens": 50_000, "input_budget": 16_000},
        )
        assert decision.action == "replan"
        assert decision.terminal is False
        assert controller.replans_used == 1
        assert controller.protocol_retries_used == 0
