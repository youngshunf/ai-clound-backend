"""平台 key（system-origin）用量归因 / 配额 / 限流（P7，事实源 10 §7.2）。

平台付费凭据对全体 owner 共享 → 须按 `(caller_owner, caller_agent, mcp_id, tool)` 记账，
设 per-owner 每日子配额 + 每分钟限流，超额抛 `MCP_9216 QUOTA_EXCEEDED`，
不静默成功、不透传第三方原始限流错误；成本摊到调用方 owner（落账本）。

owner-origin（owner 自带 key）成本归 owner 自己，**不计平台配额**（仍记账供审计）。
"""

from __future__ import annotations

import logging

from datetime import datetime, timezone

from sqlalchemy import func, select

from backend.app.external_mcp.model import ExternalMcpUsage
from backend.app.mcp.errors import McpErrorCode, McpToolError
from backend.database.db import async_db_session
from backend.database.redis import redis_client

logger = logging.getLogger(__name__)


def _utc_day() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _utc_minute() -> str:
    return datetime.now(timezone.utc).strftime('%Y%m%d%H%M')


class QuotaService:
    """system-origin 平台 key 的配额 / 限流 / 记账。"""

    async def _daily_count(self, *, mcp_id: str, owner_hasn_id: str) -> int:
        """当日（UTC）该 owner 对该 server 的成功调用计数（落账本聚合）。"""
        async with async_db_session() as db:
            count = (
                await db.execute(
                    select(func.count())
                    .select_from(ExternalMcpUsage)
                    .where(
                        ExternalMcpUsage.mcp_id == mcp_id,
                        ExternalMcpUsage.caller_owner_hasn_id == owner_hasn_id,
                        ExternalMcpUsage.day_bucket == datetime.now(timezone.utc).date(),
                        ExternalMcpUsage.success.is_(True),
                    )
                )
            ).scalar_one()
        return int(count or 0)

    async def enforce(
        self,
        *,
        mcp_id: str,
        origin: str,
        owner_hasn_id: str,
        per_owner_daily_quota: int,
        rate_limit_per_min: int,
    ) -> None:
        """调用前置闸：仅对 system-origin 执行配额/限流。超额抛 MCP_9216。

        owner/marketplace-origin 自带凭据 → 不计平台配额（直接放行）。
        """
        if origin != 'system':
            return

        # 每分钟限流（Redis 固定窗口）。
        if rate_limit_per_min and rate_limit_per_min > 0:
            key = f'extmcp:rl:{mcp_id}:{owner_hasn_id}:{_utc_minute()}'
            try:
                current = await redis_client.incr(key)
                if current == 1:
                    await redis_client.expire(key, 70)
            except Exception:
                logger.warning('external_mcp 限流计数失败（Redis），放行', exc_info=True)
                current = 0
            if current and current > rate_limit_per_min:
                raise McpToolError(
                    McpErrorCode.QUOTA_EXCEEDED,
                    f'平台 MCP 调用超过每分钟限流（{rate_limit_per_min}/min），请稍后重试',
                )

        # 每日配额（账本聚合）。
        if per_owner_daily_quota and per_owner_daily_quota > 0:
            used = await self._daily_count(mcp_id=mcp_id, owner_hasn_id=owner_hasn_id)
            if used >= per_owner_daily_quota:
                raise McpToolError(
                    McpErrorCode.QUOTA_EXCEEDED,
                    f'平台 MCP 今日配额已用尽（{per_owner_daily_quota}/日），请明日再试或联系管理员',
                )

    async def record(
        self,
        *,
        mcp_id: str,
        origin: str,
        caller_owner_hasn_id: str,
        caller_agent_hasn_id: str,
        tool_name: str,
        trace_id: str | None,
        success: bool,
        cost_units: int = 1,
    ) -> None:
        """记账：每次经平台/自带凭据的调用都落账本（用量归因 + 计费摊调用方）。"""
        async with async_db_session.begin() as db:
            db.add(
                ExternalMcpUsage(
                    mcp_id=mcp_id,
                    caller_owner_hasn_id=caller_owner_hasn_id,
                    caller_agent_hasn_id=caller_agent_hasn_id,
                    tool_name=tool_name,
                    origin=origin,
                    trace_id=trace_id,
                    success=success,
                    cost_units=cost_units,
                    day_bucket=datetime.now(timezone.utc).date(),
                )
            )

    async def usage_summary(self, *, owner_hasn_id: str, mcp_id: str | None = None) -> dict:
        """owner 维度用量摘要（管理面展示，当日 + 累计）。"""
        async with async_db_session() as db:
            today_stmt = (
                select(func.count())
                .select_from(ExternalMcpUsage)
                .where(
                    ExternalMcpUsage.caller_owner_hasn_id == owner_hasn_id,
                    ExternalMcpUsage.day_bucket == datetime.now(timezone.utc).date(),
                )
            )
            total_stmt = (
                select(func.count())
                .select_from(ExternalMcpUsage)
                .where(ExternalMcpUsage.caller_owner_hasn_id == owner_hasn_id)
            )
            if mcp_id:
                today_stmt = today_stmt.where(ExternalMcpUsage.mcp_id == mcp_id)
                total_stmt = total_stmt.where(ExternalMcpUsage.mcp_id == mcp_id)
            today = (await db.execute(today_stmt)).scalar_one()
            total = (await db.execute(total_stmt)).scalar_one()
        return {'today': int(today or 0), 'total': int(total or 0), '_day': _utc_day()}


quota_service = QuotaService()
