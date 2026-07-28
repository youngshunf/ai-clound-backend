"""Agent JWT 本地原件快照上传与投递 HTTP E2E。

真实走 Agent JWT+Redis、FastAPI、PostgreSQL、消息路由与私有对象存储；不替换业务或存储边界。
每个 HTTP 请求使用真实独立事务，测试结束按权威 ID 清理数据库；远端对象使用稳定内容 hash key，
重复执行只覆盖同一对象。
"""

from __future__ import annotations

import uuid

from collections.abc import AsyncIterator
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.api.v1.agent.hasn_assets import router as agent_assets_router
from backend.app.hasn.model import (
    HasnAgents,
    HasnAssetGrants,
    HasnAssets,
    HasnHumans,
    HasnMessages,
)
from backend.common.exception.errors import BaseExceptionError
from backend.common.security.agent_jwt import create_agent_access_token, revoke_agent_token
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

pytestmark = pytest.mark.asyncio(loop_scope='session')

_APP = FastAPI()
_APP.include_router(agent_assets_router, prefix='/api/v1/hasn/agent/assets')


@_APP.exception_handler(BaseExceptionError)
def _business_error(_request: Request, exc: BaseExceptionError) -> JSONResponse:
    """测试应用保留生产端业务异常的 HTTP 状态语义。"""
    return JSONResponse(
        status_code=exc.code,
        content={'code': exc.code, 'msg': exc.msg, 'data': exc.data},
    )


@pytest_asyncio.fixture(loop_scope='session')
async def e2e() -> AsyncIterator[SimpleNamespace]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.connect() as connection:
        await connection.execute(select(1))
    tag = uuid.uuid4().hex[:12]
    owner_hasn_id = f'h_upload_{tag}'
    agent_hasn_id = f'a_upload_{tag}'
    owner_user_id = 990001 + int(uuid.uuid4().int % 8000)
    async with maker.begin() as seed:
        seed.add_all([
            HasnHumans(
                hasn_id=owner_hasn_id,
                star_id=f's_upload_{tag}',
                user_id=owner_user_id,
                nickname='图坊上传测试主人',
                status='active',
            ),
            HasnAgents(
                hasn_id=agent_hasn_id,
                star_id=f'sa_upload_{tag}',
                owner_id=owner_hasn_id,
                display_name='图坊上传测试分身',
                agent_name='imagelab-upload-e2e',
                status='active',
            ),
        ])
    token = await create_agent_access_token(
        agent_hasn_id=agent_hasn_id,
        agent_name='图坊上传测试分身',
        owner_hasn_id=owner_hasn_id,
        owner_user_id=owner_user_id,
    )

    async def _yield_session() -> AsyncIterator[AsyncSession]:
        async with maker() as request_session:
            yield request_session

    _APP.dependency_overrides[get_db] = _yield_session

    async def _yield_transaction() -> AsyncIterator[AsyncSession]:
        async with maker.begin() as request_session:
            yield request_session

    _APP.dependency_overrides[get_db_transaction] = _yield_transaction
    session = maker()
    asset_ids: list[str] = []
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
            asset_ids=asset_ids,
        )
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await revoke_agent_token(agent_hasn_id, token.session_uuid)
        async with maker.begin() as cleanup:
            conversation_ids = (
                await cleanup.execute(
                    text(
                        'SELECT id FROM public.hasn_conversations '
                        'WHERE participant_a_id IN (:owner, :agent) '
                        'OR participant_b_id IN (:owner, :agent)'
                    ),
                    {'owner': owner_hasn_id, 'agent': agent_hasn_id},
                )
            ).scalars().all()
            if conversation_ids:
                ids = [str(conversation_id) for conversation_id in conversation_ids]
                await cleanup.execute(
                    text('DELETE FROM public.hasn_im_integration_events WHERE aggregate_id = ANY(:ids)'),
                    {'ids': ids},
                )
                await cleanup.execute(
                    text('DELETE FROM public.hasn_messages WHERE conversation_id = ANY(:ids)'),
                    {'ids': ids},
                )
                await cleanup.execute(
                    text('DELETE FROM public.hasn_unread_projection WHERE conversation_id = ANY(:ids)'),
                    {'ids': ids},
                )
                await cleanup.execute(
                    text('DELETE FROM public.hasn_conversation_memberships WHERE conversation_id = ANY(:ids)'),
                    {'ids': ids},
                )
                await cleanup.execute(
                    text('DELETE FROM public.hasn_asset_grants WHERE conversation_id = ANY(:ids)'),
                    {'ids': ids},
                )
                await cleanup.execute(
                    text('DELETE FROM public.hasn_conversations WHERE id = ANY(:ids)'),
                    {'ids': ids},
                )
            if asset_ids:
                await cleanup.execute(
                    text('DELETE FROM public.hasn_asset_grants WHERE asset_id = ANY(:ids)'),
                    {'ids': asset_ids},
                )
                await cleanup.execute(
                    text('DELETE FROM public.hasn_assets WHERE asset_id = ANY(:ids)'),
                    {'ids': asset_ids},
                )
            await cleanup.execute(
                text('DELETE FROM public.hasn_agents WHERE hasn_id = :agent'),
                {'agent': agent_hasn_id},
            )
            await cleanup.execute(
                text('DELETE FROM public.hasn_humans WHERE hasn_id = :owner'),
                {'owner': owner_hasn_id},
            )
        await engine.dispose()


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
    e2e.asset_ids.append(first_data['asset_id'])
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


