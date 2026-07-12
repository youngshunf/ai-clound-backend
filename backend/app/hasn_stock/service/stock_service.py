"""通用素材站搜索服务（A-P2-1）。

provider 适配器移植自 reel-engine `app/services/material.py`（pexels/pixabay/coverr 视频），
改 async httpx，并**补齐 pexels/pixabay 的图片搜索**。编排两种语义（§4.5）：

- **点名 source** → 只打指定站；失败直接抛（诚实结果，不偷换）。
- **默认 failover 链** → `enabled ∧ 支持 media_type`，`priority` 升序逐站尝试；某站无 key / 超时 / 报错 /
  **零结果**（空手而归也算未命中）→ 记 warn 降级下一站；**全链失败才抛**（附各站失败原因）。

出参每条：`{provider, source_url, preview_url, width, height, duration_ms?, license}`。
`source_url` 是外站直链——工具描述已防呆「禁直接入正文/发布，先 stock.download 收进私有桶」。
"""

from __future__ import annotations

import logging

from typing import Any

import httpx

from backend.app.hasn_stock.service.provider_store import ProviderView, stock_provider_store

logger = logging.getLogger(__name__)

# 出参 per_page 默认/封顶（§4.5）
_PER_PAGE_DEFAULT = 10
_PER_PAGE_MAX = 20
# pixabay 要求 per_page >= 3
_PIXABAY_MIN_PER_PAGE = 3

# 出网超时（连接/读）。海外素材站 → trust_env=True 让 HTTP(S)_PROXY 生效（既有出网代理通道，§7）。
_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
_UA = 'Mozilla/5.0 (compatible; AstraStockBot/1.0)'


class StockProviderError(Exception):
    """单站搜索失败（无 key / 超时 / 报错）。附 provider 与原因，供 failover 汇总。"""

    def __init__(self, provider: str, reason: str) -> None:
        self.provider = provider
        self.reason = reason
        super().__init__(f'{provider}: {reason}')


def _clamp_per_page(per_page: int | None) -> int:
    if not isinstance(per_page, int) or per_page <= 0:
        return _PER_PAGE_DEFAULT
    return min(per_page, _PER_PAGE_MAX)


def _pexels_orientation(orientation: str | None) -> str | None:
    # pexels 接受 landscape/portrait/square，直传。
    return orientation if orientation in ('landscape', 'portrait', 'square') else None


def _pixabay_image_orientation(orientation: str | None) -> str:
    # pixabay 图片：horizontal/vertical/all（无 square → all）。
    return {'landscape': 'horizontal', 'portrait': 'vertical'}.get(orientation or '', 'all')


async def _get_json(url: str, *, headers: dict[str, str] | None, params: dict[str, Any], provider: str) -> Any:
    """出网 GET → JSON。任何传输/状态异常 → StockProviderError（带原因）。"""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, trust_env=True, follow_redirects=True) as client:
            resp = await client.get(url, headers={'User-Agent': _UA, **(headers or {})}, params=params)
    except httpx.HTTPError as exc:
        raise StockProviderError(provider, f'请求失败：{exc.__class__.__name__}') from exc
    if resp.status_code == 429:
        raise StockProviderError(provider, '限频（429）')
    if resp.status_code in (401, 403):
        raise StockProviderError(provider, f'鉴权失败（{resp.status_code}，检查 api_key）')
    if resp.status_code >= 400:
        raise StockProviderError(provider, f'HTTP {resp.status_code}')
    try:
        return resp.json()
    except Exception as exc:
        raise StockProviderError(provider, '响应非 JSON') from exc


# ---------------------------------------------------------------- provider 适配器


