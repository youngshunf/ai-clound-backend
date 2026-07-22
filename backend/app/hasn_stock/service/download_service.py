"""素材站下载落桶服务（A-P2-2）。

「服务端替分身请求外站 URL」形状 → 必须白名单化（绝不做通用下载器）。五步（§4.6）：
1. **SSRF 闸**：https + host 命中 provider 目录 `download_domains` 并集 + 解析后禁内网/环回/链路本地 IP；
   跟随重定向**逐跳复检**。
2. **流式下载**：大小封顶（图片 20MB / 视频 200MB）+ Content-Type 校验（image/* 或 video/*）+ 超时。
3. `StorageService.upload` 落 owner 私有桶（access=private）。
4. **双登记**：`register_asset`（image→kind=image，video→kind=file）→ `hasn_artifacts_service.record`
   （kind=image|video、source_kind=external、source_tool=hasn.stock.download、meta 带 provider/license/source_url）。
5. 出参 `{asset_uri, artifact_id, kind, width, height, size_bytes}`。

范式先例：`hasn_studio/service/studio_service.py::_ensure_artifact`（上传→register_asset→登记 artifact）。
"""

from __future__ import annotations

import ipaddress
import socket

from typing import Any
from urllib.parse import urlparse

import httpx

from backend.app.hasn.schema.hasn_artifacts import RecordArtifactParam
from backend.app.hasn.service.hasn_artifacts_service import hasn_artifacts_service
from backend.app.hasn.service.hasn_asset_service import hasn_asset_service
from backend.app.hasn_stock.service.provider_store import stock_provider_store
from backend.core.conf import settings
from backend.database.db import async_db_session
from backend.plugin.s3.service.storage_service import StorageService

# 大小封顶（字节）：图片 20MB / 视频 200MB（§4.6 步骤 2）
_MAX_IMAGE_BYTES = 20 * 1024 * 1024
_MAX_VIDEO_BYTES = 200 * 1024 * 1024
# 重定向最多跳数（逐跳复检）
_MAX_REDIRECTS = 5
# 落私有桶的 category（access=private）
_UPLOAD_CATEGORY = 'published_artifact'
_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
_UA = 'Mozilla/5.0 (compatible; AstraStockBot/1.0)'


class StockDownloadError(RuntimeError):
    """下载失败（SSRF 拒绝 / 超限 / 类型不符 / 传输错误）。"""


def _host_in_whitelist(host: str, whitelist: set[str]) -> bool:
    """host 命中白名单：精确或子域后缀匹配（如 videos.pexels.com 命中 pexels.com）。"""
    host = host.lower()
    return any(host == d or host.endswith('.' + d) for d in whitelist)


def _resolve_redirect_url(current: str, location: str) -> str:
    """基于当前下载地址解析重定向 Location。"""
    return str(httpx.URL(current).join(location))


# 透明代理（Clash/Surge/Mihomo TUN 模式）默认把公网域名劫持解析到 fake-ip 占位段
# 198.18.0.0/15（RFC 2544 benchmarking 保留段，正常内网/生产环境绝不会使用）。
# 仅开发环境（ENVIRONMENT='dev'）放行该段，让本机能真实测通 stock.download；
# 生产 ENVIRONMENT='prod' 永不放行，且生产真实 DNS 永不解析到此段 → 行为/安全零变化。
# 真内网段（10/172.16/192.168/127/169.254）无论 dev/prod 一律拒，不放宽任何真实安全。
_FAKE_IP_RANGE = ipaddress.ip_network('198.18.0.0/15')


def _is_dev_fake_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """开发环境下，透明代理 fake-ip 占位段视为可放行（仅本机测试用，生产永不触发）。

    透明代理（Clash/Mihomo）对同一域名常同时返回 IPv4 fake-ip（198.18.0.x）和内嵌
    该 IPv4 的 IPv6 形式（如 ::ffff:0:c612:d4，低 32 位 c612:00d4 = 198.18.0.212）。
    两种都要识别放行，否则遍历到 IPv6 fake-ip 仍会误伤。仅 dev 生效、且必须落在
    fake-ip 段（198.18.0.0/15，正常内网/公网绝不使用），生产 prod 直接短路返回。
    """
    if settings.ENVIRONMENT != 'dev':
        return False
    candidate: ipaddress.IPv4Address | ipaddress.IPv6Address = ip
    if isinstance(ip, ipaddress.IPv6Address):
        # IPv4-mapped 优先，否则取低 32 位当内嵌 IPv4，比对 fake-ip 段
        candidate = ip.ipv4_mapped or ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return isinstance(candidate, ipaddress.IPv4Address) and candidate in _FAKE_IP_RANGE


