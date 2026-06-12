from __future__ import annotations

from typing import Any


def score_cleaned_lead(cleaned: Any, *, existing_score: int | float = 0, source_count: int = 1) -> int:
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
    if getattr(cleaned, 'extract_mode', None) in {'scrape_json', 'extract'} or getattr(cleaned, 'metadata', {}).get(
        'structured_payload_present'
    ):
        score += 10
    if (getattr(cleaned, 'llm_confidence', None) or 0) >= 0.8:
        score += 10
    if source_count > 1:
        score += min(source_count - 1, 5)
    return int(min(100, max(existing_score, score)))
