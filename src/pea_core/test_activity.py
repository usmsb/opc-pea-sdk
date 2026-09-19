"""The conversation UI sees a bounded summary, never raw Harness material."""

from peas.pea_core.activity import present_turn_activity


def test_activity_does_not_expose_tool_arguments_results_or_private_context():
    secret = "private-user-prompt-and-provider-token"
    activity = present_turn_activity(
        steps=[{"tool": "诊断报告", "args": {"prompt": secret},
                "result": {"report": secret}}],
        harness={"status": "completed", "context": {"compacted_count": 2,
                  "raw_messages": secret}},
    )
    assert activity["schema"] == "opc.pea_harness_activity.v1"
    assert activity["state"] == "completed"
    assert activity["events"][1]["label"] == "诊断报告"
    assert secret not in str(activity)
    assert "较早的对话" in activity["summary"]


def test_waiting_and_failure_states_are_honest_and_bounded():
    steps = [{"tool": f"能力{i}", "result": {}} for i in range(20)]
    waiting = present_turn_activity(
        steps=steps, harness={"status": "needs_human_input"},
        pending_interactions=[{"status": "pending"}],
    )
    assert waiting["state"] == "waiting"
    assert len(waiting["events"]) == 11
    assert waiting["events"][-1]["state"] == "waiting"
    failed = present_turn_activity(steps=[], harness={"status": "failed"})
    assert failed["state"] == "attention"
    assert not any(event["label"] == "形成本轮回复" for event in failed["events"])


def test_invalid_compaction_metadata_does_not_break_chat_response():
    activity = present_turn_activity(steps=[], harness={"context": {"compacted_count": "unknown"}})
    assert activity["state"] == "completed"
