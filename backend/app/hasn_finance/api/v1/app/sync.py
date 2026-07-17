"""金融投研 6 类产物 + watchlist 的 owner 端 `:sync` 端点（05 §5.3a / §5.5）。

WebUI 不直接调本面；daemon outbox drain 经其 `:sync` 上行投影调用本面（cloud-brokered）。
owner 只取鉴权上下文（`resolve_owner`，客户端传入的 owner 一律不可信）。响应统一信封。

隐私红线（05 C5）：端点层据各产物**字段白名单**从 `payload.fields` 剔除任何非法/隐私键
（影子账户不收 source_file_ref/source_content_hash/真实账号），云端表本就无这些列。
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from backend.app.hasn_finance.model.backtest_report import BacktestReport
from backend.app.hasn_finance.model.research_report import ResearchReport
from backend.app.hasn_finance.model.shadow_account import ShadowAccount
from backend.app.hasn_finance.model.strategy import Strategy
from backend.app.hasn_finance.model.trade_review import TradeReview
from backend.app.hasn_finance.model.watch_briefing import WatchBriefing
from backend.app.hasn_finance.model.watchlist import Watchlist
from backend.app.hasn_finance.schema.sync import SyncEnvelope, SyncResult
from backend.app.hasn_finance.service.finance_sync_service import finance_sync_service
from backend.app.hasn_project.api.v1.app._common import resolve_owner
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession

router = APIRouter()


# 各产物「允许写入 fields 的业务列」白名单——非白名单键一律丢弃（防隐私列/非法列混入）。
# owner_id/agent_hasn_id/local_ref/node_id/revision/last_client_op_id/status 由 service 统一管理，不进 fields。
_ALLOWED_FIELDS: dict[str, set[str]] = {
    'finance.research_report': {
        'symbol', 'market', 'display_name', 'title', 'verdict', 'conviction', 'summary',
        'body_md', 'findings_json', 'data_as_of', 'swarm_preset', 'swarm_run_ref',
        'engine_version', 'bound_agent_id', 'usage_json',
    },
    'finance.strategy': {
        'name', 'description', 'market', 'universe_json', 'params_json', 'code_py',
        'code_sha256', 'source', 'bound_agent_id', 'latest_backtest_id', 'platform_project_id', 'usage_json',
    },
    'finance.backtest_report': {
        'strategy_id', 'title', 'period_start', 'period_end', 'universe_json', 'initial_capital',
        'cost_model_json', 'benchmark_symbol', 'benchmark_return', 'annual_return', 'sharpe',
        'max_drawdown', 'win_rate', 'trade_count', 'metrics_json', 'equity_curve_json',
        'trades_json', 'engine_version', 'data_source', 'usage_json',
    },
    'finance.trade_review': {
        'shadow_account_id', 'title', 'body_md', 'findings_json', 'shadow_backtest_id',
        'pdf_asset_uri', 'usage_json',
    },
    'finance.shadow_account': {
        'broker', 'account_alias', 'stmt_period_start', 'stmt_period_end', 'profile_json',
        'behaviors_json', 'version', 'superseded_by', 'platform_project_id', 'usage_json',
    },
    'finance.watch_briefing': {
        'briefing_date', 'title', 'body_md', 'covered_symbols_json', 'trigger', 'usage_json',
    },
}

# 产物模型映射（resource_kind -> ORM 类）
_PRODUCT_MODELS = {
    'finance.research_report': ResearchReport,
    'finance.strategy': Strategy,
    'finance.backtest_report': BacktestReport,
    'finance.trade_review': TradeReview,
    'finance.shadow_account': ShadowAccount,
    'finance.watch_briefing': WatchBriefing,
}

# watchlist 白名单（人工资产，无 agent/node 溯源列）
_WATCHLIST_FIELDS = {'symbol', 'market', 'display_name', 'note', 'sort_order'}


def _filtered_fields(resource_kind: str, raw: dict) -> dict:
    """据白名单剔除非法/隐私键，只留该产物允许写入的业务列。"""
    allowed = _ALLOWED_FIELDS[resource_kind]
    return {k: v for k, v in (raw or {}).items() if k in allowed}


def _title_of(resource_kind: str, fields: dict) -> str:
    """登记用标题：产物多有 title/name 列，取之；缺省回落 resource_kind。"""
    return str(fields.get('title') or fields.get('name') or resource_kind)


def _summary_of(fields: dict) -> str | None:
    """登记用摘要：投研报告有一句话结论 summary，其余产物无则 None。"""
    val = fields.get('summary')
    return str(val) if val else None


async def _sync_product_endpoint(
    db: CurrentSession, request: Request, resource_kind: str, payload: SyncEnvelope
) -> ResponseSchemaModel[SyncResult]:
    """6 类产物 `:sync` 端点的公共实现——owner 解析 + 白名单过滤 + 同事务 service 调用。"""
    owner_id = await resolve_owner(db, request)
    fields = _filtered_fields(resource_kind, payload.fields)
    result = await finance_sync_service.sync_product(
        db,
        model_cls=_PRODUCT_MODELS[resource_kind],
        resource_kind=resource_kind,
        owner_id=owner_id,
        op=payload.op,
        op_id=payload.op_id,
        base_revision=payload.base_revision,
        local_ref=payload.local_ref,
        server_id=payload.server_id,
        fields=fields,
        node_id=payload.node_id,
        agent_hasn_id=payload.agent_hasn_id,
        session_id=payload.session_id,
        project_id=payload.project_id,
        title=_title_of(resource_kind, fields),
        summary=_summary_of(fields),
        source_tool=f'{resource_kind}:sync',
    )
    await db.commit()
    return response_base.success(data=SyncResult(**result))


@router.post('/research-reports:sync', summary='投研报告同步', dependencies=[DependsJwtAuth], response_model=ResponseSchemaModel[SyncResult])
async def sync_research_report(db: CurrentSession, request: Request, payload: SyncEnvelope) -> ResponseSchemaModel:
    return await _sync_product_endpoint(db, request, 'finance.research_report', payload)


@router.post('/strategies:sync', summary='策略同步', dependencies=[DependsJwtAuth], response_model=ResponseSchemaModel[SyncResult])
async def sync_strategy(db: CurrentSession, request: Request, payload: SyncEnvelope) -> ResponseSchemaModel:
    return await _sync_product_endpoint(db, request, 'finance.strategy', payload)


@router.post('/backtest-reports:sync', summary='回测报告同步', dependencies=[DependsJwtAuth], response_model=ResponseSchemaModel[SyncResult])
async def sync_backtest_report(db: CurrentSession, request: Request, payload: SyncEnvelope) -> ResponseSchemaModel:
    return await _sync_product_endpoint(db, request, 'finance.backtest_report', payload)


@router.post('/trade-reviews:sync', summary='交易复盘同步', dependencies=[DependsJwtAuth], response_model=ResponseSchemaModel[SyncResult])
async def sync_trade_review(db: CurrentSession, request: Request, payload: SyncEnvelope) -> ResponseSchemaModel:
    return await _sync_product_endpoint(db, request, 'finance.trade_review', payload)


@router.post('/shadow-accounts:sync', summary='影子账户同步', dependencies=[DependsJwtAuth], response_model=ResponseSchemaModel[SyncResult])
async def sync_shadow_account(db: CurrentSession, request: Request, payload: SyncEnvelope) -> ResponseSchemaModel:
    return await _sync_product_endpoint(db, request, 'finance.shadow_account', payload)


@router.post('/watch-briefings:sync', summary='盯盘简报同步', dependencies=[DependsJwtAuth], response_model=ResponseSchemaModel[SyncResult])
async def sync_watch_briefing(db: CurrentSession, request: Request, payload: SyncEnvelope) -> ResponseSchemaModel:
    return await _sync_product_endpoint(db, request, 'finance.watch_briefing', payload)


@router.post('/watchlist:sync', summary='自选股同步（非产物·不登记）', dependencies=[DependsJwtAuth], response_model=ResponseSchemaModel[SyncResult])
async def sync_watchlist(db: CurrentSession, request: Request, payload: SyncEnvelope) -> ResponseSchemaModel:
    owner_id = await resolve_owner(db, request)
    fields = {k: v for k, v in (payload.fields or {}).items() if k in _WATCHLIST_FIELDS}
    result = await finance_sync_service.sync_watchlist(
        db,
        model_cls=Watchlist,
        owner_id=owner_id,
        op=payload.op,
        op_id=payload.op_id,
        base_revision=payload.base_revision,
        local_ref=payload.local_ref,
        server_id=payload.server_id,
        fields=fields,
    )
    await db.commit()
    return response_base.success(data=SyncResult(**result))