async def _search_pexels(
    pv: ProviderView, query: str, media_type: str, orientation: str | None, per_page: int
) -> list[dict]:
    if not pv.api_key:
        raise StockProviderError('pexels', '未配 api_key')
    headers = {'Authorization': pv.api_key}
    params: dict[str, Any] = {'query': query, 'per_page': per_page}
    ori = _pexels_orientation(orientation)
    if ori:
        params['orientation'] = ori
    if media_type == 'video':
        data = await _get_json(
            'https://api.pexels.com/videos/search', headers=headers, params=params, provider='pexels'
        )
        out: list[dict] = []
        for v in data.get('videos', []) or []:
            files = sorted(
                (f for f in (v.get('video_files') or []) if f.get('link')),
                key=lambda f: (f.get('width') or 0) * (f.get('height') or 0),
                reverse=True,
            )
            if not files:
                continue
            best = files[0]
            out.append({
                'provider': 'pexels',
                'source_url': best['link'],
                'preview_url': v.get('image'),
                'width': best.get('width') or v.get('width'),
                'height': best.get('height') or v.get('height'),
                'duration_ms': int((v.get('duration') or 0) * 1000) or None,
                'license': pv.license_terms_url,
            })
        return out
    # image
    data = await _get_json('https://api.pexels.com/v1/search', headers=headers, params=params, provider='pexels')
    out = []
    for p in data.get('photos', []) or []:
        src = p.get('src') or {}
        source_url = src.get('original') or src.get('large2x') or src.get('large')
        if not source_url:
            continue
        out.append({
            'provider': 'pexels',
            'source_url': source_url,
            'preview_url': src.get('medium') or src.get('tiny'),
            'width': p.get('width'),
            'height': p.get('height'),
            'license': pv.license_terms_url,
        })
    return out


async def _search_pixabay(
    pv: ProviderView, query: str, media_type: str, orientation: str | None, per_page: int
) -> list[dict]:
    if not pv.api_key:
        raise StockProviderError('pixabay', '未配 api_key')
    per_page = max(per_page, _PIXABAY_MIN_PER_PAGE)
    if media_type == 'video':
        params: dict[str, Any] = {'key': pv.api_key, 'q': query, 'video_type': 'all', 'per_page': per_page}
        data = await _get_json('https://pixabay.com/api/videos/', headers=None, params=params, provider='pixabay')
        out: list[dict] = []
        for h in data.get('hits', []) or []:
            videos = h.get('videos') or {}
            picked = videos.get('large') or videos.get('medium') or videos.get('small') or videos.get('tiny')
            if not picked or not picked.get('url'):
                continue
            out.append({
                'provider': 'pixabay',
                'source_url': picked['url'],
                'preview_url': picked.get('thumbnail') or h.get('userImageURL'),
                'width': picked.get('width'),
                'height': picked.get('height'),
                'duration_ms': int((h.get('duration') or 0) * 1000) or None,
                'license': pv.license_terms_url,
            })
        return out
    # image
    params = {
        'key': pv.api_key,
        'q': query,
        'image_type': 'photo',
        'per_page': per_page,
        'orientation': _pixabay_image_orientation(orientation),
    }
    data = await _get_json('https://pixabay.com/api/', headers=None, params=params, provider='pixabay')
    out = []
    for h in data.get('hits', []) or []:
        source_url = h.get('largeImageURL') or h.get('webformatURL')
        if not source_url:
            continue
        out.append({
            'provider': 'pixabay',
            'source_url': source_url,
            'preview_url': h.get('webformatURL') or h.get('previewURL'),
            'width': h.get('imageWidth'),
            'height': h.get('imageHeight'),
            'license': pv.license_terms_url,
        })
    return out


async def _search_coverr(
    pv: ProviderView, query: str, media_type: str, orientation: str | None, per_page: int
) -> list[dict]:
    if media_type != 'video':
        raise StockProviderError('coverr', '仅支持 video')
    if not pv.api_key:
        raise StockProviderError('coverr', '未配 api_key')
    headers = {'Authorization': f'Bearer {pv.api_key}'}
    params = {'query': query, 'page_size': per_page, 'urls': 'true', 'sort': 'popular'}
    data = await _get_json('https://api.coverr.co/videos', headers=headers, params=params, provider='coverr')
    if not isinstance(data, dict):
        raise StockProviderError('coverr', '响应结构异常')
    out: list[dict] = []
    for v in data.get('hits', []) or []:
        urls = v.get('urls') or {}
        source_url = urls.get('mp4_download') or urls.get('mp4')
        if not source_url:
            continue
        raw_dur = v.get('duration')
        try:
            duration_ms = int(float(raw_dur) * 1000) if raw_dur is not None else None
        except (TypeError, ValueError):
            duration_ms = None
        out.append({
            'provider': 'coverr',
            'source_url': source_url,
            'preview_url': v.get('poster') or v.get('thumbnail'),
            'width': (v.get('info') or {}).get('width'),
            'height': (v.get('info') or {}).get('height'),
            'duration_ms': duration_ms,
            'license': pv.license_terms_url,
        })
    return out


