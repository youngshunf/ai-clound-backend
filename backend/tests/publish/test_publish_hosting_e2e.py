"""通用网页发布与分享（模块 18，P3）托管 + 公开查看 /s/{slug} 进程内 HTTP E2E（真实 PG，零 mock）。

覆盖（DoD）：
  - /content CSP：sandbox allow-scripts allow-forms + 显式 host-source（opaque origin 不写 'self'）+ connect-src 'none' 断外联
  - 四态访问：private 凭票 / unlisted 凭链接 / password 限速+票 / public 直放；缺票 401
  - 过期 410 / 撤销 410 / 不存在 404
  - unlisted/private/password 恒 X-Robots-Tag noindex；public(默认不收录)亦 noindex；public+allow_indexing 才放收录
  - /unlock：错口令 401，对口令发短时访问票，凭票 ?vt= 放行
  - 访问票随制品内**相对**子资源引用同行（路径段 /t/{vt}/）；无票/错票的子资源仍 401
  - 真实 single-html /content 流式代吐（上传真实私有桶；不可达则 skip，零 fake）

需要 export DATABASE_PORT=15432。
"""

from __future__ import annotations

import uuid

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn_publish.api.v1.open.hosting import (
    _content_csp,
    _share_origin,
    _viewer_shell_html,
)
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
    # allow-forms 只让浏览器派发 submit 事件给受控 postMessage 脚本；form-action 仍禁止直接外发。
    assert csp.startswith('sandbox allow-scripts allow-forms')
    assert "connect-src 'none'" in csp  # 断数据外联
    assert 'https://share.example.com' in csp  # 显式 host-source（不是 'self'）
    assert "img-src 'self'" not in csp  # opaque origin 下 'self' 永不匹配，绝不用
    assert "base-uri 'none'" in csp
    assert "form-action 'none'" in csp


def test_content_csp_no_allow_same_origin() -> None:
    # sandbox 不含 allow-same-origin（否则脚本可读父页）
    csp = _content_csp('https://share.example.com')
    assert 'allow-same-origin' not in csp


def test_growth_form_broker_keeps_untrusted_content_on_post_message_boundary() -> None:
    shell = _viewer_shell_html(
        'growth-demo',
        '获客页',
        False,
        '/s/growth-demo/content',
        form_api_origin='https://api.example.com',
        form_view_ticket=None,
    )

    assert 'hasn:growth-form-submit' in shell
    assert 'hasn:growth-form-result' in shell
    assert '/api/v1/publish/open/sites/' in shell
    assert '"formRef":"growth-lead-v1"' in shell
    assert "'/forms/'" in shell
    assert "'/access-token'" in shell
    assert '/api/v1/growth/open/forms/' in shell
    assert 'Idempotency-Key' in shell
    assert 'X-Publish-Form-Token' in shell
    assert 'event.source!==frame.contentWindow' in shell
    assert 'sandbox="allow-scripts allow-forms"' in shell
    assert 'allow-same-origin' not in shell


