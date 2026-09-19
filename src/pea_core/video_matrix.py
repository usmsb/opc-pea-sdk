"""Thin PEA facade over the platform's shared A2A subagent runtime."""

from __future__ import annotations

import json
import uuid
from typing import Any

from agents.a2a_subagent_runtime import (
    A2AInvokePolicy,
    A2ASubagentRuntime,
    artifact_url,
    build_message_send,
    task_state,
)


class MatrixVideoClient:
    """Submit one media capability task without owning protocol machinery."""

    def __init__(
        self,
        *,
        poll_seconds: int = 600,
        poll_interval: int = 3,
    ) -> None:
        self.runtime = A2ASubagentRuntime(
            A2AInvokePolicy(
                side_effect_class="paid_creation",
                request_timeout_seconds=120,
                poll_timeout_seconds=max(1, poll_seconds),
                poll_interval_seconds=max(0.1, poll_interval),
                poll_max_interval_seconds=max(3, poll_interval * 4),
                query_before_send=False,
                submit_attempts=1,
            )
        )

    async def invoke_url(
        self,
        rpc_url: str,
        payload: dict[str, Any],
        metadata: dict[str, Any],
    ) -> str | None:
        task_id = f"pea_media_{uuid.uuid4().hex}"
        request = build_message_send(
            task_id=task_id,
            text=json.dumps(payload, ensure_ascii=False),
            metadata={
                **metadata,
                "side_effect_class": "paid_creation",
                "idempotency_key": task_id,
                "opc": {"execution_id": task_id},
            },
        )
        task = await self.runtime.invoke(
            rpc_url=rpc_url,
            task_id=task_id,
            request=request,
        )
        if task_state(task) not in {"completed", "complete", "succeeded", "success", "done"}:
            return None
        return artifact_url(task)
