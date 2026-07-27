"""通用网页发布与分享（模块 18，P3）托管 + 公开查看 /s/{slug} 进程内 HTTP E2E（真实 PG，零 mock）。

覆盖（DoD）：
  - /content CSP：sandbox allow-scripts + 显式 host-source（opaque origin 不写 'self'）+ connect-src 'none' 断外联
  - 四态访问：private 凭票 / unlisted 凭链接 / password 限速+票 / public 直放；缺票 401
  - 过期 410 / 撤销 410 / 不存在 404
  - unlisted/private/password 恒 X-Robots-Tag noindex；public(默认不收录)亦 noindex；public+allow_indexing 才放收录
  - /unlock：错口令 401，对口令发短时访问票，凭票 ?vt= 放行
  - 真实 single-html /content 流式代吐（上传真实私有桶；不可达则 skip，零 fake）

需要 export DATABASE_PORT=15432。
"""

from __future__ import annotations

import uuid

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn_publish.api.v1.open.hosting import _content_csp, _share_origin
from backend.app.hasn_publish.api.v1.open.hosting import router as hosting_router
from backend.app.hasn_publish.service.publish_service import publish_service
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

_APP = FastAPI()
_APP.include_router(hosting_router)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


# ---------- CSP / origin（纯函数，安全红线） ----------


def test_content_csp_is_sandbox_with_host_source() -> None:
    csp = _content_csp('https://share.example.com')
    assert csp.startswith('sandbox allow-scripts')  # 直开导航也沙箱（opaque origin）
    assert "connect-src 'none'" in csp  # 断数据外联
    assert 'https://share.example.com' in csp  # 显式 host-source（不是 'self'）
    assert "img-src 'self'" not in csp  # opaque origin 下 'self' 永不匹配，绝不用
    assert "base-uri 'none'" in csp
    assert "form-action 'none'" in csp


def test_content_csp_no_allow_same_origin() -> None:
    # sandbox 不含 allow-same-origin（否则脚本可读父页）
    csp = _content_csp('https://share.example.com')
    assert 'allow-same-origin' not in csp


# ---------- E2E（真实 PG） ----------


@pytest_asyncio.fixture
async def host() -> AsyncIterator[SimpleNamespace]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    tag = _uid()
    owner = f'h_host_{tag}'
    owner_uid = 9_800_000_000 + int(uuid.uuid4().int % 1_000_000_000)
    session.add(HasnHumans(hasn_id=owner, star_id=f's_{owner_uid}', user_id=owner_uid, nickname='H', status='active'))
    await session.flush()

    async def _yield_session() -> AsyncIterator:  # noqa: RUF029
        yield session

    _APP.dependency_overrides[get_db] = _yield_session
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_APP),
        base_url='http://share.test',
        headers={'x-forwarded-for': f'2001:db8::{tag}'},
    )
    try:
        yield SimpleNamespace(client=client, session=session, owner=owner)
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await engine.dispose()


async def _make_site(host: SimpleNamespace, *, visibility: str, password: str | None = None, **extra: Any) -> dict:
    data = await publish_service.create_site(
        host.session,
        owner_id=host.owner,
        kind='page',
        title='测试发布',
        asset_id=f'ast_{_uid()}',
        content_hash=_uid(),
        size_bytes=10,
        visibility=visibility,
        password=password,
        **extra,
    )
    await host.session.flush()
    return data['site']


async def test_unlisted_shell_and_noindex(host: SimpleNamespace) -> None:
    site = await _make_site(host, visibility='unlisted')
    r = await host.client.get(f'/s/{site["slug"]}')
    assert r.status_code == 200, r.text
    assert '测试发布' in r.text
    assert 'sandbox="allow-scripts"' in r.text  # 外壳用 sandbox iframe
    # iframe 必须委派 fullscreen 权限：否则沙箱 opaque origin 子帧内「放映」requestFullscreen 被静默拒绝，
    # 只剩 CSS 放映态（铺满 iframe≠真·全屏）→ 用户报「点放映只是浏览器全屏」。回归守卫。
    assert 'allow="fullscreen"' in r.text
    assert r.headers.get('X-Robots-Tag') == 'noindex, nofollow'  # unlisted 恒 noindex
    csp = r.headers.get('Content-Security-Policy', '')
    assert 'frame-ancestors' in csp
    # 外壳是可信生成 HTML：CSP 必须放行其**自身内联** style/script，否则 #frame 100% 被拦
    # → iframe 退化默认尺寸、演示只剩一小块（回归守卫）。
    assert "style-src 'unsafe-inline'" in csp
    assert "script-src 'unsafe-inline'" in csp


async def test_public_indexing_toggle(host: SimpleNamespace) -> None:
    # public 默认不收录 → noindex
    s1 = await _make_site(host, visibility='public')
    r = await host.client.get(f'/s/{s1["slug"]}')
    assert r.status_code == 200
    assert r.headers.get('X-Robots-Tag') == 'noindex, nofollow'
    # public + allow_indexing → 放收录
    s2 = await _make_site(host, visibility='public', allow_indexing=True)
    r = await host.client.get(f'/s/{s2["slug"]}')
    assert r.status_code == 200
    assert 'X-Robots-Tag' not in r.headers


