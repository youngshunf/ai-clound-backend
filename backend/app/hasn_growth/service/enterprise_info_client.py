"""企业工商官方 API client（doc93 §3.3 可选源·企查查/天眼查等）。

工商 API 直返结构化企业信息（公司名/法定代表人/电话/地址/行政区/行业），作为硬爬的
「可切换的更稳路径」。端点 + key 由运营按所用厂商配置（**不硬编码厂商 URL**，避免猜错 API path），
响应走**防御式多键解析**——同时兼容企查查（`Name`/`OperName`/`PhoneNumber`…）与天眼查
（`name`/`legalPersonName`/`phone`…）及中文字段名（`法定代表人`/`电话`…）。

纯函数 `enterprise_record_to_structured`（记录 → cleaner 认得的 structured_payload）与 httpx 抓取
分离，便于零 mock 单测。base/key 未配 → provider 诚实跳过（不调用）；真实账号 E2E infra-gated。
"""

from __future__ import annotations

import logging

from typing import Any

import httpx

logger = logging.getLogger(__name__)

# 各厂商响应顶层「记录列表」可能的容器键（防御式，按序探测）。
_LIST_KEYS = ('result', 'data', 'items', 'rows', 'records', 'Result', 'list')
# 各字段的多厂商/中英文候选键（按序取首个非空）。
_NAME_KEYS = ('company_name', 'CompanyName', 'Name', 'name', 'companyName', '企业名称', '公司名称')
_CONTACT_KEYS = ('OperName', 'legalPersonName', 'legal_rep', 'oper_name', 'frName', '法定代表人', '负责人')
_PHONE_KEYS = ('PhoneNumber', 'phone', 'Phone', 'phoneNumber', 'tel', 'contactNumber', '电话', '联系电话')
_EMAIL_KEYS = ('Email', 'email', 'emailAddress', '邮箱')
_WEBSITE_KEYS = ('WebSite', 'website', 'webSite', 'url', '网址', '官网')
_ADDRESS_KEYS = ('Address', 'address', 'regLocation', 'addr', '地址', '注册地址')
_REGION_KEYS = ('Province', 'province', 'region', 'base', '省份', '地区')
_CITY_KEYS = ('City', 'city', '城市')
_INDUSTRY_KEYS = ('Industry', 'industry', 'industryName', 'categoryName', '行业', '所属行业')


def _first(record: dict[str, Any], keys: tuple[str, ...]) -> str:
    """按候选键顺序取首个非空字符串值（防御 None / 非 str）。"""
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
    return ''


def enterprise_record_to_structured(record: dict[str, Any]) -> dict[str, Any]:
    """工商 API 单条记录 → cleaner_service 认得的 structured_payload（键对齐 LLM 提取产物）。

    法定代表人映射为 ``contact_name``；电话/邮箱归一为单元素列表（无则空列表）。
    """
    phone = _first(record, _PHONE_KEYS)
    email = _first(record, _EMAIL_KEYS)
    return {
        'company_name': _first(record, _NAME_KEYS),
        'contact_name': _first(record, _CONTACT_KEYS),
        'phones': [phone] if phone else [],
        'emails': [email] if email else [],
        'website': _first(record, _WEBSITE_KEYS),
        'region': _first(record, _REGION_KEYS),
        'city': _first(record, _CITY_KEYS),
        'address': _first(record, _ADDRESS_KEYS),
        'industry': _first(record, _INDUSTRY_KEYS),
    }


def extract_records(data: Any) -> list[dict[str, Any]]:
    """从厂商响应里防御式抽出企业记录列表（兼容多种容器形状）。"""
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if not isinstance(data, dict):
        return []
    for key in _LIST_KEYS:
        value = data.get(key)
        if isinstance(value, list):
            return [r for r in value if isinstance(r, dict)]
        # result/data 有时再嵌一层 {items:[...]}/{rows:[...]}
        if isinstance(value, dict):
            nested = extract_records(value)
            if nested:
                return nested
    return []


async def search_enterprises(
    *,
    keyword: str,
    region: str,
    limit: int,
    api_base: str,
    api_key: str,
    client: httpx.AsyncClient | None = None,
    timeout: float = 15.0,
) -> list[dict[str, Any]]:
    """调工商 API 搜企业，返回**已结构化**记录列表；未配 base/key 或无命中 → 空列表（诚实，不 fake）。

    GET ``{api_base}`` 带 ``key`` + ``keyword``（+ 可选 ``region``）+ ``pageSize``。厂商各异，
    故响应走 :func:`extract_records` 防御式解析。网络/解析异常一律吞为空列表（上游 provider 已诚实空）。
    """
    api_base = (api_base or '').strip()
    api_key = (api_key or '').strip()
    keyword = (keyword or '').strip()
    if not api_base or not api_key or not keyword:
        return []
    params: dict[str, Any] = {'key': api_key, 'keyword': keyword, 'pageSize': max(1, min(limit, 50))}
    if region.strip():
        params['province'] = region.strip()

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=timeout)
    try:
        resp = await client.get(api_base, params=params)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning('enterprise_info API unreachable: %s', exc.__class__.__name__)
        return []
    finally:
        if owns_client:
            await client.aclose()

    records = extract_records(data)
    structured = [enterprise_record_to_structured(r) for r in records]
    return [s for s in structured if s['company_name']][:limit]
