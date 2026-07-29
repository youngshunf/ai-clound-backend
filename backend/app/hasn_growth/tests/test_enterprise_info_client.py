"""企业工商 API 源纯函数验收（doc93 §3.3 可选源·零 mock·无网络）。

覆盖多厂商记录 → structured_payload 多键映射（企查查 PascalCase / 天眼查 camelCase / 中文键）+
防御式列表抽取（多种容器形状）+ provider 注册 + 未配 base/key 时诚实空。真实 HTTP（infra-gated·
真实工商账号）不在此测——doc93 §测试策略。
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.app.hasn_growth.service.enterprise_info_client import (
    enterprise_record_to_structured,
    extract_records,
)
from backend.app.hasn_growth.service.provider_registry import CrawlRequest, get_provider
from backend.core.conf import settings


def test_record_to_structured_qichacha_shape() -> None:
    """企查查风格（PascalCase）→ structured_payload，法定代表人映射为 contact_name。"""
    rec = {
        'Name': '杭州示例科技有限公司',
        'OperName': '张三',
        'PhoneNumber': '0571-88888888',
        'Email': 'Contact@Example.com',
        'Address': '浙江省杭州市西湖区示例路 1 号',
        'Province': '浙江省',
        'City': '杭州市',
        'Industry': '软件和信息技术服务业',
    }
    s = enterprise_record_to_structured(rec)
    assert s['company_name'] == '杭州示例科技有限公司'
    assert s['contact_name'] == '张三'
    assert s['phones'] == ['0571-88888888']
    assert s['emails'] == ['Contact@Example.com']
    assert s['region'] == '浙江省' and s['city'] == '杭州市'
    assert s['address'] == '浙江省杭州市西湖区示例路 1 号'
    assert s['industry'] == '软件和信息技术服务业'


def test_record_to_structured_tianyancha_shape() -> None:
    """天眼查风格（camelCase）+ 缺字段 → 兼容归一，无电话则空列表。"""
    rec = {'name': '北京示例信息有限公司', 'legalPersonName': '李四', 'regLocation': '北京市朝阳区'}
    s = enterprise_record_to_structured(rec)
    assert s['company_name'] == '北京示例信息有限公司'
    assert s['contact_name'] == '李四'
    assert s['address'] == '北京市朝阳区'
    assert s['phones'] == [] and s['emails'] == []


def test_record_to_structured_chinese_keys() -> None:
    """纯中文字段名也能解析（部分厂商直接返回中文键）。"""
    rec = {'企业名称': '广州示例实业', '法定代表人': '王五', '电话': '020-12345678', '行业': '制造业'}
    s = enterprise_record_to_structured(rec)
    assert s['company_name'] == '广州示例实业'
    assert s['contact_name'] == '王五'
    assert s['phones'] == ['020-12345678']
    assert s['industry'] == '制造业'


def test_extract_records_container_shapes() -> None:
    """防御式抽列表：顶层 list / result / data.items / 空。"""
    assert extract_records([{'Name': 'A'}]) == [{'Name': 'A'}]
    assert extract_records({'result': [{'Name': 'B'}]}) == [{'Name': 'B'}]
    assert extract_records({'data': {'items': [{'Name': 'C'}]}}) == [{'Name': 'C'}]
    assert extract_records({'status': 'ok'}) == []
    assert extract_records('nope') == []


def test_enterprise_provider_registered() -> None:
    provider = get_provider('enterprise')
    assert type(provider).__name__ == 'EnterpriseInfoProvider'
    assert provider.source_type == 'enterprise'


@pytest.mark.asyncio
async def test_enterprise_provider_skips_when_unconfigured(monkeypatch) -> None:
    """未配 base/key → provider 诚实产空（零 fake·不打网络）。"""
    monkeypatch.setattr(settings, 'ENTERPRISE_INFO_API_BASE', '')
    monkeypatch.setattr(settings, 'ENTERPRISE_INFO_API_KEY', '')
    provider = get_provider('enterprise')
    req = CrawlRequest(job_id=1, keyword='示例科技', source_type='enterprise')
    firecrawl_client: Any = None
    items = [item async for item in provider.crawl_stream(req, firecrawl_client=firecrawl_client)]
    assert items == []
