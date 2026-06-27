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

from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.app.hasn_growth.model import LeadContact, LeadQuota
from backend.app.hasn_growth.schema.business import CreateLeadJobParam
from backend.app.hasn_growth.service.business_service import lead_automation_business_service

# 复用 funnel 的线索序列化 + PII 脱敏（单一实现，避免脱敏逻辑漂移——安全敏感不重复造）。
from backend.app.hasn_growth.service.funnel_service import _lead_to_dict
from backend.app.hasn_growth.service.industry_tagging_service import IndustryTaggingService
from backend.common.log import log
from backend.core.conf import settings
from backend.utils.timezone import timezone

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

    @staticmethod
    def _free_per_month() -> int:
        """每用户每月免费可领取线索条数（doc93 §4.2·可配·运营改环境变量不动代码）。"""
        return max(0, int(settings.GROWTH_FREE_LEADS_PER_MONTH))

    @staticmethod
    def _current_period() -> str:
        """免费额度归属月 key（YYYY-MM·时区感知·跨月即归零 free_used）。"""
        return timezone.now().strftime('%Y-%m')

    async def _quota_snapshot(self, db: AsyncSession, *, user_id: int) -> dict[str, int]:
        """只读额度快照（**不建行**）：{free_per_month, free_used, free_remaining, purchased_balance}。

        免费已用按当前 period 计——账本 period_key 与当前月不一致视为本月未用（0），保持只读。
        """
        free_per_month = self._free_per_month()
        period = self._current_period()
        row = (
            await db.execute(sa.select(LeadQuota).where(LeadQuota.user_id == user_id))
        ).scalar_one_or_none()
        free_used = row.free_used if (row and row.period_key == period) else 0
        purchased = row.purchased_balance if row else 0
        free_remaining = max(0, free_per_month - free_used)
        return {
            'free_per_month': free_per_month,
            'free_used': free_used,
            'free_remaining': free_remaining,
            'purchased_balance': purchased,
        }

    async def _get_or_init_quota_row(self, db: AsyncSession, *, user_id: int, lock: bool = False) -> LeadQuota:
        """取/建用户额度账本行（UPSERT 幂等·并发安全）并做跨月免费额度归零。

        先 `INSERT ... ON CONFLICT(user_id) DO NOTHING` 确保行存在（避免并发双插），再
        `SELECT [FOR UPDATE]` 读回；若 period_key 落后于当前月则归零 free_used（免费额度按月重置）。
        仅 flush，**不 commit**（事务由调用方/handler 掌握）。
        """
        period = self._current_period()
        await db.execute(
            pg_insert(LeadQuota)
            .values(user_id=user_id, period_key=period)
            .on_conflict_do_nothing(index_elements=['user_id'])
        )
        stmt = sa.select(LeadQuota).where(LeadQuota.user_id == user_id)
        if lock:
            stmt = stmt.with_for_update()
        row = (await db.execute(stmt)).scalar_one()
        if row.period_key != period:
            row.period_key = period
            row.free_used = 0
        return row

    async def _check_quota(self, db: AsyncSession, *, user_id: int, requested: int) -> int:
        """额度闸（doc93 §4.2 填实 2.1 接缝）：放行量 = min(请求量, 本月免费剩余 + 购买余额)。

        免费额度内放行；超额部分由 `request_leads` 返回 `shortfall` 引导**支付购买**（不走积分）。
        只读快照（不建行）：纯检查不污染账本，真正扣减在交付后 `_consume_quota`。
        """
        snap = await self._quota_snapshot(db, user_id=user_id)
        available = snap['free_remaining'] + snap['purchased_balance']
        return min(int(requested), max(0, available))

    async def _consume_quota(self, db: AsyncSession, *, user_id: int, count: int) -> dict[str, int]:
        """交付后扣减额度：**先扣免费、再扣购买**。返回 {from_free, from_purchased}。

        count 应 ≤ `_check_quota` 放行量（仅按**实际交付**的池命中数扣，缺口补爬回流池的不在此扣）。
        行级锁防并发双扣；仅 flush，事务由调用方掌握。
        """
        if count <= 0:
            return {'from_free': 0, 'from_purchased': 0}
        row = await self._get_or_init_quota_row(db, user_id=user_id, lock=True)
        free_remaining = max(0, self._free_per_month() - row.free_used)
        from_free = min(count, free_remaining)
        from_purchased = min(count - from_free, row.purchased_balance)
        row.free_used += from_free
        row.purchased_balance -= from_purchased
        row.consumed_total += from_free + from_purchased
        row.updated_time = timezone.now()
        await db.flush()
        return {'from_free': from_free, 'from_purchased': from_purchased}

    async def grant_purchased_leads(self, db: AsyncSession, *, user_id: int, count: int) -> int:
        """支付到账：增加可领取线索余额（永不过期·doc93 §4.2 line 165 不走积分）。返回新余额。

        由 `lead_pack` 支付成功回调 `handle_lead_pack_paid` 调用。行级锁 + flush；事务由回调的
        `async_db_session.begin()` 提交。
        """
        if count <= 0:
            return 0
        row = await self._get_or_init_quota_row(db, user_id=user_id, lock=True)
        row.purchased_balance += int(count)
        row.purchased_total += int(count)
        row.updated_time = timezone.now()
        await db.flush()
        return row.purchased_balance

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
        requested = max(1, int(limit))
        # 额度闸（doc93 §4.2）：放行量 = min(请求量, 免费剩余 + 购买余额)；超额引导支付购买。
        n = await self._check_quota(db, user_id=user_id, requested=requested)
        # 行业归一到 code 再查池（2.2 入库已归一·两侧同口径才命中）；归一不中保留原文兜底 ilike。
        industry_match = industry
        if industry and industry.strip():
            code = await IndustryTaggingService(db).normalize(raw_industry=industry)
            industry_match = code or industry
        hits = (
            await self.query_pool(db, industry=industry_match, region=region, keyword=keyword, city=city, limit=n)
            if n > 0
            else []
        )
        delivered = hits[:n]
        leads = [_lead_to_dict(r, reveal_pii=reveal_pii) for r in delivered]

        # 交付后扣减额度（仅按**实际交付**的池命中扣·先免费后购买）；缺口补爬回流池的不在此扣。
        await self._consume_quota(db, user_id=user_id, count=len(delivered))

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

        snap = await self._quota_snapshot(db, user_id=user_id)
        # shortfall：请求量中被额度闸拦下、需**支付购买**才能领取的条数（doc93 §4.2）。
        shortfall = max(0, requested - n)
        return {
            'delivered': len(delivered),
            'from_pool': len(delivered),
            'backfill_job_id': backfill_job_id,
            'requested': requested,
            'allowed': n,
            'shortfall': shortfall,
            'leads': leads,
            'quota': {
                'free_per_month': snap['free_per_month'],
                'free_remaining': snap['free_remaining'],
                'purchased_balance': snap['purchased_balance'],
                'unit_price_fen': max(0, int(settings.GROWTH_LEAD_UNIT_PRICE_FEN)),
            },
        }


lead_pool_query_service = LeadPoolQueryService()
