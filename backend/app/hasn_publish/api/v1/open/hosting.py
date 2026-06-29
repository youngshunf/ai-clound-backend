"""通用网页发布与分享 公开查看面 /s/{slug}（模块 18，P3）。

> 整个 /s/* 落独立分享域名（usercontent 模式，[04] §5）：API 主域 cookie 在该域不可见。
> /content 响应恒带 `Content-Security-Policy: sandbox allow-scripts`——直开导航也被沙箱；
>   资源指令用**显式 host-source**（opaque origin 下 'self' 永不匹配，会拦死制品自己的 assets）。
> connect-src 'none' 阻断数据外联（打包期已收敛外链，[02]）。

鉴权（[03] §3）：
  slug 不存在/已删/已撤销 → 404/410（不存在 slug 的探测按 IP 限速）
  expires_at 已过 → 410 Gone
  private → 需短时访问票 ?vt=（[01] §3.1），否则 401
  password → 无票 → 输口令页；/unlock 校验（限速）通过发票
  unlisted/public → 直接放行；unlisted 恒 X-Robots-Tag: noindex

非信封面（返回 HTML/二进制/纯 JSON）：见 test_response_envelope_contract.py 白名单。
"""

from __future__ import annotations

import html

from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from backend.app.hasn.service.hasn_asset_service import hasn_asset_service
from backend.app.hasn_publish.service.publish_service import publish_service
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
    """分享域名 origin：配置优先，否则回退请求 origin（dev/同域）。"""
    configured = (settings.WEB_PUBLISH_SHARE_ORIGIN or '').rstrip('/')
    if configured:
        return configured
    return f'{request.url.scheme}://{request.headers.get("host", request.url.netloc)}'


def _content_csp(origin: str) -> str:
    """/content 的 CSP：sandbox allow-scripts（opaque origin）+ 显式 host-source 资源指令 + 断外联。"""
    return (
        'sandbox allow-scripts; '
        "default-src 'none'; "
        f'img-src {origin} data: blob:; '
        f"style-src {origin} 'unsafe-inline'; "
        f'font-src {origin} data:; '
        f'media-src {origin} data: blob:; '
        f"script-src {origin} 'unsafe-inline'; "
        "connect-src 'none'; "
        "base-uri 'none'; "
        "form-action 'none'"
    )


def _noindex_headers(site) -> dict[str, str]:  # noqa: ANN001
    # unlisted 恒 noindex；public 仅 allow_indexing=true 才允许收录
    if site.visibility == 'unlisted' or (site.visibility == 'public' and not site.allow_indexing):
        return {'X-Robots-Tag': 'noindex, nofollow'}
    if site.visibility in ('private', 'password'):
        return {'X-Robots-Tag': 'noindex, nofollow'}
    return {}


async def _authorize_view(
    db: CurrentSession, request: Request, slug: str, vt: str | None
) -> tuple[object, Response | None]:
    """返回 (site, error_response)。error_response 非 None 时直接返回它。"""
    site = await publish_service.get_site_by_slug(db, slug=slug)
    if site is None or site.status == 'revoked':
        # 不存在/已撤销：限速探测 + 404/410
        if await _rate_limited(f'publish:probe:{_client_ip(request)}', window=_PROBE_WINDOW_SECONDS, limit=_PROBE_MAX):
            return None, JSONResponse(status_code=429, content={'code': 429, 'msg': '请求过于频繁', 'data': None})
        if site is None:
            return None, JSONResponse(status_code=404, content={'code': 404, 'msg': '分享不存在', 'data': None})
        return None, JSONResponse(status_code=410, content={'code': 410, 'msg': '分享已撤销', 'data': None})
    if publish_service.is_expired(site):
        return None, JSONResponse(status_code=410, content={'code': 410, 'msg': '分享已过期', 'data': None})
    if site.current_revision_id is None:
        return None, JSONResponse(status_code=410, content={'code': 410, 'msg': '分享内容不可用', 'data': None})

    ticket_ok = bool(vt) and publish_service.verify_view_ticket(vt, site_id=site.id)
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
    await publish_service.increment_view_count(db, site_id=site.id)
    origin = _share_origin(request)
    # 查看器外壳是本服务生成的**可信** HTML（title 经 html.escape，无用户脚本注入面）：
    # 必须允许它**自身的内联 style/script**，否则 `#frame{width/height:100%}` 等内联样式被 CSP
    # 拦掉 → iframe 退化成浏览器默认 ~300×150、外壳脚本（全屏/底栏）也失效 → 演示只剩一小块。
    # 真正不可信的发布制品在 /content 子 iframe，自带 `sandbox allow-scripts` 的独立 CSP，
    # 与本外壳 CSP 互不影响（见 _content_csp）。
    headers = {
        'X-Frame-Options': 'SAMEORIGIN',
        'Content-Security-Policy': (
            f"frame-ancestors 'self'; default-src 'self' {origin}; "
            "style-src 'unsafe-inline'; script-src 'unsafe-inline'"
        ),
        **_noindex_headers(site),
    }
    vt_qs = f'?vt={html.escape(vt)}' if vt else ''
    return HTMLResponse(content=_viewer_shell_html(slug, site.title, site.allow_present, vt_qs), headers=headers)


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