# provider 标识 → 适配器
_ADAPTERS = {
    'pexels': _search_pexels,
    'pixabay': _search_pixabay,
    'coverr': _search_coverr,
}


async def _search_one(
    pv: ProviderView, query: str, media_type: str, orientation: str | None, per_page: int
) -> list[dict]:
    """打单个 provider。未接适配器 → 报错（目录加了行但没写代码）。"""
    adapter = _ADAPTERS.get(pv.provider)
    if adapter is None:
        raise StockProviderError(pv.provider, '无对应适配器（需在 StockService 补 provider 代码）')
    return await adapter(pv, query, media_type, orientation, per_page)


class StockService:
    """素材站搜索编排（点名 vs 默认 failover 链）。"""

    async def search(
        self,
        *,
        query: str,
        media_type: str = 'image',
        source: str | None = None,
        orientation: str | None = None,
        per_page: int | None = None,
    ) -> list[dict]:
        """搜素材站。source 点名→只打该站失败即抛；不传→默认 failover 链。"""
        query = (query or '').strip()
        if not query:
            raise RuntimeError("stock.search: 'query' 必填")
        if media_type not in ('image', 'video'):
            raise RuntimeError("stock.search: 'media_type' 只能是 image / video")
        per_page = _clamp_per_page(per_page)

        if source:
            # 点名 source：以 DB 为权威复校，不静默换站；失败即抛（§4.5）。
            pv = await self._resolve_point_source(source, media_type)
            return await _search_one(pv, query, media_type, orientation, per_page)
        # 不传 source：走默认 failover 链。
        return await self._search_failover(query, media_type, orientation, per_page)

    async def _resolve_point_source(self, source: str, media_type: str) -> ProviderView:
        """点名 source 的 DB 权威复校（存在/启用/支持 media_type）。任一不满足即抛，附支持清单。"""
        pv = await stock_provider_store.resolve_source(source=source)
        supported = await stock_provider_store.enabled_sources(media_type=media_type)
        if pv is None:
            raise RuntimeError(f"stock.search: 素材站 '{source}' 不存在。当前支持：{supported}")
        if not pv.enabled:
            raise RuntimeError(f"stock.search: 素材站 '{source}' 已禁用。当前支持：{supported}")
        if media_type not in pv.media_types:
            raise RuntimeError(
                f"stock.search: 素材站 '{source}' 不支持 {media_type}（支持 {list(pv.media_types)}）。"
                f'支持 {media_type} 的站：{supported}'
            )
        return pv

    async def _search_failover(
        self, query: str, media_type: str, orientation: str | None, per_page: int
    ) -> list[dict]:
        """默认 failover 链：enabled ∧ 支持 media_type、priority 升序逐站尝试；某站失败/零结果记 warn
        降级下一站；全链失败才抛（附各站原因）。"""
        chain = await stock_provider_store.failover_chain(media_type=media_type)
        if not chain:
            raise RuntimeError(f'stock.search: 没有支持 {media_type} 的已启用素材站，请在后台配置')
        failures: list[str] = []
        for pv in chain:
            try:
                results = await _search_one(pv, query, media_type, orientation, per_page)
            except StockProviderError as exc:
                logger.warning('stock.search failover 降级 %s：%s', pv.provider, exc.reason)
                failures.append(f'{pv.provider}: {exc.reason}')
                continue
            except Exception as exc:
                logger.warning('stock.search failover 降级 %s（意外异常）：%s', pv.provider, exc)
                failures.append(f'{pv.provider}: {exc.__class__.__name__}')
                continue
            if not results:
                # 零结果也算未命中（素材搜索要的是候选），记 warn 降级下一站。
                logger.warning('stock.search failover 降级 %s：零结果', pv.provider)
                failures.append(f'{pv.provider}: 零结果')
                continue
            return results
        raise RuntimeError('stock.search: 全部素材站均未命中。各站原因：' + '；'.join(failures))


stock_service = StockService()
