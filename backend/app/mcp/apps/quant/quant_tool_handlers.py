"""量化交易 AI-Native 工具 handler（云端 gateway_internal，设计 doc23 §6）。

形态（与 creator/community/finance 一致）：quant 是 **cloud-brokered** 应用——`hasn.quant.*` 工具一律走
云端 MCP：`ai_native_runtime_gateway` 在 `transport=gateway_internal` 时进程内直调本文件 handler，
handler 再调 `quant_service`（落 hasn_quant PG）+ 经 `quant_engine_provider` 调引擎服务跑真回测。

每个 handler 签名 `(db, agent: AgentTokenPayload, input_payload: dict) -> dict`：
- 身份恒取自 Agent JWT claims（`owner_hasn_id`/`agent_hasn_id`），绝不从入参读身份（PLANFIX-6）；
- owner 行级隔离由 service 强制；返回**裸 data**（gateway 负责信封/审计）。

⚠️ 本期（P0–P5 回测研究平台，零资金风险）只暴露回测/读/存（出厂 allow）：
  backtest / get_backtest / save_strategy / list_strategies / get_strategy。
  实盘线（deploy_live / submit_order / resume，出厂 ask，P6+）受 P0-闸1 产品/法务硬闸，本文件不实现。

⚠️ 注册待办（被并发 finance 会话占用的 `app/mcp/routes.py` + `ai_native_runtime_gateway.py` 阻塞）：
  待其落库后，在 gateway 的 app→handler 映射注册 `quant` 5 工具（参见 creator 注册块），
  并在 `app/mcp/scopes.py` 聚合 `QUANT_SCOPE_CATALOG`、`app_catalog_registry` 注册 `build_quant_app()`。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.app.hasn_quant.service.quant_service import quant_service

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.common.dataclasses import AgentTokenPayload


def _int(payload: dict[str, Any], key: str) -> int:
    return int(payload[key])


def _opt_int(payload: dict[str, Any], key: str) -> int | None:
    val = payload.get(key)
    return int(val) if val is not None else None


# ---------------- 策略库（quant:write / quant:read，出厂 allow） ----------------


async def handle_save_strategy(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """保存/更新策略（不动钱）。新建必带 name + (code/strategy_class | builtin_strategy)；传 strategy_id 则更新。"""
    return await quant_service.save_strategy(
        db,
        owner_hasn_id=agent.owner_hasn_id,
        agent_hasn_id=agent.agent_hasn_id,
        strategy_id=_opt_int(input_payload, 'strategy_id'),
        name=input_payload.get('name'),
        description=input_payload.get('description'),
        code=str(input_payload.get('code') or ''),
        strategy_class=str(input_payload.get('strategy_class') or ''),
        builtin_strategy=input_payload.get('builtin_strategy'),
        params=input_payload.get('params'),
        instrument_ids=input_payload.get('instrument_ids'),
        venue=input_payload.get('venue'),
    )


async def handle_list_strategies(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    items = await quant_service.list_strategies(db, owner_hasn_id=agent.owner_hasn_id)
    return {'items': items}


async def handle_get_strategy(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    return await quant_service.get_strategy(
        db, owner_hasn_id=agent.owner_hasn_id, strategy_id=_int(input_payload, 'strategy_id')
    )


# ---------------- 回测（quant:backtest / quant:read，出厂 allow） ----------------


async def handle_backtest(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """提交一次回测（job 式：立即返回 queued/running，用 get_backtest 轮询绩效）。

    策略来源：`strategy_id`（载已存策略）或内联 `builtin_strategy` / `strategy_code`+`strategy_class`。
    只花算力、不动钱——分身可随便迭代。
    """
    return await quant_service.submit_backtest(
        db,
        owner_hasn_id=agent.owner_hasn_id,
        agent_hasn_id=agent.agent_hasn_id,
        strategy_id=_opt_int(input_payload, 'strategy_id'),
        builtin_strategy=input_payload.get('builtin_strategy'),
        strategy_code=input_payload.get('strategy_code'),
        strategy_class=input_payload.get('strategy_class'),
        dataset=str(input_payload.get('dataset') or 'synthetic-oscillator-eth'),
        params=input_payload.get('params'),
        starting_balance=float(input_payload.get('starting_balance', 1_000_000.0)),
        trade_size=input_payload.get('trade_size'),
        fast_ema_period=_opt_int(input_payload, 'fast_ema_period'),
        slow_ema_period=_opt_int(input_payload, 'slow_ema_period'),
    )


async def handle_get_backtest(
    db: AsyncSession, agent: AgentTokenPayload, input_payload: dict[str, Any]
) -> dict[str, Any]:
    """读回测（含惰性轮询引擎落绩效）。owner 隔离。"""
    return await quant_service.get_backtest(
        db, owner_hasn_id=agent.owner_hasn_id, backtest_id=_int(input_payload, 'backtest_id')
    )


# 工具键 → handler 映射（gateway 注册用；解锁 routes.py 后接入）。
QUANT_TOOL_HANDLERS = {
    'hasn.quant.save_strategy': handle_save_strategy,
    'hasn.quant.list_strategies': handle_list_strategies,
    'hasn.quant.get_strategy': handle_get_strategy,
    'hasn.quant.backtest': handle_backtest,
    'hasn.quant.get_backtest': handle_get_backtest,
}
