"""高德/百度 地图 Place API 客户端（doc93 §3.2 maps 源·POI 文本搜索）。

直连官方 Place API 出 POI 线索——POI 已结构化（公司名/地址/电话/行政区），**跳过 LLM 提取**
（doc08 §3 混合架构：地图走 Place API，不走 firecrawl+LLM）。高德优先（`AMAP_API_KEY`），
回落百度（`BAIDU_MAP_AK`）；两者都没配 → 调用方 honest 返回空（不 fake·真实 key 由福仔提供，
真抓 E2E infra-gated）。

纯函数（参数构造 / 响应解析 / POI→structured_payload）与 httpx 抓取分离，便于零 mock 单测：
解析直接喂真实形状的 Place API 响应字典断言，无需打网络。
"""

from __future__ import annotations

from typing import Any

import httpx

AMAP_TEXT_SEARCH_URL = 'https://restapi.amap.com/v3/place/text'
BAIDU_PLACE_SEARCH_URL = 'https://api.map.baidu.com/place/v2/search'

_AMAP_PAGE_CAP = 25  # 高德单页 offset 上限（v3 文档）
_BAIDU_PAGE_CAP = 20  # 百度单页 page_size 上限


def _txt(value: Any) -> str:
    """高德空字段会返回 ``[]`` 而非空串；统一归一为去空白字符串。"""
    return value.strip() if isinstance(value, str) else ''


# ───────────────────────── 高德（AMap）v3 ─────────────────────────


def build_amap_params(*, keyword: str, city: str, limit: int, key: str) -> dict[str, str]:
    """高德 v3 place/text 文本搜索参数（extensions=all 取电话/地址）。"""
    return {
        'key': key,
        'keywords': keyword,
        'city': city or '',
        'citylimit': 'true' if city else 'false',
        'offset': str(min(max(limit, 1), _AMAP_PAGE_CAP)),
        'page': '1',
        'extensions': 'all',
        'output': 'JSON',
    }


def parse_amap_pois(data: Any) -> list[dict[str, Any]]:
    """解析高德响应为归一 POI 列表（status!='1' 或无 pois → 空·诚实）。"""
    if not isinstance(data, dict) or str(data.get('status')) != '1':
        return []
    out: list[dict[str, Any]] = []
    for p in data.get('pois') or []:
        if not isinstance(p, dict):
            continue
        name = _txt(p.get('name'))
        if not name:
            continue
        out.append(
            {
                'name': name,
                'tel': _txt(p.get('tel')),
                'province': _txt(p.get('pname')),
                'city': _txt(p.get('cityname')),
                'address': _txt(p.get('address')),
                'industry': _txt(p.get('type')).split(';')[0],
                'location': _txt(p.get('location')),
                'source': 'amap',
            }
        )
    return out


# ───────────────────────── 百度（Baidu）place/v2 ─────────────────────────


def build_baidu_params(*, keyword: str, city: str, limit: int, ak: str) -> dict[str, str]:
    """百度 place/v2/search 参数（scope=2 取详情含电话）。"""
    return {
        'query': keyword,
        'region': city or '全国',
        'city_limit': 'true' if city else 'false',
        'output': 'json',
        'scope': '2',
        'page_size': str(min(max(limit, 1), _BAIDU_PAGE_CAP)),
        'page_num': '0',
        'ak': ak,
    }


def parse_baidu_pois(data: Any) -> list[dict[str, Any]]:
    """解析百度响应为归一 POI 列表（status!=0 或无 results → 空·诚实）。"""
    if not isinstance(data, dict):
        return []
    try:
        status = int(data.get('status'))  # 0=成功；None/非数字 → 拒（不能用 `or` 兜底，0 是成功值且 falsy）
    except (TypeError, ValueError):
        return []
    if status != 0:
        return []
    out: list[dict[str, Any]] = []
    for p in data.get('results') or []:
        if not isinstance(p, dict):
            continue
        name = _txt(p.get('name'))
        if not name:
            continue
        detail = p.get('detail_info') if isinstance(p.get('detail_info'), dict) else {}
        out.append(
            {
                'name': name,
                'tel': _txt(p.get('telephone')),
                'province': _txt(p.get('province')),
                'city': _txt(p.get('city')),
                'address': _txt(p.get('address')),
                'industry': _txt(detail.get('tag')).split(';')[0] if detail else '',
                'location': '',
                'source': 'baidu',
            }
        )
    return out


# ───────────────────────── POI → structured_payload ─────────────────────────


def poi_to_structured(poi: dict[str, Any]) -> dict[str, Any]:
    """归一 POI → cleaner_service 认得的 structured_payload（键对齐 LLM 提取产物）。

    电话可能是 ``;`` / ``，`` 分隔的多号 → 拆成 ``phones`` 列表（cleaner 取首个有效号）。
    """
    raw_tel = str(poi.get('tel') or '').replace('，', ';').replace(',', ';')
    phones = [t.strip() for t in raw_tel.split(';') if t.strip()]
    return {
        'company_name': poi.get('name') or '',
        'phones': phones,
        'region': poi.get('province') or '',
        'city': poi.get('city') or '',
        'address': poi.get('address') or '',
        'industry': poi.get('industry') or '',
    }


# ───────────────────────── httpx 抓取（infra-gated·真实 key） ─────────────────────────


async def search_place_pois(
    *,
    keyword: str,
    city: str,
    limit: int,
    amap_key: str = '',
    baidu_ak: str = '',
    client: httpx.AsyncClient | None = None,
    timeout: float = 15.0,
) -> list[dict[str, Any]]:
    """文本搜索 POI：高德优先，回落百度；都没配 → 空。真实 HTTP（零 mock），失败抛错不静默。"""
    amap_key = (amap_key or '').strip()
    baidu_ak = (baidu_ak or '').strip()
    if amap_key:
        url, params = AMAP_TEXT_SEARCH_URL, build_amap_params(keyword=keyword, city=city, limit=limit, key=amap_key)
        parse = parse_amap_pois
    elif baidu_ak:
        url, params = BAIDU_PLACE_SEARCH_URL, build_baidu_params(keyword=keyword, city=city, limit=limit, ak=baidu_ak)
        parse = parse_baidu_pois
    else:
        return []

    if client is not None:
        resp = await client.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        return parse(resp.json())
    async with httpx.AsyncClient(timeout=timeout) as owned:
        resp = await owned.get(url, params=params)
        resp.raise_for_status()
        return parse(resp.json())
