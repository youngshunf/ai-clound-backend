"""获客计量上报服务（设计 07 §10.1/§759 G7：采集按量计积分）。

**获客只报量不自建账本**：采集 job 完成时按 `firecrawl_success_count` 把用量上报给平台计费模块
（user_tier `CreditService.deduct_credits`，平台 CreditTransaction 即权威账本）。本服务不维护任何
获客侧积分台账。单价开发期默认 0（free，G7「开发期先 free 便于联调，上线前切」），由环境变量
`GROWTH_CRAWL_CREDIT_UNIT` 配置，真正定价随计费模块定价体系（运营项，§810）。

best-effort：计量失败/积分不足只告警，**绝不阻断已完成的采集**（采集纯按量无订阅门槛，§G7）。
"""

from __future__ import annotations

import os

from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.billing.service.credit_service import InsufficientCreditsError, credit_service
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
        try:
            await credit_service.deduct_credits(
                db,
                user_id=user_id,
                credits=credits,
                reference_id=f'{_REFERENCE_TYPE}:{job_id}',
                reference_type=_REFERENCE_TYPE,
                description=f'获客采集计量 job={job_id} 成功 {success_count} 条',
                extra_data={'job_id': job_id, 'success_count': success_count, 'unit': str(unit)},
                app_code='huanxing',
            )
        except InsufficientCreditsError:
            # 纯按量无订阅门槛：积分不足只告警，不回滚已完成的采集。
            log.warning(f'[GrowthMetering] 积分不足，采集已完成不阻断: user_id={user_id}, job_id={job_id}, credits={credits}')
            return {'reported': False, 'credits': float(credits), 'count': success_count, 'error': 'insufficient_credits'}
        except Exception as exc:
            log.warning(f'[GrowthMetering] 计量上报失败，忽略: user_id={user_id}, job_id={job_id}, error={exc!r}')
            return {'reported': False, 'credits': float(credits), 'count': success_count, 'error': 'report_failed'}
        return {'reported': True, 'credits': float(credits), 'count': success_count}


growth_metering_service = GrowthMeteringService()
