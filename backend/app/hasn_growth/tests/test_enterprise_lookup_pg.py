"""企业数据读穿中台（GROWTH-QCC-4）真实 PG 验证（零 mock，事务末尾回滚）。

覆盖 `enterprise_lookup_service` 的读穿核心**不依赖 live qcc** 的全部业务逻辑：
- 纯函数：`_qcc_records`（structured/text 防御式抽记录）/`_is_fresh`（维度 TTL）/`_best_company_match`；
- 真实 PG（无网关调用）：`_ingest_record`（结构化→入公共池→全量保真 meta→建 owner 引用）、
  `lookup_company` 池命中即返回（**证明命中路径零 qcc 调用**——未注册 system server 时调网关必抛，
  本测试干净返回即证明没碰网关）、`enrich_company` 的 owner 归属闸 + 维度缓存命中。

miss→网关→qcc 取数那一跳由 GROWTH-QCC-3（真打 qcc `call_system_tool` 端到端，零 mock）覆盖，
受平台 Bearer + 出站可达门控（infra-gated），不在此重复。

需要 export DATABASE_PORT=15432。
"""

from __future__ import annotations

import json
import uuid

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_core import HasnHumans
from backend.app.hasn_growth.model.lead_contact import LeadContact
from backend.app.hasn_growth.model.lead_ref import LeadRef
from backend.app.hasn_growth.service.enterprise_lookup_service import (
    _best_company_match,
    _is_fresh,
    _qcc_records,
    enterprise_lookup_service,
)
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

# 企查查风格单条记录（防御式多键解析认得 Name/OperName/PhoneNumber…）。
_QCC_RECORD = {
    'Name': '北京示例科技有限公司',
    'OperName': '张三',
    'PhoneNumber': '010-88886666',
    'Email': 'contact@example-bj.com',
    'Industry': '软件和信息技术服务业',
    'Address': '北京市海淀区中关村大街1号',
    'Province': '北京市',
    'City': '北京市',
    'unified_social_credit_code': '91110108MA0EXAMPLE',
}


# ---------------- 纯函数（无 DB/无网关） ----------------


async def test_qcc_records_structured_preferred() -> None:  # noqa: RUF029
    """structured 优先：`{result:[record]}` → 抽出记录列表。"""
    out = _qcc_records({'is_error': False, 'structured': {'result': [_QCC_RECORD]}, 'text': None})
    assert out == [_QCC_RECORD]


async def test_qcc_records_text_json_fallback() -> None:  # noqa: RUF029
    """structured 缺 → text JSON 解析回落。"""
    out = _qcc_records({'is_error': False, 'structured': None, 'text': json.dumps({'data': [_QCC_RECORD]})})
    assert out == [_QCC_RECORD]


async def test_qcc_records_error_or_empty_returns_empty() -> None:  # noqa: RUF029
    """is_error / 空 / 不可解析 → 空列表（诚实，不 fake）。"""
    assert _qcc_records({'is_error': True, 'structured': {'result': [_QCC_RECORD]}}) == []
    assert _qcc_records({'is_error': False, 'structured': None, 'text': 'not json'}) == []
    assert _qcc_records({}) == []


async def test_is_fresh_within_and_beyond_ttl() -> None:  # noqa: RUF029
    """维度缓存 TTL：内 → True；过期/解析失败/naive → False（保守重取）。"""
    fresh = timezone.now().isoformat()
    expired = (timezone.now() - timedelta(hours=48)).isoformat()
    assert _is_fresh(fresh, ttl_hours=24) is True
    assert _is_fresh(expired, ttl_hours=24) is False
    assert _is_fresh(None, ttl_hours=24) is False
    assert _is_fresh('2026-01-01T00:00:00', ttl_hours=24) is False  # naive → False


async def test_best_company_match_prefers_name_contains() -> None:  # noqa: RUF029
    """池命中里优先公司名包含 query 的；都不含 → None。"""
    a = LeadContact(company_name='北京示例科技有限公司', confidence_score=Decimal(80))
    b = LeadContact(company_name='上海无关贸易有限公司', confidence_score=Decimal(90))
    assert _best_company_match([a, b], '示例') is a
    assert _best_company_match([b], '示例') is None


# ---------------- 真实 PG（零 mock，无网关） ----------------


@pytest_asyncio.fixture
async def ctx() -> AsyncIterator[SimpleNamespace]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    tag = uuid.uuid4().hex[:8]
    owner = f'h_qcc_{tag}'
    owner_uid = 91_700_000_000 + int(uuid.uuid4().int % 900_000_000)
    agent_hasn = f'a_qcc_{tag}'
    other_uid = owner_uid + 1
    session.add(
        HasnHumans(
            hasn_id=owner,
            star_id=f's_{owner_uid}',
            user_id=owner_uid,
            nickname=f'主人-{tag}',
            status='active',
        )
    )
    await session.flush()
    try:
        yield SimpleNamespace(
            session=session,
            owner=owner,
            owner_uid=owner_uid,
            other_uid=other_uid,
            agent_hasn=agent_hasn,
            tag=tag,
        )
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


