"""通用网页发布与分享 公开查看面 /s/{slug}（模块 18，P3）。

> 外壳（/s/{slug}）与**制品内容**（/content、/assets/*）分属两个域：制品落 usercontent 隔离域
>   （`WEB_PUBLISH_CONTENT_ORIGIN`），该域上没有登录态、没有 /api/ 反代（nginx 只反代 /s/*）。
> 制品带 `allow-same-origin`（localStorage/IndexedDB 需要它）**当且仅当它真的跨域**——判据是实际
>   origin 不同，不是配置项非空，见 `_content_is_isolated`。同域时退回 opaque origin，否则制品能读
>   该域 cookie、读外壳注入的 view ticket、并同源调 /api/。
> 资源指令用**显式 host-source**；`connect-src https:` 放开外部 API（客户端动态站的前提），
>   `form-action 'none'` 仍锁死直接表单外发。

鉴权（[03] §3）：
  slug 不存在/已删/已撤销 → 404/410（不存在 slug 的探测按 IP 限速）
  expires_at 已过 → 410 Gone
  bundle-zip 物化在途（current_revision 尚未翻转）→ 409；外壳渲染自动刷新的「发布进行中」过渡页
  private → 需短时访问票（[01] §3.1），否则 401
  password → 无票 → 输口令页；/unlock 校验（限速）通过发票
  unlisted/public → 直接放行；unlisted 恒 X-Robots-Tag: noindex

访问票有两种承载位置，**制品内容与其子资源必须用路径段那种**，理由见 `_TICKET_PATH_PREFIX` 注释。

非信封面（返回 HTML/二进制/纯 JSON）：见 test_response_envelope_contract.py 白名单。
"""

from __future__ import annotations

import html
import json

from typing import Annotated
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from backend.app.hasn.service.hasn_asset_service import hasn_asset_service
from backend.app.hasn_publish.model.site import Site
from backend.app.hasn_publish.service.publish_service import MATERIALIZE_PENDING, publish_service
from backend.core.conf import settings
from backend.database.db import CurrentSession
from backend.database.redis import redis_client

router = APIRouter()

# 限速（按 IP 固定窗口）：不存在 slug 探测 + 口令爆破
_PROBE_WINDOW_SECONDS = 60
_PROBE_MAX = 60  # 每 IP 每分钟最多 60 次未命中探测
_UNLOCK_WINDOW_SECONDS = 60
_UNLOCK_MAX = 10  # 每 IP+slug 每分钟最多 10 次口令尝试


class UnlockRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get('x-forwarded-for')
    if fwd:
        return fwd.split(',')[0].strip()
    return request.client.host if request.client else 'unknown'


async def _rate_limited(key: str, *, window: int, limit: int) -> bool:
    """固定窗口计数；超限返回 True。Redis 不可用时放行（不阻断查看）。"""
    try:
        count = await redis_client.incr(key)
        if count == 1:
            await redis_client.expire(key, window)
        return count > limit
    except Exception:
        return False


def _share_origin(request: Request) -> str:
    """外壳（查看器 /s/{slug}）所在 origin：配置优先，否则回退请求 origin（dev/同域）。"""
    configured = (settings.WEB_PUBLISH_SHARE_ORIGIN or '').rstrip('/')
    if configured:
        return configured
    return f'{request.url.scheme}://{request.headers.get("host", request.url.netloc)}'


def _content_origin(request: Request) -> str:
    """制品内容域 origin（usercontent 隔离域）；未配置则回退外壳 origin。"""
    configured = (settings.WEB_PUBLISH_CONTENT_ORIGIN or '').rstrip('/')
    if configured:
        return configured
    return _share_origin(request)


def _request_origin(request: Request) -> str:
    """访客**实际**打到的 origin。经 nginx 反代时 `request.url.scheme` 恒为 http，
    必须以 `X-Forwarded-Proto` 为准，否则 https 请求会被算成 http 而与配置值比不上。
    """
    proto = request.headers.get('x-forwarded-proto') or request.url.scheme
    host = request.headers.get('host') or request.url.netloc
    return f'{proto}://{host}'


def _content_is_isolated(request: Request) -> bool:
    """制品是否真的落在独立内容域上——决定能不能安全地给 allow-same-origin。

    判据是**这一次请求实际打在哪个域**，不是两个配置项的比较，也不是「配置项非空」。
    浏览器判同源看的是制品实际被加载的 origin：只要 /content 仍能从 API 主域访问到
    （旧分享链接、官网旧构建、直连 8020 探活都会这样），那一次访问里的制品就**真的**
    在 API 主域上——此时若照配置发 allow-same-origin，制品立刻能读该域 cookie 并同源
    调 /api/。所以必须逐请求判定，同域一律退回 opaque origin。
    """
    configured = (settings.WEB_PUBLISH_CONTENT_ORIGIN or '').rstrip('/')
    if not configured:
        return False
    return configured == _request_origin(request) and configured != _share_origin(request)