@router.get('/s/{slug}/content', summary='取制品内容（服务端代吐 + CSP sandbox）')
async def content(
    request: Request, db: CurrentSession, slug: str, vt: Annotated[str | None, Query()] = None
) -> Response:
    site, err = await _authorize_view(db, request, slug, vt)
    if err is not None:
        return err
    revision = await publish_service.get_current_revision(db, site_id=site.id)
    if revision is None:
        return JSONResponse(status_code=410, content={'code': 410, 'msg': '内容不可用', 'data': None})
    asset = await hasn_asset_service.get_by_asset_id(db, revision.asset_id)
    if asset is None:
        return JSONResponse(status_code=410, content={'code': 410, 'msg': '制品丢失', 'data': None})

    from backend.plugin.s3.service.storage_service import storage_service

    origin = _share_origin(request)
    base_headers = {
        'Content-Security-Policy': _content_csp(origin),
        'X-Content-Type-Options': 'nosniff',
        'Referrer-Policy': 'no-referrer',
        **_noindex_headers(site),
    }
    if revision.runtime == 'video-landing':
        # studio 视频成片落地页（doc22 §3.6 / §9 S18）：revision.asset_id 指向成片本身（hasn_assets）。
        # **serve 边界现解析签名 URL**（绝不在发布期烤进 HTML），生成单页 <video> 落地。media-src
        # 显式放行成片签名 URL host（私有桶 CDN 域）+ 站点 origin（见 _content_csp_for_video）。
        resolved = await hasn_asset_service.resolve(db, requester_hasn_id=site.owner_id, asset_ids=[revision.asset_id])
        video_url = next((r.display_url for r in resolved if r.asset_id == revision.asset_id), None)
        if not video_url:
            return JSONResponse(status_code=410, content={'code': 410, 'msg': '成片资产不可用', 'data': None})
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
        return JSONResponse(status_code=410, content={'code': 410, 'msg': 'bundle 缺少入口 index.html', 'data': None})
    data = await storage_service.read_bytes(db, storage_id=asset.storage_id, object_key=entry['object_key'])
    return Response(content=data, media_type='text/html; charset=utf-8', headers=base_headers)


@router.get('/s/{slug}/assets/{name:path}', summary='bundle-zip 子资源（复数 assets）')
async def asset(
    request: Request, db: CurrentSession, slug: str, name: str, vt: Annotated[str | None, Query()] = None
) -> Response:
    site, err = await _authorize_view(db, request, slug, vt)
    if err is not None:
        return err
    revision = await publish_service.get_current_revision(db, site_id=site.id)
    if revision is None or revision.runtime != 'bundle-zip':
        return JSONResponse(status_code=404, content={'code': 404, 'msg': '资源不存在', 'data': None})
    entry = _bundle_entry(revision.manifest_json, f'assets/{name}') or _bundle_entry(revision.manifest_json, name)
    if entry is None:
        return JSONResponse(status_code=404, content={'code': 404, 'msg': '资源不存在', 'data': None})

    asset = await hasn_asset_service.get_by_asset_id(db, revision.asset_id)
    if asset is None:
        return JSONResponse(status_code=404, content={'code': 404, 'msg': '制品丢失', 'data': None})

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


def _viewer_shell_html(slug: str, title: str, allow_present: bool, vt_qs: str) -> str:
    t = html.escape(title or '分享')
    s = html.escape(slug)
    present = 'true' if allow_present else 'false'
    # iframe 必须带 `allow="fullscreen"`（+ legacy `allowfullscreen`）：制品在 `sandbox="allow-scripts"`
    # 的 opaque origin 子帧里跑，内置查看运行时点「放映」会 `element.requestFullscreen()`。沙箱本身不含
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
<iframe id="frame" sandbox="allow-scripts" allow="fullscreen" allowfullscreen src="/s/{s}/content{vt_qs}" title="{t}"></iframe>
<div id="bar"><span>{t}</span><button onclick="fs()">全屏</button></div>
<script>
var presentable={present};var bar=document.getElementById('bar');var idle;
function show(){{if(!presentable)return;bar.classList.add('show');clearTimeout(idle);
idle=setTimeout(function(){{bar.classList.remove('show');}},2000);}}
document.addEventListener('mousemove',show);show();
function fs(){{var el=document.documentElement;if(document.fullscreenElement){{document.exitFullscreen();}}
else if(el.requestFullscreen){{el.requestFullscreen();}}}}
document.addEventListener('keydown',function(e){{if(e.key==='f'||e.key==='F')fs();}});
</script></body></html>"""
