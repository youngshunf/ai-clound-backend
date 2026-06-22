"""金融数据 AI-Native 工具 handler（云端 gateway_internal，设计 §4/§5）。

形态（与 growth/creator 一致）：金融数据是**纯云端只读数据应用**，14 个 `hasn.finance.*` 工具一律走
云端 MCP——`ai_native_runtime_gateway` 在 `transport=gateway_internal` 时进程内直调本文件 handler，
handler 再经 `finance_provider`（httpx）调独立部署的 finance-data-service（唯一接触 akshare 的地方）。

每个 handler 签名 `(db, agent: AgentTokenPayload, input_payload: dict) -> dict`：
- 全只读（finance:read），无身份相关裁剪——金融行情是公共数据，不分主人/不脱敏；
- 返回 finance-data-service 的**规范化信封**裸 data（ok/source/rows/...），gateway 负责审计；
- 上游失败由 provider 归一成诚实 ok:false（零 fake，绝不透 traceback）。

`db`/`agent` 形参为契约一致而保留（本应用无 DB、无 per-owner 状态）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.app.hasn_finance.provider import finance_provider

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.common.dataclasses import AgentTokenPayload


async def handle_stock_quote_history(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    return await finance_provider.query('stock.quote_history', input_payload)


async def handle_stock_realtime(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    return await finance_provider.query('stock.realtime', input_payload)


async def handle_stock_info(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    return await finance_provider.query('stock.info', input_payload)


async def handle_stock_fund_flow(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    return await finance_provider.query('stock.fund_flow', input_payload)


async def handle_stock_billboard(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    return await finance_provider.query('stock.billboard', input_payload)


async def handle_stock_financial(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    return await finance_provider.query('stock.financial', input_payload)


async def handle_hk_quote_history(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    return await finance_provider.query('hk.quote_history', input_payload)


async def handle_us_quote_history(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    return await finance_provider.query('us.quote_history', input_payload)


async def handle_index_quote_history(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    return await finance_provider.query('index.quote_history', input_payload)


async def handle_fund_nav_history(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    return await finance_provider.query('fund.nav_history', input_payload)


async def handle_fund_position(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    return await finance_provider.query('fund.position', input_payload)


async def handle_futures_quote_history(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    return await finance_provider.query('futures.quote_history', input_payload)


async def handle_macro_indicator(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    return await finance_provider.query('macro.indicator', input_payload)


async def handle_bond_quote_history(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    return await finance_provider.query('bond.quote_history', input_payload)
