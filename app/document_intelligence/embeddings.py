"""Embedding 生命周期：生成、缓存、版本和幂等。"""

import hashlib
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Protocol

import httpx

from app.domain import DocumentChunk, EmbeddingRecord
from app.platform.ids import new_id
from app.platform.repository import Repository


class EmbeddingProvider(Protocol):
    """Embedding 模型抽象，支持本地确定性实现和远程模型。"""

    model_version: str
    dimension: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class DeterministicEmbeddingProvider:
    """本地确定性 Embedding 提供者，用于测试和离线契约验证。

    使用 feature hashing 生成固定维度单位向量；语义相近的文本在测试
    用例中会得到更高的余弦相似度。不用于生产语义质量评估。
    """

    dimension: int = 1536
    model_version: str = "deterministic-embedding-v1"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [_feature_hash_vector(text, self.dimension) for text in texts]


@dataclass
class OpenAIEmbeddingProvider:
    """OpenAI-compatible embedding API 提供者。"""

    api_key: str
    model: str = "text-embedding-3-small"
    base_url: str = "https://api.openai.com/v1"
    timeout_seconds: float = 30.0

    @property
    def model_version(self) -> str:
        return f"openai-{self.model}"

    @property
    def dimension(self) -> int:
        # text-embedding-3-small; caller should override for other models.
        return 1536

    def embed(self, texts: list[str]) -> list[list[float]]:
        url = self.base_url.rstrip("/") + "/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "input": texts,
        }
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(url, headers=headers, json=body)
        except httpx.TimeoutException as exc:
            raise EmbeddingProviderError("EMBEDDING_TIMEOUT", str(exc)) from exc
        except httpx.HTTPError as exc:
            raise EmbeddingProviderError("EMBEDDING_HTTP_ERROR", str(exc)) from exc
        if response.status_code >= 400:
            raise EmbeddingProviderError(
                "EMBEDDING_PROVIDER_ERROR",
                f"status={response.status_code} body={response.text[:400]}",
            )
        data = response.json()
        try:
            items = data["data"]
            return [items[i]["embedding"] for i in range(len(texts))]
        except (KeyError, IndexError, TypeError) as exc:
            raise EmbeddingProviderError(
                "EMBEDDING_RESPONSE_INVALID", str(data)[:400]
            ) from exc


class EmbeddingProviderError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


# 确定性 Embedding 的语义维度词汇表。每个维度对应一组相关词，
# 命中即在该维度加 1。这样语义相近的文本在相同维度上有响应，
# 不同主题的文本在向量空间上明显分离。
_SEMANTIC_DIMENSIONS: tuple[tuple[str, ...], ...] = (
    ("净利润", "利润", "净收益", "盈利"),
    ("增长", "上升", "增加", "同比增", "同比增长", "同比升", "环比增"),
    ("下降", "减少", "下滑", "同比降", "同比下降", "环比降"),
    ("营业收入", "营收", "销售收入", "主营业务收入"),
    ("合同", "协议", "订单", "销售合同", "采购合同"),
    ("金额", "亿元", "万元", "人民币", "元"),
    ("股东", "持股", "增持", "减持", "股权"),
    ("罚款", "处罚", "监管", "警示", "立案"),
    ("半年度", "季度", "年度", "2026年", "2025年"),
    ("重大", "重要", "特别"),
    ("市场", "行业", "经济", "宏观"),
    ("风险", "不确定性", "波动"),
)


def _token_features(text: str) -> list[str]:
    """提取稳定语义特征：数字、百分数和语义维度词。"""
    text = text.lower()
    features: list[str] = []
    # 连续数字和百分数
    features.extend(re.findall(r"\d+%?", text))
    # 语义维度词
    for terms in _SEMANTIC_DIMENSIONS:
        for term in terms:
            if term in text:
                features.append(term)
                break
    return features


