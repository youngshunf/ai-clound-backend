"""P6 工作台「每日关注简报 + 应用入口」全链路 E2E（真实 PostgreSQL，零 mock）。

把 P1→P5 的云端权威面在**一个场景**里串起来跑通（设计 doc 04 §10 DoD）：
  1. 主脑默认回落（role=primary）→ 改主脑持久 + explicit。
  2. 简报 publish（合 schema 落库）→ GET latest 取回完整文档（四类操作齐全）。
  3. dismiss 关注项 → 写反馈（喂下次去重）。
  4. 应用注册即用：/workbench/apps 列全部已注册；/workbench/apps/{id}/entry 返回
     gateway_internal 句柄 + entry_route，且**不含 credential**（凭据不下发浏览器）。

LLM 触发的「cron→runtime→publish」自动生成属运行时职责，其唯一契约即调用 publish 工具，
本测试在 publish 边界直接验证（零 mock：真实 service 真实落库），不引入不确定的活体 LLM。
daemon→云端代理路径另由 daemon 契约测试锁定（briefing/latest、apps/{id}/entry）。
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

from backend.app.workbench.api.v1.app.workbench import router as app_workbench_router
from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.workbench.model.hasn_workbench_briefing_feedback import HasnWorkbenchBriefingFeedback
from backend.app.workbench.service.hasn_workbench_briefing_service import hasn_workbench_briefing_service
from backend.common.exception.exception_handler import register_exception
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

pytestmark = pytest.mark.asyncio

_APP = FastAPI()
_APP.add_middleware(ContextMiddleware, plugins=(RequestIdPlugin(),))
register_exception(_APP)
_APP.include_router(app_workbench_router, prefix='/api/v1/hasn/app')

_BASE = '/api/v1/hasn/app/workbench'


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _new_user_id() -> int:
    return 940_000_000 + int(uuid.uuid4().int % 25_000_000)


def _human(hasn_id: str, user_id: int) -> HasnHumans:
    return HasnHumans(
        hasn_id=hasn_id, star_id=f's_{hasn_id}', user_id=user_id, nickname=f'全链路_{hasn_id[-6:]}', status='active'
    )


def _agent(hasn_id: str, owner: str, *, role: str) -> HasnAgents:
    return HasnAgents(
        hasn_id=hasn_id,
        star_id=f's_{hasn_id}',
        owner_id=owner,
        display_name=f'分身{hasn_id[-4:]}',
        agent_name=hasn_id,
        role=role,
        status='active',
    )


def _doc(summary: str, *, agent: str) -> dict:
    """一份覆盖四类操作的简报文档。"""
    return {
        'agent_id': agent,
        'period': '2026-06-05',
        'state': 'ready',
        'summary': summary,
        'focus_items': [
            {
                'item_id': 'fi_task',
                'category': 'task',
                'urgency': 'high',
                'title': '财报汇总待你确认',
                'summary': '分身已拆完，等待你过目',
                'source': {'app_id': 'tasks', 'ref': 'task:T-12', 'deep_link': '/tasks/T-12'},
                'evidence': ['已生成 3 张图表'],
                'actions': [
                    {'kind': 'run_task', 'label': '让分身续写', 'prompt': '把财报续写成周报', 'confirm': True},
                    {'kind': 'open_route', 'label': '查看任务', 'route': '/tasks/T-12'},
                    {'kind': 'dismiss', 'label': '忽略'},
                ],
            },
            {
                'item_id': 'fi_social',
                'category': 'social',
                'urgency': 'low',
                'title': '李四给你的分身留言',
                'summary': '一条普通问候',
                'source': {'app_id': 'messages', 'ref': 'msg:88'},
                'evidence': [],
                'actions': [{'kind': 'open_app', 'label': '打开消息', 'app_id': 'community', 'deep_link': '/community'}],
            },
        ],
        'plans': [
            {
                'plan_id': 'pl_1',
                'title': '本周目标',
                'horizon': 'week',
                'steps': ['完成财报', '回复李四'],
                'actions': [],
            }
        ],
    }


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
    owner = f'h_e2e_{_uid()}'
    session.add(_human(owner, user_id))
    await session.flush()

    async def _yield_session():
        yield session

    async def _auth_inject(request: Request):
        request.scope['user'] = SimpleNamespace(id=user_id)
        request.scope['auth'] = ['authenticated']
        return 'e2e-token'

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[DependsJwtAuth.dependency] = _auth_inject

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield SimpleNamespace(client=client, owner=owner, session=session, user_id=user_id)
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


async def test_workbench_briefing_and_apps_full_flow(env) -> None:
    s, owner, c = env.session, env.owner, env.client

    # —— P1 主脑：默认回落 role=primary，改主脑持久 + explicit ——
    primary = f'a_pri_{_uid()}'
    chosen = f'a_chosen_{_uid()}'
    s.add(_agent(primary, owner, role='primary'))
    s.add(_agent(chosen, owner, role='specialist'))
    await s.flush()

    pref = _data(await c.get(f'{_BASE}/pref'))
    assert pref['primary_agent_id'] == primary and pref['primary_agent_explicit'] is False

    pref = _data(
        await c.put(
            f'{_BASE}/pref',
            json={'primary_agent_id': chosen, 'briefing_time': '09:30', 'briefing_sources': ['task', 'social']},
        )
    )
    assert pref['primary_agent_id'] == chosen and pref['primary_agent_explicit'] is True
    assert pref['briefing_time'] == '09:30' and pref['briefing_sources'] == ['task', 'social']

    # —— P3 简报：主脑 publish（合 schema 落库）→ GET latest 取回完整文档 ——
    row = await hasn_workbench_briefing_service.publish(
        db=s, owner_hasn_id=owner, agent_hasn_id=chosen, document=_doc('今天有 2 件事值得关注。', agent=chosen)
    )
    assert row.owner_hasn_id == owner and row.agent_hasn_id == chosen
    await s.flush()

    latest = _data(await c.get(f'{_BASE}/briefing/latest'))
    assert latest['has_briefing'] is True and latest['state'] == 'ready'
    doc = latest['document']
    assert doc['summary'] == '今天有 2 件事值得关注。'
    cats = {f['category'] for f in doc['focus_items']}
    assert cats == {'task', 'social'}
    kinds = {a['kind'] for f in doc['focus_items'] for a in f['actions']}
    assert {'run_task', 'open_route', 'dismiss', 'open_app'} <= kinds, '四类操作齐全'
    assert doc['plans'][0]['horizon'] == 'week'

    # —— P3 dismiss：写反馈（喂下次去重）——
    _data(
        await c.post(
            f'{_BASE}/briefing/items/fi_social/dismiss',
            json={'period': '2026-06-05', 'action': 'dismiss', 'source_ref': 'msg:88'},
        )
    )
    fb = (
        await s.execute(
            select(HasnWorkbenchBriefingFeedback).where(
                HasnWorkbenchBriefingFeedback.owner_hasn_id == owner,
                HasnWorkbenchBriefingFeedback.item_id == 'fi_social',
            )
        )
    ).scalars().first()
    assert fb is not None and fb.source_ref == 'msg:88'

    # —— P5 应用入口：注册即用 + entry 句柄无凭据 ——
    apps = _data(await c.get(f'{_BASE}/apps'))
    ids = {a['id'] for a in apps}
    assert {'knowledge', 'community'} <= ids, '列出全部已注册应用'

    entry = _data(await c.get(f'{_BASE}/apps/knowledge/entry'))
    assert entry['transport'] == 'gateway_internal'
    assert entry['entry_route'] == '/workbench/apps/knowledge'
    assert entry['requires_credential'] is False
    assert 'credential' not in entry, '凭据不下发浏览器'

    # 未知应用如实 404。
    assert (await c.get(f'{_BASE}/apps/nope/entry')).status_code == 404
