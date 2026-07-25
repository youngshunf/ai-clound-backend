"""获客计量上报服务（设计 07 §10.1/§759 G7：采集按量计积分）。

**获客只报量不自建账本**：采集 job 完成时按 `firecrawl_success_count` 把用量上报给平台计费模块
（doc94 D1 之后，积分权威在 NewAPI，云端不再有扣分原语）。本服务不维护任何
获客侧积分台账。单价开发期默认 0（free，G7「开发期先 free 便于联调，上线前切」），由环境变量
`GROWTH_CRAWL_CREDIT_UNIT` 配置，真正定价随计费模块定价体系（运营项，§810）。

best-effort：计量失败/积分不足只告警，**绝不阻断已完成的采集**（采集纯按量无订阅门槛，§G7）。
"""

from __future__ import annotations

import os

from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.log import log

# 采集单条成功的积分单价（开发期 0=free；上线由计费模块定价，运营改环境变量不动代码）。
_CRAWL_UNIT_ENV = 'GROWTH_CRAWL_CREDIT_UNIT'
_REFERENCE_TYPE = 'growth_crawl'


def _crawl_unit() -> Decimal:
    try:
        return Decimal(os.environ.get(_CRAWL_UNIT_ENV, '0'))
    except (InvalidOperation, TypeError):
        return Decimal(0)


class GrowthMeteringService:
    """采集计量上报（薄钩子，接平台计费模块；获客侧无台账）。"""

    @staticmethod
    async def report_crawl_usage(
        db: AsyncSession, *, user_id: int | None, job_id: int, success_count: int
    ) -> dict[str, Any]:
        """采集 job 完成时上报成功条数 → 平台计扣。返回 {reported, credits, count, error?}。"""
        unit = _crawl_unit()
        credits = unit * Decimal(max(success_count, 0))
        if not user_id or success_count <= 0 or credits <= 0:
            # free（unit=0）或无量 → 不上报（不写任何台账，符合「只报量」）。
            return {'reported': False, 'credits': float(credits), 'count': max(success_count, 0)}
        # doc94 D1：云端的扣分原语已删除——积分的唯一权威是 NewAPI，云端不再有
        # 任何直接改余额的入口。获客采集目前没有接 NewAPI 计量接缝，因此这里
        # **只记录应计量，不扣费**，并把这件事显式说出来，而不是假装扣成功了。
        log.warning(
            f'[GrowthMetering] 采集计量暂未接 NewAPI 计费接缝，仅记录不扣费: '
            f'user_id={user_id}, job_id={job_id}, credits={credits}, count={success_count}'
        )
        return {
            'reported': False,
            'credits': float(credits),
            'count': success_count,
            'error': 'metering_seam_not_wired',
        }


growth_metering_service = GrowthMeteringService()
