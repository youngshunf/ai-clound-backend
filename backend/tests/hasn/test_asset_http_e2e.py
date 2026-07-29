"""资产上传/解析进程内 HTTP E2E（真实 PostgreSQL + 真实对象存储）。

最小 app 挂真实 assets app 路由，dependency_overrides 注入 owner + 真实 PG 会话；
经 ASGITransport 走完整 FastAPI HTTP 栈（multipart 解析 + 依赖注入 + 统一信封），
覆盖 upload→落消息写 grant→resolve 三态——正是 service 层测不到的路由依赖/外壳漂移层。

测试写入真实七牛私有桶，并按 Owner 精确删除对象和账本；需要 export DATABASE_PORT=15432。
"""

from __future__ import annotations

import uuid

from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI, Request
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.api.v1.app.hasn_assets_app import router as assets_router
from backend.app.hasn.model import HasnAssetBindings, HasnAssets, HasnConversations
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn_im.application.message_service import persist_message
from backend.common.exception import errors
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import (
    SQLALCHEMY_DATABASE_URL,
    async_db_session,
    get_db,
    get_db_transaction,
)
from backend.plugin.s3.service.storage_service import StorageService

pytestmark = pytest.mark.asyncio

_APP = FastAPI()
_APP.include_router(assets_router, prefix='/api/v1/hasn/app/assets')


def _uid() -> str:
    return uuid.uuid4().hex[:10]


@pytest_asyncio.fixture
async def e2e():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()

    # 三个隔离测试身份与一个配额账户；提交后统一存储服务的独立事务才能看到它们。
    uid_a = 1_200_000_000 + int(uuid.uuid4().int % 800_000_000)
    uid_b, uid_c = uid_a + 1, uid_a + 2
    owner_a, peer_b, stranger_c = f'h_a_{_uid()}', f'h_b_{_uid()}', f'h_c_{_uid()}'
    session.add_all(
        [
            HasnHumans(
                hasn_id=owner_a,
                star_id=f's_{uid_a}',
                user_id=uid_a,
                nickname=f'资产HTTP_A_{owner_a[-10:]}',
                status='active',
            ),
            HasnHumans(
                hasn_id=peer_b,
                star_id=f's_{uid_b}',
                user_id=uid_b,
                nickname=f'资产HTTP_B_{peer_b[-10:]}',
                status='active',
            ),
            HasnHumans(
                hasn_id=stranger_c,
                star_id=f's_{uid_c}',
                user_id=uid_c,
                nickname=f'资产HTTP_C_{stranger_c[-10:]}',
                status='active',
            ),
        ]
    )
    conv = HasnConversations(
        type='direct', participant_a_id=owner_a, participant_a_type='human',
        participant_b_id=peer_b, participant_b_type='human',
    )
    session.add(conv)
    await session.flush()
    conversation_id = str(conv.id)
    await session.execute(
        text(
            """
            INSERT INTO hasn_storage_accounts
                (owner_hasn_id, quota_bytes, used_bytes, reserved_bytes, quota_source,
                 quota_version, quota_valid_until, state, created_time)
            VALUES
                (:owner, 104857600, 0, 0, 'admin_override', 'asset-http-e2e',
                 now() + interval '1 hour', 'active', now())
            """
        ),
        {'owner': owner_a},
    )
    await session.commit()

    current = {'uid': uid_a}

    async def _yield_session():
        yield session

    async def _auth_inject(request: Request) -> str:
        request.scope['user'] = SimpleNamespace(id=current['uid'])
        request.scope['auth'] = ['authenticated']
        return 'e2e-token'

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[DependsJwtAuth.dependency] = _auth_inject

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield SimpleNamespace(
            client=client, session=session, current=current,
            owner_a=owner_a, peer_b=peer_b, stranger_c=stranger_c,
            uid_a=uid_a, uid_b=uid_b, uid_c=uid_c, conv_id=conversation_id,
        )
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
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
                    {'owner': owner_a},
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
                text('DELETE FROM hasn_messages WHERE conversation_id = CAST(:conversation_id AS uuid)'),
                {'conversation_id': conversation_id},
            )
            await cleanup_db.execute(
                text('DELETE FROM hasn_asset_grants WHERE conversation_id = CAST(:conversation_id AS uuid)'),
                {'conversation_id': conversation_id},
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
                    text(f'DELETE FROM {table} WHERE owner_hasn_id = :owner'),  # noqa: S608
                    {'owner': owner_a},
                )
            await cleanup_db.execute(
                text('DELETE FROM hasn_conversations WHERE id = CAST(:conversation_id AS uuid)'),
                {'conversation_id': conversation_id},
            )
            await cleanup_db.execute(
                text('DELETE FROM hasn_humans WHERE hasn_id = ANY(CAST(:owners AS varchar[]))'),
                {'owners': [owner_a, peer_b, stranger_c]},
            )
        await engine.dispose()


