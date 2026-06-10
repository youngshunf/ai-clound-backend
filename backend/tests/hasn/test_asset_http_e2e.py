"""资产上传/解析 进程内 HTTP E2E（09 Stage1 收尾，真实 PG，零 mock 业务）。

最小 app 挂真实 assets app 路由，dependency_overrides 注入 owner + 真实 PG 会话；
经 ASGITransport 走完整 FastAPI HTTP 栈（multipart 解析 + 依赖注入 + 统一信封），
覆盖 upload→落消息写 grant→resolve 三态——正是 service 层测不到的路由依赖/外壳漂移层。

只 mock S3 网络边界（write_bytes 写 / signed_urls_cached 签名），不碰真实七牛；事务末尾回滚不污染库。
需要 export DATABASE_PORT=15432。
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

from backend.app.hasn.api.v1.app.hasn_assets_app import router as assets_router
from backend.app.hasn.model import HasnAssets, HasnConversations
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.service import hasn_asset_service as asset_svc_mod
from backend.app.hasn.service.message_router import _grant_private_attachments
from backend.common.exception import errors
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction
from backend.plugin.s3.model import S3Storage
from backend.plugin.s3.service import storage_service as storage_mod

pytestmark = pytest.mark.asyncio

_APP = FastAPI()
_APP.include_router(assets_router, prefix='/api/v1/hasn/app/assets')


def _uid() -> str:
    return uuid.uuid4().hex[:10]


async def _fake_sign(_db, *, items, expires_in=3600):
    return {it: f'https://signed/{it[1]}?e={expires_in}' for it in items}


@pytest_asyncio.fixture
async def e2e(monkeypatch):
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()

    # 三个真实身份 + 一个私有存储空间（同一回滚事务内 seed）
    uid_a = 980000 + int(uuid.uuid4().int % 9000)
    uid_b, uid_c = uid_a + 1, uid_a + 2
    owner_a, peer_b, stranger_c = f'h_a_{_uid()}', f'h_b_{_uid()}', f'h_c_{_uid()}'
    session.add_all([
        HasnHumans(hasn_id=owner_a, star_id=f's_{uid_a}', user_id=uid_a, nickname='A', status='active'),
        HasnHumans(hasn_id=peer_b, star_id=f's_{uid_b}', user_id=uid_b, nickname='B', status='active'),
        HasnHumans(hasn_id=stranger_c, star_id=f's_{uid_c}', user_id=uid_c, nickname='C', status='active'),
    ])
    priv_storage = S3Storage(
        name='e2e-private', endpoint='https://oss.test', access_key='ak', secret_key='sk',
        bucket='priv', prefix='hx', region='any', access='private', sign_strategy='s3_presign',
    )
    session.add(priv_storage)
    conv = HasnConversations(
        type='direct', participant_a_id=owner_a, participant_a_type='human',
        participant_b_id=peer_b, participant_b_type='human',
    )
    session.add(conv)
    await session.flush()

    # 只 mock S3 网络边界：write 不落真实七牛、签名不打真实 S3/Redis
    async def _noop_write(*a, **k) -> None:
        return None

    monkeypatch.setattr(storage_mod, 'write_bytes', _noop_write)
    monkeypatch.setattr(
        asset_svc_mod.StorageService, 'signed_urls_cached',
        classmethod(lambda cls, db, **kw: _fake_sign(db, **kw)), raising=True,
    )

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
            uid_a=uid_a, uid_b=uid_b, uid_c=uid_c, conv_id=str(conv.id),
        )
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await engine.dispose()


async def test_upload_send_resolve_three_state(e2e) -> None:
    c = e2e.client

    # 1) owner A 经真实 HTTP 上传一张图（私有桶 dm_attachment）
    e2e.current['uid'] = e2e.uid_a
    resp = await c.post(
        '/api/v1/hasn/app/assets/upload',
        files={'file': ('photo.png', b'fake-image-bytes', 'image/png')},
        data={'width': '120', 'height': '80'},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body['code'] == 200  # 统一信封
    asset = body['data']
    assert asset['kind'] == 'image' and asset['width'] == 120 and asset['mime'] == 'image/png'
    asset_id = asset['asset_id']
    await e2e.session.flush()

    # 2) 落消息：为该私有附件按会话写 grant（1f 真实路径）
    await _grant_private_attachments(
        e2e.session, e2e.conv_id, {'attachments': [{'uri': f'hasn://asset/{asset_id}'}]}
    )
    await e2e.session.flush()

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
    assert item['display_url'].startswith('https://signed/')
    assert item['expires_at'] is not None


async def test_upload_published_artifact_skips_extract(e2e) -> None:
    """模块 18：category=published_artifact 落私有桶且 extract_status=done（不进抽取流水线）。"""
    e2e.current['uid'] = e2e.uid_a
    resp = await e2e.client.post(
        '/api/v1/hasn/app/assets/upload',
        files={'file': ('site.html', b'<!doctype html><h1>hi</h1>', 'text/html')},
        data={'category': 'published_artifact'},
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
        )
