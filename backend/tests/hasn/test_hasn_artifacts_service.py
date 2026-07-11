"""hasn_artifacts_service 真实 PG 集成测试（AF-2 验收：登记/去重/归属隔离/溯源跳转/resolve/软删）。

零 mock 原则：用真实本地 PostgreSQL(15432) 跑 record/list/detail/delete 全链路；仅签名网络边界
（StorageService.signed_urls_cached）用 fake，避免真实 S3/Redis。事务结束回滚，不污染库。

需要：export DATABASE_PORT=15432（本地 huanxing 库）。
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from backend.app.hasn.model import HasnAgents, HasnConversations
from backend.app.hasn.schema.hasn_artifacts import RecordArtifactParam
from backend.app.hasn.service import hasn_asset_service as asset_mod
from backend.app.hasn.service.hasn_artifacts_service import HasnArtifactsService, hasn_artifacts_service
from backend.app.hasn.service.hasn_asset_service import HasnAssetService
from backend.common.exception import errors
from backend.database.db import async_db_session
from backend.plugin.s3.service.storage_service import ObjectRef

# 多个真实-DB async 测试共享同一事件循环（module 级），避免全局 async_engine 的连接池被
# 上一个测试的已关闭事件循环回收时触发 "Event loop is closed"。
pytestmark = pytest.mark.asyncio(loop_scope='module')


def _short_id(prefix: str) -> str:
    return f'{prefix}_{uuid4().hex[:20]}'  # ≤ varchar(40)


async def _fake_sign(_db, *, items, expires_in=3600):
    return {it: f'https://signed/{it[1]}?e={expires_in}' for it in items}


async def _make_agent(db, *, owner_hasn_id: str, agent_hasn_id: str) -> None:
    # star_id 唯一：用 agent_hasn_id 派生，避开本地库既有 star_id='' 行的唯一冲突。
    db.add(
        HasnAgents(
            hasn_id=agent_hasn_id,
            star_id=f'star_{agent_hasn_id[-16:]}',
            owner_id=owner_hasn_id,
            display_name='测试分身',
        )
    )
    await db.flush()


async def test_derive_source_link_variants() -> None:
    """纯函数：跳转主锚 D4——会话+消息精确到气泡；仅 session 降级；皆无则 None。"""
    f = HasnArtifactsService.derive_source_link
    assert f('11111111-1111-1111-1111-111111111111', 42, None) == (
        'hasn://messages/c/11111111-1111-1111-1111-111111111111#42'
    )
    assert f('22222222-2222-2222-2222-222222222222', None, None) == (
        'hasn://messages/c/22222222-2222-2222-2222-222222222222'
    )
    assert f(None, None, 'sess_abc') == 'hasn://tasks/sessions/sess_abc'
    assert f(None, None, None) is None


async def test_record_dedup_and_validation() -> None:
    owner = _short_id('hasnOwner')
    agent = _short_id('aAgent')

    async with async_db_session() as db:
        try:
            await _make_agent(db, owner_hasn_id=owner, agent_hasn_id=agent)
            asset_id = _short_id('ast')

            # 缺 asset_id 与 resource_uri → 拒绝
            with pytest.raises(errors.RequestError):
                await hasn_artifacts_service.record(
                    db, agent_hasn_id=agent, owner_hasn_id=owner, params=RecordArtifactParam(kind='image')
                )

            # 首次登记
            params = RecordArtifactParam(
                kind='image',
                title='星空海报',
                asset_id=asset_id,
                source_tool='hasn.image.generate',
                source_kind='tool_output',
                dispatch_id='disp_1',
            )
            aid1 = await hasn_artifacts_service.record(db, agent_hasn_id=agent, owner_hasn_id=owner, params=params)
            assert aid1.startswith('art_')

            # 同 (agent, dispatch_id, asset_id) 重试 → 去重返回同一 id（幂等）
            aid2 = await hasn_artifacts_service.record(db, agent_hasn_id=agent, owner_hasn_id=owner, params=params)
            assert aid2 == aid1

            # 非法 kind 归一为 other
            aid3 = await hasn_artifacts_service.record(
                db,
                agent_hasn_id=agent,
                owner_hasn_id=owner,
                params=RecordArtifactParam(kind='banana', resource_uri='hasn://deck/d_1'),
            )
            assert aid3 != aid1
        finally:
            await db.rollback()


async def test_body_artifact_origin_ref_and_video_kind() -> None:
    """P6：文本产物只带 body 直接入库（不上传文件）+ video kind 放行 + 按 origin_ref 反查。"""
    owner = _short_id('hasnOwner')
    agent = _short_id('aAgent')
    origin = f'resource:plan:todo:{uuid4().hex[:8]}'

    async with async_db_session() as db:
        try:
            await _make_agent(db, owner_hasn_id=owner, agent_hasn_id=agent)

            # 文本/markdown 产物：只带 body（无 asset_id/resource_uri）也能登记，正文直接入库
            aid_doc = await hasn_artifacts_service.record(
                db,
                agent_hasn_id=agent,
                owner_hasn_id=owner,
                params=RecordArtifactParam(
                    kind='document',
                    title='竞品调研报告',
                    body='# 竞品调研\n\n## 市场概览\n...结论可执行',
                    origin_ref=origin,
                    source_kind='task_result',
                    source_tool='hasn.artifact.record',
                ),
            )
            assert aid_doc.startswith('art_')

            # video kind 放行（非归一为 other），同一 origin_ref
            aid_video = await hasn_artifacts_service.record(
                db,
                agent_hasn_id=agent,
                owner_hasn_id=owner,
                params=RecordArtifactParam(
                    kind='video', title='成片', resource_uri='hasn://asset/ast_demo', origin_ref=origin
                ),
            )

            # 按 origin_ref 反查 → 2 条（document + video），document 带 body、kind 未被归一
            items, total = await hasn_artifacts_service.list_by_origin(
                db, owner_hasn_id=owner, origin_ref=origin
            )
            assert total == 2
            by_id = {it.artifact_id: it for it in items}
            assert by_id[aid_doc].body and by_id[aid_doc].body.startswith('# 竞品调研')
            assert by_id[aid_doc].origin_ref == origin
            assert by_id[aid_video].kind == 'video'  # 放行未归一 other

            # 不同 owner 反查同一 origin_ref → 隔离为空
            other_items, other_total = await hasn_artifacts_service.list_by_origin(
                db, owner_hasn_id=_short_id('hasnOther'), origin_ref=origin
            )
            assert other_total == 0 and other_items == []
        finally:
            await db.rollback()


async def test_list_by_session_filter_and_isolation() -> None:
    """RC-P4 工作会话页资源栏：按 session_id 反查本会话产物（应用资源 + 工具产出），
    时间倒序、只本 owner、只本会话；软删/异会话不返回。"""
    owner = _short_id('hasnOwner')
    agent = _short_id('aAgent')
    session = _short_id('sess')
    other_session = _short_id('sess')

    async with async_db_session() as db:
        try:
            await _make_agent(db, owner_hasn_id=owner, agent_hasn_id=agent)

            # 本会话产出两条：deck 应用资源（resource_uri）+ webpage 应用资源，同一 session_id。
            aid_deck = await hasn_artifacts_service.record(
                db,
                agent_hasn_id=agent,
                owner_hasn_id=owner,
                params=RecordArtifactParam(
                    kind='deck', title='季度汇报', resource_uri='hasn://deck/d_srv_1', session_id=session
                ),
            )
            aid_web = await hasn_artifacts_service.record(
                db,
                agent_hasn_id=agent,
                owner_hasn_id=owner,
                params=RecordArtifactParam(
                    kind='webpage', title='产品官网', resource_uri='hasn://webpage/w_srv_1', session_id=session
                ),
            )
            # 另一会话产出一条 → 反查本会话时不应出现。
            await hasn_artifacts_service.record(
                db,
                agent_hasn_id=agent,
                owner_hasn_id=owner,
                params=RecordArtifactParam(
                    kind='deck', title='别的会话', resource_uri='hasn://deck/d_srv_2', session_id=other_session
                ),
            )

            items, total = await hasn_artifacts_service.list_by_session(
                db, owner_hasn_id=owner, session_id=session
            )
            assert total == 2
            ids = {it.artifact_id for it in items}
            assert ids == {aid_deck, aid_web}

            # 不同 owner 反查同一 session_id → 隔离为空。
            other_items, other_total = await hasn_artifacts_service.list_by_session(
                db, owner_hasn_id=_short_id('hasnOther'), session_id=session
            )
            assert other_total == 0 and other_items == []

            # 软删一条后 → 只剩 1 条（status='active' 过滤生效）。
            await hasn_artifacts_service.soft_delete(db, owner_hasn_id=owner, artifact_id=aid_web)
            after_items, after_total = await hasn_artifacts_service.list_by_session(
                db, owner_hasn_id=owner, session_id=session
            )
            assert after_total == 1 and after_items[0].artifact_id == aid_deck
        finally:
            await db.rollback()


async def test_list_ownership_isolation_and_source_link(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        asset_mod.StorageService,
        'signed_urls_cached',
        classmethod(lambda cls, db, **kw: _fake_sign(db, **kw)),
        raising=True,
    )
    owner = _short_id('hasnOwner')
    stranger = _short_id('hasnStranger')
    agent = _short_id('aAgent')

    async with async_db_session() as db:
        try:
            await _make_agent(db, owner_hasn_id=owner, agent_hasn_id=agent)
            # owner 私有图片资产
            img = await HasnAssetService.register_asset(
                db,
                owner_hasn_id=owner,
                ref=ObjectRef(
                    storage_id=1, object_key='gen/poster.png', access='private', stable_url='', mime='image/png', size=123
                ),
                kind='image',
            )
            conv = HasnConversations(
                type='direct',
                participant_a_id=owner,
                participant_a_type='human',
                participant_b_id=agent,
                participant_b_type='agent',
            )
            db.add(conv)
            await db.flush()

            await hasn_artifacts_service.record(
                db,
                agent_hasn_id=agent,
                owner_hasn_id=owner,
                params=RecordArtifactParam(
                    kind='image',
                    title='海报',
                    asset_id=img.asset_id,
                    conversation_id=str(conv.id),
                    message_id=99,
                    source_tool='hasn.image.generate',
                    dispatch_id='d1',
                ),
            )

            # owner 列表：1 条，含派生 source_link + 解析出的 display_url
            items, total = await hasn_artifacts_service.list_by_agent(
                db, owner_hasn_id=owner, agent_hasn_id=agent
            )
            assert total == 1
            it = items[0]
            assert it.source_link == f'hasn://messages/c/{conv.id}#99'
            assert it.display_url and it.display_url.startswith('https://signed/')

            # 陌生人查同一分身 → 无权（归属隔离）
            with pytest.raises(errors.ForbiddenError):
                await hasn_artifacts_service.list_by_agent(
                    db, owner_hasn_id=stranger, agent_hasn_id=agent
                )
        finally:
            await db.rollback()


async def test_detail_and_soft_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        asset_mod.StorageService,
        'signed_urls_cached',
        classmethod(lambda cls, db, **kw: _fake_sign(db, **kw)),
        raising=True,
    )
    owner = _short_id('hasnOwner')
    agent = _short_id('aAgent')

    async with async_db_session() as db:
        try:
            await _make_agent(db, owner_hasn_id=owner, agent_hasn_id=agent)
            img = await HasnAssetService.register_asset(
                db,
                owner_hasn_id=owner,
                ref=ObjectRef(
                    storage_id=1, object_key='gen/a.png', access='private', stable_url='', mime='image/png', size=1
                ),
                kind='image',
            )
            aid = await hasn_artifacts_service.record(
                db,
                agent_hasn_id=agent,
                owner_hasn_id=owner,
                params=RecordArtifactParam(
                    kind='image', asset_id=img.asset_id, source_tool='hasn.image.generate',
                    dispatch_id='d1', metadata={'mime': 'image/png', 'width': 800},
                ),
            )

            # 详情：含 download_url + metadata
            detail = await hasn_artifacts_service.get_detail(db, owner_hasn_id=owner, artifact_id=aid)
            assert detail.download_url and detail.download_url.startswith('https://signed/')
            assert detail.metadata.get('width') == 800

            # 软删
            await hasn_artifacts_service.soft_delete(db, owner_hasn_id=owner, artifact_id=aid)
            # 删后列表为空、详情 NotFound
            _items, total = await hasn_artifacts_service.list_by_agent(
                db, owner_hasn_id=owner, agent_hasn_id=agent
            )
            assert total == 0
            with pytest.raises(errors.NotFoundError):
                await hasn_artifacts_service.get_detail(db, owner_hasn_id=owner, artifact_id=aid)
            # 再次软删不存在 → NotFound
            with pytest.raises(errors.NotFoundError):
                await hasn_artifacts_service.soft_delete(db, owner_hasn_id=owner, artifact_id=aid)
        finally:
            await db.rollback()