def test_regular_publish_shell_does_not_install_growth_form_broker() -> None:
    shell = _viewer_shell_html('regular-demo', '普通页面', False, '/s/regular-demo/content')

    assert 'hasn:growth-form-submit' not in shell
    assert '/api/v1/growth/open/forms/' not in shell


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
    extra.setdefault('asset_id', f'ast_{_uid()}')  # 需要真制品的用例自带 asset_id
    data = await publish_service.create_site(
        host.session,
        owner_id=host.owner,
        kind='page',
        title='测试发布',
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
    assert 'sandbox="allow-scripts allow-forms"' in r.text  # 外壳用 sandbox iframe
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
    assert csp.startswith('sandbox allow-scripts allow-forms')  # 直开 /content 也被沙箱
    assert "connect-src 'none'" in csp
    assert r.headers.get('X-Content-Type-Options') == 'nosniff'


def test_share_origin_fallback() -> None:
    req: Any = SimpleNamespace(url=SimpleNamespace(scheme='https', netloc='share.test'), headers={'host': 'share.test'})
    assert _share_origin(req) == 'https://share.test'  # 未配置 → 回退请求 origin


# ---------- 访问票必须能随制品内的**相对**子资源引用一起走（2026-08-20 生产事故回归） ----------
#
# 事故形态：password 站点 /content 出得来正文，但 10 张图全 401——制品 HTML 里是相对路径
# `assets/ast_xxx`，而**相对 URL 不继承 query**，`?vt=` 在这一跳丢掉。public/unlisted 无票
# 也放行，所以这条路径直到第一个口令保护的 deck 才被真正判过。
# 下面每个断言都用 urljoin 按浏览器规则**算出**子资源 URL，绝不手写路径——手写等于把 bug
# 复制进测试。判据用 401 vs 404 区分「票没带上」与「票带上了但 manifest 没这个文件」。


def _resolve_like_a_browser(document_url: str, relative_ref: str) -> str:
    """按浏览器解析制品内相对引用；document_url 用最终 URL（跟随重定向后）。"""
    return urljoin(document_url, relative_ref)


async def test_ticketed_content_carries_ticket_to_relative_assets(host: SimpleNamespace) -> None:
    """口令站点：解锁 → content → 制品内相对 `assets/x` 必须仍在票的保护下可达（不是 401）。

    判的是**行为**不是实现：从浏览器实际停留的 content URL 出发解析相对引用，
    票怎么承载（重定向/外壳直指/别的）随便换，这条都成立。
    """
    site = await _make_site(host, visibility='password', password='letmein')
    r = await host.client.post(f'/s/{site["slug"]}/unlock', json={'password': 'letmein'})
    assert r.status_code == 200, r.text
    ticket = r.json()['data']['ticket']

    # follow_redirects：拿到浏览器最终停留的地址，相对引用就是从它解析的
    r = await host.client.get(f'/s/{site["slug"]}/content', params={'vt': ticket}, follow_redirects=True)
    assert r.status_code != 401, '带票访问 content 必须被 _authorize_view 认可'
    content_url = str(r.url)

    # 核心：按浏览器规则解析制品里的 `assets/ast_x`，它必须仍带着票
    asset_url = _resolve_like_a_browser(content_url, 'assets/ast_demo')
    r = await host.client.get(asset_url)
    # 404=票通过了、只是 manifest 里没这个文件；401=票根本没随相对引用走（本次事故）
    assert r.status_code == 404, f'相对子资源丢票 → {r.status_code}（事故复发）'


async def test_query_ticket_entry_stays_compatible(host: SimpleNamespace) -> None:
    """已发出去的 `?vt=` 旧链接与官网 SPA 不改造：入口 302 到路径段形式，且绝不可被缓存。"""
    site = await _make_site(host, visibility='password', password='letmein')
    r = await host.client.post(f'/s/{site["slug"]}/unlock', json={'password': 'letmein'})
    ticket = r.json()['data']['ticket']

    r = await host.client.get(f'/s/{site["slug"]}/content', params={'vt': ticket})
    assert r.status_code == 302, r.text
    assert f'/s/{site["slug"]}/t/' in r.headers['location']
    assert r.headers.get('Cache-Control') == 'no-store'  # 票会过期，重定向绝不可被缓存


async def test_relative_asset_without_ticket_still_401(host: SimpleNamespace) -> None:
    """安全语义不许被这次修复放宽：无票的口令站点子资源仍然 401。"""
    site = await _make_site(host, visibility='password', password='letmein')
    r = await host.client.get(f'/s/{site["slug"]}/assets/ast_demo')
    assert r.status_code == 401, r.text
    # 路径段上放一张伪造/过期的票同样拒绝
    r = await host.client.get(f'/s/{site["slug"]}/t/not-a-real-ticket/assets/ast_demo')
    assert r.status_code == 401, r.text
    r = await host.client.get(f'/s/{site["slug"]}/t/not-a-real-ticket/content')
    assert r.status_code == 401, r.text


async def test_public_site_relative_assets_need_no_ticket(host: SimpleNamespace) -> None:
    """public 站点无票直放：/content 不重定向，相对子资源解析后照样放行到读取层。"""
    site = await _make_site(host, visibility='public')
    r = await host.client.get(f'/s/{site["slug"]}/content')
    assert r.status_code != 302, 'public 无票，不该产生带票重定向'
    asset_url = _resolve_like_a_browser(str(r.url), 'assets/ast_demo')
    r = await host.client.get(asset_url)
    assert r.status_code == 404, r.text  # 放行到读取层，只是 manifest 无此项


async def test_ticketed_shell_points_iframe_at_path_ticket(host: SimpleNamespace) -> None:
    """外壳自己带票时，iframe 直接指向路径段形式，省掉一跳 302。"""
    site = await _make_site(host, visibility='private')
    issued = publish_service.issue_view_ticket(site_id=site['id'], owner_id=host.owner)
    r = await host.client.get(f'/s/{site["slug"]}', params={'vt': issued['ticket']})
    assert r.status_code == 200, r.text
    assert f'/s/{site["slug"]}/t/' in r.text
    assert f'src="/s/{site["slug"]}/content?' not in r.text


async def test_ticketed_asset_serves_real_bytes(host: SimpleNamespace) -> None:
    """真桶端到端：口令站点下，制品里的相对图片引用必须真的吐出图片字节（不可达则 skip）。"""
    from backend.app.hasn.service.hasn_asset_service import hasn_asset_service
    from backend.plugin.s3.service.storage_service import storage_service

    # 1×1 PNG（真字节，零 fake）
    png = bytes.fromhex(
        '89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4'
        '890000000a49444154789c6360000002000100ffff03000006000557bfabd400'
        '00000049454e44ae426082'
    )
    page = b'<!doctype html><html><body><img src="assets/pic.png"></body></html>'
    try:
        page_ref = await storage_service.upload(
            host.session, page, category='published_artifact', filename='page.html', content_type='text/html'
        )
        png_ref = await storage_service.upload(
            host.session, png, category='published_artifact', filename='pic.png', content_type='image/png'
        )
    except Exception as exc:
        pytest.skip(f'私有桶不可达，跳过真实子资源代吐: {exc!r}')

    asset = await hasn_asset_service.register_asset(
        host.session, owner_hasn_id=host.owner, ref=page_ref, kind='file', extract_status='done'
    )
    await host.session.flush()
    site = await _make_site(
        host,
        visibility='password',
        password='letmein',
        asset_id=asset.asset_id,
        runtime='single-html',
        manifest_json={
            'files': {
                'assets/pic.png': {
                    'object_key': png_ref.object_key,
                    'mime': 'image/png',
                    'size': len(png),
                    'storage_id': png_ref.storage_id,
                }
            }
        },
    )
    r = await host.client.post(f'/s/{site["slug"]}/unlock', json={'password': 'letmein'})
    assert r.status_code == 200, r.text
    ticket = r.json()['data']['ticket']

    r = await host.client.get(f'/s/{site["slug"]}/content', params={'vt': ticket}, follow_redirects=True)
    assert r.status_code == 200, r.text
    content_url = str(r.url)
    assert b'assets/pic.png' in r.content  # 制品里就是相对引用

    r = await host.client.get(_resolve_like_a_browser(content_url, 'assets/pic.png'))
    assert r.status_code == 200, r.text
    assert r.content == png  # 同桶字节必一致
    assert r.headers.get('content-type') == 'image/png'
