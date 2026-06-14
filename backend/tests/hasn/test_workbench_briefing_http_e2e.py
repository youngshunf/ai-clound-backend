"""P3 简报产出 真实 HTTP/service E2E（真实 PostgreSQL，零 mock）。

覆盖（设计 doc 04 §4/§5）：
  - service.publish：合 schema 落库（owner+period 覆盖式 upsert）、身份由凭证回填、
    覆盖当日最新一份；不合 schema → ValidationError（工具入口让模型重试）。
  - GET /workbench/briefing/latest：返回最新文档 / 空态如实（has_briefing=False，不造卡）。
  - POST /workbench/briefing/items/{item_id}/dismiss：写反馈。
  - owner 隔离：A 的简报/反馈不串到 B。

每测试唯一 user_id（避免提交型测试累积让 _resolve_owner_id 取错）。
"""
from __future__ import annotations

import uuid

from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI, Request
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette_context.middleware import ContextMiddleware
from starlette_context.plugins import RequestIdPlugin

from backend.app.workbench.api.v1.app.workbench import router as app_workbench_router
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.workbench.model.hasn_workbench_briefing import HasnWorkbenchBriefing
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

_LATEST = '/api/v1/hasn/app/workbench/briefing/latest'


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _new_user_id() -> int:
    return 960_000_000 + int(uuid.uuid4().int % 20_000_000)


def _human(hasn_id: str, user_id: int, nickname: str) -> HasnHumans:
    return HasnHumans(
        hasn_id=hasn_id, star_id=f's_{hasn_id}', user_id=user_id, nickname=f'{nickname}_{hasn_id[-6:]}', status='active'
    )


def _doc(summary: str, *, item_id: str = 'fi_1', period: str | None = None) -> dict:
    doc = {
        'summary': summary,
        'focus_items': [
            {
                'item_id': item_id,
                'category': 'task',
                'urgency': 'high',
                'title': '客户合同未回复',
                'summary': '挂了 3 天',
                'source': {'app_id': 'knowledge', 'ref': 'doc:1234'},
                'actions': [{'kind': 'dismiss', 'label': '知道了'}],
            }
        ],
        'plans': [],
    }
    if period:
        doc['period'] = period
    return doc


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
    owner = f'h_brf_{_uid()}'
    agent = f'a_brain_{_uid()}'
    session.add(_human(owner, user_id, '简报E2E'))
    await session.flush()

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


async def test_publish_valid_persists_and_get_latest_returns(env) -> None:
    """合 schema 发布 → 落库（身份回填）→ GET latest 返回文档。"""
    s, owner, agent, c = env.session, env.owner, env.agent, env.client
    row = await hasn_workbench_briefing_service.publish(
        db=s, owner_hasn_id=owner, agent_hasn_id=agent, document=_doc('今天 1 件事', period='2026-06-05')
    )
    await s.flush()
    assert row.owner_hasn_id == owner and row.agent_hasn_id == agent
    assert row.state == 'ready' and row.period == '2026-06-05'
    # 身份由凭证回填，绝不信任入参
    assert row.document_json['owner_id'] == owner and row.document_json['agent_id'] == agent
    assert row.document_json['briefing_id']  # 云端补 ULID

    data = _data(await c.get(_LATEST))
    assert data['has_briefing'] is True
    assert data['state'] == 'ready' and data['period'] == '2026-06-05'
    assert data['document']['summary'] == '今天 1 件事'
    assert len(data['document']['focus_items']) == 1


async def test_publish_overwrites_same_period(env) -> None:
    """同 owner+period 再发布 → 覆盖式 upsert（1 行，最新内容）。"""
    s, owner, agent = env.session, env.owner, env.agent
    await hasn_workbench_briefing_service.publish(
        db=s, owner_hasn_id=owner, agent_hasn_id=agent, document=_doc('旧', period='2026-06-05')
    )
    await s.flush()
    await hasn_workbench_briefing_service.publish(
        db=s, owner_hasn_id=owner, agent_hasn_id=agent, document=_doc('新', period='2026-06-05')
    )
    await s.flush()
    rows = (
        await s.execute(select(HasnWorkbenchBriefing).where(HasnWorkbenchBriefing.owner_hasn_id == owner))
    ).scalars().all()
    assert len(rows) == 1, '当日覆盖式：同 period 只一行'
    assert rows[0].document_json['summary'] == '新'


async def test_publish_invalid_schema_raises(env) -> None:
    """不合 schema（非法 category）→ ValidationError，不落库（工具入口让模型重试）。"""
    s, owner, agent = env.session, env.owner, env.agent
    bad = {'summary': 'x', 'focus_items': [{'item_id': 'fi', 'category': 'BOGUS', 'urgency': 'high', 'title': 't'}]}
    with pytest.raises(ValidationError):
        await hasn_workbench_briefing_service.publish(db=s, owner_hasn_id=owner, agent_hasn_id=agent, document=bad)
    # 缺必填 summary 也拒
    with pytest.raises(ValidationError):
        await hasn_workbench_briefing_service.publish(
            db=s, owner_hasn_id=owner, agent_hasn_id=agent, document={'focus_items': []}
        )


async def test_get_latest_empty_state(env) -> None:
    """无简报 → has_briefing=False（空态如实，不造卡）。"""
    data = _data(await env.client.get(_LATEST))
    assert data['has_briefing'] is False
    assert data['document'] is None and data['state'] is None


async def test_dismiss_records_feedback(env) -> None:
    """dismiss 关注项 → 写反馈行（owner+period+item_id）。"""
    s, owner, agent, c = env.session, env.owner, env.agent, env.client
    await hasn_workbench_briefing_service.publish(
        db=s, owner_hasn_id=owner, agent_hasn_id=agent, document=_doc('待处理', period='2026-06-05')
    )
    await s.flush()
    data = _data(
        await c.post(
            '/api/v1/hasn/app/workbench/briefing/items/fi_1/dismiss',
            json={'period': '2026-06-05', 'action': 'done', 'source_ref': 'doc:1234'},
        )
    )
    assert data['item_id'] == 'fi_1' and data['action'] == 'done'
    fb = (
        await s.execute(
            select(HasnWorkbenchBriefingFeedback).where(HasnWorkbenchBriefingFeedback.owner_hasn_id == owner)
        )
    ).scalars().all()
    assert len(fb) == 1
    assert fb[0].item_id == 'fi_1' and fb[0].action == 'done' and fb[0].source_ref == 'doc:1234'


async def test_owner_isolation(env) -> None:
    """A 发布简报，切到 B（无简报）→ B GET 得空态，不串 A。"""
    s, owner, agent, c = env.session, env.owner, env.agent, env.client
    await hasn_workbench_briefing_service.publish(
        db=s, owner_hasn_id=owner, agent_hasn_id=agent, document=_doc('A 的简报', period='2026-06-05')
    )
    await s.flush()

    owner_b = f'h_brfB_{_uid()}'
    user_b = _new_user_id()
    s.add(_human(owner_b, user_b, 'B'))
    await s.flush()
    env.auth_state['user_id'] = user_b

    data_b = _data(await c.get(_LATEST))
    assert data_b['has_briefing'] is False, 'B 无简报，不串 A（owner 隔离）'
