"""金融数据 owner 用户端只读 API（FIN-S3，设计 §6）。

认证：Owner JWT。主人在 WebUI `/apps/finance` 看行情/K线/资金流/财务/宏观/龙虎榜。
**WebUI 经 daemon `/api/v1/finance/*` 薄代理调用本面**（铁律：WebUI 不直连云端、不直连数据服务）。

定位：与 Agent 工具面（`hasn.finance.*`）**共用同一 `finance_provider`**——单一耦合点（设计 §5）。
本面无 DB、无 per-owner 状态、无脱敏（金融行情是公共数据）；身份取自 Owner JWT 仅用于鉴权。

响应：一律统一信封（`ResponseModel` + `response_base.success`），data 即数据服务的规范化信封
（`{ok, source, rows, columns, ...}` 或 `{ok:false, error, message}`）。**上游业务失败（ok:false）
也回 HTTP 200 + success 信封**——传输成功、业务失败在 data.ok 里，零 fake，webui 据 data.ok 显诚实错误态。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from backend.app.hasn_finance.provider import finance_provider
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth

router = APIRouter()


def _params(**kwargs: Any) -> dict[str, Any]:
    """组装入参：丢弃 None（让数据服务用其默认值），保留显式传入的空串/0。"""
    return {k: v for k, v in kwargs.items() if v is not None}


# ============================ 健康 / 发现 ============================


@router.get('/healthz', summary='[Owner] 数据服务探活', dependencies=[DependsJwtAuth])
async def finance_healthz() -> ResponseModel:
    """探活 finance-data-service（看板诊断；未配置/不可达回诚实 ok:false）。"""
    return response_base.success(data=await finance_provider.healthz())


# ============================ A 股 ============================


@router.get('/stock/quote-history', summary='[Owner] A股历史K线', dependencies=[DependsJwtAuth])
async def stock_quote_history(
    symbol: str = Query(..., min_length=1, description='A股代码，如 600519'),
    period: str | None = Query(default=None, description='daily/weekly/monthly'),
    start_date: str | None = Query(default=None, description='起始日 YYYYMMDD'),
    end_date: str | None = Query(default=None, description='结束日 YYYYMMDD'),
    adjust: str | None = Query(default=None, description='复权 qfq/hfq/空'),
    limit: int | None = Query(default=None, ge=1, le=300),
) -> ResponseModel:
    data = await finance_provider.query(
        'stock.quote_history',
        _params(symbol=symbol, period=period, start_date=start_date, end_date=end_date, adjust=adjust, limit=limit),
    )
    return response_base.success(data=data)


@router.get('/stock/realtime', summary='[Owner] A股实时行情', dependencies=[DependsJwtAuth])
async def stock_realtime(
    symbols: str = Query(..., min_length=1, description='代码逗号分隔，如 600519,000001'),
    limit: int | None = Query(default=None, ge=1, le=300),
) -> ResponseModel:
    data = await finance_provider.query('stock.realtime', _params(symbols=symbols, limit=limit))
    return response_base.success(data=data)


@router.get('/stock/info', summary='[Owner] 个股基本面/简介', dependencies=[DependsJwtAuth])
async def stock_info(symbol: str = Query(..., min_length=1, description='A股代码')) -> ResponseModel:
    data = await finance_provider.query('stock.info', _params(symbol=symbol))
    return response_base.success(data=data)


@router.get('/stock/fund-flow', summary='[Owner] 个股资金流向', dependencies=[DependsJwtAuth])
async def stock_fund_flow(
    symbol: str = Query(..., min_length=1, description='A股代码'),
    market: str | None = Query(default=None, description='sh/sz/bj'),
    limit: int | None = Query(default=None, ge=1, le=300),
) -> ResponseModel:
    data = await finance_provider.query('stock.fund_flow', _params(symbol=symbol, market=market, limit=limit))
    return response_base.success(data=data)


@router.get('/stock/billboard', summary='[Owner] 龙虎榜', dependencies=[DependsJwtAuth])
async def stock_billboard(
    start_date: str = Query(..., min_length=1, description='起始日 YYYYMMDD'),
    end_date: str = Query(..., min_length=1, description='结束日 YYYYMMDD'),
    limit: int | None = Query(default=None, ge=1, le=300),
) -> ResponseModel:
    data = await finance_provider.query(
        'stock.billboard', _params(start_date=start_date, end_date=end_date, limit=limit)
    )
    return response_base.success(data=data)


@router.get('/stock/financial', summary='[Owner] 财务摘要', dependencies=[DependsJwtAuth])
async def stock_financial(
    symbol: str = Query(..., min_length=1, description='A股代码'),
    limit: int | None = Query(default=None, ge=1, le=300),
) -> ResponseModel:
    data = await finance_provider.query('stock.financial', _params(symbol=symbol, limit=limit))
    return response_base.success(data=data)


# ============================ 港股 / 美股 / 指数 ============================


@router.get('/hk/quote-history', summary='[Owner] 港股历史K线', dependencies=[DependsJwtAuth])
async def hk_quote_history(
    symbol: str = Query(..., min_length=1, description='港股代码 5 位，如 00700'),
    period: str | None = Query(default=None),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    adjust: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=300),
) -> ResponseModel:
    data = await finance_provider.query(
        'hk.quote_history',
        _params(symbol=symbol, period=period, start_date=start_date, end_date=end_date, adjust=adjust, limit=limit),
    )
    return response_base.success(data=data)


@router.get('/us/quote-history', summary='[Owner] 美股历史K线', dependencies=[DependsJwtAuth])
async def us_quote_history(
    symbol: str = Query(..., min_length=1, description='美股代码，如 105.AAPL'),
    period: str | None = Query(default=None),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    adjust: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=300),
) -> ResponseModel:
    data = await finance_provider.query(
        'us.quote_history',
        _params(symbol=symbol, period=period, start_date=start_date, end_date=end_date, adjust=adjust, limit=limit),
    )
    return response_base.success(data=data)


@router.get('/index/quote-history', summary='[Owner] 指数历史K线', dependencies=[DependsJwtAuth])
async def index_quote_history(
    symbol: str = Query(..., min_length=1, description='指数代码，如 000001'),
    period: str | None = Query(default=None),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=300),
) -> ResponseModel:
    data = await finance_provider.query(
        'index.quote_history',
        _params(symbol=symbol, period=period, start_date=start_date, end_date=end_date, limit=limit),
    )
    return response_base.success(data=data)


# ============================ 基金 / 期货 / 债券 ============================


@router.get('/fund/nav-history', summary='[Owner] 基金历史净值', dependencies=[DependsJwtAuth])
async def fund_nav_history(
    symbol: str = Query(..., min_length=1, description='基金代码'),
    indicator: str | None = Query(default=None, description='默认 单位净值走势'),
    limit: int | None = Query(default=None, ge=1, le=300),
) -> ResponseModel:
    data = await finance_provider.query('fund.nav_history', _params(symbol=symbol, indicator=indicator, limit=limit))
    return response_base.success(data=data)


@router.get('/fund/position', summary='[Owner] 基金持仓', dependencies=[DependsJwtAuth])
async def fund_position(
    symbol: str = Query(..., min_length=1, description='基金代码'),
    date: str | None = Query(default=None, description='报告年份，如 2024'),
    limit: int | None = Query(default=None, ge=1, le=300),
) -> ResponseModel:
    data = await finance_provider.query('fund.position', _params(symbol=symbol, date=date, limit=limit))
    return response_base.success(data=data)


@router.get('/futures/quote-history', summary='[Owner] 期货历史行情', dependencies=[DependsJwtAuth])
async def futures_quote_history(
    symbol: str = Query(..., min_length=1, description='期货合约代码，如 V2501'),
    limit: int | None = Query(default=None, ge=1, le=300),
) -> ResponseModel:
    data = await finance_provider.query('futures.quote_history', _params(symbol=symbol, limit=limit))
    return response_base.success(data=data)


@router.get('/bond/quote-history', summary='[Owner] 债券历史行情', dependencies=[DependsJwtAuth])
async def bond_quote_history(
    symbol: str = Query(..., min_length=1, description='债券代码，如 sh010107'),
    limit: int | None = Query(default=None, ge=1, le=300),
) -> ResponseModel:
    data = await finance_provider.query('bond.quote_history', _params(symbol=symbol, limit=limit))
    return response_base.success(data=data)


# ============================ 宏观 ============================


@router.get('/macro/indicator', summary='[Owner] 宏观指标', dependencies=[DependsJwtAuth])
async def macro_indicator(
    indicator: str = Query(default='cpi', description='cpi/ppi/gdp/pmi'),
    limit: int | None = Query(default=None, ge=1, le=300),
) -> ResponseModel:
    data = await finance_provider.query('macro.indicator', _params(indicator=indicator, limit=limit))
    return response_base.success(data=data)
