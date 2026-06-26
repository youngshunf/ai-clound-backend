"""URL 级去重 service（doc08 §4.4；阶段一 ②）。

抓取前用规范化 URL 查 ``crawled_url`` 登记表：近期已成功抓过的 URL 跳过（命中计数++），
防重复烧 firecrawl 成本；抓取后 upsert 登记结果。平台级（不分 user），承载众包线索池「已抓 URL 池」。

被 provider 在抓取循环里调用（filter_unseen 抓前过滤 + register 抓后登记）。真实 DB 操作，零 mock。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import sqlalchemy as sa

from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.app.hasn_growth.model import CrawledUrl
from backend.app.hasn_growth.service.url_normalize import normalize_url

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# 去重窗口：N 天内成功抓过的同一 URL 跳过；超过则允许重抓（站点内容会更新）。
DEFAULT_DEDUP_WINDOW_DAYS = 30


class UrlDedupService:
    """单个采集源（job + source_type）维度的 URL 去重器。"""

    def __init__(
        self,
        db: AsyncSession,
        *,
        job_id: int,
        source_type: str,
        window_days: int = DEFAULT_DEDUP_WINDOW_DAYS,
    ) -> None:
        self.db = db
        self.job_id = job_id
        self.source_type = source_type
        self.window_days = window_days
        self.skipped = 0  # 本次被去重跳过的 URL 数（省成本度量）

    async def filter_unseen(self, urls: list[str]) -> list[str]:
        """过滤掉近期已成功抓过的 URL（命中则 hit_count++），返回应当抓取的原始 URL 列表。

        无法规范化的 URL 不去重（放行让 provider 处理）；失败/无内容/过期的 URL 允许重抓。
        """
        cutoff = datetime.now(UTC) - timedelta(days=self.window_days)
        kept: list[str] = []
        for url in urls:
            normalized = normalize_url(url)
            if normalized is None:
                kept.append(url)
                continue
            _, url_hash, _ = normalized
            row = (
                await self.db.execute(
                    sa.select(CrawledUrl.id, CrawledUrl.last_crawled_at, CrawledUrl.last_outcome).where(
                        CrawledUrl.url_hash == url_hash
                    )
                )
            ).first()
            recently_succeeded = (
                row is not None
                and row.last_outcome == 'succeeded'
                and row.last_crawled_at is not None
                and row.last_crawled_at >= cutoff
            )
            if recently_succeeded:
                await self.db.execute(
                    sa.update(CrawledUrl).where(CrawledUrl.id == row.id).values(hit_count=CrawledUrl.hit_count + 1)
                )
                self.skipped += 1
                continue
            kept.append(url)
        return kept

    async def register(self, url: str, *, outcome: str, lead_yield: int = 0) -> None:
        """抓取后 upsert 登记（按 url_hash 唯一约束 on conflict 累加 crawl_count/lead_yield）。"""
        normalized = normalize_url(url)
        if normalized is None:
            return
        normalized_url, url_hash, domain = normalized
        now = datetime.now(UTC)
        stmt = (
            pg_insert(CrawledUrl)
            .values(
                url_hash=url_hash,
                normalized_url=normalized_url,
                domain=domain,
                source_type=self.source_type,
                crawl_count=1,
                hit_count=0,
                lead_yield=lead_yield,
                last_outcome=outcome,
                last_job_id=self.job_id,
                first_crawled_at=now,
                last_crawled_at=now,
                meta_data={},
            )
            .on_conflict_do_update(
                constraint='uq_growth_crawled_url_hash',
                set_={
                    'crawl_count': CrawledUrl.crawl_count + 1,
                    'lead_yield': CrawledUrl.lead_yield + lead_yield,
                    'last_outcome': outcome,
                    'last_job_id': self.job_id,
                    'last_crawled_at': now,
                    'source_type': sa.func.coalesce(CrawledUrl.source_type, self.source_type),
                },
            )
        )
        await self.db.execute(stmt)

    async def bump_lead_yield(self, url: str, *, delta: int = 1) -> None:
        """新增有效线索后回填该 URL 的产出计数（lead_yield += delta），用于众包池「高产 URL」评估。"""
        normalized = normalize_url(url)
        if normalized is None:
            return
        _, url_hash, _ = normalized
        await self.db.execute(
            sa.update(CrawledUrl)
            .where(CrawledUrl.url_hash == url_hash)
            .values(lead_yield=CrawledUrl.lead_yield + delta)
        )
