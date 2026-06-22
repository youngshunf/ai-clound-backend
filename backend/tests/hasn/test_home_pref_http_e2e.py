"""P1 主脑 + 工作台偏好 真实 HTTP E2E（真实 PostgreSQL，零 mock）。

模块级把 app workbench 路由挂最小 app，fixture 用 dependency_overrides 把 DependsJwtAuth
换成注入“每测试唯一” user_id、get_db/get_db_transaction 指向真实 PG 会话。覆盖：
  - GET 默认回落主脑（role=primary 优先，否则最早活跃分身）
  - PUT 设主脑持久化 + explicit 标记 + 归属/活跃校验（非本人分身 → 403）
  - PUT 简报偏好（开关/时刻/数据源）+ 时刻/数据源校验（非法 → 400）
  - owner 隔离（A 的偏好/分身不影响 B）
  - 统一信封外壳

每测试用唯一 user_id（避免提交型测试累积同 user_id 的 human 让 _resolve_owner_id 取错）。

事实源: docs/hasn-node设计文档/13-工作台/04-...设计.md §2.2/§2.3；实施 §3。
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette_context.middleware import ContextMiddleware
from starlette_context.plugins import RequestIdPlugin

from backend.app.home.api.v1.app.home import router as app_workbench_router
from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.common.exception.exception_handler import register_exception
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

pytestmark = pytest.mark.asyncio

_APP = FastAPI()
_APP.add_middleware(ContextMiddleware, plugins=(RequestIdPlugin(),))
register_exception(_APP)
_APP.include_router(app_workbench_router, prefix='/api/v1/hasn/app')

_PREF = '/api/v1/hasn/app/home/pref'


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _new_user_id() -> int:
    return 970_000_000 + int(uuid.uuid4().int % 20_000_000)


def _agent(hasn_id: str, owner: str, *, role: str = 'specialist', status: str = 'active') -> HasnAgents:
    # star_id 有唯一索引，按 hasn_id 派生唯一值避免与存量空串撞键
    return HasnAgents(
        hasn_id=hasn_id,
        star_id=f's_{hasn_id}',
        owner_id=owner,
        display_name=f'分身{hasn_id[-4:]}',
        agent_name=hasn_id,
        role=role,
        status=status,
    )


def _human(hasn_id: str, user_id: int, nickname: str) -> HasnHumans:
    # star_id 与 nickname 均有唯一索引，按 hasn_id 派生唯一值
    return HasnHumans(
        hasn_id=hasn_id, star_id=f's_{hasn_id}', user_id=user_id, nickname=f'{nickname}_{hasn_id[-6:]}', status='active'
    )


@pytest_asyncio.fixture
async def env():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    user_id = _new_user_id()
    owner = f'h_wb_{_uid()}'
    session.add(_human(owner, user_id, '主脑E2E'))
    await session.flush()

    # 当前生效身份（可在测试内切换以验证 owner 隔离）
    auth_state = {'user_id': user_id}

    async def _yield_session():
        yield session

    async def _auth_inject(request: Request):
        request.scope['user'] = SimpleNamespace(id=auth_state['user_id'])
        request.scope['auth'] = ['authenticated']
        return 'e2e-token'

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[DependsJwtAuth.dependency] = _auth_inject

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield SimpleNamespace(client=client, owner=owner, session=session, user_id=user_id, auth_state=auth_state)
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await engine.dispose()


def _data(resp: httpx.Response):
    assert resp.status_code == 200, f'{resp.request.method} {resp.request.url} -> {resp.status_code}: {resp.text}'
    body = resp.json()
    assert body.get('code') == 200, f'envelope 非 200: {body}'
    return body['data']


async def test_get_default_falls_back_to_primary_agent(env) -> None:
    """无偏好行时，GET 回落 role='primary' 活跃分身 + 简报默认值，explicit=False。"""
    s, owner, c = env.session, env.owner, env.client
    primary = f'a_pri_{_uid()}'
    other = f'a_spec_{_uid()}'
    s.add(_agent(primary, owner, role='primary'))
    s.add(_agent(other, owner, role='specialist'))
    await s.flush()

    data = _data(await c.get(_PREF))
    assert data['primary_agent_id'] == primary, '默认主脑=role=primary 分身'
    assert data['primary_agent_explicit'] is False, '回落非显式'
    assert data['briefing_enabled'] is True
    assert data['briefing_time'] == '08:00'
    assert data['briefing_sources'] == ['task', 'social', 'app', 'plan']


async def test_get_default_no_primary_role_uses_earliest_active(env) -> None:
    """无 role=primary 时回落最早创建的活跃分身。"""
    s, owner, c = env.session, env.owner, env.client
    early = f'a_early_{_uid()}'
    late = f'a_late_{_uid()}'
    s.add(_agent(early, owner, role='specialist'))
    await s.flush()  # 先 flush early 拿更早 created_time
    s.add(_agent(late, owner, role='specialist'))
    await s.flush()

    data = _data(await c.get(_PREF))
    assert data['primary_agent_id'] == early, '应回落最早创建的活跃分身'


async def test_set_primary_agent_persists_and_validates(env) -> None:
    """PUT 设主脑：归属本人活跃 → 持久 + explicit=True；非本人分身 → 403。"""
    s, owner, c = env.session, env.owner, env.client
    a1 = f'a_one_{_uid()}'
    a2 = f'a_two_{_uid()}'
    s.add(_agent(a1, owner, role='primary'))
    s.add(_agent(a2, owner, role='specialist'))
    # 别人的分身（不同 owner / user_id）
    foreign_owner = f'h_other_{_uid()}'
    foreign_agent = f'a_foreign_{_uid()}'
    s.add(_human(foreign_owner, _new_user_id(), '别人'))
    s.add(_agent(foreign_agent, foreign_owner, role='primary'))
    await s.flush()

    # 设为 a2（本人活跃）→ 持久 + explicit
    data = _data(await c.put(_PREF, json={'primary_agent_id': a2}))
    assert data['primary_agent_id'] == a2 and data['primary_agent_explicit'] is True
    # 再 GET 仍是 a2（已落库）
    assert _data(await c.get(_PREF))['primary_agent_id'] == a2

    # 设为别人的分身 → 403
    resp = await c.put(_PREF, json={'primary_agent_id': foreign_agent})
    assert resp.status_code == 403, f'非本人分身应 403，实际 {resp.status_code}: {resp.text}'
    # 主脑未被改坏
    assert _data(await c.get(_PREF))['primary_agent_id'] == a2


async def test_briefing_prefs_update_and_validation(env) -> None:
    """简报偏好部分更新 + 时刻/数据源校验。"""
    s, owner, c = env.session, env.owner, env.client
    s.add(_agent(f'a_b_{_uid()}', owner, role='primary'))
    await s.flush()

    # 合法部分更新
    data = _data(await c.put(_PREF, json={'briefing_enabled': False, 'briefing_time': '07:30'}))
    assert data['briefing_enabled'] is False and data['briefing_time'] == '07:30'
    # 数据源更新
    data = _data(await c.put(_PREF, json={'briefing_sources': ['task', 'plan']}))
    assert data['briefing_sources'] == ['task', 'plan']
    # 时刻持久（上次 enabled=False 不被本次覆盖）
    assert data['briefing_enabled'] is False and data['briefing_time'] == '07:30'

    # 非法时刻 → 400
    assert (await c.put(_PREF, json={'briefing_time': '25:00'})).status_code == 400
    assert (await c.put(_PREF, json={'briefing_time': '8:00'})).status_code == 400
    # 非法数据源 → 400
    assert (await c.put(_PREF, json={'briefing_sources': ['task', 'bogus']})).status_code == 400


async def test_owner_isolation(env) -> None:
    """另一 owner 的偏好不串。切到 owner B（不同 user_id）GET 得默认空态。"""
    s, owner, c = env.session, env.owner, env.client
    s.add(_agent(f'a_iso_{_uid()}', owner, role='primary'))
    await s.flush()
    _data(await c.put(_PREF, json={'briefing_time': '06:00'}))

    # 切到 owner B（无分身、无偏好）
    owner_b = f'h_wbB_{_uid()}'
    user_b = _new_user_id()
    s.add(_human(owner_b, user_b, 'B'))
    await s.flush()
    env.auth_state['user_id'] = user_b  # 后续请求以 B 身份发起

    data_b = _data(await c.get(_PREF))
    assert data_b['owner_hasn_id'] == owner_b
    assert data_b['primary_agent_id'] is None, 'B 无分身 → None（零 fake）'
    assert data_b['briefing_time'] == '08:00', 'B 用默认时刻，不串 A 的 06:00'


async def test_builtin_tasks_catalog_lists_daily_briefing(env) -> None:
    """GET /home/builtin-tasks 返回启用目录 + 聚合版本；含种子 daily_briefing。

    种子由迁移 2026-06-05-seed-builtin-daily-briefing.sql 落库（committed）。
    """
    c = env.client
    data = _data(await c.get('/api/v1/hasn/app/home/builtin-tasks'))
    assert 'items' in data and 'catalog_revision' in data
    assert data['catalog_revision'] >= 1, '聚合版本应 ≥ 启用条目 revision 之和'
    daily = next((it for it in data['items'] if it['builtin_key'] == 'daily_briefing'), None)
    assert daily is not None, '种子 daily_briefing 应在目录'
    assert daily['schedule_type'] == 'cron'
    assert daily['schedule_config'] == {'expr': '0 8 * * *'}
    assert daily['skill_bundle'] == 'huanxing/workbench-briefing'
    assert daily['system_prompt'] and 'workbench.briefing.publish' in daily['system_prompt']
    # 内部字段不外泄（拉取面只给定义型字段）
    assert 'id' not in daily and 'created_time' not in daily
