"""Lexical / 关键词检索工具函数：分词、关键词提取与简单 BM25 风格打分。"""

import re
from typing import Optional

# 匹配 CJK 统一表意文字范围，用于对无空格中文进行字符级切分。
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


def tokenize_keywords(query: str) -> list[str]:
    """把查询字符串拆成关键词列表。

    对英文/空格分隔的术语保留原词；对连续 CJK 字符再按单字扩展，
    保证无分词器时仍能命中包含这些字符的文档。
    """
    if not query:
        return []

    terms: list[str] = []
    seen: set[str] = set()

    for raw in query.split():
        part = raw.strip().lower()
        if not part or part in seen:
            continue
        seen.add(part)
        terms.append(part)

        # 对中文片段再按字扩展，但不重复添加已作为完整词存在的单字。
        for cjk_match in _CJK_RE.finditer(part):
            for ch in cjk_match.group(0):
                if ch not in seen:
                    seen.add(ch)
                    terms.append(ch)

    return terms


def score_chunk_text(text: str, keywords: list[str]) -> float:
    """基于关键词命中次数计算块的 lexical 得分（简单 saturating TF）。"""
    if not keywords or not text:
        return 0.0

    text_lower = text.lower()
    score = 0.0
    for keyword in keywords:
        kw = keyword.lower()
        tf = text_lower.count(kw)
        if tf:
            # 长词权重更高，高频词做平方根饱和。
            score += len(kw) * (tf**0.5)
    return score


def build_tsquery(keywords: list[str]) -> Optional[str]:
    """把关键词列表构造成 PostgreSQL `to_tsquery` 可接受的 OR 查询串。"""
    if not keywords:
        return None

    def _escape(term: str) -> str:
        # tsquery 内部单引号通过双写转义；其它特殊字符用空格分隔即可。
        return term.replace("'", "''")

    return " | ".join(f"'{_escape(kw)}'" for kw in keywords)
