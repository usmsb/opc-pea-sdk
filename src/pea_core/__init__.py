"""PEA 通用内核：可继承的 Harness 父类 + 分层上下文 + 向量 RAG 记忆 + 滚动摘要。

三体 PEA（雕刻时光/底牌堂/喵星球）共享此包（单一真源，避免漂移）。
部署时随各 PEA 镜像一并 copy（见 deploy/pea 渲染脚本与 Dockerfile）。
"""
from __future__ import annotations

from . import context
from .admin import (EntitySpec, core_entities, make_admin_dependency, make_admin_router,
                    make_token, spec_for, verify_token)
from .embeddings import DIM, EmbeddingProvider, MiniMaxEmbedding, SandboxEmbedding, cosine
from .opc_client import CentralChat, CentralEmbedding, CentralLyrics, CentralMusic, attest_runtime, central_runtime_enabled
from .harness import BaseHarness, TurnResult, parse_action
from .safety import review_safety_artifact
from .context_manifest import ContextManifest, ContextSource, build_manifest
# Long-Artifact PEA work must use the exact same convergence semantics as the
# Agent matrix.  Dockerfiles and the no-build renderer now package these
# modules.  Keep the old local mirror only for source-only legacy images, where
# the full refinement entrypoint fails explicitly rather than pretending the
# two implementations are interchangeable.
try:
    from agents.complex_task_harness import (
        COMPLEX_TASK_HARNESS_SCHEMA,
        ComplexTaskSession,
        HarnessConvergenceController,
        HarnessLoopBudget,
        HarnessLoopDecision,
        HarnessObjective,
        HarnessFailureObservation,
        classify_harness_output_failure,
        recovery_action_for_failure,
    )
except ModuleNotFoundError:  # pragma: no cover - legacy/source-only fallback
    from .complex_task_harness import (
        COMPLEX_TASK_HARNESS_SCHEMA,
        ComplexTaskSession,
        HarnessConvergenceController,
        HarnessLoopBudget,
        HarnessLoopDecision,
        HarnessObjective,
        HarnessFailureObservation,
        classify_harness_output_failure,
        recovery_action_for_failure,
    )
from .quality_contract import (
    QUALITY_CONTRACT_ID,
    QUALITY_CONTRACT_VERSION,
    quality_contract_binding,
    quality_contract_manifest,
    render_quality_contract,
)
from .interaction import GoalContract, InteractionRequest, TaskEnvelope
from .artifact_quality import review_complex_artifact
from .memory import ChunkStore, RollingSummary, VectorMemory

__all__ = [
    "BaseHarness", "TurnResult", "parse_action",
    "review_safety_artifact",
    "COMPLEX_TASK_HARNESS_SCHEMA",
    "ComplexTaskSession", "HarnessConvergenceController", "HarnessLoopBudget",
    "HarnessLoopDecision", "HarnessObjective",
    "HarnessFailureObservation", "classify_harness_output_failure", "recovery_action_for_failure",
    "GoalContract", "InteractionRequest", "TaskEnvelope",
    "review_complex_artifact",
    "ContextManifest", "ContextSource", "build_manifest",
    "VectorMemory", "RollingSummary", "ChunkStore",
    "EmbeddingProvider", "SandboxEmbedding", "MiniMaxEmbedding", "cosine", "DIM",
    "CentralChat", "CentralEmbedding", "CentralLyrics", "CentralMusic", "attest_runtime", "central_runtime_enabled",
    "context",
    "make_admin_router", "make_admin_dependency", "core_entities", "spec_for", "EntitySpec",
    "make_token", "verify_token",
]
