"""lead-crawler-service 的薄 HTTP client（doc93 §3.1 Scrapy 黄页/B2B 深爬·cloud-brokered）。

Scrapy 有独立 Twisted reactor 与 FastAPI async 冲突 → 抓取下沉到 `huanxing-apps/lead-crawler-service`
独立服务，主云端经本 client 中转（**daemon 不反代**，遵 cloud-brokered 原则）。服务自带 spider
规则（列表分页 → 详情页定向深爬），返回**已结构化** items → ScrapyProvider 预置 structured_payload
跳过 LLM。传输层失败（服务未配置/不可达/超时）一律归一成诚实 ok:false（零 fake，绝不抛、绝不造假）。

纯函数 `scrapy_item_to_structured`（item → cleaner 认得的 structured_payload）与 httpx 抓取分离，
便于零 mock 单测。真实部署/深爬 E2E 按 doc93 §测试策略 infra-gated（需 Scrapy 服务上线）。
"""

from __future__ import annotations

import logging

from typing import Any

import httpx

from backend.common.service_http import get_service_client
from backend.common.service_registry import service_endpoint

logger = logging.getLogger(__name__)

_SERVICE = 'lead-crawler'


def _error(error: str, message: str) -> dict[str, Any]:
    return {'ok': False, 'error': error, 'message': message, 'items': []}


def scrapy_item_to_structured(item: dict[str, Any]) -> dict[str, Any]:
    """深爬服务返回的 item → cleaner_service 认得的 structured_payload（键对齐 LLM 提取产物）。

    服务侧已抽取好字段；本层只做键名归一与防御（list/str 兼容），不再调 LLM。
    """

    def _as_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        text = str(value or '').strip()
        return [text] if text else []

    return {
        'company_name': str(item.get('company_name') or item.get('company') or '').strip(),
        'contact_name': str(item.get('contact_name') or '').strip(),
        'phones': _as_list(item.get('phones') if item.get('phones') is not None else item.get('phone')),
        'emails': _as_list(item.get('emails') if item.get('emails') is not None else item.get('email')),
        'website': str(item.get('website') or '').strip(),
        'region': str(item.get('region') or item.get('province') or '').strip(),
        'city': str(item.get('city') or '').strip(),
        'address': str(item.get('address') or '').strip(),
        'industry': str(item.get('industry') or '').strip(),
    }


async def crawl_leads(
    *,
    source_type: str,
    keyword: str,
    max_results: int,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """调深爬服务 `/v1/crawl` 出结构化 items（Bearer svc-token + 超时 + 错误归一）。

    :return: 规范化信封 dict（ok / error / items[...]）；ok:false 表示传输层或服务侧失败（诚实空）。
    """
    endpoint = service_endpoint(_SERVICE)
    if not endpoint.base_url:
        return _error('service_unconfigured', '获客深爬服务未配置（LEAD_CRAWLER_URL 为空）')
    headers: dict[str, str] = {}
    if endpoint.token:
        headers['Authorization'] = f'Bearer {endpoint.token}'
    body = {
        'source_type': source_type,
        'keyword': keyword,
        'max_results': max_results,
        'options': options or {},
    }
    try:
        client = get_service_client(_SERVICE)
        resp = await client.post(
            f'{endpoint.base_url}/v1/crawl', json=body, headers=headers, timeout=endpoint.timeout
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.TimeoutException:
        return _error('upstream_timeout', '深爬服务超时，请稍后重试')
    except httpx.HTTPStatusError as exc:
        logger.warning('scrapy_crawler HTTP %s for %s', exc.response.status_code, source_type)
        return _error('upstream_error', f'深爬服务返回错误 HTTP {exc.response.status_code}')
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning('scrapy_crawler unreachable for %s: %s', source_type, exc.__class__.__name__)
        return _error('upstream_error', f'深爬服务暂时不可用: {exc.__class__.__name__}')
    if not isinstance(data, dict):
        return _error('upstream_error', '深爬服务返回了非预期格式')
    return data