async def test_upload_send_resolve_three_state(e2e) -> None:
    c = e2e.client

    # 1) owner A 经真实 HTTP 上传一张图（私有桶 dm_attachment）
    e2e.current['uid'] = e2e.uid_a
    resp = await c.post(
        '/api/v1/hasn/app/assets/upload',
        files={'file': ('photo.png', b'fake-image-bytes', 'image/png')},
        data={'width': '120', 'height': '80'},
        headers={'Idempotency-Key': f'asset-http-image-{e2e.owner_a}'},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['code'] == 200  # 统一信封
    asset = body['data']
    assert asset['kind'] == 'image' and asset['width'] == 120 and asset['mime'] == 'image/png'
    asset_id = asset['asset_id']
    await e2e.session.flush()

    # 2) 落消息：同一事务自动写会话 grant 与删除保护 binding。
    message = await persist_message(
        e2e.session,
        e2e.conv_id,
        e2e.owner_a,
        e2e.peer_b,
        {'attachments': [{'uri': f'hasn://asset/{asset_id}', 'kind': 'image', 'mime': 'image/png'}]},
    )
    await e2e.session.flush()
    binding = await e2e.session.execute(
        select(HasnAssetBindings).where(
            HasnAssetBindings.asset_id == asset_id,
            HasnAssetBindings.resource_uri
            == f'hasn://messages/c/{e2e.conv_id}#{message.id}',
            HasnAssetBindings.status == 'active',
        )
    )
    assert binding.scalar_one().role == 'attachment'

    async def _resolve(uid: int) -> set[str]:
        e2e.current['uid'] = uid
        r = await c.post(
            '/api/v1/hasn/app/assets/resolve',
            json={'asset_ids': [asset_id], 'conversation_id': e2e.conv_id},
        )
        assert r.status_code == 200, r.text
        assert r.json()['code'] == 200
        return {item['asset_id'] for item in r.json()['data']}

    # 3) 鉴权三态（全经真实 HTTP）
    assert await _resolve(e2e.uid_a) == {asset_id}  # owner ✓
    assert await _resolve(e2e.uid_b) == {asset_id}  # 会话参与者 + 已 grant ✓
    assert await _resolve(e2e.uid_c) == set()       # 陌生人 ✗（私有不返回）

    # resolve 私有项带签名 URL + expires_at
    e2e.current['uid'] = e2e.uid_b
    r = await c.post('/api/v1/hasn/app/assets/resolve', json={'asset_ids': [asset_id], 'conversation_id': e2e.conv_id})
    item = r.json()['data'][0]
    assert item['display_url'].startswith('https://')
    assert item['expires_at'] is not None


async def test_upload_published_artifact_skips_extract(e2e) -> None:
    """模块 18：category=published_artifact 落私有桶且 extract_status=done（不进抽取流水线）。"""
    e2e.current['uid'] = e2e.uid_a
    resp = await e2e.client.post(
        '/api/v1/hasn/app/assets/upload',
        files={'file': ('site.html', b'<!doctype html><h1>hi</h1>', 'text/html')},
        data={'category': 'published_artifact'},
        headers={'Idempotency-Key': f'asset-http-published-{e2e.owner_a}'},
    )
    assert resp.status_code == 200, resp.text
    asset_id = resp.json()['data']['asset_id']
    await e2e.session.flush()

    row = (
        await e2e.session.execute(select(HasnAssets).where(HasnAssets.asset_id == asset_id))
    ).scalar_one()
    # 私有桶 + 不抽取（已 done，跳过语义抽取流水线）
    assert row.access == 'private'
    assert row.extract_status == 'done'
    assert row.kind == 'file'


async def test_upload_rejects_unknown_category(e2e) -> None:
    """白名单外 category 直接抛 RequestError（真实外壳落 400），不静默落库。"""
    e2e.current['uid'] = e2e.uid_a
    with pytest.raises(errors.RequestError):
        await e2e.client.post(
            '/api/v1/hasn/app/assets/upload',
            files={'file': ('x.bin', b'data', 'application/octet-stream')},
            data={'category': 'arbitrary_evil'},
            headers={'Idempotency-Key': f'asset-http-invalid-{e2e.owner_a}'},
        )
