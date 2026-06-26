"""行业标准化打标（doc08 §4.3；阶段二 2.2）。

原始行业自由文本（"LED显示屏" / "LED屏" / "led大屏"）→ 标准行业 code（"led_display"），
使公共池按 industry code 检索时同类线索能聚到一起（否则字面不同分不到一类，查池命不中）。

两级：① 规则匹配（纯函数 ``match_industry_rule``：原始行业/公司名/简介命中字典 name 或 alias，
最长匹配优先避免短词误命中）；② LLM 兜底（规则不中且配了 new-api 网关时，让模型从字典里选一个
code）。都不中 → None（调用方保留原始文本，不强行归类）。真实查 ``industry_tag`` 字典表，零 mock。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa

from backend.app.hasn_growth.model import IndustryTag
from backend.common.log import log

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def match_industry_rule(
    tags: list[dict],
    *,
    raw_industry: str | None = None,
    company_name: str | None = None,
    description: str | None = None,
) -> str | None:
    """规则匹配：文本含某 tag 的 name/alias → 该 code（最长匹配优先，避免短词误命中）。纯函数便于单测。

    ``tags`` 形如 ``[{'code','name','aliases':[...]}, ...]``。原始行业、公司名、简介拼成 haystack，
    取命中词最长的 tag（如「LED显示屏」优先于「led」），都不中返回 None。
    """
    parts = [p for p in (raw_industry, company_name, description) if p and p.strip()]
    if not parts:
        return None
    haystack = ' '.join(parts).lower()
    best_len = 0
    best_code: str | None = None
    for tag in tags:
        terms = [tag.get('name') or '', *(tag.get('aliases') or [])]
        for term in terms:
            needle = str(term).lower().strip()
            if needle and needle in haystack and len(needle) > best_len:
                best_len = len(needle)
                best_code = tag['code']
    return best_code


class IndustryTaggingService:
    """把原始行业文本归一到标准 industry code（规则 + LLM 兜底）。按 db 实例缓存字典。"""

    def __init__(self, db: AsyncSession, *, enable_llm: bool = True) -> None:
        self.db = db
        self.enable_llm = enable_llm
        self._tags: list[dict] | None = None

    async def load_tags(self) -> list[dict]:
        """加载启用的行业字典（实例级缓存，一次 job 内多条线索复用）。"""
        if self._tags is None:
            rows = (
                (await self.db.execute(sa.select(IndustryTag).where(IndustryTag.enabled.is_(True)))).scalars().all()
            )
            self._tags = [{'code': r.code, 'name': r.name, 'aliases': list(r.aliases or [])} for r in rows]
        return self._tags

    async def normalize(
        self,
        *,
        raw_industry: str | None = None,
        company_name: str | None = None,
        description: str | None = None,
    ) -> str | None:
        """原始行业文本 → 标准 code；规则优先，不中走 LLM 兜底，再不中返回 None（保留原始）。"""
        tags = await self.load_tags()
        if not tags:
            return None
        code = match_industry_rule(
            tags, raw_industry=raw_industry, company_name=company_name, description=description
        )
        if code:
            return code
        if self.enable_llm:
            return await self._llm_classify(tags, raw_industry, company_name, description)
        return None

    async def _llm_classify(
        self,
        tags: list[dict],
        raw_industry: str | None,
        company_name: str | None,
        description: str | None,
    ) -> str | None:
        """规则不中 → 让 new-api 网关从字典里选一个 code（无网关/失败/不中返回 None，保留原始文本）。"""
        from backend.core.conf import settings

        base_url = (settings.LLM_API_BASE_URL or '').strip()
        api_key = (settings.LLM_API_KEY or '').strip()
        text = ' / '.join(p for p in (raw_industry, company_name, description) if p)
        if not base_url or not api_key or not text.strip():
            return None
        model = (settings.GROWTH_LLM_MODEL or 'agnes-2.0-flash').strip()
        codes = {t['code'] for t in tags}
        catalog = '\n'.join(f'{t["code"]}: {t["name"]}' for t in tags)
        prompt = (
            '你是行业分类助手。从下面的行业字典里选出最匹配的一个行业 code；若都不匹配，只回 none。'
            f'只回 code 或 none，不要解释。\n\n行业字典：\n{catalog}\n\n待分类文本：{text}'
        )
        payload = {
            'model': model,
            'messages': [{'role': 'user', 'content': prompt}],
            'temperature': 0,
            'max_tokens': 20,
            'stream': False,
        }
        headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
        try:
            import httpx

            async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
                resp = await client.post(
                    f'{base_url.rstrip("/")}/chat/completions', json=payload, headers=headers
                )
                resp.raise_for_status()
                body = resp.json()
            answer = ((body.get('choices') or [{}])[0].get('message', {}).get('content') or '').strip().lower()
        except Exception as exc:
            log.warning(f'[IndustryTagging] LLM 分类失败，保留原始行业: {exc!r}')
            return None
        answer = answer.strip('`').strip()
        return answer if answer in codes else None
