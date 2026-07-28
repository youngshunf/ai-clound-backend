"""Agent JWT 用户云存储 HTTP E2E（真实 PostgreSQL、Redis 与七牛）。

测试不覆盖认证依赖，不注入伪造 Agent 身份；使用生产签发器生成 Agent JWT 并写入
真实 Redis 吊销表，经 FastAPI multipart 解析调用真实统一存储编排。需要
``DATABASE_PORT=15432`` 和开发环境已配置的七牛私有桶。
"""

from __future__ import annotations

import hashlib
import uuid

from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette_context.middleware import ContextMiddleware
from starlette_context.plugins import RequestIdPlugin

from backend.app.hasn.api.v1.agent.hasn_assets_agent import router as agent_assets_router
from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.common.exception.exception_handler import register_exception
from backend.common.security.agent_jwt import create_agent_access_token, revoke_agent_token
from backend.database.db import SQLALCHEMY_DATABASE_URL, async_db_session
from backend.plugin.s3.service.storage_service import StorageService

pytestmark = pytest.mark.asyncio

_APP = FastAPI()
_APP.include_router(agent_assets_router, prefix='/api/v1/hasn/agent/assets')
register_exception(_APP)
_APP.add_middleware(ContextMiddleware, plugins=[RequestIdPlugin(validate=False)])


def _suffix() -> str:
    return uuid.uuid4().hex[:12]


@pytest_asyncio.fixture
async def e2e():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    tag = _suffix()
    owner = f'h_agent_storage_{tag}'
    agent = f'a_agent_storage_{tag}'
    owner_user_id = 1_300_000_000 + int(uuid.uuid4().int % 600_000_000)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    session.add_all(
        [
            HasnHumans(
                hasn_id=owner,
                star_id=f's_agent_storage_{tag}',
                user_id=owner_user_id,
                nickname=f'Agent 存储 E2E {tag}',
                status='active',
            ),
            HasnAgents(
                hasn_id=agent,
                star_id=f'a_star_{tag}',
                owner_id=owner,
                display_name='存储验收分身',
                agent_name=f'storage_{tag}',
                type='desktop',
                runtime_location='local',
                role='specialist',
                api_key_hash='',
                status='active',
                created_via='client',
            ),
        ]
    )
    await session.execute(
        text(
            """
            INSERT INTO hasn_storage_accounts
                (owner_hasn_id, quota_bytes, used_bytes, reserved_bytes, quota_source,
                 quota_version, quota_valid_until, state, created_time)
            VALUES
                (:owner, 104857600, 0, 0, 'admin_override', 'agent-http-real-e2e',
                 now() + interval '1 hour', 'active', now())
            """
        ),
        {'owner': owner},
    )
    await session.commit()
    token = await create_agent_access_token(
        agent_hasn_id=agent,
        agent_name='存储验收分身',
        owner_hasn_id=owner,
        owner_user_id=owner_user_id,
    )
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield SimpleNamespace(
            client=client,
            owner=owner,
            agent=agent,
            owner_user_id=owner_user_id,
            token=token,
        )
    finally:
        await client.aclose()
        await revoke_agent_token(agent, token.session_uuid)
        async with async_db_session() as cleanup_db:
            objects = (
                await cleanup_db.execute(
                    text(
                        """
                        SELECT storage_id, object_key
                        FROM hasn_storage_objects
                        WHERE owner_hasn_id = :owner
                        """
                    ),
                    {'owner': owner},
                )
            ).mappings().all()
            for obj in objects:
                await StorageService.delete_object(
                    cleanup_db,
                    storage_id=int(obj['storage_id']),
                    object_key=str(obj['object_key']),
                )
        async with async_db_session.begin() as cleanup_db:
            await cleanup_db.execute(
                text('DELETE FROM hasn_storage_export_items WHERE owner_hasn_id = :owner'),
                {'owner': owner},
            )
            for table in (
                'hasn_storage_entries',
                'hasn_asset_bindings',
                'hasn_assets',
                'hasn_storage_objects',
                'hasn_storage_reservations',
                'hasn_storage_jobs',
                'hasn_storage_accounts',
            ):
                await cleanup_db.execute(
                    text(f'DELETE FROM {table} WHERE owner_hasn_id = :owner'),
                    {'owner': owner},
                )
            await cleanup_db.execute(
                text('DELETE FROM hasn_agents WHERE hasn_id = :agent'),
                {'agent': agent},
            )
            await cleanup_db.execute(
                text('DELETE FROM hasn_humans WHERE hasn_id = :owner'),
                {'owner': owner},
            )
        await session.close()
        await engine.dispose()


async def test_agent_jwt_upload_uses_claim_owner_and_real_revocation(e2e) -> None:
    """Agent 上传只认 JWT Owner；伪造用户头无效，吊销后同一 token 立即被拒绝。"""
    headers = {
        'Authorization': f'Bearer {e2e.token.access_token}',
        'Idempotency-Key': f'agent-storage-http-{_suffix()}',
        'X-User-Id': str(e2e.owner_user_id + 999),
    }
    response = await e2e.client.post(
        '/api/v1/hasn/agent/assets/upload',
        files={'file': ('分身报告.txt', '真实 Agent 上传内容'.encode(), 'text/plain')},
        data={'category': 'published_artifact', 'source_app': 'agent_http_real_e2e'},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    uploaded = response.json()['data']
    asset_id = uploaded['asset_id']
    assert uploaded['asset_uri'] == f'hasn://asset/{asset_id}'
    assert uploaded['content_sha256'] == hashlib.sha256('真实 Agent 上传内容'.encode()).hexdigest()

    async with async_db_session() as db:
        row = (
            await db.execute(
                text(
                    """
                    SELECT a.owner_hasn_id, a.source_app, o.object_key, o.size_bytes, o.state
                    FROM hasn_assets AS a
                    JOIN hasn_storage_objects AS o ON o.object_id = a.object_id
                    WHERE a.asset_id = :asset_id
                    """
                ),
                {'asset_id': asset_id},
            )
        ).mappings().one()
    assert row['owner_hasn_id'] == e2e.owner
    assert row['source_app'] == 'agent_http_real_e2e'
    assert row['state'] == 'active'
    assert int(row['size_bytes']) == len('真实 Agent 上传内容'.encode())
    assert str(row['object_key']).startswith(f'owners/{e2e.owner}/objects/obj_')
    assert '分身报告' not in str(row['object_key'])
    assert e2e.agent not in str(row['object_key'])

    await revoke_agent_token(e2e.agent, e2e.token.session_uuid)
    revoked = await e2e.client.post(
        '/api/v1/hasn/agent/assets/multipart',
        json={
            'declared_size': 1,
            'filename': '吊销后.txt',
            'mime': 'text/plain',
            'category': 'published_artifact',
            'source_app': 'agent_http_real_e2e',
        },
        headers={
            'Authorization': f'Bearer {e2e.token.access_token}',
            'Idempotency-Key': f'agent-storage-revoked-{_suffix()}',
        },
    )
    assert revoked.status_code == 401, revoked.text
