"""公共池检索编排（doc08 §4 · 阶段二 2.1「先查池 → 缺口补爬」）。

用户「**请求线索**」而非「发起采集」——平台黑盒地先查公共池命中（`lead_contact` 按
industry/region/keyword/city 维度检索，**不限 lead_scope = 公共池语义**），命中 M 条：
- M ≥ N → 直接交付 N 条（**零采集成本**）；
- M < N → 交付 M 条 + 后台触发**补爬 job**（`max_results = N − M`，**复用既有 run_job 采集栈**）
  回流公共池补足。

行业两侧同口径：请求行业先经 `IndustryTaggingService` 归一到标准 code 再查池（2.2 入库已归一）。
额度闸 `_check_quota` 是阶段二 no-op 接缝，阶段四 4.2 填实（免费额度 + 超额走支付）。零 mock，真实查 PG。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from backend.app.hasn_growth.model import LeadContact
from backend.app.hasn_growth.schema.business import CreateLeadJobParam
from backend.app.hasn_growth.service.business_service import lead_automation_business_service

# 复用 funnel 的线索序列化 + PII 脱敏（单一实现，避免脱敏逻辑漂移——安全敏感不重复造）。
from backend.app.hasn_growth.service.funnel_service import _lead_to_dict
from backend.app.hasn_growth.service.industry_tagging_service import IndustryTaggingService
from backend.common.log import log

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_POOL_QUERY_HARD_CAP = 200  # query_pool 单次最多返回候选，护 DB


class LeadPoolQueryService:
    """公共池查询 + 「先查池 → 缺口补爬」编排。"""

    async def query_pool(
        self,
        db: AsyncSession,
        *,
        industry: str | None = None,
        region: str | None = None,
        keyword: str | None = None,
        city: str | None = None,
        limit: int = 20,
    ) -> list[LeadContact]:
        """按公共池维度查 lead_contact（**不限 lead_scope = 公共池语义**），按置信度倒序返回 limit 条。

        industry 期望传标准 code（2.2 已把入库行业归一到 code），ilike 既命中 code 也兜底旧自由文本；
        region/city/keyword 各维度 ilike 模糊匹配。排除 rejected。
        """
        stmt = sa.select(LeadContact).where(LeadContact.status != 'rejected')
        if industry and industry.strip():
            stmt = stmt.where(LeadContact.industry.ilike(f'%{industry.strip()}%'))
        if region and region.strip():
            stmt = stmt.where(LeadContact.region.ilike(f'%{region.strip()}%'))
        if city and city.strip():
            stmt = stmt.where(LeadContact.city.ilike(f'%{city.strip()}%'))
        if keyword and keyword.strip():
            like = f'%{keyword.strip()}%'
            stmt = stmt.where(
                sa.or_(
                    LeadContact.company_name.ilike(like),
                    LeadContact.industry.ilike(like),
                    LeadContact.keyword.ilike(like),
                    LeadContact.contact_name.ilike(like),
                )
            )
        stmt = stmt.order_by(LeadContact.confidence_score.desc().nullslast()).limit(min(limit, _POOL_QUERY_HARD_CAP))
        return list((await db.execute(stmt)).scalars().all())

    async def _check_quota(self, db: AsyncSession, *, user_id: int, requested: int) -> int:
        """额度闸接缝（阶段二 no-op：放行全部请求量；阶段四 4.2 填实免费额度 + 超额走支付）。"""
        return requested

    @staticmethod
    def _backfill_keyword(
        *, keyword: str | None, industry: str | None, region: str | None, city: str | None
    ) -> str:
        """补爬 job 的搜索词：拼请求的自由文本维度（行业用**原文**不用 code，便于搜索引擎命中）。"""
        parts = [p.strip() for p in (keyword, industry, region, city) if p and p.strip()]
        return ' '.join(parts)

    async def request_leads(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        limit: int,
        industry: str | None = None,
        region: str | None = None,
        keyword: str | None = None,
        city: str | None = None,
        reveal_pii: bool = False,
    ) -> dict[str, Any]:
        """先查池 → 缺口补爬编排：M≥N 直接交付；M<N 交付 M + 后台补爬 N−M 回流公共池。

        返回 ``{delivered, from_pool, backfill_job_id, requested, leads}``；``backfill_job_id``
        非空时，**调用方（handler）须挂 after_commit 钩子入队该 job**（提交前 worker 读不到未提交 job·
        对齐 collect.start 时序，见 [[project_growth_collect_execution_chain_landed]]）。
        """
        n = await self._check_quota(db, user_id=user_id, requested=max(1, int(limit)))
        # 行业归一到 code 再查池（2.2 入库已归一·两侧同口径才命中）；归一不中保留原文兜底 ilike。
        industry_match = industry
        if industry and industry.strip():
            code = await IndustryTaggingService(db).normalize(raw_industry=industry)
            industry_match = code or industry
        hits = await self.query_pool(db, industry=industry_match, region=region, keyword=keyword, city=city, limit=n)
        delivered = hits[:n]
        leads = [_lead_to_dict(r, reveal_pii=reveal_pii) for r in delivered]

        backfill_job_id: int | None = None
        gap = n - len(delivered)
        if gap > 0:
            # 缺口补爬：公共池采集 job（回流公共池），max_results=N−M 精确补足；无可用搜索词则不补。
            search = self._backfill_keyword(keyword=keyword, industry=industry, region=region, city=city)
            if search:
                job = await lead_automation_business_service.create_job(
                    db,
                    CreateLeadJobParam(
                        keyword=search,
                        lead_scope='public',
                        max_results=gap,
                        request_config={'region': region, 'city': city, 'industry': industry},
                    ),
                )
                backfill_job_id = int(job['id'])
                log.info(f'[LeadPool] 查池命中 {len(delivered)}/{n}，缺口 {gap} 触发补爬 job={backfill_job_id}')
        return {
            'delivered': len(delivered),
            'from_pool': len(delivered),
            'backfill_job_id': backfill_job_id,
            'requested': n,
            'leads': leads,
        }


lead_pool_query_service = LeadPoolQueryService()