def _reject_private_ip(host: str) -> None:
    """解析 host → IP，任一为内网/环回/链路本地/保留 → 拒绝（SSRF 防线之二）。"""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise StockDownloadError(f'stock.download: 域名解析失败 {host}') from exc
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        # 开发环境放行透明代理 fake-ip 占位段（198.18.0.0/15）——本机测通用，生产永不触发
        if _is_dev_fake_ip(ip):
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise StockDownloadError(f'stock.download: 目标解析到非法 IP（{ip_str}），拒绝')


def _ssrf_check(url: str, whitelist: set[str]) -> None:
    """单个 URL 的 SSRF 复检：https + host 白名单 + 非内网 IP。"""
    parsed = urlparse(url)
    if parsed.scheme != 'https':
        raise StockDownloadError('stock.download: 仅允许 https 直链')
    host = (parsed.hostname or '').lower()
    if not host:
        raise StockDownloadError('stock.download: URL 无 host')
    if not _host_in_whitelist(host, whitelist):
        raise StockDownloadError(
            f'stock.download: host {host} 不在素材站下载白名单内（工具只下载素材站候选，非通用下载器）'
        )
    _reject_private_ip(host)


def _kind_and_cap(content_type: str) -> tuple[str, int]:
    """按 Content-Type 判媒体类型与大小上限。非 image/video → 拒绝。"""
    ct = (content_type or '').split(';')[0].strip().lower()
    if ct.startswith('image/'):
        return 'image', _MAX_IMAGE_BYTES
    if ct.startswith('video/'):
        return 'video', _MAX_VIDEO_BYTES
    raise StockDownloadError(f'stock.download: 不支持的 Content-Type「{ct or "空"}」（仅 image/* 或 video/*）')


