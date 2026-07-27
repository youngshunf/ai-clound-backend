"""FIN-P3b — 金融投研 7 类资源下行读契约 真实 PG 验证（05 §3.2.1 / §3.1.5）。

零 mock：真实本地 PostgreSQL(15432)，用真实上行路径（`finance_sync_service.sync_product`）造行，
再直调 `finance_read_service` 断言下行读契约：

- 规则 7（内部幂等元数据不下发）：list/get 响应永不含 `local_ref` / `last_client_op_id`。
- owner 隔离：别人的行一律 404（不是 403——不让越权探测者据状态码区分「不存在」与「不是你的」）。
- tombstone 可下行：默认不返回 deleted，`include_deleted=True` 才带上（daemon 据它删本地镜像）。
- 列表剔重字段：正文/源码/曲线只在详情给。
- 过滤白名单：声明的键生效，未声明的键静默忽略。
- 隐私红线（§3.1.5）：7 张表永不出现 `source_file_ref` / `source_content_hash` 列。

需要：本地 PG huanxing@15432 且 hasn_finance 7 表已建（export DATABASE_PORT=15432）。
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn_finance.model.research_report import ResearchReport
from backend.app.hasn_finance.service.finance_read_service import (
    PRODUCT_MODELS,
    _INTERNAL_FIELDS,
    finance_read_service,
)
from backend.app.hasn_finance.service.finance_sync_service import finance_sync_service
from backend.common.exception import errors
from backend.database.db import async_db_session

pytestmark = pytest.mark.asyncio(loop_scope='session')

# 05 §3.1.5 隐私红线：这两个字段是**本地绝对路径 / 当前原件 hash**，永远不该有云端列。
# 一旦有人加了同名列，此守卫立刻红——比事后在响应里剔除可靠得多（剔除会漏，缺列漏不了）。
_FORBIDDEN_COLUMNS = ('source_file_ref', 'source_content_hash')


# ============================ 纯 Python（无 DB）============================


def test_no_finance_table_has_local_path_or_source_hash_columns() -> None:
    """隐私红线守卫：7 张 finance 表都不含 source_file_ref / source_content_hash 列。"""
    for kind, model_cls in PRODUCT_MODELS.items():
        cols = {c.name for c in model_cls.__table__.columns}
        for forbidden in _FORBIDDEN_COLUMNS:
            assert forbidden not in cols, f'{kind} 出现隐私红线列 {forbidden}（05 §3.1.5：本地绝对路径永不上云）'


def test_internal_fields_cover_every_model_that_has_them() -> None:
    """规则 7 守卫：凡是有 local_ref/last_client_op_id 列的表，这两列都在拒绝清单里。"""
    for kind, model_cls in PRODUCT_MODELS.items():
        cols = {c.name for c in model_cls.__table__.columns}
        for internal in cols & {'local_ref', 'last_client_op_id'}:
            assert internal in _INTERNAL_FIELDS, f'{kind}.{internal} 未进 _INTERNAL_FIELDS'


async def test_unknown_resource_kind_raises_not_found() -> None:
    """认不出的 resource_kind 直接抛，不回落到「默认类」（回落=返回另一类资源，比报错更难查）。"""
    async with async_db_session() as db:
        with pytest.raises(errors.NotFoundError):
            await finance_read_service.list_resources(db, resource_kind='finance.nope', owner_id='h_x')
        with pytest.raises(errors.NotFoundError):
            await finance_read_service.get_resource(db, resource_kind='finance.nope', owner_id='h_x', pk=1)


# ============================ 真实 PostgreSQL ============================


async def _seed_owner(db) -> str:  # noqa: ANN001
    uid = 750_000_000 + (uuid4().int % 100_000_000)
    hid = f'h_{uuid4().hex[:16]}'
    db.add(HasnHumans(hasn_id=hid, user_id=uid, star_id=str(uid), nickname=hid, status='active'))
    await db.flush()
    return hid


async def _seed_report(db, *, owner: str, symbol: str = '600519', title: str = '贵州茅台投研') -> str:  # noqa: ANN001
    """经真实上行路径造一条投研报告，返回云端权威 id。"""
    res = await finance_sync_service.sync_product(
        db,
        model_cls=ResearchReport,
        resource_kind='finance.research_report',
        owner_id=owner,
        op='create',
        op_id=f'op-{uuid4().hex[:8]}',
        base_revision=None,
        local_ref=f'lr-{uuid4().hex[:8]}',
        server_id=None,
        fields={
            'findings_json': [{'point': '毛利率提升'}],
            'data_as_of': date(2026, 7, 17),
            'usage_json': {'tokens': 0},
            'title': title,
            'symbol': symbol,
            'body_md': '# 正文\n很长的一段研究正文……',
        },
        agent_hasn_id=f'a_{uuid4().hex[:16]}',
        title=title,
    )
    return res['id']


async def test_list_and_get_never_expose_internal_idempotency_metadata() -> None:
    """规则 7：list/get 都不含 local_ref / last_client_op_id（服务端内部幂等元数据）。"""
    async with async_db_session() as db:
        try:
            owner = await _seed_owner(db)
            sid = await _seed_report(db, owner=owner)

            listed = await finance_read_service.list_resources(
                db, resource_kind='finance.research_report', owner_id=owner
            )
            assert len(listed['items']) == 1
            for field in ('local_ref', 'last_client_op_id'):
                assert field not in listed['items'][0], f'list 泄漏内部字段 {field}'

            detail = await finance_read_service.get_resource(
                db, resource_kind='finance.research_report', owner_id=owner, pk=int(sid)
            )
            for field in ('local_ref', 'last_client_op_id'):
                assert field not in detail, f'get 泄漏内部字段 {field}'
            # 下行该给的照给：server id + revision + 业务字段
            assert detail['id'] == int(sid)
            assert detail['revision'] == 1
            assert detail['symbol'] == '600519'
        finally:
            await db.rollback()


async def test_owner_isolation_other_owners_row_is_404() -> None:
    """owner 隔离：别人的行 get 不到（404），list 也看不见。"""
    async with async_db_session() as db:
        try:
            alice = await _seed_owner(db)
            bob = await _seed_owner(db)
            sid = await _seed_report(db, owner=alice)

            with pytest.raises(errors.NotFoundError):
                await finance_read_service.get_resource(
                    db, resource_kind='finance.research_report', owner_id=bob, pk=int(sid)
                )

            bobs = await finance_read_service.list_resources(db, resource_kind='finance.research_report', owner_id=bob)
            assert bobs['items'] == []
        finally:
            await db.rollback()


async def test_tombstone_hidden_by_default_and_visible_on_demand() -> None:
    """tombstone：默认不返回 deleted；include_deleted=True 才带上（daemon 据它删本地镜像）。"""
    async with async_db_session() as db:
        try:
            owner = await _seed_owner(db)
            sid = await _seed_report(db, owner=owner)
            await finance_sync_service.sync_product(
                db,
                model_cls=ResearchReport,
                resource_kind='finance.research_report',
                owner_id=owner,
                op='delete',
                op_id=f'op-{uuid4().hex[:8]}',
                base_revision=1,
                local_ref=None,
                server_id=sid,
                fields={},
            )

            default_list = await finance_read_service.list_resources(
                db, resource_kind='finance.research_report', owner_id=owner
            )
            assert default_list['items'] == [], '默认列表不该带 tombstone'

            with_deleted = await finance_read_service.list_resources(
                db, resource_kind='finance.research_report', owner_id=owner, include_deleted=True
            )
            assert len(with_deleted['items']) == 1
            assert with_deleted['items'][0]['status'] == 'deleted'

            # get 照常返回 tombstone：daemon 据 status 判定本地镜像该删
            detail = await finance_read_service.get_resource(
                db, resource_kind='finance.research_report', owner_id=owner, pk=int(sid)
            )
            assert detail['status'] == 'deleted'
        finally:
            await db.rollback()


async def test_list_omits_heavy_body_but_detail_returns_it() -> None:
    """列表剔重字段：body_md/findings_json 只在详情给（不是隐私裁剪，是别让列表拖到几 MB）。"""
    async with async_db_session() as db:
        try:
            owner = await _seed_owner(db)
            sid = await _seed_report(db, owner=owner)

            listed = await finance_read_service.list_resources(
                db, resource_kind='finance.research_report', owner_id=owner
            )
            item = listed['items'][0]
            assert 'body_md' not in item and 'findings_json' not in item
            assert item['title'] == '贵州茅台投研', '列表该给的摘要字段还得在'

            detail = await finance_read_service.get_resource(
                db, resource_kind='finance.research_report', owner_id=owner, pk=int(sid)
            )
            assert detail['body_md'].startswith('# 正文')
        finally:
            await db.rollback()


async def test_filters_honor_whitelist_and_ignore_unknown_keys() -> None:
    """过滤白名单：声明的键生效；未声明的键静默忽略（不让客户端拿任意列当过滤面）。"""
    async with async_db_session() as db:
        try:
            owner = await _seed_owner(db)
            await _seed_report(db, owner=owner, symbol='600519', title='茅台')
            await _seed_report(db, owner=owner, symbol='000001', title='平安')

            hit = await finance_read_service.list_resources(
                db, resource_kind='finance.research_report', owner_id=owner, filters={'symbol': '600519'}
            )
            assert [i['symbol'] for i in hit['items']] == ['600519']

            # body_md 不在白名单 → 该过滤条件被忽略，两条都返回（而不是 0 条或报错）
            ignored = await finance_read_service.list_resources(
                db, resource_kind='finance.research_report', owner_id=owner, filters={'body_md': '不存在的正文'}
            )
            assert len(ignored['items']) == 2
        finally:
            await db.rollback()


async def test_pagination_reports_has_more_without_count_query() -> None:
    """分页：多取一条探测 has_more，最后一页为 False。"""
    async with async_db_session() as db:
        try:
            owner = await _seed_owner(db)
            for i in range(3):
                await _seed_report(db, owner=owner, symbol=f'60000{i}', title=f'报告{i}')

            page1 = await finance_read_service.list_resources(
                db, resource_kind='finance.research_report', owner_id=owner, limit=2, offset=0
            )
            assert len(page1['items']) == 2 and page1['has_more'] is True

            page2 = await finance_read_service.list_resources(
                db, resource_kind='finance.research_report', owner_id=owner, limit=2, offset=2
            )
            assert len(page2['items']) == 1 and page2['has_more'] is False
        finally:
            await db.rollback()