def _content_csp(origin: str, *, isolated: bool) -> str:
    """/content 的 CSP。

    `isolated=True`（制品在独立 usercontent 域）时给 `allow-same-origin`：制品拿到真实
    origin，localStorage / sessionStorage / IndexedDB / cookie 才能用——没有它，浏览器对
    这些 API 一律抛 SecurityError（2026-08-20 实测）。因为该域上没有登录态、没有 /api/、
    也不承载外壳注入的 view ticket，同源能读到的只有制品自己那点东西。
    ⚠️ 同域时**绝不能**给：那等于把外壳的 token 和主域 cookie 一起交出去。

    `connect-src`：放开到 https:，制品可以调外部公开 API（天气/汇率/地图等）——这是
    「客户端动态站」的必要条件。代价是制品也能把数据发出去；制品由主人自己的分身产出、
    主人对发布内容负责，接受这个代价。form-action 仍锁死，不允许直接表单外发。
    """
    sandbox = (
        'sandbox allow-scripts allow-forms allow-same-origin; '
        if isolated
        else 'sandbox allow-scripts allow-forms; '
    )
    return (
        f'{sandbox}'
        "default-src 'none'; "
        f'img-src {origin} data: blob: https:; '
        f"style-src {origin} 'unsafe-inline'; "
        f'font-src {origin} data: https:; '
        f'media-src {origin} data: blob: https:; '
        f"script-src {origin} 'unsafe-inline'; "
        'connect-src https:; '
        "base-uri 'none'; "
        "form-action 'none'"
    )


