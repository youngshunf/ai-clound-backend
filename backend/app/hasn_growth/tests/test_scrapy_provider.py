"""Scrapy 深爬 provider + 客户端纯函数验收（doc93 §3.1·零 mock·无网络）。

覆盖深爬服务 item → structured_payload 映射（键对齐 cleaner_service·list/str 兼容）+ provider
注册（yellow_pages/b2b 同一类）+ 未配服务时诚实空。真实深爬（infra-gated·需 lead-crawler-service
部署）不在此测——doc93 §测试策略。
"""

from __future__ import annotations

import pytest

from backend.app.hasn_growth.service.provider_registry import CrawlRequest, get_provider
from backend.app.hasn_growth.service.scrapy_crawler_client import (
    build_crawl_body,
    scrapy_item_to_structured,
)
from backend.core.conf import settings


def test_scrapy_item_to_structured_keys_align_cleaner_contract() -> None:
    """深爬 item（复数键 phones/emails）→ structured_payload 键对齐 cleaner 提取产物。"""
    item = {
        'company_name': '上海示例贸易有限公司',
        'contact_name': '王经理',
        'phones': ['021-12345678', '13800000000'],
        'emails': ['sales@example.com'],
        'website': 'https://example.com',
        'province': '上海市',
        'city': '上海市',
        'address': '浦东新区张江路 1 号',
        'industry': '批发零售',
        'source_url': 'https://b2b.example.com/co/123',
    }
    s = scrapy_item_to_structured(item)
    assert s['company_name'] == '上海示例贸易有限公司'
    assert s['contact_name'] == '王经理'
    assert s['phones'] == ['021-12345678', '13800000000']
    assert s['emails'] == ['sales@example.com']
    assert s['website'] == 'https://example.com'
    assert s['region'] == '上海市' and s['city'] == '上海市'  # province → region
    assert s['address'] == '浦东新区张江路 1 号'
    assert s['industry'] == '批发零售'


def test_scrapy_item_to_structured_singular_and_alias() -> None:
    """单数 phone/email + company 别名 + 缺字段 → 兼容归一（不抛）。"""
    s = scrapy_item_to_structured({'company': '某公司', 'phone': '010-1', 'email': 'a@b.com'})
    assert s['company_name'] == '某公司'
    assert s['phones'] == ['010-1'] and s['emails'] == ['a@b.com']
    assert s['contact_name'] == '' and s['website'] == '' and s['region'] == ''


def test_scrapy_item_to_structured_empty_collections() -> None:
    s = scrapy_item_to_structured({'company_name': 'X'})
    assert s['phones'] == [] and s['emails'] == []


def test_scrapy_provider_registered_for_both_source_types() -> None:
    """同一 ScrapyProvider 类经叠加装饰器承载 yellow_pages + b2b 两个 source_type。"""
    yp = get_provider('yellow_pages')
    b2b = get_provider('b2b')
    assert type(yp).__name__ == 'ScrapyProvider' and yp.source_type == 'yellow_pages'
    assert type(b2b).__name__ == 'ScrapyProvider' and b2b.source_type == 'b2b'


def test_build_crawl_body_omits_proxy_when_empty() -> None:
    """doc93 §4.1：未配代理 → body 不带 proxy_url（不暴露空字段）。"""
    body = build_crawl_body(
        source_type='yellow_pages', keyword='建材', max_results=20, options=None, proxy_url=''
    )
    assert body == {'source_type': 'yellow_pages', 'keyword': '建材', 'max_results': 20, 'options': {}}
    assert 'proxy_url' not in body


def test_build_crawl_body_passes_proxy_when_set() -> None:
    """doc93 §4.1：云端集中配的住宅代理出口透传给深爬服务作 override（去首尾空白）。"""
    body = build_crawl_body(
        source_type='b2b',
        keyword='医疗器械',
        max_results=10,
        options={'detail_link_css': 'a'},
        proxy_url='  socks5h://u:p@host:39080  ',
    )
    assert body['proxy_url'] == 'socks5h://u:p@host:39080'
    assert body['options'] == {'detail_link_css': 'a'}


@pytest.mark.asyncio
async def test_scrapy_provider_skips_when_unconfigured(monkeypatch) -> None:
    """未配 LEAD_CRAWLER_URL（且非 dev 回落）→ provider 诚实产空（零 fake·不打网络）。"""
    # 强制服务未配置：清掉 settings + env，并把环境视为 prod（dev 会回落 127.0.0.1）。
    monkeypatch.setattr(settings, 'LEAD_CRAWLER_URL', '')
    monkeypatch.setattr(settings, 'ENVIRONMENT', 'prod')
    monkeypatch.delenv('LEAD_CRAWLER_URL', raising=False)
    provider = get_provider('yellow_pages')
    req = CrawlRequest(job_id=1, keyword='建材批发', source_type='yellow_pages')

    items = [item async for item in provider.crawl_stream(req, firecrawl_client=None)]
    assert items == []
