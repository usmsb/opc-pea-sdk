"""嵌入向量 provider（RAG 的基元）。sandbox-first：无 key 也能跑可测。

- SandboxEmbedding：确定性 hash 伪向量（词袋哈希）——离线/测试/无 key 用，质量降级但稳定可复现。
- MiniMaxEmbedding：真实嵌入（复用对话 key，minimaxi.com）。
共享给三体 PEA；具体选哪个由各 PEA 的 providers 工厂按配置决定（有无 key）。
"""
from __future__ import annotations

import math
import re
from typing import Any, Protocol, runtime_checkable

DIM = 256


@runtime_checkable
class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str], kind: str = "db") -> list[list[float]]:
        ...


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else v


_TOKEN = re.compile(r"[一-鿿]|[a-zA-Z0-9]+")


class SandboxEmbedding:
    """词袋哈希伪向量：中文按字、英文按词，哈希到固定维度。捕捉词面重叠，离线可测。"""

    def __init__(self, dim: int = DIM):
        self.dim = dim
        self.sandbox = True

    async def embed(self, texts: list[str], kind: str = "db") -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            v = [0.0] * self.dim
            for tok in _TOKEN.findall((t or "").lower()):
                h = hash(tok) % self.dim
                v[h] += 1.0
            out.append(_normalize(v))
        return out


class MiniMaxEmbedding:
    """真实嵌入：MiniMax embeddings（embo-01）。失败抛错，由上层兜底回退到沙箱向量。"""

    def __init__(self, api_key: str, base_url: str = "https://api.minimaxi.com/v1",
                 model: str = "embo-01", timeout: float = 20.0):
        self.api_key = api_key
        self.url = base_url.rstrip("/") + "/embeddings"
        self.model = model
        self.timeout = timeout
        self.sandbox = False

    async def embed(self, texts: list[str], kind: str = "db") -> list[list[float]]:
        import httpx

        payload: dict[str, Any] = {"model": self.model, "texts": texts, "type": kind}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(self.url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        vecs = data.get("vectors") or data.get("embeddings") or []
        if not vecs or len(vecs) != len(texts):
            raise ValueError("embedding response shape mismatch")
        return [_normalize([float(x) for x in v]) for v in vecs]