def _growth_form_api_origin(request: Request) -> str:
    """解析受信任表单 broker 的 API origin，拒绝可注入 CSP/脚本的非 origin 配置。"""
    configured = (settings.GROWTH_PUBLIC_FORM_API_ORIGIN or '').strip()
    raw = configured or _share_origin(request)
    parsed = urlsplit(raw)
    if (
        parsed.scheme not in {'http', 'https'}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {'', '/'}
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError('GROWTH_PUBLIC_FORM_API_ORIGIN 必须是合法的 http(s) origin')
    return f'{parsed.scheme}://{parsed.netloc}'


def _noindex_headers(site) -> dict[str, str]:  # noqa: ANN001
    # unlisted 恒 noindex；public 仅 allow_indexing=true 才允许收录
    if site.visibility == 'unlisted' or (site.visibility == 'public' and not site.allow_indexing):
        return {'X-Robots-Tag': 'noindex, nofollow'}
    if site.visibility in ('private', 'password'):
        return {'X-Robots-Tag': 'noindex, nofollow'}
    return {}


# 错误响应禁缓存：404/410 属 RFC 9110 可默认启发式缓存状态码，不带 Cache-Control 时浏览器会
# 长期记住旧错误页（2026-08-19 实测：前一日 bundle 缺入口的 410 被 Chrome 缓存，次日修复后
# 用户仍看到旧 410 且根本不回源）。所有错误 JSON 一律 no-store。
_NO_STORE_HEADERS = {'Cache-Control': 'no-store'}


async def _authorize_view(
    db: CurrentSession, request: Request, slug: str, vt: str | None
) -> tuple[Site | None, Response | None]:
    """返回 (site, error_response)。error_response 非 None 时直接返回它。"""
    site = await publish_service.get_site_by_slug(db, slug=slug)
    if site is None or site.status == 'revoked':
        # 不存在/已撤销：限速探测 + 404/410
        if await _rate_limited(f'publish:probe:{_client_ip(request)}', window=_PROBE_WINDOW_SECONDS, limit=_PROBE_MAX):
            return None, JSONResponse(status_code=429, content={'code': 429, 'msg': '请求过于频繁', 'data': None})
        if site is None:
            return None, JSONResponse(
                status_code=404, headers=_NO_STORE_HEADERS, content={'code': 404, 'msg': '分享不存在', 'data': None}
            )
        return None, JSONResponse(
            status_code=410, headers=_NO_STORE_HEADERS, content={'code': 410, 'msg': '分享已撤销', 'data': None}
        )
    if publish_service.is_expired(site):
        return None, JSONResponse(
            status_code=410, headers=_NO_STORE_HEADERS, content={'code': 410, 'msg': '分享已过期', 'data': None}
        )
    if site.current_revision_id is None:
        # 指针为空的两种真相：bundle-zip 异步物化在途/失败（2026-08-29 发布异步化引入），
        # 或从未有过内容。在途必须给「发布进行中」而不是 410——410 会被主人当成坏链，
        # 而此刻内容其实正在就绪（visitor_shell 把 409 渲染成自动刷新过渡页）。
        latest = await publish_service.get_latest_revision(db, site_id=site.id)
        if latest is not None and latest.materialize_status == MATERIALIZE_PENDING:
            return site, JSONResponse(
                status_code=409, headers=_NO_STORE_HEADERS, content={'code': 409, 'msg': '发布进行中', 'data': None}
            )
        return None, JSONResponse(
            status_code=410, headers=_NO_STORE_HEADERS, content={'code': 410, 'msg': '分享内容不可用', 'data': None}
        )

    ticket_ok = publish_service.verify_view_ticket(vt, site_id=site.id) if vt else False
    if site.visibility == 'private' and not ticket_ok:
        return None, JSONResponse(
            status_code=401, content={'code': 401, 'msg': '私有分享，请在唤星桌面端打开', 'data': None}
        )
    if site.visibility == 'password' and not ticket_ok:
        return None, JSONResponse(status_code=401, content={'code': 401, 'msg': '需要口令', 'data': None})
    return site, None


@router.get('/s/{slug}', summary='发布查看器外壳')
async def viewer_shell(
    request: Request, db: CurrentSession, slug: str, vt: Annotated[str | None, Query()] = None
) -> Response:
    site, err = await _authorize_view(db, request, slug, vt)
    if err is not None:
        # bundle-zip 物化在途 → 自动刷新过渡页（200 + meta refresh），与 401→口令页同模式
        if isinstance(err, JSONResponse) and err.status_code == 409 and site is not None:
            return HTMLResponse(
                content=_materializing_page(site.title),
                headers={'X-Robots-Tag': 'noindex, nofollow', 'Cache-Control': 'no-store'},
            )
        # password 未授权时返回输口令页（200 + 表单），其余原样
        if (
            isinstance(err, JSONResponse)
            and err.status_code == 401
            and (got := await publish_service.get_site_by_slug(db, slug=slug))
            and got.visibility == 'password'
            and not publish_service.is_expired(got)
        ):
            return HTMLResponse(content=_password_page(slug), headers={'X-Robots-Tag': 'noindex, nofollow'})
        return err
    if site is None:
        raise RuntimeError('分享授权成功但站点实体缺失')
    await publish_service.increment_view_count(db, site_id=site.id)
    origin = _share_origin(request)
    content_origin = _content_origin(request)
    # 查看器外壳是本服务生成的**可信** HTML（title 经 html.escape，无用户脚本注入面）：
    # 必须允许它**自身的内联 style/script**，否则 `#frame{width/height:100%}` 等内联样式被 CSP
    # 拦掉 → iframe 退化成浏览器默认 ~300×150、外壳脚本（全屏/底栏）也失效 → 演示只剩一小块。
    # 真正不可信的发布制品在 /content 子 iframe，自带独立 CSP，与本外壳 CSP 互不影响（见 _content_csp）。
    # frame-src 必须显式放行内容域：制品移到 usercontent 隔离域后，默认 default-src 拦不住也放不进。
    headers = {
        'X-Frame-Options': 'SAMEORIGIN',
        'Content-Security-Policy': (
            f"frame-ancestors 'self'; default-src 'self' {origin}; "
            f'frame-src {content_origin}; '
            "style-src 'unsafe-inline'; script-src 'unsafe-inline'"
        ),
        # 外壳同样 no-cache：可见性/标题/内容指针变更必须即刻对访客生效
        'Cache-Control': 'no-cache',
        **_noindex_headers(site),
    }
    form_api_origin = (
        _growth_form_api_origin(request) if site.source_app == 'growth' and site.source_ref else None
    )
    if form_api_origin:
        headers['Content-Security-Policy'] += f'; connect-src {form_api_origin}'
    # 有票时外壳直接指向路径段形式，省掉一跳 302，且制品内相对子资源自动带票（见 _TICKET_PATH_PREFIX）。
    # 制品落在内容域，所以这里必须是**绝对** URL——相对路径会把制品拉回外壳域，
    # 那样它与外壳同源，allow-same-origin 一给就能读外壳注入的 view ticket。
    content_path = _ticket_path(slug, vt, 'content') if vt else f'/s/{quote(slug, safe="")}/content'
    content_src = f'{content_origin}{content_path}'
    return HTMLResponse(
        content=_viewer_shell_html(
            slug,
            site.title,
            site.allow_present,
            content_src,
            form_api_origin=form_api_origin,
            form_view_ticket=vt,
        ),
        headers=headers,
    )


@router.post('/s/{slug}/unlock', summary='口令解锁（发短时访问票）')
async def unlock(request: Request, db: CurrentSession, slug: str, body: UnlockRequest) -> JSONResponse:
    if await _rate_limited(
        f'publish:unlock:{_client_ip(request)}:{slug}', window=_UNLOCK_WINDOW_SECONDS, limit=_UNLOCK_MAX
    ):
        return JSONResponse(status_code=429, content={'code': 429, 'msg': '尝试过于频繁，请稍后再试', 'data': None})
    site = await publish_service.get_site_by_slug(db, slug=slug)
    if site is None or site.status == 'revoked' or publish_service.is_expired(site):
        return JSONResponse(status_code=404, content={'code': 404, 'msg': '分享不存在', 'data': None})
    if not await publish_service.verify_unlock(db, site=site, password=body.password):
        return JSONResponse(status_code=401, content={'code': 401, 'msg': '口令错误', 'data': None})
    issued = publish_service.issue_view_ticket(site_id=site.id, owner_id=site.owner_id)
    return JSONResponse(status_code=200, content={'code': 200, 'msg': 'ok', 'data': issued})


# 访问票的承载位置：查询串 `?vt=` 只用于**入口**，制品内容与其子资源一律走路径段 /t/{vt}/。
#
# 制品 HTML 里的图片是**相对路径**（`assets/ast_xxx`，打包期资产引用化的产物），而
# **相对 URL 不继承 query**：从 `/s/{slug}/content?vt=T` 解析 `assets/x` 得到的是
# `/s/{slug}/assets/x`——票在这一跳丢掉。public/unlisted 无票也放行，所以这条路径长期没被判过；
# password/private 站点下每张图都被 _authorize_view 判 401：正文出得来、图片全裂
# （2026-08-20 生产实测 site 11 `b9CsAYD5XfBR`：/content 200 而 manifest 里 10 张图全 401）。
#
# 票落在**路径段**上，相对引用就自动带着它走：`/s/{slug}/t/{T}/content` → `/s/{slug}/t/{T}/assets/x`。
# 这同时覆盖 CSS `url(...)`、srcset 等所有相对引用，且**不改制品字节**——改字节会让
# revision.content_hash 与 ETag 语义失真（同一 revision 每个访客拿到不同 HTML）。
# 旧的 `?vt=` 入口保留并 302 到路径段形式，官网 SPA 与已发出去的分享链接零改造。
_TICKET_PATH_PREFIX = '/t'


def _ticket_path(slug: str, vt: str, tail: str) -> str:
    """拼路径段带票 URL；slug/vt 全部转义，避免脏输入越出本站点前缀。"""
    return f'/s/{quote(slug, safe="")}{_TICKET_PATH_PREFIX}/{quote(vt, safe="")}/{tail}'


@router.get('/s/{slug}/content', summary='取制品内容（服务端代吐 + CSP sandbox）')
async def content(
    request: Request, db: CurrentSession, slug: str, vt: Annotated[str | None, Query()] = None
) -> Response:
    # 带票入口：一律 302 到路径段形式，让制品里的相对子资源引用也带上票（见 _TICKET_PATH_PREFIX）。
    # 票的有效性交给重定向目标判定，避免同一次访问查两遍库；错票在那边照样 401。
    if vt:
        return RedirectResponse(url=_ticket_path(slug, vt, 'content'), status_code=302, headers=_NO_STORE_HEADERS)
    return await _serve_content(request, db, slug, None)


@router.get('/s/{slug}' + _TICKET_PATH_PREFIX + '/{vt}/content', summary='取制品内容（路径段带票）')
async def content_with_ticket(request: Request, db: CurrentSession, slug: str, vt: str) -> Response:
    return await _serve_content(request, db, slug, vt)


async def _serve_content(request: Request, db: CurrentSession, slug: str, vt: str | None) -> Response:
    site, err = await _authorize_view(db, request, slug, vt)
    if err is not None:
        return err
    if site is None:
        raise RuntimeError('分享授权成功但站点实体缺失')
    revision = await publish_service.get_current_revision(db, site_id=site.id)
    if revision is None:
        return JSONResponse(
            status_code=410, headers=_NO_STORE_HEADERS, content={'code': 410, 'msg': '内容不可用', 'data': None}
        )
    asset = await hasn_asset_service.get_by_asset_id(db, revision.asset_id)
    if asset is None or asset.object_state == 'missing':
        return JSONResponse(
            status_code=410, headers=_NO_STORE_HEADERS, content={'code': 410, 'msg': '制品丢失', 'data': None}
        )

    from backend.plugin.s3.service.storage_service import storage_service

    # 制品的 CSP host-source 用**内容域**（制品自己所在的域），不是外壳域
    origin = _content_origin(request)
    base_headers = {
        'Content-Security-Policy': _content_csp(origin, isolated=_content_is_isolated(request)),
        'X-Content-Type-Options': 'nosniff',
        'Referrer-Policy': 'no-referrer',
        # no-cache：每次访问都回源按 ETag 校验，命中则 304。缺省时浏览器会启发式缓存
        # content，「重新发布」后访客（含主人自己）可能长期看到旧版本。
        'Cache-Control': 'no-cache',
        **_noindex_headers(site),
    }
    # ETag = revision.content_hash：重新发布产生新 revision → 新 hash → 旧缓存自然失效；
    # 内容未变则 304 省掉整份 HTML 传输。video-landing 的签名 URL 每次现解析，不走 304。
    etag = f'"{revision.content_hash}"' if revision.content_hash else None
    if etag and revision.runtime in ('single-html', 'bundle-zip'):
        inm = request.headers.get('if-none-match', '')
        if etag in {token.strip() for token in inm.split(',')}:
            return Response(status_code=304, headers={'ETag': etag, 'Cache-Control': 'no-cache'})
        base_headers['ETag'] = etag
    if revision.runtime == 'video-landing':
        # studio 视频成片落地页（doc22 §3.6 / §9 S18）：revision.asset_id 指向成片本身（hasn_assets）。
        # **serve 边界现解析签名 URL**（绝不在发布期烤进 HTML），生成单页 <video> 落地。media-src
        # 显式放行成片签名 URL host（私有桶 CDN 域）+ 站点 origin（见 _content_csp_for_video）。
        resolved = await hasn_asset_service.resolve(db, requester_hasn_id=site.owner_id, asset_ids=[revision.asset_id])
        video_url = next((r.display_url for r in resolved if r.asset_id == revision.asset_id), None)
        if not video_url:
            return JSONResponse(
                status_code=410, headers=_NO_STORE_HEADERS, content={'code': 410, 'msg': '成片资产不可用', 'data': None}
            )
        headers = {
            'Content-Security-Policy': _content_csp_for_video(origin, video_url),
            'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'no-referrer',
            **_noindex_headers(site),
        }
        return HTMLResponse(
            content=_video_landing_html(site.title, video_url, allow_download=site.allow_download), headers=headers
        )
    if revision.runtime == 'single-html':
        stream = storage_service.read_stream(db, storage_id=asset.storage_id, object_key=asset.object_key)
        return StreamingResponse(stream, media_type='text/html; charset=utf-8', headers=base_headers)
    # bundle-zip：根入口为 index.html（发布时解包逐对象，manifest_json）
    entry = _bundle_entry(revision.manifest_json, 'index.html')
    if entry is None:
        return JSONResponse(
            status_code=410,
            headers=_NO_STORE_HEADERS,
            content={'code': 410, 'msg': 'bundle 缺少入口 index.html', 'data': None},
        )
    data = await storage_service.read_bytes(db, storage_id=asset.storage_id, object_key=entry['object_key'])
    return Response(content=data, media_type='text/html; charset=utf-8', headers=base_headers)


@router.get('/s/{slug}/assets/{name:path}', summary='bundle-zip 子资源（复数 assets）')
async def asset(
    request: Request, db: CurrentSession, slug: str, name: str, vt: Annotated[str | None, Query()] = None
) -> Response:
    return await _serve_asset(request, db, slug, name, vt)


@router.get('/s/{slug}' + _TICKET_PATH_PREFIX + '/{vt}/assets/{name:path}', summary='子资源（路径段带票）')
async def asset_with_ticket(request: Request, db: CurrentSession, slug: str, vt: str, name: str) -> Response:
    """制品内相对引用 `assets/x` 在带票 content 下解析到这里，票随路径自动携带。"""
    return await _serve_asset(request, db, slug, name, vt)


async def _serve_asset(request: Request, db: CurrentSession, slug: str, name: str, vt: str | None) -> Response:
    site, err = await _authorize_view(db, request, slug, vt)
    if err is not None:
        return err
    if site is None:
        raise RuntimeError('分享授权成功但站点实体缺失')
    revision = await publish_service.get_current_revision(db, site_id=site.id)
    if revision is None:
        return JSONResponse(
            status_code=404, headers=_NO_STORE_HEADERS, content={'code': 404, 'msg': '资源不存在', 'data': None}
        )
    # 代吐判据是 manifest.files 里有没有该项，不看 runtime：资产引用化后 single-html
    # 的图片同样由本端点代吐（bundle-zip 是解包逐对象，引用资产是 server-side copy）。
    entry = _bundle_entry(revision.manifest_json, f'assets/{name}') or _bundle_entry(revision.manifest_json, name)
    if entry is None:
        return JSONResponse(
            status_code=404, headers=_NO_STORE_HEADERS, content={'code': 404, 'msg': '资源不存在', 'data': None}
        )

    asset = await hasn_asset_service.get_by_asset_id(db, revision.asset_id)
    if asset is None or asset.object_state == 'missing':
        return JSONResponse(
            status_code=404, headers=_NO_STORE_HEADERS, content={'code': 404, 'msg': '制品丢失', 'data': None}
        )

    from backend.plugin.s3.service.storage_service import storage_service

    # 子对象与制品同存储桶（发布时解包写回同 storage_id），object_key 取自 manifest
    storage_id = int(entry.get('storage_id') or asset.storage_id)
    data = await storage_service.read_bytes(db, storage_id=storage_id, object_key=entry['object_key'])
    headers = {'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, max-age=300', **_noindex_headers(site)}
    return Response(content=data, media_type=entry.get('mime') or 'application/octet-stream', headers=headers)


# ---------- helpers ----------


def _origin_of(url: str) -> str:
    """从 URL 抽 scheme://host[:port] 作为 CSP host-source（解析失败回退 https: 通配，宁松不锁死视频）。"""
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if parts.scheme and parts.netloc:
        return f'{parts.scheme}://{parts.netloc}'
    return 'https:'


def _content_csp_for_video(origin: str, video_url: str) -> str:
    """video-landing 的 CSP：sandbox（opaque origin）+ 放行成片 CDN host（media-src）+ 断外联。

    与 _content_csp 同基调，但 media-src 额外含成片签名 URL 的 host（私有桶/CDN 域），否则 <video> 拉不到流。
    """
    media_host = _origin_of(video_url)
    return (
        'sandbox allow-scripts; '
        "default-src 'none'; "
        f'img-src {origin} {media_host} data: blob:; '
        f"style-src {origin} 'unsafe-inline'; "
        f'media-src {origin} {media_host} data: blob:; '
        f"script-src {origin} 'unsafe-inline'; "
        "connect-src 'none'; "
        "base-uri 'none'; "
        "form-action 'none'"
    )


def _video_landing_html(title: str, video_url: str, *, allow_download: bool) -> str:
    """单页 <video> 落地（成片对外公开）。title 经 html.escape；video_url 是 serve 期现解析的签名 URL。"""
    t = html.escape(title or '视频')
    src = html.escape(video_url, quote=True)
    download_attr = '' if allow_download else ' controlsList="nodownload" disablePictureInPicture'
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{t} · 唤星</title><style>
html,body{{margin:0;height:100%;background:#0b0b0f;display:flex;flex-direction:column}}
header{{color:#e5e7eb;font:600 15px system-ui,-apple-system,sans-serif;padding:14px 18px}}
main{{flex:1;display:flex;align-items:center;justify-content:center;min-height:0;padding:0 16px 16px}}
video{{max-width:100%;max-height:100%;border-radius:12px;background:#000;outline:none}}
</style></head><body>
<header>{t}</header>
<main><video src="{src}" controls playsinline preload="metadata"{download_attr}></video></main>
</body></html>"""


def _bundle_entry(manifest: dict | None, name: str) -> dict | None:
    if not isinstance(manifest, dict):
        return None
    files = manifest.get('files') if isinstance(manifest.get('files'), dict) else manifest
    if not isinstance(files, dict):
        return None
    entry = files.get(name)
    if isinstance(entry, dict) and entry.get('object_key'):
        return entry
    return None


def _password_page(slug: str) -> str:
    s = html.escape(slug)
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>需要口令 · 唤星</title><style>
body{{font-family:system-ui,-apple-system,sans-serif;background:#f9fafb;color:#111827;display:flex;
min-height:100vh;align-items:center;justify-content:center;margin:0}}
.card{{background:#fff;padding:32px;border-radius:16px;width:320px;text-align:center}}
h1{{font-size:16px;margin:0 0 16px}}input{{width:100%;padding:10px 12px;border:1px solid #e5e7eb;border-radius:8px;
font-size:14px;box-sizing:border-box}}button{{margin-top:12px;width:100%;padding:10px;background:#6D28D9;color:#fff;
border:0;border-radius:8px;font-size:14px;cursor:pointer}}.err{{color:#dc2626;font-size:13px;margin-top:8px;min-height:18px}}
</style></head><body><div class="card"><h1>该分享受口令保护</h1>
<input id="pw" type="password" placeholder="请输入访问口令" autofocus>
<button onclick="go()">查看</button><div class="err" id="err"></div></div><script>
async function go(){{var pw=document.getElementById('pw').value;if(!pw)return;
var r=await fetch('/s/{s}/unlock',{{method:'POST',headers:{{'content-type':'application/json'}},
body:JSON.stringify({{password:pw}})}});var j=await r.json();
if(r.ok&&j.data&&j.data.ticket){{location.href='/s/{s}?vt='+encodeURIComponent(j.data.ticket);}}
else{{document.getElementById('err').textContent=(j&&j.msg)||'口令错误';}}}}
document.getElementById('pw').addEventListener('keydown',function(e){{if(e.key==='Enter')go();}});
</script></body></html>"""


def _materializing_page(title: str) -> str:
    """bundle-zip 异步物化在途的过渡页：5s 自动刷新，就绪即进正式查看器。

    存在理由：发布异步化后 create 立即返回、物化在 worker 里跑（大包几十秒）。此间打开
    链接若给 410「内容不可用」是假话（内容正在就绪），且会被主人当成坏链关掉。
    title 经 html.escape（用户输入，防注入）。
    """
    t = html.escape(title) if title else ''
    heading = f'「{t}」正在发布' if t else '页面正在发布'
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="5">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>发布进行中 · 唤星</title><style>
body{{font-family:system-ui,-apple-system,sans-serif;background:#f9fafb;color:#111827;display:flex;
min-height:100vh;align-items:center;justify-content:center;margin:0}}
.card{{background:#fff;padding:32px;border-radius:16px;width:320px;text-align:center}}
h1{{font-size:16px;margin:0 0 12px}}p{{font-size:13px;color:#6b7280;margin:0;line-height:1.7}}
.dot{{display:inline-block;width:8px;height:8px;border-radius:50%;background:#2563EB;margin-right:8px;
animation:p 1s ease-in-out infinite alternate}}@keyframes p{{from{{opacity:.25}}to{{opacity:1}}}}
</style></head><body><div class="card"><h1><span class="dot"></span>{heading}</h1>
<p>内容正在打包上线，通常几十秒内就绪。<br>本页会自动刷新，无需操作。</p></div></body></html>"""


def _growth_form_broker_script(
    *,
    slug: str,
    api_origin: str,
    view_ticket: str | None,
) -> str:
    """生成受信任外壳表单 broker；不可信制品只能 postMessage，不能读取 token 或访问父页。"""
    config = json.dumps(
        {
            'apiOrigin': api_origin,
            'slug': slug,
            'formRef': 'growth-lead-v1',
            'viewTicket': view_ticket,
        },
        ensure_ascii=True,
        separators=(',', ':'),
    )
    return f"""
var growthFormBroker={config};
var growthFormKeys=new Map();
function growthFormReply(event,requestId,result){{
  event.source.postMessage({{type:'hasn:growth-form-result',requestId:requestId,...result}},'*');
}}
async function growthFormJson(response){{
  try{{return await response.json();}}catch(_error){{return null;}}
}}
window.addEventListener('message',async function(event){{
  if(event.source!==frame.contentWindow||!event.data||event.data.type!=='hasn:growth-form-submit')return;
  var requestId=typeof event.data.requestId==='string'?event.data.requestId:'';
  var payload=event.data.payload;
  if(!requestId||requestId.length>128||!payload||typeof payload!=='object'||Array.isArray(payload)){{
    growthFormReply(event,requestId,{{ok:false,status:422,message:'表单请求格式无效'}});
    return;
  }}
  var idempotencyKey=growthFormKeys.get(requestId);
  if(!idempotencyKey){{
    if(!globalThis.crypto||typeof globalThis.crypto.randomUUID!=='function'){{
      growthFormReply(event,requestId,{{ok:false,status:503,message:'当前浏览器无法生成幂等标识'}});
      return;
    }}
    idempotencyKey=globalThis.crypto.randomUUID();
    if(growthFormKeys.size>=100)growthFormKeys.delete(growthFormKeys.keys().next().value);
    growthFormKeys.set(requestId,idempotencyKey);
  }}
  var base=growthFormBroker.apiOrigin;
  var slug=encodeURIComponent(growthFormBroker.slug);
  try{{
    var tokenResponse=await fetch(
      base+'/api/v1/publish/open/sites/'+slug+'/forms/'+growthFormBroker.formRef+'/access-token',
      {{
        method:'POST',
        credentials:'omit',
        referrerPolicy:'no-referrer',
        headers:{{'Content-Type':'application/json'}},
        body:JSON.stringify({{view_ticket:growthFormBroker.viewTicket}})
      }}
    );
    var tokenPayload=await growthFormJson(tokenResponse);
    var token=tokenPayload&&tokenPayload.data&&tokenPayload.data.form_access_token;
    if(!tokenResponse.ok||typeof token!=='string'){{
      growthFormReply(event,requestId,{{
        ok:false,
        status:tokenResponse.status,
        message:(tokenPayload&&tokenPayload.msg)||'无法取得表单访问令牌'
      }});
      return;
    }}
    var submitResponse=await fetch(
      base+'/api/v1/growth/open/forms/'+slug+'/submit',
      {{
        method:'POST',
        credentials:'omit',
        referrerPolicy:'no-referrer',
        headers:{{
          'Content-Type':'application/json',
          'Idempotency-Key':idempotencyKey,
          'X-Publish-Form-Token':token
        }},
        body:JSON.stringify(payload)
      }}
    );
    var submitPayload=await growthFormJson(submitResponse);
    if(!submitResponse.ok){{
      growthFormReply(event,requestId,{{
        ok:false,
        status:submitResponse.status,
        message:(submitPayload&&submitPayload.msg)||'留资提交失败',
        retryAfter:submitResponse.headers.get('Retry-After')
      }});
      return;
    }}
    growthFormReply(event,requestId,{{
      ok:true,
      status:submitResponse.status,
      receiptRef:submitPayload&&submitPayload.data&&submitPayload.data.receipt_ref
    }});
  }}catch(_error){{
    growthFormReply(event,requestId,{{ok:false,status:0,message:'网络不可用，请稍后重试'}});
  }}
}});
"""


def _viewer_shell_html(
    slug: str,
    title: str,
    allow_present: bool,
    content_src: str,
    *,
    form_api_origin: str | None = None,
    form_view_ticket: str | None = None,
) -> str:
    t = html.escape(title or '分享')
    src = html.escape(content_src, quote=True)
    present = 'true' if allow_present else 'false'
    form_broker = (
        _growth_form_broker_script(
            slug=slug,
            api_origin=form_api_origin,
            view_ticket=form_view_ticket,
        )
        if form_api_origin
        else ''
    )
    # iframe 的 sandbox 与 /content 响应头 CSP 的 sandbox **取交集**（更严格者生效）。这里给到最宽，
    # 由后端按「制品是否真落在独立内容域」决定实际放不放 allow-same-origin（见 _content_csp 的 isolated）：
    # 同域时后端不给，制品照样是 opaque origin——**权威判定在后端，不在这个属性上**。
    # 反过来若这里不给，后端给了也没用，localStorage 依旧 SecurityError。
    #
    # iframe 必须带 `allow="fullscreen"`（+ legacy `allowfullscreen`）：制品在沙箱子帧里跑，
    # 内置查看运行时点「放映」会 `element.requestFullscreen()`。沙箱本身不含
    # fullscreen flag、不拦全屏，但**全屏须经 Permissions-Policy 委派**——没有 allow，浏览器静默拒绝内层
    # requestFullscreen → 只剩运行时的 CSS 放映态（铺满 iframe=铺满浏览器视口），用户看到「只是浏览器全屏、
    # 没进真·全屏」。委派后内层请求把 iframe 升为原生全屏，deck 铺满整屏（回归守卫见 hosting e2e）。
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{t} · 唤星</title><style>
html,body{{margin:0;height:100%;background:#0b0b0f;overflow:hidden}}
#frame{{border:0;width:100%;height:100%;display:block;background:#fff}}
#bar{{position:fixed;bottom:0;left:0;right:0;height:44px;display:flex;align-items:center;justify-content:center;
gap:16px;background:rgba(17,17,24,.78);backdrop-filter:blur(6px);color:#e5e7eb;font:13px system-ui;
opacity:0;transition:opacity .2s;pointer-events:none}}
#bar.show{{opacity:1;pointer-events:auto}}#bar button{{background:transparent;border:1px solid #3f3f46;color:#e5e7eb;
border-radius:6px;padding:4px 10px;font-size:13px;cursor:pointer}}
</style></head><body>
<iframe id="frame" sandbox="allow-scripts allow-forms allow-same-origin" allow="fullscreen" allowfullscreen src="{src}" title="{t}"></iframe>
<div id="bar"><span>{t}</span><button onclick="fs()">全屏</button></div>
<script>
var presentable={present};var bar=document.getElementById('bar');var frame=document.getElementById('frame');var idle;
function show(){{if(!presentable)return;bar.classList.add('show');clearTimeout(idle);
idle=setTimeout(function(){{bar.classList.remove('show');}},2000);}}
document.addEventListener('mousemove',show);show();
function fs(){{var el=document.documentElement;if(document.fullscreenElement){{document.exitFullscreen();}}
else if(el.requestFullscreen){{el.requestFullscreen();}}}}
document.addEventListener('keydown',function(e){{if(e.key==='f'||e.key==='F')fs();}});
{form_broker}
</script></body></html>"""
