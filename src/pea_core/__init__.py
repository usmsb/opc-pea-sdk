"""PEA 通用内核：可继承的 Harness 父类 + 分层上下文 + 向量 RAG 记忆 + 滚动摘要。

三体 PEA（雕刻时光/底牌堂/喵星球）共享此包（单一真源，避免漂移）。
部署时随各 PEA 镜像一并 copy（见 deploy/pea 渲染脚本与 Dockerfile）。
"""
from __future__ import annotations

from . import context
from .admin import (EntitySpec, core_entities, make_admin_dependency, make_admin_router,
                    make_token, spec_for, verify_token)
from .embeddings import DIM, EmbeddingProvider, MiniMaxEmbedding, SandboxEmbedding, cosine
from .harness import BaseHarness, TurnResult, parse_action
from .memory import ChunkStore, RollingSummary, VectorMemory

__all__ = [
    "BaseHarness", "TurnResult", "parse_action",
    "VectorMemory", "RollingSummary", "ChunkStore",
    "EmbeddingProvider", "SandboxEmbedding", "MiniMaxEmbedding", "cosine", "DIM",
    "context",
    "make_admin_router", "make_admin_dependency", "core_entities", "spec_for", "EntitySpec",
    "make_token", "verify_token",
]
