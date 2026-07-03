"""简报 dismiss 持久化 + 历史列表 真实 HTTP/service E2E（真实 PostgreSQL，零 mock）。

覆盖本轮新增（工作台历史 + dismiss 持久化）：
  - GET /home/briefing/latest 今日视图：dismiss 后刷新不再出现被忽略项（服务端 join 反馈过滤）；
    item_id 命中 或 source.ref 命中 或 计划 plan_id 命中 均过滤；dismissed_refs 出参。
  - include_dismissed=true：返回完整文档（含被忽略项）+ dismissed_refs（历史视图据此标「已忽略」）。
  - latest?period=YYYY-MM-DD：精确取某日（按日查看）。
  - GET /home/briefing/history：按 period 倒序列出，含 focus_count/plan_count/summary 预览。
  - owner 隔离：A 的历史不串到 B。

每测试唯一 user_id（避免提交型测试累积让 _resolve_owner_id 取错）。
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

from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.home.api.v1.app.home import router as app_workbench_router
from backend.app.home.service.hasn_workbench_briefing_service import hasn_workbench_briefing_service
from backend.common.exception.exception_handler import register_exception
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

pytestmark = pytest.mark.asyncio

_APP = FastAPI()
_APP.add_middleware(ContextMiddleware, plugins=(RequestIdPlugin(),))
register_exception(_APP)
_APP.include_router(app_workbench_router, prefix='/api/v1/hasn/app')

_BASE = '/api/v1/hasn/app/home/briefing'
_LATEST = f'{_BASE}/latest'
_HISTORY = f'{_BASE}/history'


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _new_user_id() -> int:
    return 940_000_000 + int(uuid.uuid4().int % 20_000_000)


def _human(hasn_id: str, user_id: int, nickname: str) -> HasnHumans:
    return HasnHumans(
        hasn_id=hasn_id, star_id=f's_{hasn_id}', user_id=user_id, nickname=f'{nickname}_{hasn_id[-6:]}', status='active'
    )


def _focus(item_id: str, ref: str, *, title: str = '关注项') -> dict:
    return {
        'item_id': item_id,
        'category': 'task',
        'urgency': 'high',
        'title': title,
        'summary': '',
        'source': {'app_id': 'tasks', 'ref': ref},
        'actions': [{'kind': 'dismiss', 'label': '知道了'}],
    }


def _doc(summary: str, period: str, *, focus: list[dict] | None = None, plans: list[dict] | None = None) -> dict:
    return {
        'summary': summary,
        'period': period,
        'focus_items': focus if focus is not None else [_focus('fi_keep', 'task:keep'), _focus('fi_drop', 'msg:drop')],
        'plans': plans if plans is not None else [{'plan_id': 'pl_1', 'title': '本周计划', 'horizon': 'week', 'steps': ['a', 'b'], 'actions': []}],
    }


@pytest_asyncio.fixture
async def env():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    user_id = _new_user_id()
    owner = f'h_bhx_{_uid()}'
    agent = f'a_brain_{_uid()}'
    session.add(_human(owner, user_id, '历史E2E'))
    await session.flush()

    auth_state = {'user_id': user_id}

    async def _yield_session():
        yield session

    async def _auth_inject(request: Request) -> str:
        request.scope['user'] = SimpleNamespace(id=auth_state['user_id'])
        request.scope['auth'] = ['authenticated']
        return 'e2e-token'

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[DependsJwtAuth.dependency] = _auth_inject

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield SimpleNamespace(
            client=client, owner=owner, agent=agent, session=session, user_id=user_id, auth_state=auth_state
        )
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


async def _publish(env, summary: str, period: str, **kw) -> None:
    await hasn_workbench_briefing_service.publish(
        db=env.session, owner_hasn_id=env.owner, agent_hasn_id=env.agent, document=_doc(summary, period, **kw)
    )
    await env.session.flush()


async def _dismiss(env, item_id: str, period: str, source_ref: str | None = None) -> None:
    body: dict = {'period': period, 'action': 'dismiss'}
    if source_ref is not None:
        body['source_ref'] = source_ref
    _data(await env.client.post(f'{_BASE}/items/{item_id}/dismiss', json=body))
    await env.session.flush()


async def test_dismiss_persists_filtered_from_latest(env) -> None:
    """核心 bug：dismiss 后刷新 latest 不再出现被忽略项（item_id 命中），保留项仍在。"""
    await _publish(env, '两件事', '2026-06-06')
    d0 = _data(await env.client.get(_LATEST))
    assert len(d0['document']['focus_items']) == 2 and d0['dismissed_refs'] == []

    await _dismiss(env, 'fi_drop', '2026-06-06', source_ref='msg:drop')

    d1 = _data(await env.client.get(_LATEST))
    ids = [i['item_id'] for i in d1['document']['focus_items']]
    assert ids == ['fi_keep'], f'被忽略项应被服务端过滤（刷新不回来）: {ids}'
    assert 'fi_drop' in d1['dismissed_refs'] and 'msg:drop' in d1['dismissed_refs']


async def test_dismiss_by_source_ref_filters(env) -> None:
    """source.ref 命中也过滤（即便 dismiss 时用了别的 item_id）。"""
    await _publish(env, '两件事', '2026-06-06')
    await _dismiss(env, 'some_other_id', '2026-06-06', source_ref='task:keep')
    d = _data(await env.client.get(_LATEST))
    ids = [i['item_id'] for i in d['document']['focus_items']]
    assert ids == ['fi_drop'], f'source.ref 命中应过滤 fi_keep: {ids}'


async def test_dismiss_plan_filters(env) -> None:
    """计划项 plan_id 命中 dismiss → latest 中该计划被过滤。"""
    await _publish(env, '有计划', '2026-06-06')
    await _dismiss(env, 'pl_1', '2026-06-06')
    d = _data(await env.client.get(_LATEST))
    assert d['document']['plans'] == [], '被忽略计划应过滤'
    # 关注项不受影响
    assert len(d['document']['focus_items']) == 2


async def test_include_dismissed_returns_full(env) -> None:
    """include_dismissed=true → 完整文档（含被忽略）+ dismissed_refs（历史视图据此标已忽略）。"""
    await _publish(env, '两件事', '2026-06-06')
    await _dismiss(env, 'fi_drop', '2026-06-06', source_ref='msg:drop')
    d = _data(await env.client.get(f'{_LATEST}?include_dismissed=true'))
    ids = sorted(i['item_id'] for i in d['document']['focus_items'])
    assert ids == ['fi_drop', 'fi_keep'], '历史视图返回完整文档（含已忽略）'
    assert 'fi_drop' in d['dismissed_refs']


async def test_latest_by_period_fetches_that_day(env) -> None:
    """latest?period=YYYY-MM-DD 精确取某日（按日查看历史）。"""
    await _publish(env, '四号简报', '2026-06-04')
    await _publish(env, '五号简报', '2026-06-05')
    d = _data(await env.client.get(f'{_LATEST}?period=2026-06-04'))
    assert d['period'] == '2026-06-04' and d['document']['summary'] == '四号简报'


async def test_history_lists_periods_desc_with_counts(env) -> None:
    """GET /history 按 period 倒序，含 focus_count/plan_count/summary 预览。"""
    await _publish(env, '四号', '2026-06-04', focus=[_focus('a', 'r:a')], plans=[])
    await _publish(env, '五号', '2026-06-05')  # 默认 2 focus + 1 plan
    await _publish(env, '六号', '2026-06-06', focus=[_focus('x', 'r:x')], plans=[])

    h = _data(await env.client.get(_HISTORY))
    periods = [e['period'] for e in h['items']]
    assert periods == ['2026-06-06', '2026-06-05', '2026-06-04'], f'按日倒序: {periods}'
    e5 = next(e for e in h['items'] if e['period'] == '2026-06-05')
    assert e5['focus_count'] == 2 and e5['plan_count'] == 1, e5
    assert e5['summary'] == '五号' and isinstance(e5['generated_at'], int)


async def test_history_owner_isolation(env) -> None:
    """A 有历史，切到 B → B 历史为空（owner 隔离）。"""
    await _publish(env, 'A 的简报', '2026-06-05')
    owner_b = f'h_bhxB_{_uid()}'
    user_b = _new_user_id()
    env.session.add(_human(owner_b, user_b, 'B'))
    await env.session.flush()
    env.auth_state['user_id'] = user_b
    h = _data(await env.client.get(_HISTORY))
    assert h['items'] == [], 'B 无历史，不串 A'