async def test_agent_delivers_private_snapshot_once_with_stable_asset_uri(e2e: SimpleNamespace) -> None:
    """同一逐目标幂等键只落一条消息，并把私有资产授权给权威会话。"""
    upload = await e2e.client.post(
        '/api/v1/hasn/agent/assets/upload',
        files={'file': ('source.png', b'imagelab-delivery-live-e2e', 'image/png')},
        data={'width': '4', 'height': '5'},
    )
    assert upload.status_code == 200, upload.text
    asset = upload.json()['data']
    e2e.asset_ids.append(asset['asset_id'])
    payload = {
        'asset_uri': asset['asset_uri'],
        'target': e2e.owner_hasn_id,
        'idempotency_key': f'imagelab-share:{uuid.uuid4().hex}',
    }

    first = await e2e.client.post('/api/v1/hasn/agent/assets/deliver', json=payload)
    assert first.status_code == 200, first.text
    first_data = first.json()['data']
    assert first_data['status'] == 'sent'
    assert first_data['target'] == e2e.owner_hasn_id
    assert first_data['message_id']
    assert first_data['conversation_id']
    assert first_data['deduped'] is False

    retry = await e2e.client.post('/api/v1/hasn/agent/assets/deliver', json=payload)
    assert retry.status_code == 200, retry.text
    retry_data = retry.json()['data']
    assert retry_data['status'] == 'sent'
    assert retry_data['message_id'] == first_data['message_id']
    assert retry_data['conversation_id'] == first_data['conversation_id']
    assert retry_data['deduped'] is True

    rows = (
        (await e2e.session.execute(select(HasnMessages).where(HasnMessages.id == int(first_data['message_id']))))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    message = rows[0]
    assert message.local_id.startswith('imagelab-share:')
    assert message.local_id != payload['idempotency_key']
    duplicate_count = (
        await e2e.session.execute(
            select(func.count()).select_from(HasnMessages).where(HasnMessages.local_id == message.local_id)
        )
    ).scalar_one()
    assert duplicate_count == 1
    assert message.from_id == e2e.agent_hasn_id
    assert message.to_id == e2e.owner_hasn_id
    assert message.content['attachments'] == [
        {
            'uri': asset['asset_uri'],
            'kind': 'image',
            'mime': 'image/png',
            'name': '图坊图片',
            'size': len(b'imagelab-delivery-live-e2e'),
            'width': 4,
            'height': 5,
        }
    ]
    assert 'path' not in str(message.content).lower()

    grant_count = (
        await e2e.session.execute(
            select(func.count())
            .select_from(HasnAssetGrants)
            .where(
                HasnAssetGrants.asset_id == asset['asset_id'],
                HasnAssetGrants.conversation_id == message.conversation_id,
            )
        )
    ).scalar_one()
    assert grant_count == 1


async def test_agent_delivery_returns_deterministic_failure_for_missing_target(e2e: SimpleNamespace) -> None:
    """目标不存在是逐目标失败，不伪造成已发送，也不落消息。"""
    upload = await e2e.client.post(
        '/api/v1/hasn/agent/assets/upload',
        files={'file': ('source.bin', b'imagelab-missing-target-live-e2e', 'application/octet-stream')},
    )
    assert upload.status_code == 200, upload.text
    asset = upload.json()['data']
    e2e.asset_ids.append(asset['asset_id'])
    idempotency_key = f'imagelab-share:{uuid.uuid4().hex}'
    target = f'h_missing_{uuid.uuid4().hex[:12]}'

    before = (
        await e2e.session.execute(
            select(func.count()).select_from(HasnMessages).where(HasnMessages.from_id == e2e.agent_hasn_id)
        )
    ).scalar_one()
    response = await e2e.client.post(
        '/api/v1/hasn/agent/assets/deliver',
        json={
            'asset_uri': asset['asset_uri'],
            'target': target,
            'idempotency_key': idempotency_key,
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data == {
        'target': target,
        'idempotency_key': idempotency_key,
        'status': 'failed',
        'message_id': None,
        'conversation_id': None,
        'error_code': '3001',
        'error_message': data['error_message'],
        'deduped': False,
    }
    assert '不存在' in data['error_message']
    count = (
        await e2e.session.execute(
            select(func.count()).select_from(HasnMessages).where(HasnMessages.from_id == e2e.agent_hasn_id)
        )
    ).scalar_one()
    assert count == before


async def test_agent_cannot_deliver_another_owners_snapshot(e2e: SimpleNamespace) -> None:
    """投递端点重新校验资产主人，不能凭已知 asset URI 越权外发。"""
    upload = await e2e.client.post(
        '/api/v1/hasn/agent/assets/upload',
        files={'file': ('private.png', b'imagelab-foreign-owner-live-e2e', 'image/png')},
    )
    assert upload.status_code == 200, upload.text
    asset_id = upload.json()['data']['asset_id']
    e2e.asset_ids.append(asset_id)
    before = (
        await e2e.session.execute(
            select(func.count()).select_from(HasnMessages).where(HasnMessages.from_id == e2e.agent_hasn_id)
        )
    ).scalar_one()
    asset = (await e2e.session.execute(select(HasnAssets).where(HasnAssets.asset_id == asset_id))).scalar_one()
    asset.owner_hasn_id = f'h_other_{uuid.uuid4().hex[:12]}'
    await e2e.session.commit()

    response = await e2e.client.post(
        '/api/v1/hasn/agent/assets/deliver',
        json={
            'asset_uri': f'hasn://asset/{asset_id}',
            'target': e2e.owner_hasn_id,
            'idempotency_key': f'imagelab-share:{uuid.uuid4().hex}',
        },
    )
    assert response.status_code == 403, response.text
    assert response.json()['msg'] == '无权投递其他主人的原件快照'
    count = (
        await e2e.session.execute(
            select(func.count()).select_from(HasnMessages).where(HasnMessages.from_id == e2e.agent_hasn_id)
        )
    ).scalar_one()
    assert count == before
