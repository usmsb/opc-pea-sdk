from __future__ import annotations

import pytest

from peas.pea_core.harness import _action_object, _artifact_first_domain_prompt
from peas.pea_core.safety import review_safety_artifact


def test_pea_domain_prompt_removes_legacy_json_action_requirement():
    converted = _artifact_first_domain_prompt(
        '# 输出格式（每步一个 JSON）\n说话：{"action":"say","text":"..."}\n保留领域要求'
    )

    assert '{"action"' not in converted
    assert "不输出 JSON" in converted
    assert "保留领域要求" in converted
    assert "Harness 会在下一次独立 LLM 调用" in converted


def test_pea_action_parser_distinguishes_semantic_artifact_from_machine_projection():
    assert _action_object("建议下一步调用歌词工具，主题是给妈妈。") is None
    assert _action_object('{"action":"tool","name":"歌词","args":{"theme":"给妈妈"}}') == {
        "action": "tool",
        "name": "歌词",
        "args": {"theme": "给妈妈"},
    }


@pytest.mark.asyncio
async def test_safety_review_uses_natural_artifact_then_non_json_projection():
    class Chat:
        def __init__(self):
            self.calls = []

        async def complete(self, messages, **kwargs):
            self.calls.append((messages, kwargs))
            if len(self.calls) == 1:
                return "审查结论：没有发现红线。证据：普通产品说明。"
            return "<safe>true</safe><reason>普通产品说明</reason>"

    chat = Chat()
    assert await review_safety_artifact(chat, "普通产品说明") == (
        True,
        "普通产品说明",
    )
    assert len(chat.calls) == 2
    assert "不要输出 JSON" in chat.calls[0][0][0]["content"]
    assert chat.calls[1][0][1]["content"] == chat.calls[0][0][1]["content"].replace(
        "普通产品说明", "审查结论：没有发现红线。证据：普通产品说明。"
    )


@pytest.mark.asyncio
async def test_safety_projection_failure_is_fail_closed():
    class Chat:
        async def complete(self, messages, **kwargs):
            del messages, kwargs
            return "无法形成明确控制投影"

    safe, reason = await review_safety_artifact(Chat(), "待审核内容")
    assert safe is False
    assert "安全阻断" in reason