async def test_ingest_record_creates_contact_with_qcc_meta(ctx: SimpleNamespace) -> None:
    """单条 qcc 记录 → 入公共池 + 全量保真 meta_data['qcc'] + 为 owner 建引用。"""
    s = ctx.session
    contact = await enterprise_lookup_service._ingest_record(
        s, record=dict(_QCC_RECORD), user_id=ctx.owner_uid, keyword='企查查测试'
    )
    assert contact is not None
    assert contact.company_name == '北京示例科技有限公司'
    assert contact.pool_visibility == 'public'
    # 全量保真：原始记录整体落 meta_data['qcc']['registration'] + fetched_at。
    assert contact.meta_data['qcc']['registration'] == _QCC_RECORD
    assert contact.meta_data['qcc']['fetched_at']
    # 统一池众包语义：owner 引用已建（拥有=引用）。
    ref = (
        await s.execute(
            select(LeadRef.id).where(LeadRef.user_id == ctx.owner_uid, LeadRef.lead_contact_id == contact.id)
        )
    ).scalar_one_or_none()
    assert ref is not None


async def test_lookup_company_pool_hit_skips_gateway(ctx: SimpleNamespace) -> None:
    """池命中即返回 from_pool=True 且不碰网关（未注册 system server 时调网关必抛，干净返回即证明）。"""
    s = ctx.session
    seeded = await enterprise_lookup_service._ingest_record(
        s, record=dict(_QCC_RECORD), user_id=ctx.owner_uid, keyword='企查查测试'
    )
    assert seeded is not None

    result = await enterprise_lookup_service.lookup_company(
        s,
        user_id=ctx.owner_uid,
        owner_hasn_id=ctx.owner,
        agent_hasn_id=ctx.agent_hasn,
        query='北京示例科技有限公司',
    )
    assert result['from_pool'] is True
    assert result['lead']['lead_contact_id'] == seeded.id


async def test_lookup_company_empty_query_raises(ctx: SimpleNamespace) -> None:
    """空 query → ValueError（边界校验，不调网关）。"""
    with pytest.raises(ValueError, match='query 不能为空'):
        await enterprise_lookup_service.lookup_company(
            ctx.session,
            user_id=ctx.owner_uid,
            owner_hasn_id=ctx.owner,
            agent_hasn_id=None,
            query='   ',
        )


async def test_enrich_company_requires_ownership(ctx: SimpleNamespace) -> None:
    """非线索拥有者富化 → ValueError（防越权刷别 owner 配额）。"""
    s = ctx.session
    contact = await enterprise_lookup_service._ingest_record(
        s, record=dict(_QCC_RECORD), user_id=ctx.owner_uid, keyword='企查查测试'
    )
    assert contact is not None
    with pytest.raises(ValueError, match='无权富化'):
        await enterprise_lookup_service.enrich_company(
            s,
            lead_contact_id=contact.id,
            user_id=ctx.other_uid,  # 无 ref
            owner_hasn_id=ctx.owner,
            agent_hasn_id=ctx.agent_hasn,
            dimensions=['risk'],
        )


async def test_enrich_company_cache_hit_returns_cached(ctx: SimpleNamespace) -> None:
    """维度缓存 TTL 内命中即返回 cached=True，且不碰网关。"""
    s = ctx.session
    contact = await enterprise_lookup_service._ingest_record(
        s, record=dict(_QCC_RECORD), user_id=ctx.owner_uid, keyword='企查查测试'
    )
    assert contact is not None
    # 预置一条新鲜的 risk 维度缓存（JSONB 重赋值触发更新）。
    meta = dict(contact.meta_data or {})
    meta['enrichment'] = {
        'risk': {'data': {'k': 'v'}, 'summary': '无重大风险', 'source': 'qcc', 'fetched_at': timezone.now().isoformat()}
    }
    contact.meta_data = meta
    await s.flush()

    result = await enterprise_lookup_service.enrich_company(
        s,
        lead_contact_id=contact.id,
        user_id=ctx.owner_uid,
        owner_hasn_id=ctx.owner,
        agent_hasn_id=ctx.agent_hasn,
        dimensions=['risk'],
    )
    assert result['dimensions']['risk']['cached'] is True
    assert result['dimensions']['risk']['summary'] == '无重大风险'