class StockDownloadService:
    """下载素材站资源 → owner 私有桶 → 双登记。"""

    async def download(
        self,
        *,
        owner_hasn_id: str,
        agent_hasn_id: str,
        url: str,
        title: str | None = None,
        description: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """执行五步下载落桶双登记。身份由调用方（AgentContext）注入。

        分两段：`_stream_download`（外站取字节，唯一需真实出网的一步）→ `_store_and_register`
        （落桶+双登记+组装出参）。拆开是为让「落桶→双登记→检索」后半程能被真实基础设施
        E2E 独立覆盖（不依赖外站出网），前半程的纯网络取字节留给真实出网环境验证。

        `description`：素材站的语义描述（pexels alt / pixabay tags / coverr title+简介），
        取自 `hasn.stock.search` 出参的 `description`，落进 artifact `summary` 提升检索召回——
        下载的素材文件名/标题往往无意义（`pexels-photo-123.jpg`），有了语义 summary 才能被
        `hasn.artifact.search` 按「日落/城市/花卉」等关键词搜回。
        """
        whitelist = await stock_provider_store.enabled_download_domains()
        if not whitelist:
            raise StockDownloadError('stock.download: 未配置任何素材站下载域名，无法下载')

        data, content_type = await self._stream_download(url, whitelist)
        return await self._store_and_register(
            owner_hasn_id=owner_hasn_id,
            agent_hasn_id=agent_hasn_id,
            data=data,
            content_type=content_type,
            url=url,
            title=title,
            description=description,
            session_id=session_id,
        )

    async def _store_and_register(
        self,
        *,
        owner_hasn_id: str,
        agent_hasn_id: str,
        data: bytes,
        content_type: str,
        url: str,
        title: str | None = None,
        description: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """下载后半程：已取到的字节 → 落 owner 私有桶 → 双登记 → 组装出参。

        与 `_stream_download`（外站取字节）解耦，便于用真实桶 + 真实 PG 覆盖
        「落桶→register_asset→hasn_artifacts.record」这一环，无需真实外站出网。

        `description`（素材站语义描述）落进 artifact `summary`，提升素材被 `hasn.artifact.search`
        搜回的召回（文件名/标题常无意义，summary 才承载「日落/城市/花卉」等可检索语义）。
        """
        kind_media, _cap = _kind_and_cap(content_type)  # image / video
        provider_view = await stock_provider_store.provider_for_domain(host=(urlparse(url).hostname or ''))

        filename = self._filename(url, kind_media)
        async with async_db_session.begin() as db:
            ref = await StorageService.upload(
                db, data, category=_UPLOAD_CATEGORY, filename=filename, content_type=content_type
            )
            # register_asset：image→kind=image，video→kind=file（hasn_assets.kind 仅 image/voice/file）。
            asset = await hasn_asset_service.register_asset(
                db,
                owner_hasn_id=owner_hasn_id,
                ref=ref,
                kind='image' if kind_media == 'image' else 'file',
                extract_status='done',  # 外部素材不进抽取流水线
            )
            asset_uri = f'hasn://asset/{asset.asset_id}'
            artifact_id = await hasn_artifacts_service.record(
                db,
                agent_hasn_id=agent_hasn_id,
                owner_hasn_id=owner_hasn_id,
                params=RecordArtifactParam(
                    kind=kind_media,  # artifact 层 kind=image|video（doc35 6 枚举内）
                    title=(title or filename),
                    # 素材站语义描述落 summary → hasn.artifact.search 按语义关键词可搜回
                    # （search 命中 title OR summary；素材文件名/标题常无意义，summary 才承载可检索语义）。
                    summary=(description or None),
                    asset_id=asset.asset_id,
                    session_id=session_id,
                    source_tool='hasn.stock.download',
                    # 外部取材（doc35 §5.2 点名 hasn.stock.download 就是 external_tool 的典型产出者）。
                    # 旧值 'external' 语义相同但不在 6 枚举内，Literal 收敛后会 422。
                    source_kind='external_import',
                    metadata={
                        'provider': provider_view.provider if provider_view else None,
                        'license': provider_view.license_terms_url if provider_view else None,
                        'source_url': url,
                        'mime': content_type,
                        'size_bytes': ref.size,
                    },
                ),
            )
        return {
            'asset_uri': asset_uri,
            'artifact_id': artifact_id,
            'kind': kind_media,
            'width': asset.width,
            'height': asset.height,
            'size_bytes': ref.size,
        }

    async def _stream_download(self, url: str, whitelist: set[str]) -> tuple[bytes, str]:
        """手动跟随重定向（逐跳 SSRF 复检）+ 流式下载 + 大小封顶 + 类型校验。返回 (字节, content_type)。"""
        current = url
        async with httpx.AsyncClient(timeout=_TIMEOUT, trust_env=True, follow_redirects=False) as client:
            for _ in range(_MAX_REDIRECTS + 1):
                _ssrf_check(current, whitelist)  # 逐跳复检
                async with client.stream('GET', current, headers={'User-Agent': _UA}) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        location = resp.headers.get('location')
                        if not location:
                            raise StockDownloadError('stock.download: 重定向缺 Location')
                        current = _resolve_redirect_url(current, location)
                        continue
                    if resp.status_code >= 400:
                        raise StockDownloadError(f'stock.download: 源站 HTTP {resp.status_code}')
                    content_type = resp.headers.get('content-type', '')
                    _kind, cap = _kind_and_cap(content_type)  # 先按类型定 cap（也拒非 image/video）
                    # Content-Length 预检（有则先挡）
                    clen = resp.headers.get('content-length')
                    if clen and clen.isdigit() and int(clen) > cap:
                        raise StockDownloadError(f'stock.download: 文件超限（声明 {clen} 字节 > 上限 {cap}）')
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in resp.aiter_bytes():
                        total += len(chunk)
                        if total > cap:
                            raise StockDownloadError(f'stock.download: 文件超限（> {cap} 字节），已中止')
                        chunks.append(chunk)
                    if total == 0:
                        raise StockDownloadError('stock.download: 下载到空文件')
                    return b''.join(chunks), content_type
        raise StockDownloadError(f'stock.download: 重定向超过 {_MAX_REDIRECTS} 跳')

    def _filename(self, url: str, kind_media: str) -> str:
        """从 URL 末段取文件名，缺省按媒体类型给默认后缀。"""
        path = urlparse(url).path
        name = path.rsplit('/', 1)[-1] if path else ''
        if name and '.' in name:
            return name[:120]
        return f'stock.{"jpg" if kind_media == "image" else "mp4"}'


stock_download_service = StockDownloadService()