async def test_private_requires_ticket(host: SimpleNamespace) -> None:
    site = await _make_site(host, visibility='private')
    # 无票 → 401
    r = await host.client.get(f'/s/{site["slug"]}')
    assert r.status_code == 401, r.text
    # 有效票 → 200
    issued = publish_service.issue_view_ticket(site_id=site['id'], owner_id=host.owner)
    r = await host.client.get(f'/s/{site["slug"]}', params={'vt': issued['ticket']})
    assert r.status_code == 200, r.text
    # 别的 site 的票对本 site 无效
    other = await _make_site(host, visibility='private')
    r = await host.client.get(f'/s/{other["slug"]}', params={'vt': issued['ticket']})
    assert r.status_code == 401


async def test_password_flow(host: SimpleNamespace) -> None:
    site = await _make_site(host, visibility='password', password='letmein')
    # 无票 → 输口令页（200 HTML）
    r = await host.client.get(f'/s/{site["slug"]}')
    assert r.status_code == 200
    assert '口令' in r.text
    assert r.headers.get('X-Robots-Tag') == 'noindex, nofollow'
    # 错口令 → 401
    r = await host.client.post(f'/s/{site["slug"]}/unlock', json={'password': 'wrong'})
    assert r.status_code == 401, r.text
    # 对口令 → 发票
    r = await host.client.post(f'/s/{site["slug"]}/unlock', json={'password': 'letmein'})
    assert r.status_code == 200, r.text
    ticket = r.json()['data']['ticket']
    # 凭票放行
    r = await host.client.get(f'/s/{site["slug"]}', params={'vt': ticket})
    assert r.status_code == 200


async def test_not_found_and_revoked_and_expired(host: SimpleNamespace) -> None:
    # 不存在 → 404
    r = await host.client.get('/s/doesnotexist123')
    assert r.status_code == 404, r.text

    # 撤销 → 410
    site = await _make_site(host, visibility='public')
    await publish_service.revoke(host.session, owner_id=host.owner, site_id=site['id'])
    await host.session.flush()
    r = await host.client.get(f'/s/{site["slug"]}')
    assert r.status_code == 410, r.text

    # 过期 → 410
    expired = await _make_site(host, visibility='public', expires_at=datetime.now(UTC) - timedelta(hours=1))
    r = await host.client.get(f'/s/{expired["slug"]}')
    assert r.status_code == 410, r.text


async def test_content_auth_gate_before_read(host: SimpleNamespace) -> None:
    # private /content 无票 → 401（在读私有桶之前就拒，不泄露内容）
    site = await _make_site(host, visibility='private')
    r = await host.client.get(f'/s/{site["slug"]}/content')
    assert r.status_code == 401, r.text


async def test_content_streams_real_artifact_with_csp(host: SimpleNamespace) -> None:
    """真实 single-html /content：上传真制品到私有桶 → 取回带 CSP sandbox 头（不可达则 skip）。"""
    from backend.app.hasn.service.hasn_asset_service import hasn_asset_service
    from backend.plugin.s3.service.storage_service import storage_service

    html = b'<!doctype html><html><body><h1>HELLO-PUBLISH</h1></body></html>'
    try:
        ref = await storage_service.upload(
            host.session, html, category='published_artifact', filename='page.html', content_type='text/html'
        )
    except Exception as exc:
        pytest.skip(f'私有桶不可达，跳过真实 content 流式: {exc!r}')

    asset = await hasn_asset_service.register_asset(
        host.session, owner_hasn_id=host.owner, ref=ref, kind='file', extract_status='done'
    )
    await host.session.flush()
    site_data = await publish_service.create_site(
        host.session,
        owner_id=host.owner,
        kind='page',
        title='真实制品',
        asset_id=asset.asset_id,
        runtime='single-html',
        content_hash=_uid(),
        size_bytes=len(html),
        visibility='public',
        allow_indexing=True,
    )
    await host.session.flush()
    slug = site_data['site']['slug']

    r = await host.client.get(f'/s/{slug}/content')
    assert r.status_code == 200, r.text
    assert b'HELLO-PUBLISH' in r.content  # 服务端代吐真内容
    csp = r.headers.get('Content-Security-Policy', '')
    assert csp.startswith('sandbox allow-scripts')  # 直开 /content 也被沙箱
    assert "connect-src 'none'" in csp
    assert r.headers.get('X-Content-Type-Options') == 'nosniff'


def test_share_origin_fallback() -> None:
    req: Any = SimpleNamespace(url=SimpleNamespace(scheme='https', netloc='share.test'), headers={'host': 'share.test'})
    assert _share_origin(req) == 'https://share.test'  # 未配置 → 回退请求 origin
