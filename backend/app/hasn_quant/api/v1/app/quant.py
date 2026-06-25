"""量化研究用户端（owner）业务 API（设计 23 §6 owner read-API + §7 工作台）。

认证：Owner JWT。主人在 WebUI `/apps/quant` 操作回测研究全链路——写策略 / 提交回测 / 轮询绩效 /
看净值曲线。**WebUI 经 daemon `/api/v1/quant/*` 薄代理调用本面**（铁律：WebUI 不直连云端、不直连引擎）。

定位：owner 面 = 业务操作（包裹 quant_service），不是 codegen 裸 CRUD；Agent 工具面（`hasn.quant.*`）
走云端 MCP（gateway_internal handler），不经本面。**单一 Broker**：owner 面与 Agent 面共用同一
`quant_service` + `quant_engine_provider`（设计 §5），引擎是唯一耦合点。

身份恒取自 Owner JWT（request.user.id → owner_hasn_id，行级隔离）；owner 直接操作时 agent_hasn_id 留空。
一律返回统一信封（ResponseModel + response_base.success）。回测失败时引擎真实错误落在 run.error，
HTTP 仍 200（传输成功、业务态在 data.status/data.error 里），零 fake。

实盘线（deploy_live/submit_order，P6+ 真钱强闸·受产品/法务硬闸）**不在本面暴露**——本期仅回测研究。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Request

from backend.app.hasn.service.app_catalog_service import resolve_owner_hasn_id
from backend.app.hasn_quant.provider import quant_engine_provider
from backend.app.hasn_quant.service.quant_service import quant_service
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth

if TYPE_CHECKING:
    from backend.app.hasn_quant.schema.owner import SaveStrategyParam, SubmitBacktestParam
    from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


async def _owner(db: CurrentSession | CurrentSessionTransaction, request: Request) -> str:
    """从 Owner JWT 解析 owner_hasn_id；无平台身份映射则拒（行级隔离前提）。"""
    owner_hasn_id = await resolve_owner_hasn_id(db, user_id=request.user.id)
    if not owner_hasn_id:
        raise errors.ForbiddenError(msg='当前账号未关联唤星身份，无法访问量化研究')
    return owner_hasn_id


# ============================ 引擎健康 ============================


@router.get('/healthz', summary='[Owner] 回测引擎探活', dependencies=[DependsJwtAuth])
async def quant_healthz() -> ResponseModel:
    """探活 quant-engine-service（看板诊断；未配置/不可达回诚实 ok:false）。"""
    return response_base.success(data=await quant_engine_provider.healthz())


# ============================ 策略库 ============================


@router.get('/strategies', summary='[Owner] 策略列表', dependencies=[DependsJwtAuth])
async def list_strategies(request: Request, db: CurrentSession) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    items = await quant_service.list_strategies(db, owner_hasn_id=owner_hasn_id)
    return response_base.success(data={'items': items})


@router.post('/strategies', summary='[Owner] 新建/更新策略（version 自增）', dependencies=[DependsJwtAuth])
async def save_strategy(request: Request, db: CurrentSessionTransaction, obj: SaveStrategyParam) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    data = await quant_service.save_strategy(
        db,
        owner_hasn_id=owner_hasn_id,
        agent_hasn_id=None,  # owner 直接操作，非分身代理
        strategy_id=obj.strategy_id,
        name=obj.name,
        description=obj.description,
        code=obj.code,
        strategy_class=obj.strategy_class,
        builtin_strategy=obj.builtin_strategy,
        params=obj.params,
        instrument_ids=obj.instrument_ids,
        venue=obj.venue,
    )
    return response_base.success(data=data)


@router.get('/strategies/{strategy_id}', summary='[Owner] 策略详情', dependencies=[DependsJwtAuth])
async def get_strategy(request: Request, db: CurrentSession, strategy_id: int) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    data = await quant_service.get_strategy(db, owner_hasn_id=owner_hasn_id, strategy_id=strategy_id)
    return response_base.success(data=data)


# ============================ 回测（job 式） ============================


@router.post('/backtests', summary='[Owner] 提交回测（不动钱，放手跑）', dependencies=[DependsJwtAuth])
async def submit_backtest(request: Request, db: CurrentSessionTransaction, obj: SubmitBacktestParam) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    data = await quant_service.submit_backtest(
        db,
        owner_hasn_id=owner_hasn_id,
        agent_hasn_id=None,
        strategy_id=obj.strategy_id,
        builtin_strategy=obj.builtin_strategy,
        strategy_code=obj.code,
        strategy_class=obj.strategy_class,
        dataset=obj.dataset,
        params=obj.params,
        starting_balance=obj.starting_balance,
        trade_size=obj.trade_size,
        fast_ema_period=obj.fast_ema_period,
        slow_ema_period=obj.slow_ema_period,
    )
    return response_base.success(data=data)


@router.get('/backtests/{backtest_id}', summary='[Owner] 读回测绩效（非终态惰性轮询引擎）', dependencies=[DependsJwtAuth])
async def get_backtest(request: Request, db: CurrentSession, backtest_id: int) -> ResponseModel:
    owner_hasn_id = await _owner(db, request)
    data = await quant_service.get_backtest(db, owner_hasn_id=owner_hasn_id, backtest_id=backtest_id)
    return response_base.success(data=data)
