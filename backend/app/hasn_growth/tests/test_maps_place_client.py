"""地图 Place API 客户端纯函数验收（doc93 §3.2 maps 源·零 mock·无网络）。

覆盖高德/百度参数构造 + 响应解析（喂真实形状的 Place API 响应字典）+ POI→structured_payload
映射（键对齐 cleaner_service 的提取产物契约）+ provider 未配 key 时诚实空。真实 HTTP 抓取
（infra-gated·真实 key）不在此测——doc93 §测试策略。
"""

from __future__ import annotations

import pytest

from backend.app.hasn_growth.service.maps_place_client import (
    build_amap_params,
    build_baidu_params,
    parse_amap_pois,
    parse_baidu_pois,
    poi_to_structured,
)

# ── 真实形状样本（节选自高德 v3 place/text、百度 place/v2/search 官方响应）──

_AMAP_OK = {
    'status': '1',
    'info': 'OK',
    'count': '2',
    'pois': [
        {
            'name': '杭州示例科技有限公司',
            'tel': '0571-88888888;13900000000',
            'pname': '浙江省',
            'cityname': '杭州市',
            'address': '西湖区文三路 100 号',
            'type': '公司企业;科技公司;信息技术',
            'location': '120.12,30.28',
        },
        {  # 空字段返回 [] 而非空串（高德真实行为），name 仍在 → 保留
            'name': '示例咨询工作室',
            'tel': [],
            'pname': '浙江省',
            'cityname': '杭州市',
            'address': [],
            'type': '公司企业',
            'location': '120.13,30.29',
        },
        {'name': '', 'tel': '123'},  # 无名 POI → 丢弃
    ],
}

_BAIDU_OK = {
    'status': 0,
    'message': 'ok',
    'results': [
        {
            'name': '北京示例商贸有限公司',
            'telephone': '010-66666666',
            'province': '北京市',
            'city': '北京市',
            'address': '朝阳区建国路 88 号',
            'detail_info': {'tag': '公司企业;贸易'},
        },
        {'name': '', 'telephone': '010-1'},  # 无名 → 丢弃
    ],
}


def test_build_amap_params_with_city() -> None:
    p = build_amap_params(keyword='科技公司', city='杭州', limit=30, key='AK')
    assert p['key'] == 'AK' and p['keywords'] == '科技公司'
    assert p['city'] == '杭州' and p['citylimit'] == 'true'
    assert p['offset'] == '25'  # 30 被压到单页上限 25
    assert p['extensions'] == 'all'


def test_build_amap_params_no_city_unbounds_citylimit() -> None:
    p = build_amap_params(keyword='餐厅', city='', limit=0, key='AK')
    assert p['citylimit'] == 'false'
    assert p['offset'] == '1'  # limit 0 被抬到下限 1


def test_parse_amap_pois_real_shape() -> None:
    pois = parse_amap_pois(_AMAP_OK)
    assert len(pois) == 2  # 无名 POI 被丢
    first = pois[0]
    assert first['name'] == '杭州示例科技有限公司'
    assert first['tel'] == '0571-88888888;13900000000'
    assert first['province'] == '浙江省' and first['city'] == '杭州市'
    assert first['industry'] == '公司企业'  # type 取首段
    assert first['source'] == 'amap'
    # 空字段 [] 归一为空串，不抛
    assert pois[1]['tel'] == '' and pois[1]['address'] == ''


def test_parse_amap_pois_failed_status_empty() -> None:
    assert parse_amap_pois({'status': '0', 'info': 'INVALID_PARAMS'}) == []
    assert parse_amap_pois({}) == []
    assert parse_amap_pois(None) == []


def test_build_baidu_params() -> None:
    p = build_baidu_params(keyword='商贸公司', city='北京', limit=50, ak='BK')
    assert p['query'] == '商贸公司' and p['ak'] == 'BK'
    assert p['region'] == '北京' and p['city_limit'] == 'true'
    assert p['scope'] == '2'  # 取详情含电话
    assert p['page_size'] == '20'  # 50 被压到百度单页上限 20


def test_parse_baidu_pois_real_shape() -> None:
    pois = parse_baidu_pois(_BAIDU_OK)
    assert len(pois) == 1
    first = pois[0]
    assert first['name'] == '北京示例商贸有限公司'
    assert first['tel'] == '010-66666666'
    assert first['industry'] == '公司企业'  # detail_info.tag 取首段
    assert first['source'] == 'baidu'


def test_parse_baidu_pois_failed_status_empty() -> None:
    assert parse_baidu_pois({'status': 2, 'message': 'fail'}) == []
    assert parse_baidu_pois({}) == []
    assert parse_baidu_pois(None) == []


def test_poi_to_structured_keys_align_cleaner_contract() -> None:
    """structured_payload 键必须对齐 cleaner_service 认得的提取产物（公司名/电话/行政区/地址/行业）。"""
    poi = parse_amap_pois(_AMAP_OK)[0]
    s = poi_to_structured(poi)
    assert s['company_name'] == '杭州示例科技有限公司'
    # 多号电话拆 phones 列表（; 分隔）
    assert s['phones'] == ['0571-88888888', '13900000000']
    assert s['region'] == '浙江省' and s['city'] == '杭州市'
    assert s['address'] == '西湖区文三路 100 号'
    assert s['industry'] == '公司企业'


def test_poi_to_structured_chinese_comma_split() -> None:
    """中文逗号 / 半角逗号分隔的多号也正确拆分（容错真实数据脏格式）。"""
    s = poi_to_structured({'name': 'X', 'tel': '0571-1，0571-2,0571-3'})
    assert s['phones'] == ['0571-1', '0571-2', '0571-3']


def test_poi_to_structured_empty_tel_yields_empty_phones() -> None:
    s = poi_to_structured({'name': 'X', 'tel': ''})
    assert s['phones'] == [] and s['company_name'] == 'X'


@pytest.mark.asyncio
async def test_maps_provider_skips_when_unconfigured(monkeypatch) -> None:
    """未配 AMAP/BAIDU key → MapsProvider 诚实产出空（零 fake·不打网络）。"""
    from backend.app.hasn_growth.service.provider_registry import CrawlRequest, get_provider
    from backend.core.conf import settings

    monkeypatch.setattr(settings, 'AMAP_API_KEY', '')
    monkeypatch.setattr(settings, 'BAIDU_MAP_AK', '')
    provider = get_provider('maps')
    req = CrawlRequest(job_id=1, keyword='科技公司', source_type='maps', lead_scope='public', config={'city': '杭州'})

    items = [item async for item in provider.crawl_stream(req, firecrawl_client=None)]
    assert items == []
