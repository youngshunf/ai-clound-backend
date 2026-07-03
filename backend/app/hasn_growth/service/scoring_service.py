from __future__ import annotations

from typing import Any


def score_cleaned_lead(cleaned: Any, *, existing_score: float = 0, source_count: int = 1) -> int:
    score = 0
    if getattr(cleaned, 'email_normalized', None):
        score += 20
    if getattr(cleaned, 'phone_normalized', None):
        score += 20
    if getattr(cleaned, 'company_name', None):
        score += 10
    if getattr(cleaned, 'website', None) or getattr(cleaned, 'domain', None):
        score += 10
    if getattr(cleaned, 'address', None):
        score += 5
    if getattr(cleaned, 'industry', None):
        score += 5
    if getattr(cleaned, 'source_url', None):
        score += 10
    # 结构化提取成功（firecrawl 原生 extract / scrape_json，或方案 A 后端 LLM 提取 llm_backend）加分；
    # llm_backend 当前也会被 metadata.structured_payload_present 兜住，这里显式列入更鲁棒（doc08 §7 ③）。
    if getattr(cleaned, 'extract_mode', None) in {'scrape_json', 'extract', 'llm_backend'} or getattr(
        cleaned, 'metadata', {}
    ).get('structured_payload_present'):
        score += 10
    if (getattr(cleaned, 'llm_confidence', None) or 0) >= 0.8:
        score += 10
    if source_count > 1:
        score += min(source_count - 1, 5)
    return int(min(100, max(existing_score, score)))
