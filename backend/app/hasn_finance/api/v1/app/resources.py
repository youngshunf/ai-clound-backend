"""金融投研 6 类产物 + watchlist 的 owner 端**下行读** list/get（05 §3.2.1）。

与同目录 `sync.py`（上行投影）成对：WebUI 不直接调本面，daemon 的读 handler 在 read-through 时经
owner 通道回源这里，随后**只从本地 store 返回**（云端不可达安静回退本地，离线读不失败）。

owner 只取鉴权上下文（`resolve_owner`），客户端传入的 owner 一律不可信。业务规则（内部幂等元数据
不下发 / tombstone 显式带出 / 过滤白名单 / 单页上限）全在 `finance_read_service`，本文件只做薄路由。

路径对齐 05 §3 的资源路由列：`research-reports` / `strategies` / `backtest-reports` /
`trade-reviews` / `shadow-accounts` / `watch-briefings` / `watchlist`。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query, Request

from backend.app.hasn_finance.service.finance_read_service import finance_read_service
from backend.app.hasn_project.api.v1.app._common import resolve_owner
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession

router = APIRouter()

# 三个公共 query 参数（每个 list 端点都有）——单页上限由 service 兜（_MAX_LIMIT）
_Limit = Annotated[int, Query(ge=1, le=200, description='单页条数')]
_Offset = Annotated[int, Query(ge=0, description='偏移量')]
_IncludeDeleted = Annotated[bool, Query(description='是否带上 tombstone（daemon 同步据它删本地镜像）')]


async def _list(
    db: CurrentSession, request: Request, resource_kind: str, limit: int, offset: int, include_deleted: bool, **filters
) -> ResponseModel:
    """7 个 list 端点的公共实现——owner 解析 + 转 service。"""
    owner_id = await resolve_owner(db, request)
    data = await finance_read_service.list_resources(
        db,
        resource_kind=resource_kind,
        owner_id=owner_id,
        limit=limit,
        offset=offset,
        include_deleted=include_deleted,
        filters=filters,
    )
    return response_base.success(data=data)


async def _get(db: CurrentSession, request: Request, resource_kind: str, pk: int) -> ResponseModel:
    """7 个 get 端点的公共实现——owner 解析 + 转 service（owner 不匹配一律 404）。"""
    owner_id = await resolve_owner(db, request)
    data = await finance_read_service.get_resource(db, resource_kind=resource_kind, owner_id=owner_id, pk=pk)
    return response_base.success(data=data)


@router.get(
    '/research-reports', summary='投研报告列表', dependencies=[DependsJwtAuth], name='finance_app_list_research_reports'
)
async def list_research_reports(
    db: CurrentSession,
    request: Request,
    limit: _Limit = 50,
    offset: _Offset = 0,
    include_deleted: _IncludeDeleted = False,
    symbol: Annotated[str | None, Query(description='标的代码')] = None,
    market: Annotated[str | None, Query(description='市场 cn/hk/us')] = None,
    verdict: Annotated[str | None, Query(description='结论')] = None,
) -> ResponseModel:
    return await _list(
        db,
        request,
        'finance.research_report',
        limit,
        offset,
        include_deleted,
        symbol=symbol,
        market=market,
        verdict=verdict,
    )


@router.get(
    '/research-reports/{pk}',
    summary='投研报告详情',
    dependencies=[DependsJwtAuth],
    name='finance_app_get_research_report',
)
async def get_research_report(db: CurrentSession, request: Request, pk: Annotated[int, Path()]) -> ResponseModel:
    return await _get(db, request, 'finance.research_report', pk)


@router.get('/strategies', summary='策略列表', dependencies=[DependsJwtAuth], name='finance_app_list_strategies')
async def list_strategies(
    db: CurrentSession,
    request: Request,
    limit: _Limit = 50,
    offset: _Offset = 0,
    include_deleted: _IncludeDeleted = False,
    market: Annotated[str | None, Query(description='市场 cn/hk/us')] = None,
    source: Annotated[str | None, Query(description='来源 swarm/manual/default')] = None,
    platform_project_id: Annotated[str | None, Query(description='挂靠的平台项目 id')] = None,
) -> ResponseModel:
    return await _list(
        db,
        request,
        'finance.strategy',
        limit,
        offset,
        include_deleted,
        market=market,
        source=source,
        platform_project_id=platform_project_id,
    )


@router.get(
    '/strategies/{pk}', summary='策略详情（含源码）', dependencies=[DependsJwtAuth], name='finance_app_get_strategy'
)
async def get_strategy(db: CurrentSession, request: Request, pk: Annotated[int, Path()]) -> ResponseModel:
    return await _get(db, request, 'finance.strategy', pk)


@router.get(
    '/backtest-reports', summary='回测报告列表', dependencies=[DependsJwtAuth], name='finance_app_list_backtest_reports'
)
async def list_backtest_reports(
    db: CurrentSession,
    request: Request,
    limit: _Limit = 50,
    offset: _Offset = 0,
    include_deleted: _IncludeDeleted = False,
    strategy_id: Annotated[int | None, Query(description='按策略过滤')] = None,
) -> ResponseModel:
    return await _list(db, request, 'finance.backtest_report', limit, offset, include_deleted, strategy_id=strategy_id)


@router.get(
    '/backtest-reports/{pk}',
    summary='回测报告详情（含净值曲线/逐笔）',
    dependencies=[DependsJwtAuth],
    name='finance_app_get_backtest_report',
)
async def get_backtest_report(db: CurrentSession, request: Request, pk: Annotated[int, Path()]) -> ResponseModel:
    return await _get(db, request, 'finance.backtest_report', pk)


@router.get(
    '/trade-reviews', summary='交易复盘列表', dependencies=[DependsJwtAuth], name='finance_app_list_trade_reviews'
)
async def list_trade_reviews(
    db: CurrentSession,
    request: Request,
    limit: _Limit = 50,
    offset: _Offset = 0,
    include_deleted: _IncludeDeleted = False,
    shadow_account_id: Annotated[int | None, Query(description='按影子账户过滤')] = None,
) -> ResponseModel:
    return await _list(
        db, request, 'finance.trade_review', limit, offset, include_deleted, shadow_account_id=shadow_account_id
    )


@router.get(
    '/trade-reviews/{pk}', summary='交易复盘详情', dependencies=[DependsJwtAuth], name='finance_app_get_trade_review'
)
async def get_trade_review(db: CurrentSession, request: Request, pk: Annotated[int, Path()]) -> ResponseModel:
    return await _get(db, request, 'finance.trade_review', pk)


@router.get(
    '/shadow-accounts', summary='影子账户列表', dependencies=[DependsJwtAuth], name='finance_app_list_shadow_accounts'
)
async def list_shadow_accounts(
    db: CurrentSession,
    request: Request,
    limit: _Limit = 50,
    offset: _Offset = 0,
    include_deleted: _IncludeDeleted = False,
    broker: Annotated[str | None, Query(description='券商')] = None,
    platform_project_id: Annotated[str | None, Query(description='挂靠的平台项目 id')] = None,
) -> ResponseModel:
    return await _list(
        db,
        request,
        'finance.shadow_account',
        limit,
        offset,
        include_deleted,
        broker=broker,
        platform_project_id=platform_project_id,
    )


@router.get(
    '/shadow-accounts/{pk}',
    summary='影子账户详情（含画像/行为）',
    dependencies=[DependsJwtAuth],
    name='finance_app_get_shadow_account',
)
async def get_shadow_account(db: CurrentSession, request: Request, pk: Annotated[int, Path()]) -> ResponseModel:
    return await _get(db, request, 'finance.shadow_account', pk)


@router.get(
    '/watch-briefings', summary='盯盘简报列表', dependencies=[DependsJwtAuth], name='finance_app_list_watch_briefings'
)
async def list_watch_briefings(
    db: CurrentSession,
    request: Request,
    limit: _Limit = 50,
    offset: _Offset = 0,
    include_deleted: _IncludeDeleted = False,
    trigger: Annotated[str | None, Query(description='触发方式')] = None,
) -> ResponseModel:
    return await _list(db, request, 'finance.watch_briefing', limit, offset, include_deleted, trigger=trigger)


@router.get(
    '/watch-briefings/{pk}',
    summary='盯盘简报详情',
    dependencies=[DependsJwtAuth],
    name='finance_app_get_watch_briefing',
)
async def get_watch_briefing(db: CurrentSession, request: Request, pk: Annotated[int, Path()]) -> ResponseModel:
    return await _get(db, request, 'finance.watch_briefing', pk)


@router.get(
    '/watchlist', summary='自选股列表（非产物）', dependencies=[DependsJwtAuth], name='finance_app_list_watchlist'
)
async def list_watchlist(
    db: CurrentSession,
    request: Request,
    limit: _Limit = 200,
    offset: _Offset = 0,
    include_deleted: _IncludeDeleted = False,
    symbol: Annotated[str | None, Query(description='标的代码')] = None,
    market: Annotated[str | None, Query(description='市场 cn/hk/us')] = None,
) -> ResponseModel:
    return await _list(db, request, 'finance.watchlist', limit, offset, include_deleted, symbol=symbol, market=market)


@router.get(
    '/watchlist/{pk}', summary='自选股详情', dependencies=[DependsJwtAuth], name='finance_app_get_watchlist_item'
)
async def get_watchlist_item(db: CurrentSession, request: Request, pk: Annotated[int, Path()]) -> ResponseModel:
    return await _get(db, request, 'finance.watchlist', pk)