def _feature_hash_vector(text: str, dimension: int) -> list[float]:
    """基于语义维度 + 特征哈希生成确定性单位向量。"""
    vector = [0.0] * dimension
    # 语义维度占据前 len(_SEMANTIC_DIMENSIONS) 维
    text_lower = text.lower()
    for index, terms in enumerate(_SEMANTIC_DIMENSIONS):
        if any(term in text_lower for term in terms):
            vector[index] += 1.0
    # 其余维度用特征哈希填充，保留更多区分信息
    semantic_dim_count = len(_SEMANTIC_DIMENSIONS)
    for feature in _token_features(text_lower):
        for seed in (0, 1, 2):
            digest = hashlib.sha256(f"{feature}:{seed}".encode()).digest()
            index = (
                int.from_bytes(digest[:4], "big") % (dimension - semantic_dim_count)
            ) + semantic_dim_count
            sign = 1 if digest[4] % 2 == 0 else -1
            vector[index] += sign
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0:
        return vector
    return [round(v / norm, 6) for v in vector]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算两个等长向量的余弦相似度，结果在 [-1, 1] 区间。"""
    if len(a) != len(b):
        raise ValueError("EMBEDDING_DIMENSION_MISMATCH")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return round(dot / (norm_a * norm_b), 6)


def mean_vector(vectors: list[list[float]]) -> list[float]:
    """计算多个等长向量的均值向量并归一化。"""
    if not vectors:
        raise ValueError("EMPTY_VECTORS")
    dimension = len(vectors[0])
    sums = [0.0] * dimension
    for vector in vectors:
        if len(vector) != dimension:
            raise ValueError("EMBEDDING_DIMENSION_MISMATCH")
        for i, value in enumerate(vector):
            sums[i] += value
    mean = [value / len(vectors) for value in sums]
    norm = math.sqrt(sum(v * v for v in mean))
    if norm == 0:
        return mean
    return [round(v / norm, 6) for v in mean]


class EmbeddingService:
    """DocumentChunk Embedding 生命周期服务。"""

    def __init__(
        self,
        repository: Repository,
        provider: Optional[EmbeddingProvider] = None,
    ) -> None:
        self.repository = repository
        self.provider = provider or DeterministicEmbeddingProvider()

    def embed_chunks(
        self,
        chunks: list[DocumentChunk],
        model_version: Optional[str] = None,
    ) -> list[EmbeddingRecord]:
        """为 chunks 生成或复用 EmbeddingRecord，按 chunk 顺序返回。"""
        model_version = model_version or self.provider.model_version
        records: list[EmbeddingRecord] = []
        pending: list[tuple[int, DocumentChunk]] = []

        for index, chunk in enumerate(chunks):
            existing = self.repository.find_embedding_record_by_chunk_and_model(
                chunk.id, model_version
            )
            if existing is not None:
                records.append((index, existing))
                continue
            pending.append((index, chunk))

        if pending:
            texts = [chunk.text for _, chunk in pending]
            try:
                vectors = self.provider.embed(texts)
                status = "completed"
                error_code: Optional[str] = None
            except EmbeddingProviderError as exc:
                vectors = [[] for _ in pending]
                status = "failed"
                error_code = exc.code

            for (index, chunk), vector in zip(pending, vectors):
                record = EmbeddingRecord(
                    id=new_id("emb"),
                    chunk_id=chunk.id,
                    embedding_model_version=model_version,
                    embedding=vector,
                    content_hash=chunk.content_hash,
                    status=status,
                    error_code=error_code,
                    created_at=datetime.now(timezone.utc),
                )
                self.repository.save_embedding_record(record)
                records.append((index, record))

        records.sort(key=lambda item: item[0])
        return [record for _, record in records]

    def representative_embedding(
        self,
        chunks: list[DocumentChunk],
        model_version: Optional[str] = None,
    ) -> Optional[list[float]]:
        """生成 chunks 的代表性向量（均值），用于文档/组级别相似度比较。"""
        records = self.embed_chunks(chunks, model_version=model_version)
        completed = [record for record in records if record.status == "completed"]
        if not completed:
            return None
        return mean_vector([record.embedding for record in completed])
