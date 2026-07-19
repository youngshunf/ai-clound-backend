"""Agent JWT 本地原件快照上传 HTTP E2E。

真实走 Agent JWT+Redis、FastAPI multipart、PostgreSQL 与私有对象存储；不替换业务或存储边界。
数据库事务回滚，远端对象使用稳定内容 hash key，重复执行只覆盖同一对象。
"""

from __future__ import annotations

import uuid

from collections.abc import AsyncIterator
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.api.v1.agent.hasn_assets import router as agent_assets_router
from backend.app.hasn.model import HasnAssets
from backend.common.security.agent_jwt import create_agent_access_token, revoke_agent_token
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

pytestmark = pytest.mark.asyncio

_APP = FastAPI()
_APP.include_router(agent_assets_router, prefix='/api/v1/hasn/agent/assets')


@pytest_asyncio.fixture
async def e2e() -> AsyncIterator[SimpleNamespace]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    async with engine.connect() as conn:
        await conn.execute(select(1))

    session = async_sessionmaker(engine, expire_on_commit=False)()
    tag = uuid.uuid4().hex[:12]
    owner_hasn_id = f'h_upload_{tag}'
    agent_hasn_id = f'a_upload_{tag}'
    token = await create_agent_access_token(
        agent_hasn_id=agent_hasn_id,
        agent_name='图坊上传测试分身',
        owner_hasn_id=owner_hasn_id,
        owner_user_id=990001,
    )

    async def _yield_session() -> AsyncIterator[AsyncSession]:  # ruff: ignore[unused-async]
        yield session

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_APP),
        base_url='http://e2e',
        headers={'Authorization': f'Bearer {token.access_token}'},
    )
    try:
        yield SimpleNamespace(
            client=client,
            session=session,
            owner_hasn_id=owner_hasn_id,
            agent_hasn_id=agent_hasn_id,
        )
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await engine.dispose()
        await revoke_agent_token(agent_hasn_id, token.session_uuid)


async def test_agent_upload_uses_token_owner_and_is_idempotent(e2e: SimpleNamespace) -> None:
    """请求体不能冒充 owner；同内容改名重试仍只上传登记一次。"""
    content = b'huanxing-imagelab-agent-upload-http-live-e2e-v1'

    first = await e2e.client.post(
        '/api/v1/hasn/agent/assets/upload',
        files={'file': ('first.png', content, 'image/png')},
        data={'width': '2', 'height': '3', 'owner_hasn_id': 'h_attacker'},
    )
    assert first.status_code == 200, first.text
    first_data = first.json()['data']
    assert first_data['asset_uri'] == f'hasn://asset/{first_data["asset_id"]}'
    assert len(first_data['content_sha256']) == 64

    retry = await e2e.client.post(
        '/api/v1/hasn/agent/assets/upload',
        files={'file': ('renamed.png', content, 'image/png')},
        data={'width': '2', 'height': '3'},
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()['data']['asset_id'] == first_data['asset_id']

    row = (
        await e2e.session.execute(select(HasnAssets).where(HasnAssets.asset_id == first_data['asset_id']))
    ).scalar_one()
    assert row.owner_hasn_id == e2e.owner_hasn_id
    count = (
        await e2e.session.execute(
            select(func.count())
            .select_from(HasnAssets)
            .where(
                HasnAssets.owner_hasn_id == e2e.owner_hasn_id,
                HasnAssets.content_sha256 == first_data['content_sha256'],
            )
        )
    ).scalar_one()
    assert count == 1
