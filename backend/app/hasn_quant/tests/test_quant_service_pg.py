"""量化回测线全链路真实 E2E（零 mock）：云端 QuantService → 引擎服务真回测 → 落 hasn_quant PG。

证明 doc23 P0-闸2（技术通路）在**云端层**端到端成立：
  分身经云端 service 提交回测 → quant_engine_provider 内网 REST 调 quant-engine-service
  → 真 nautilus 回测（子进程隔离）→ 绩效/净值真实落 `hasn_quant.quant_backtest_run`。

依赖（任一不满足即 skip，不伪造）：
- 本地 PostgreSQL :15432（DATABASE_PORT，schema hasn_quant 已建）。
- quant-engine-service 的 arm64 venv（`.venv/bin/python` + uvicorn）；本测试自启它（loopback，无 token）。
  可经 `QUANT_ENGINE_URL` 指向一个已运行的引擎跳过自启，或 `QUANT_ENGINE_DIR` 覆盖引擎目录。
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import time

from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_quant.service.quant_service import quant_service
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio

_HASN_A = 'hasn:test:quant-a'
_HASN_B = 'hasn:test:quant-b'
_AGENT_A = 'hasn:agent:quant-trader-a'

# 引擎目录候选（首个存在 .venv/bin/python 者）：env 覆盖 > 父仓 huanxing-apps > 开发 worktree。
_ENGINE_DIR_CANDIDATES = (
    '/Users/mac/openclaw-workspace/huanxing/huanxing-project/huanxing-apps/quant-engine-service',
    '/Users/mac/openclaw-workspace/huanxing/.worktrees/quant/huanxing-apps/quant-engine-service',
)


def _resolve_engine_dir() -> Path | None:
    explicit = os.environ.get('QUANT_ENGINE_DIR')
    candidates = [explicit, *_ENGINE_DIR_CANDIDATES] if explicit else list(_ENGINE_DIR_CANDIDATES)
    for cand in candidates:
        if cand and (Path(cand) / '.venv' / 'bin' / 'python').exists():
            return Path(cand)
    return None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _wait_healthy(proc: subprocess.Popen, base: str, timeout: float = 40.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f'引擎进程提前退出（exit {proc.returncode}）')
        try:
            resp = httpx.get(f'{base}/v1/healthz', timeout=2.0)
            if resp.status_code == 200 and resp.json().get('status') == 'ok':
                return
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(0.5)
    raise TimeoutError('引擎 /v1/healthz 在超时内未就绪')


@pytest.fixture(scope='module')
def engine_service():
    """自启 quant-engine-service（loopback，无 token），yield base_url；预置 QUANT_ENGINE_URL 则直接复用。"""
    preset = os.environ.get('QUANT_ENGINE_URL')
    if preset:
        yield preset.rstrip('/')
        return

    engine_dir = _resolve_engine_dir()
    if engine_dir is None:
        pytest.skip('quant-engine-service 的 .venv 不存在（设 QUANT_ENGINE_DIR 或 QUANT_ENGINE_URL）')

    py = engine_dir / '.venv' / 'bin' / 'python'
    port = _free_port()
    base = f'http://127.0.0.1:{port}'
    env = {**os.environ}
    env.pop('QUANT_ENGINE_TOKEN', None)  # 无 token → 引擎仅允许 loopback（本测试即 loopback）
    proc = subprocess.Popen(
        [str(py), '-m', 'uvicorn', 'service.app:app', '--host', '127.0.0.1', '--port', str(port)],
        cwd=str(engine_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    try:
        _wait_healthy(proc, base)
    except Exception as exc:  # noqa: BLE001
        proc.terminate()
        try:
            out = proc.communicate(timeout=5)[0] or ''
        except Exception:  # noqa: BLE001
            out = ''
        pytest.skip(f'引擎服务未就绪，跳过: {exc}\n{out[-800:]}')

    prev = os.environ.get('QUANT_ENGINE_URL')
    os.environ['QUANT_ENGINE_URL'] = base
    try:
        yield base
    finally:
        if prev is None:
            os.environ.pop('QUANT_ENGINE_URL', None)
        else:
            os.environ['QUANT_ENGINE_URL'] = prev
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    sess = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield sess
    finally:
        await sess.rollback()
        await sess.close()
        await engine.dispose()


async def _poll_until_terminal(session, *, owner_hasn_id: str, backtest_id: int, timeout: float = 90.0) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        last = await quant_service.get_backtest(session, owner_hasn_id=owner_hasn_id, backtest_id=backtest_id)
        if last['status'] in ('succeeded', 'failed'):
            return last
        await asyncio.sleep(1.0)
    return last


async def test_backtest_full_loop_builtin(engine_service, session) -> None:
    """存内置 EMA 策略 → 提交回测 → 引擎真跑 → 绩效真落 PG（零 mock）。"""
    strat = await quant_service.save_strategy(
        session,
        owner_hasn_id=_HASN_A,
        agent_hasn_id=_AGENT_A,
        name='EMA 交叉演示',
        builtin_strategy='ema_cross_long_only',
        params={'fast_ema_period': 10, 'slow_ema_period': 20, 'trade_size': 0.5},
        instrument_ids=['ETHUSDT.BINANCE'],
    )
    assert strat['id'] > 0
    assert strat['status'] == 'draft'
    assert strat['agent_hasn_id'] == _AGENT_A  # PLANFIX-6：归属取凭证身份

    submitted = await quant_service.submit_backtest(
        session,
        owner_hasn_id=_HASN_A,
        agent_hasn_id=_AGENT_A,
        strategy_id=strat['id'],
    )
    bt_id = submitted['id']
    assert submitted['status'] in ('queued', 'running')
    assert submitted['engine_job_id']  # 引擎已收 job
    assert submitted['dataset'] == 'synthetic-oscillator-eth'

    # 策略应被标记最近回测 + 转 backtested
    s2 = await quant_service.get_strategy(session, owner_hasn_id=_HASN_A, strategy_id=strat['id'])
    assert s2['latest_backtest_id'] == bt_id
    assert s2['status'] == 'backtested'

    final = await _poll_until_terminal(session, owner_hasn_id=_HASN_A, backtest_id=bt_id)
    assert final['status'] == 'succeeded', f'回测未成功（真实引擎错误透传）: {final.get("error")}'
    assert final['metrics'] is not None
    # 绩效字段齐全 + 成交真实发生（内置 EMA 在合成振荡数据上必有成交）
    assert final['metrics']['trades_count'] >= 1
    assert final['metrics']['fills_count'] >= 1
    assert final['equity_curve'] and len(final['equity_curve']) >= 2
    assert final['duration_secs'] is not None and final['duration_secs'] > 0
    assert final['error'] is None

    # 落库核实：直接查 hasn_quant.quant_backtest_run 行（绕过 service 缓存，验真持久化）
    from backend.app.hasn_quant.model import QuantBacktestRun

    row = (
        await session.execute(
            select(QuantBacktestRun).where(QuantBacktestRun.id == bt_id)
        )
    ).scalar_one()
    assert row.owner_hasn_id == _HASN_A
    assert row.status == 'succeeded'
    assert row.metrics is not None and row.metrics.get('trades_count') >= 1
    assert row.engine_job_id == submitted['engine_job_id']


async def test_backtest_inline_default_builtin(engine_service, session) -> None:
    """不存策略、不给来源 → 回落内置 EMA 演示，仍真跑出绩效（自检路径）。"""
    submitted = await quant_service.submit_backtest(session, owner_hasn_id=_HASN_A, agent_hasn_id=_AGENT_A)
    assert submitted['strategy_id'] is None  # 内联无关联策略
    assert submitted['engine_job_id']
    final = await _poll_until_terminal(session, owner_hasn_id=_HASN_A, backtest_id=submitted['id'])
    assert final['status'] == 'succeeded', f'{final.get("error")}'
    assert final['metrics']['fills_count'] >= 1


async def test_cross_owner_isolation(engine_service, session) -> None:
    """A 的策略/回测 B 不可见（行级隔离）。"""
    strat = await quant_service.save_strategy(
        session, owner_hasn_id=_HASN_A, agent_hasn_id=_AGENT_A, name='A 私有策略',
        builtin_strategy='ema_cross_long_only',
    )
    submitted = await quant_service.submit_backtest(
        session, owner_hasn_id=_HASN_A, agent_hasn_id=_AGENT_A, strategy_id=strat['id'],
    )
    # B 读 A 的策略/回测 → NotFoundError
    with pytest.raises(errors.NotFoundError):
        await quant_service.get_strategy(session, owner_hasn_id=_HASN_B, strategy_id=strat['id'])
    with pytest.raises(errors.NotFoundError):
        await quant_service.get_backtest(session, owner_hasn_id=_HASN_B, backtest_id=submitted['id'])
    # B 列表看不到 A 的
    b_list = await quant_service.list_strategies(session, owner_hasn_id=_HASN_B)
    assert all(s['id'] != strat['id'] for s in b_list)


async def test_save_strategy_validation(engine_service, session) -> None:
    """缺名/缺来源 fail-fast；更新 version 自增。"""
    with pytest.raises(errors.RequestError):
        await quant_service.save_strategy(session, owner_hasn_id=_HASN_A, name='', builtin_strategy='ema_cross_long_only')
    with pytest.raises(errors.RequestError):
        await quant_service.save_strategy(session, owner_hasn_id=_HASN_A, name='无来源', code='', builtin_strategy=None)

    s = await quant_service.save_strategy(
        session, owner_hasn_id=_HASN_A, agent_hasn_id=_AGENT_A, name='可迭代', builtin_strategy='ema_cross_long_only',
    )
    assert s['version'] == 1
    s2 = await quant_service.save_strategy(
        session, owner_hasn_id=_HASN_A, strategy_id=s['id'], params={'fast_ema_period': 5},
    )
    assert s2['version'] == 2
    assert s2['params']['fast_ema_period'] == 5


# ============================ Owner read-API（HTTP 业务面，行级隔离 + 统一信封） ============================
#
# 证明 QUANT-P3 owner read-API（webui 经 daemon 薄代理调用的那一面）端到端成立：
#   Owner JWT 身份（user_id → hasn_id 真实解析自 hasn_humans） → 包裹 quant_service → 真引擎回测
#   → 统一信封返回。直驱 endpoint 函数（避开 Starlette 鉴权中间件，仍真打 owner 解析/服务/引擎/PG）。

from types import SimpleNamespace  # noqa: E402

from backend.app.hasn.model.hasn_humans import HasnHumans  # noqa: E402
from backend.app.hasn_quant.api.v1.app import quant as owner_api  # noqa: E402
from backend.app.hasn_quant.schema.owner import SaveStrategyParam, SubmitBacktestParam  # noqa: E402

_OWNER_USER_ID = 990_271  # 测试专用，session 回滚不留痕
_OWNER_HASN = 'h_test_quant_api'


async def _seed_owner(session) -> SimpleNamespace:
    """种一行 hasn_humans 映射 user_id→hasn_id，并返回带 .user.id 的 stub Request。"""
    # star_id 走唯一索引（存量行多为空串）→ 给唯一非空值避撞；session 回滚不留痕。
    session.add(
        HasnHumans(hasn_id=_OWNER_HASN, star_id='qt_990271', user_id=_OWNER_USER_ID, status='active')
    )
    await session.flush()
    return SimpleNamespace(user=SimpleNamespace(id=_OWNER_USER_ID))


async def _poll_owner_until_terminal(req, session, *, backtest_id: int, timeout: float = 90.0) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        resp = await owner_api.get_backtest(req, session, backtest_id)
        last = resp.data
        if last['status'] in ('succeeded', 'failed'):
            return last
        await asyncio.sleep(1.0)
    return last


async def test_owner_api_backtest_full_loop(engine_service, session) -> None:
    """Owner 经 read-API 写策略→提交回测→轮询绩效（真身份解析 + 真引擎 + 统一信封）。"""
    req = await _seed_owner(session)

    saved = await owner_api.save_strategy(
        req,
        session,
        SaveStrategyParam(
            name='Owner EMA 策略',
            builtin_strategy='ema_cross_long_only',
            params={'fast_ema_period': 10, 'slow_ema_period': 20, 'trade_size': 0.5},
            instrument_ids=['ETHUSDT.BINANCE'],
        ),
    )
    strat = saved.data
    assert strat['id'] > 0
    assert strat['owner_hasn_id'] == _OWNER_HASN  # 身份取自 JWT→hasn_humans，非 body
    assert strat['agent_hasn_id'] is None  # owner 直接操作，非分身代理

    submitted = await owner_api.submit_backtest(
        req, session, SubmitBacktestParam(strategy_id=strat['id'])
    )
    bt = submitted.data
    assert bt['status'] in ('queued', 'running')
    assert bt['engine_job_id']

    # 列表/详情走 read-API（行级隔离生效：只见自己的）
    listed = (await owner_api.list_strategies(req, session)).data
    assert any(s['id'] == strat['id'] for s in listed['items'])

    final = await _poll_owner_until_terminal(req, session, backtest_id=bt['id'])
    assert final['status'] == 'succeeded', f'回测未成功（真实引擎错误透传）: {final.get("error")}'
    assert final['metrics']['trades_count'] >= 1
    assert final['equity_curve'] and len(final['equity_curve']) >= 2
    assert final['error'] is None


async def test_owner_api_inline_backtest(engine_service, session) -> None:
    """Owner 即席回测（不存策略，回落内置 EMA）：strategy_id 为 None 仍真跑出绩效。"""
    req = await _seed_owner(session)
    submitted = await owner_api.submit_backtest(req, session, SubmitBacktestParam())
    bt = submitted.data
    assert bt['strategy_id'] is None
    final = await _poll_owner_until_terminal(req, session, backtest_id=bt['id'])
    assert final['status'] == 'succeeded', f'{final.get("error")}'
    assert final['metrics']['fills_count'] >= 1


async def test_owner_api_requires_hasn_identity(session) -> None:
    """无 hasn_humans 映射的账号访问 read-API → ForbiddenError（行级隔离前提，不放行）。"""
    req = SimpleNamespace(user=SimpleNamespace(id=424_242))  # 未种映射
    with pytest.raises(errors.ForbiddenError):
        await owner_api.list_strategies(req, session)
