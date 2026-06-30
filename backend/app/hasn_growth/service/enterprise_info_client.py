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

# 各厂商响应顶层「记录列表」可能的容器键（防御式，按序探测）。含 qcc 中文容器键（如 get_company_by_query
# 候选列表落在 `企业信息`）；未知容器键由 extract_records 的通用扫描兜底。
_LIST_KEYS = (
    'result',
    'data',
    'items',
    'rows',
    'records',
    'Result',
    'list',
    '企业信息',
    '企业列表',
    '结果',
    '数据',
    '列表',
    '候选',
)
# 各字段的多厂商/中英文候选键（按序取首个非空）。中文键含 qcc 真实自省返回的字段名（registration 直返
# `企业名称`/`法定代表人`/`注册地址`/`所属地区`/`国标行业`；search 候选用 `法定代表人名称`（列表值））。
_NAME_KEYS = ('company_name', 'CompanyName', 'Name', 'name', 'companyName', '企业名称', '公司名称')
_CONTACT_KEYS = (
    'OperName',
    'legalPersonName',
    'legal_rep',
    'oper_name',
    'frName',
    '法定代表人',
    '法定代表人名称',
    '负责人',
)
_PHONE_KEYS = ('PhoneNumber', 'phone', 'Phone', 'phoneNumber', 'tel', 'contactNumber', '电话', '联系电话')
_EMAIL_KEYS = ('Email', 'email', 'emailAddress', '邮箱')
_WEBSITE_KEYS = ('WebSite', 'website', 'webSite', 'url', '网址', '官网')
_ADDRESS_KEYS = ('Address', 'address', 'regLocation', 'addr', '地址', '注册地址', '通信地址')
_REGION_KEYS = ('Province', 'province', 'region', 'base', '省份', '地区', '所属地区')
_CITY_KEYS = ('City', 'city', '城市')
_INDUSTRY_KEYS = ('Industry', 'industry', 'industryName', 'categoryName', '行业', '所属行业', '国标行业')


def _first(record: dict[str, Any], keys: tuple[str, ...]) -> str:
    """按候选键顺序取首个非空字符串值（防御 None / 非 str；list 取首个非空字符串元素，兼容 qcc
    `法定代表人名称: ["王传福"]` 形态）。"""
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    return item.strip()
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


def _dict_list(value: Any) -> list[dict[str, Any]]:
    """value 若为 list 则取其中的 dict 元素（过滤非 dict），否则空列表。"""
    if isinstance(value, list):
        return [r for r in value if isinstance(r, dict)]
    return []


def _scan_for_records(data: dict[str, Any]) -> list[dict[str, Any]]:
    """先按已知容器键（含一层嵌套）探测，未命中再通用扫描所有值取首个 dict 列表。"""
    for key in _LIST_KEYS:
        value = data.get(key)
        records = _dict_list(value)
        if records:
            return records
        # result/data 有时再嵌一层 {items:[...]}/{rows:[...]}
        if isinstance(value, dict):
            nested = extract_records(value)
            if nested:
                return nested
    # 通用兜底：扫描任意值，取首个「dict 列表」（兼容未知中文容器键，如 企业信息/候选）
    for value in data.values():
        records = _dict_list(value)
        if records:
            return records
    return []


def extract_records(data: Any) -> list[dict[str, Any]]:
    """从厂商响应里防御式抽出企业记录列表（兼容多种容器形状 + 中文容器键 + 单条对象直返）。

    qcc 真实形态：``get_company_registration_info`` 直返**单个公司 flat dict**（无 list 容器）；
    ``get_company_by_query`` 候选列表落在中文键 ``企业信息``。故在已知容器键之外，再做通用「首个 dict 列表」
    扫描（兜底未知中文容器键）+ 单条企业对象兜底（dict 自身即一条记录）。
    """
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if not isinstance(data, dict):
        return []
    records = _scan_for_records(data)
    if records:
        return records
    # 单条企业对象兜底：dict 自身即一条记录（qcc registration 直返单公司，无 list 容器）
    if any(key in data for key in _NAME_KEYS):
        return [data]
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
