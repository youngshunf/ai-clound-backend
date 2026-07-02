"""量化交易 AI-Native 业务编排（云端唯一 Broker，设计 doc23 §3/§5/§6）。

quant 是 **cloud-brokered** 应用（对齐 creator/community/finance）：分身经云端 MCP 调 `hasn.quant.*` →
本 service 落产品数据（`hasn_quant` PG，权威）+ 经 `quant_engine_provider` 调引擎服务跑真回测。

本期（P0–P5 回测研究平台，零资金风险）只编排回测线：
- `save_strategy` / `list_strategies` / `get_strategy`：策略库 CRUD（owner 行级隔离）。
- `submit_backtest`：提交回测（job 式：调引擎拿 job_id，落 `quant_backtest_run`，立即返回）。
- `get_backtest`：读回测；非终态时**惰性轮询**引擎、把绩效/净值/错误落库（零 fake，引擎真实错误透传）。

归属（PLANFIX-6）：分身建带归属资源时 `agent_hasn_id` 取**凭证身份**（gateway 从 Agent JWT 注入），
`owner_hasn_id` 行级隔离键。实盘线（deploy_live/submit_order）受 P0-闸1 产品/法务硬闸，本期不接。
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from backend.app.hasn_quant.model import QuantBacktestRun, QuantStrategy
from backend.app.hasn_quant.provider import quant_engine_provider
from backend.app.hasn_quant.provider.engine_client import QuantEngineError
from backend.common.exception import errors

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# 默认合成数据集（确定性、自包含、无外部数据依赖；引擎 datasets.CATALOG）。
_DEFAULT_DATASET = 'synthetic-oscillator-eth'
_DEFAULT_BUILTIN = 'ema_cross_long_only'
_TERMINAL = ('succeeded', 'failed')
_MAX_EQUITY_POINTS = 2000


def _now() -> datetime:
    return datetime.now(timezone.utc)


class QuantService:
    """量化回测线编排（owner 隔离 + 引擎 broker）。无 mock：回测真打引擎、绩效真落库。"""

    # ---------------------------------------------------------------- 策略库

    @staticmethod
    async def save_strategy(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        agent_hasn_id: str | None = None,
        strategy_id: int | None = None,
        name: str | None = None,
        description: str | None = None,
        code: str = '',
        strategy_class: str = '',
        builtin_strategy: str | None = None,
        params: dict[str, Any] | None = None,
        instrument_ids: list[str] | None = None,
        venue: str | None = None,
    ) -> dict[str, Any]:
        """新建或更新策略（owner 隔离）。更新每次 version 自增，保留迭代 history 语义。"""
        if strategy_id is not None:
            row = await QuantService._load_strategy(db, owner_hasn_id=owner_hasn_id, strategy_id=strategy_id)
            if name is not None:
                row.name = name
            if description is not None:
                row.description = description
            if code:
                row.code = code
            if strategy_class:
                row.strategy_class = strategy_class
            if builtin_strategy is not None:
                row.builtin_strategy = builtin_strategy
            if params is not None:
                row.params = dict(params)
            if instrument_ids is not None:
                row.instrument_ids = list(instrument_ids)
            if venue is not None:
                row.venue = venue
            row.version = (row.version or 0) + 1
            await db.flush()
            return _serialize_strategy(row)

        if not name:
            raise errors.RequestError(msg='策略名称必填')
        if not code and not builtin_strategy:
            raise errors.RequestError(msg='须提供 code（+strategy_class）或 builtin_strategy')
        row = QuantStrategy(
            owner_hasn_id=owner_hasn_id,
            agent_hasn_id=agent_hasn_id,
            name=name,
            description=description,
            code=code or '',
            strategy_class=strategy_class or '',
            builtin_strategy=builtin_strategy,
            params=dict(params or {}),
            instrument_ids=list(instrument_ids or []),
            venue=venue,
            status='draft',
            version=1,
            latest_backtest_id=None,
        )
        db.add(row)
        await db.flush()
        return _serialize_strategy(row)

    @staticmethod
    async def list_strategies(db: AsyncSession, *, owner_hasn_id: str) -> list[dict[str, Any]]:
        """列出某主人的全部策略（行级隔离，最近优先）。"""
        stmt = (
            select(QuantStrategy)
            .where(QuantStrategy.owner_hasn_id == owner_hasn_id)
            .order_by(QuantStrategy.id.desc())
        )
        rows = (await db.execute(stmt)).scalars().all()
        return [_serialize_strategy(r) for r in rows]

    @staticmethod
    async def get_strategy(db: AsyncSession, *, owner_hasn_id: str, strategy_id: int) -> dict[str, Any]:
        row = await QuantService._load_strategy(db, owner_hasn_id=owner_hasn_id, strategy_id=strategy_id)
        return _serialize_strategy(row)

    @staticmethod
    async def list_backtest_runs(
        db: AsyncSession, *, owner_hasn_id: str, status: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """列出某主人的回测任务（行级隔离，最近优先，可选 status 过滤）。"""
        conds = [QuantBacktestRun.owner_hasn_id == owner_hasn_id]
        if status:
            conds.append(QuantBacktestRun.status == status)
        stmt = select(QuantBacktestRun).where(*conds).order_by(QuantBacktestRun.id.desc()).limit(min(limit, 200))
        rows = (await db.execute(stmt)).scalars().all()
        return [_serialize_run(r) for r in rows]

    # ---------------------------------------------------------------- 回测线

    @staticmethod
    async def submit_backtest(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        agent_hasn_id: str | None = None,
        strategy_id: int | None = None,
        builtin_strategy: str | None = None,
        strategy_code: str | None = None,
        strategy_class: str | None = None,
        dataset: str = _DEFAULT_DATASET,
        params: dict[str, Any] | None = None,
        starting_balance: float = 1_000_000.0,
        trade_size: float | None = None,
        fast_ema_period: int | None = None,
        slow_ema_period: int | None = None,
    ) -> dict[str, Any]:
        """提交一次回测（job 式）。落 `quant_backtest_run`（queued/running），立即返回供轮询。

        策略来源：`strategy_id`（载已存策略，owner 隔离）或内联 `builtin_strategy` /
        `strategy_code`+`strategy_class`；都不给则回落内置 EMA 演示（自检/演示）。
        引擎传输层失败 → 落 status=failed + 透传真实 error（零 fake），不抛给上层。
        """
        effective_params = dict(params or {})
        source: dict[str, Any] = {}
        strategy_row: QuantStrategy | None = None
        if strategy_id is not None:
            strategy_row = await QuantService._load_strategy(
                db, owner_hasn_id=owner_hasn_id, strategy_id=strategy_id
            )
            source = {
                'builtin_strategy': strategy_row.builtin_strategy,
                'strategy_code': strategy_row.code or None,
                'strategy_class': strategy_row.strategy_class or None,
            }
            effective_params = {**(strategy_row.params or {}), **effective_params}
        else:
            source = {
                'builtin_strategy': builtin_strategy,
                'strategy_code': strategy_code,
                'strategy_class': strategy_class,
            }
        if not source.get('builtin_strategy') and not source.get('strategy_code'):
            source['builtin_strategy'] = _DEFAULT_BUILTIN

        engine_request = _build_engine_request(
            source=source,
            dataset=dataset,
            params=effective_params,
            starting_balance=starting_balance,
            trade_size=trade_size,
            fast_ema_period=fast_ema_period,
            slow_ema_period=slow_ema_period,
        )

        run = QuantBacktestRun(
            strategy_id=strategy_id,  # None=内联/即席回测（无已存策略）
            owner_hasn_id=owner_hasn_id,
            agent_hasn_id=agent_hasn_id,
            params=engine_request,  # 快照：本次回测的完整引擎入参（不回指策略当前值）
            dataset=dataset,
            data_source='synthetic',
            status='queued',
        )
        try:
            job = await quant_engine_provider.submit_backtest(engine_request)
            run.engine_job_id = job.get('job_id')
            run.status = job.get('status') if job.get('status') in ('queued', 'running') else 'running'
            run.started_at = _now()
        except QuantEngineError as exc:
            run.status = 'failed'
            run.error = str(exc)
            run.finished_at = _now()
        db.add(run)
        await db.flush()

        if strategy_row is not None:
            strategy_row.latest_backtest_id = run.id
            if strategy_row.status == 'draft':
                strategy_row.status = 'backtested'
            await db.flush()
        return _serialize_run(run)

    @staticmethod
    async def get_backtest(db: AsyncSession, *, owner_hasn_id: str, backtest_id: int) -> dict[str, Any]:
        """读回测（owner 隔离）。非终态且有 engine_job_id → 惰性轮询引擎并把绩效/错误落库。"""
        run = await QuantService._load_run(db, owner_hasn_id=owner_hasn_id, backtest_id=backtest_id)
        if run.status not in _TERMINAL and run.engine_job_id:
            await QuantService._poll_and_apply(db, run)
        return _serialize_run(run)

    @staticmethod
    async def _poll_and_apply(db: AsyncSession, run: QuantBacktestRun) -> None:
        """轮询引擎一次，把终态结果落库。传输层失败保持原态（下次重试），不造假。"""
        try:
            job = await quant_engine_provider.get_backtest(run.engine_job_id)
        except QuantEngineError as exc:
            # 404=job 不存在（引擎重启丢内存态）→ 标失败透传；其余瞬时错误保持原态等下次。
            if 'HTTP 404' in str(exc):
                run.status = 'failed'
                run.error = f'引擎侧 job 已不存在: {exc}'
                run.finished_at = _now()
                await db.flush()
            return
        engine_status = job.get('status')
        if engine_status == 'running' and run.status != 'running':
            run.status = 'running'
            if run.started_at is None:
                run.started_at = _now()
            await db.flush()
            return
        if engine_status not in _TERMINAL:
            return
        result = job.get('result') or {}
        run.status = engine_status
        run.metrics = result.get('metrics')
        equity = result.get('equity_curve') or []
        run.equity_curve = equity[:_MAX_EQUITY_POINTS] if isinstance(equity, list) else None
        run.error = result.get('error')
        duration = result.get('duration_secs')
        run.duration_secs = Decimal(str(duration)) if duration is not None else None
        run.finished_at = _now()
        await db.flush()

    # ---------------------------------------------------------------- 内部

    @staticmethod
    async def _load_strategy(db: AsyncSession, *, owner_hasn_id: str, strategy_id: int) -> QuantStrategy:
        stmt = select(QuantStrategy).where(
            QuantStrategy.id == strategy_id, QuantStrategy.owner_hasn_id == owner_hasn_id
        )
        row = (await db.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise errors.NotFoundError(msg='策略不存在')
        return row

    @staticmethod
    async def _load_run(db: AsyncSession, *, owner_hasn_id: str, backtest_id: int) -> QuantBacktestRun:
        stmt = select(QuantBacktestRun).where(
            QuantBacktestRun.id == backtest_id, QuantBacktestRun.owner_hasn_id == owner_hasn_id
        )
        row = (await db.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise errors.NotFoundError(msg='回测任务不存在')
        return row


def _build_engine_request(
    *,
    source: dict[str, Any],
    dataset: str,
    params: dict[str, Any],
    starting_balance: float,
    trade_size: float | None,
    fast_ema_period: int | None,
    slow_ema_period: int | None,
) -> dict[str, Any]:
    """组装引擎 BacktestRequest（顶层 trade_size/fast_ema/slow_ema 优先入参 > params > 引擎默认）。"""
    req: dict[str, Any] = {
        'dataset': dataset,
        'params': params,
        'starting_balance': float(starting_balance),
    }
    if source.get('builtin_strategy'):
        req['builtin_strategy'] = source['builtin_strategy']
    if source.get('strategy_code'):
        req['strategy_code'] = source['strategy_code']
    if source.get('strategy_class'):
        req['strategy_class'] = source['strategy_class']
    ts = trade_size if trade_size is not None else params.get('trade_size')
    if ts is not None:
        req['trade_size'] = float(ts)
    fe = fast_ema_period if fast_ema_period is not None else params.get('fast_ema_period')
    if fe is not None:
        req['fast_ema_period'] = int(fe)
    se = slow_ema_period if slow_ema_period is not None else params.get('slow_ema_period')
    if se is not None:
        req['slow_ema_period'] = int(se)
    return req


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _to_float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _serialize_strategy(row: QuantStrategy) -> dict[str, Any]:
    return {
        'id': row.id,
        'owner_hasn_id': row.owner_hasn_id,
        'agent_hasn_id': row.agent_hasn_id,
        'name': row.name,
        'description': row.description,
        'code': row.code,
        'strategy_class': row.strategy_class,
        'builtin_strategy': row.builtin_strategy,
        'params': row.params or {},
        'instrument_ids': row.instrument_ids or [],
        'venue': row.venue,
        'status': row.status,
        'version': row.version,
        'latest_backtest_id': row.latest_backtest_id,
        'created_time': _iso(getattr(row, 'created_time', None)),
        'updated_time': _iso(getattr(row, 'updated_time', None)),
    }


def _serialize_run(row: QuantBacktestRun) -> dict[str, Any]:
    return {
        'id': row.id,
        'strategy_id': row.strategy_id,
        'owner_hasn_id': row.owner_hasn_id,
        'agent_hasn_id': row.agent_hasn_id,
        'dataset': row.dataset,
        'data_source': row.data_source,
        'status': row.status,
        'params': row.params or {},
        'metrics': row.metrics,
        'equity_curve': row.equity_curve,
        'error': row.error,
        'engine_job_id': row.engine_job_id,
        'duration_secs': _to_float(row.duration_secs),
        'started_at': _iso(row.started_at),
        'finished_at': _iso(row.finished_at),
        'created_time': _iso(getattr(row, 'created_time', None)),
    }


quant_service = QuantService()
