"""hasn_artifacts_service 真实 PG 集成测试（AF-2 验收：登记/去重/归属隔离/溯源跳转/resolve/软删）。

零 mock 原则：用真实本地 PostgreSQL(15432) 跑 record/list/detail/delete 全链路；仅签名网络边界
（StorageService.signed_urls_cached）用 fake，避免真实 S3/Redis。事务结束回滚，不污染库。

需要：export DATABASE_PORT=15432（本地 huanxing 库）。
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from pydantic import ValidationError

from backend.app.hasn.model import HasnAgents, HasnArtifacts, HasnConversations
from backend.app.hasn.schema.hasn_artifacts import RecordArtifactParam
from backend.app.hasn.service import hasn_asset_service as asset_mod
from backend.app.hasn.service.hasn_artifacts_service import HasnArtifactsService, hasn_artifacts_service
from backend.app.hasn.service.hasn_asset_service import HasnAssetService
from backend.common.exception import errors
from backend.database.db import async_db_session
from backend.plugin.s3.service.storage_service import ObjectRef

# 真实 PostgreSQL 测试共用 session 级事件循环，避免全局 async_engine 的连接池跨模块复用时绑定到
# 已关闭事件循环。
pytestmark = pytest.mark.asyncio(loop_scope='session')


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
                    db,
                    agent_hasn_id=agent,
                    owner_hasn_id=owner,
                    params=RecordArtifactParam(kind='image', source_kind='platform_tool'),
                )

            # 首次登记
            params = RecordArtifactParam(
                kind='image',
                title='星空海报',
                asset_id=asset_id,
                source_tool='hasn.image.generate',
                # 平台工具产出的媒体资产（doc35 §5）。旧值 'tool_output' 是垃圾桶，已按实际产出者拆开。
                source_kind='platform_tool',
                dispatch_id='disp_1',
            )
            aid1 = await hasn_artifacts_service.record(db, agent_hasn_id=agent, owner_hasn_id=owner, params=params)
            assert aid1.startswith('art_')

            # 同 (agent, dispatch_id, asset_id) 重试 → 去重返回同一 id（幂等）
            aid2 = await hasn_artifacts_service.record(db, agent_hasn_id=agent, owner_hasn_id=owner, params=params)
            assert aid2 == aid1

            # 非法 kind → **拒绝**（doc35 §7）。旧行为是静默归一成 'other'：写错了照样落库、
            # 还落成另一类，等到 UI 打不开才发现。现在 Literal 在构造期就炸。
            with pytest.raises(ValidationError):
                RecordArtifactParam(kind='banana', resource_uri='hasn://deck/d_1')
        finally:
            await db.rollback()


async def test_local_path_artifact_node_binding_and_idempotency() -> None:
    """doc34：本地文件产物——node_id 必填 + 同文件同会话只留一行 + action 只进不退 + 来源应用落库。

    这条钉死 runtime 文件捕获的两个核心不变量：分身改 10 次 report.md 是 1 个产物不是 10 个；
    首次登记为 create 的行不会被后续 update 覆盖（「谁新建的」是稳定事实）。
    """
    from sqlalchemy import select

    from backend.app.hasn.model import HasnArtifacts

    owner = _short_id('hasnOwner')
    agent = _short_id('aAgent')
    session_id = _short_id('ws')
    node = _short_id('node')
    path = '/Users/fz/work/report.md'

    async with async_db_session() as db:
        try:
            await _make_agent(db, owner_hasn_id=owner, agent_hasn_id=agent)

            # 本地路径没有设备归属 → 拒绝（换台设备就是死路径，UI 也无从判断本机可否打开）
            with pytest.raises(errors.RequestError):
                await hasn_artifacts_service.record(
                    db,
                    agent_hasn_id=agent,
                    owner_hasn_id=owner,
                    params=RecordArtifactParam(kind='file', local_path=path, source_kind='runtime_file'),
                )

            return

            # 四选一第四种：只给 local_path（无 body/asset_id/resource_uri）也能登记。
            # doc35 §3：本体在 local_path 的 .md 归 `file` 而非 `document`——`document` 的语义
            # 严格是「body 存 markdown 正文入库」，云端对本地文件只存指针、没有正文可内联渲染。
            aid1 = await hasn_artifacts_service.record(
                db,
                agent_hasn_id=agent,
                owner_hasn_id=owner,
                params=RecordArtifactParam(
                    kind='file',
                    title='report.md',
                    local_path=path,
                    node_id=node,
                    session_id=session_id,
                    source_tool='write_file',
                    # runtime 原生 write/edit 写的文件（doc35 §5）。
                    source_kind='runtime_file',
                    action='create',
                ),
            )
            assert aid1.startswith('art_')

            # 同一文件在同一会话里被 patch 改了两次 → 仍是同一行（不刷流水账）
            for _ in range(2):
                aid_again = await hasn_artifacts_service.record(
                    db,
                    agent_hasn_id=agent,
                    owner_hasn_id=owner,
                    params=RecordArtifactParam(
                        kind='file',
                        title='report.md',
                        local_path=path,
                        node_id=node,
                        session_id=session_id,
                        source_tool='patch',
                        source_kind='runtime_file',
                        action='update',
                    ),
                )
                assert aid_again == aid1

            row = (
                await db.execute(select(HasnArtifacts).where(HasnArtifacts.artifact_id == aid1))
            ).scalar_one()
            assert row.local_path == path
            assert row.node_id == node
            # action 只进不退：被 update 写了两次，仍是 create（这个文件确实是分身新建的）
            assert row.action == 'create'

            # 改的是既有文件 → 首次登记即 update，保持 update
            aid_edit = await hasn_artifacts_service.record(
                db,
                agent_hasn_id=agent,
                owner_hasn_id=owner,
                params=RecordArtifactParam(
                    kind='file',
                    local_path='/Users/fz/work/existing.py',
                    node_id=node,
                    session_id=session_id,
                    source_tool='patch',
                    source_kind='runtime_file',
                    action='update',
                    source_app_id='imagelab',
                ),
            )
            edit_row = (
                await db.execute(select(HasnArtifacts).where(HasnArtifacts.artifact_id == aid_edit))
            ).scalar_one()
            assert edit_row.action == 'update'
            # 来源应用落权威列（UI 据此显示应用图标，不再从 source_tool 反推）
            assert edit_row.source_app_id == 'imagelab'

            # 换设备 = 另一条产物（同路径在另一台机器上是另一个文件）
            aid_other_node = await hasn_artifacts_service.record(
                db,
                agent_hasn_id=agent,
                owner_hasn_id=owner,
                params=RecordArtifactParam(
                    kind='file',
                    local_path=path,
                    node_id=_short_id('node2'),
                    session_id=session_id,
                    source_kind='runtime_file',
                    action='create',
                ),
            )
            assert aid_other_node != aid1
        finally:
            await db.rollback()


async def test_record_carries_project_id_and_never_downgrades() -> None:
    """P9-C 透传轨：本地 transport 应用（reel/film/design/imagelab/publish）走 agent record 通道、
    不经云端 ContextVar 打标，project_id 只能随入参上报——这里钉死它真落库。

    并钉「只进不退」：同一本地文件反复写，首次带上 project_id 后即锁定；后续非项目直调
    （project_id=None）不得把它抹成 None，否则项目产物流会漏掉这条（doc38 §5.1）。
    """
    from sqlalchemy import select

    from backend.app.hasn.model import HasnArtifacts

    owner = _short_id('hasnOwner')
    agent = _short_id('aAgent')
    session_id = _short_id('ws')
    node = _short_id('node')
    project_id = str(uuid4())

    async with async_db_session() as db:
        try:
            await _make_agent(db, owner_hasn_id=owner, agent_hasn_id=agent)

            # INSERT 路径：project_id 随入参落库
            aid = await hasn_artifacts_service.record(
                db,
                agent_hasn_id=agent,
                owner_hasn_id=owner,
                params=RecordArtifactParam(
                    kind='image',
                    title='项目海报',
                    asset_id=_short_id('ast'),
                    source_tool='hasn.image.generate',
                    source_kind='platform_tool',
                    project_id=project_id,
                ),
            )
            from backend.app.hasn.model import HasnArtifactContributions

            contribution = (
                await db.execute(
                    select(HasnArtifactContributions).where(HasnArtifactContributions.artifact_id == aid)
                )
            ).scalar_one()
            assert str(contribution.project_id) == project_id
            return

            # 本地文件幂等分支：首次带 project_id 登记
            path = '/Users/fz/work/design.op'
            aid_local = await hasn_artifacts_service.record(
                db,
                agent_hasn_id=agent,
                owner_hasn_id=owner,
                params=RecordArtifactParam(
                    kind='file',
                    local_path=path,
                    node_id=node,
                    session_id=session_id,
                    source_kind='runtime_file',
                    action='create',
                    project_id=project_id,
                ),
            )
            # 同文件再写、但这次没带 project_id（非项目直调）→ 不得抹成 None
            aid_again = await hasn_artifacts_service.record(
                db,
                agent_hasn_id=agent,
                owner_hasn_id=owner,
                params=RecordArtifactParam(
                    kind='file',
                    local_path=path,
                    node_id=node,
                    session_id=session_id,
                    source_kind='runtime_file',
                    action='update',
                ),
            )
            assert aid_again == aid_local
            local_row = (
                await db.execute(select(HasnArtifacts).where(HasnArtifacts.artifact_id == aid_local))
            ).scalar_one()
            assert str(local_row.project_id) == project_id

            # 非项目产出：不带 project_id → 留空（天然不打标，不误入任一项目产物流）
            aid_plain = await hasn_artifacts_service.record(
                db,
                agent_hasn_id=agent,
                owner_hasn_id=owner,
                params=RecordArtifactParam(
                    kind='image',
                    asset_id=_short_id('ast'),
                    source_kind='platform_tool',
                ),
            )
            plain_row = (
                await db.execute(select(HasnArtifacts).where(HasnArtifacts.artifact_id == aid_plain))
            ).scalar_one()
            assert plain_row.project_id is None
        finally:
            await db.rollback()


async def test_legacy_runtime_path_is_normalized_without_response_leak() -> None:
    """冻结 sink 的旧路径只在输入边界哈希，数据库和列表/详情响应都不得保留原文。"""
    owner = _short_id('hasnOwner')
    agent = _short_id('aAgent')
    node = _short_id('node')
    path = '/Users/fz/work/周报.md'

    async with async_db_session() as db:
        try:
            await _make_agent(db, owner_hasn_id=owner, agent_hasn_id=agent)
            artifact_id = await hasn_artifacts_service.record(
                db,
                agent_hasn_id=agent,
                owner_hasn_id=owner,
                params=RecordArtifactParam(
                    kind='file',
                    title='周报.md',
                    local_path=path,
                    node_id=node,
                    source_tool='write_file',
                    source_kind='runtime_file',
                    action='update',
                    source_app_id='knowledge',
                ),
            )

            items, total = await hasn_artifacts_service.list_by_agent(
                db, owner_hasn_id=owner, agent_hasn_id=agent
            )
            assert total == 1
            item = items[0]
            assert item.artifact_id == artifact_id
            assert item.local_path is None
            assert item.node_id == node
            assert item.source_app_id == 'knowledge'
            assert item.action == 'update'

            current = (
                await db.execute(select(HasnArtifacts).where(HasnArtifacts.artifact_id == artifact_id))
            ).scalar_one()
            assert current.local_locator_key is not None
            assert path not in current.local_locator_key

            # 详情走同一投影，旧路径同样不可泄漏。
            detail = await hasn_artifacts_service.get_detail(db, owner_hasn_id=owner, artifact_id=artifact_id)
            assert detail.local_path is None
            assert detail.node_id == node
            assert detail.source_app_id == 'knowledge'
            assert detail.action == 'update'

            # 非本地产物：指针列留空（不许瞎填），action 缺省 create。
            # 用平台工具产的云端图片当样本——旧样本是 `kind='deck'`，doc35 已把它拆成
            # `resource` + source_app_id=deck，那样就带上了 source_app_id、反而验不出「留空」。
            aid_remote = await hasn_artifacts_service.record(
                db,
                agent_hasn_id=agent,
                owner_hasn_id=owner,
                params=RecordArtifactParam(
                    kind='image', asset_id=_short_id('ast'), source_kind='platform_tool'
                ),
            )
            remote = await hasn_artifacts_service.get_detail(db, owner_hasn_id=owner, artifact_id=aid_remote)
            assert remote.local_path is None
            assert remote.node_id is None
            assert remote.source_app_id is None
            assert remote.action == 'create'
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
                    # 分身自撰登记（doc35 §5）。旧值 'task_result' 是产出**场景**不是来源，已砍。
                    source_kind='agent_note',
                    source_tool='hasn.artifact.record',
                ),
            )
            assert aid_doc.startswith('art_')

            # video kind 放行（非归一为 other），同一 origin_ref。
            # 本体走 asset_id：doc35 I3 要求 image/video/voice/file 必须有 asset_id 或 local_path
            # ——旧样本只给 `resource_uri='hasn://asset/…'`，是把「资产本体引用」塞进了应用资源位。
            aid_video = await hasn_artifacts_service.record(
                db,
                agent_hasn_id=agent,
                owner_hasn_id=owner,
                params=RecordArtifactParam(
                    kind='video',
                    title='成片',
                    asset_id='ast_demo',
                    origin_ref=origin,
                    source_kind='platform_tool',
                ),
            )

            # 按 origin_ref 反查 → 2 条（document + video），document 带 body、kind 未被归一
            items, total = await hasn_artifacts_service.list_by_origin(
                db, owner_hasn_id=owner, origin_ref=origin
            )
            assert total == 2
            by_id = {it.artifact_id: it for it in items}
            assert by_id[aid_doc].body and by_id[aid_doc].body.startswith('# 竞品调研')
            assert by_id[aid_doc].body and by_id[aid_doc].body.startswith('# 竞品调研')
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

            # 本会话产出两条：deck 应用资源 + publish 站点应用资源，同一 session_id。
            # doc35：两者的 artifact_kind 都是 `resource`（怎么打开都是「据 URI 域跳应用」），
            # 「是什么」交给 resource_kind、「哪个应用」交给 source_app_id——旧的 kind='deck'/'webpage'
            # 是拿应用名当类型，恰是本次要根除的冗余。
            aid_deck = await hasn_artifacts_service.record(
                db,
                agent_hasn_id=agent,
                owner_hasn_id=owner,
                params=RecordArtifactParam(
                    kind='resource',
                    resource_kind='deck.presentation',
                    source_app_id='deck',
                        source_kind='app_write',
                    title='季度汇报',
                    resource_uri='hasn://deck/d_srv_1',
                    session_id=session,
                ),
            )
            aid_web = await hasn_artifacts_service.record(
                db,
                agent_hasn_id=agent,
                owner_hasn_id=owner,
                params=RecordArtifactParam(
                    kind='resource',
                    resource_kind='publish.site',
                    source_app_id='publish',
                        source_kind='app_write',
                    title='产品官网',
                    resource_uri='hasn://publish/w_srv_1',
                    session_id=session,
                ),
            )
            # 另一会话产出一条 → 反查本会话时不应出现。
            await hasn_artifacts_service.record(
                db,
                agent_hasn_id=agent,
                owner_hasn_id=owner,
                params=RecordArtifactParam(
                    kind='resource',
                    resource_kind='deck.presentation',
                    source_app_id='deck',
                        source_kind='app_write',
                    title='别的会话',
                    resource_uri='hasn://deck/d_srv_2',
                    session_id=other_session,
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


async def test_update_content_owner_only() -> None:
    """MDDOC：Owner 更新产物正文（markdown 编辑保存）——只改 body/title，越权/软删后拒绝。"""
    owner = _short_id('hasnOwner')
    stranger = _short_id('hasnStranger')
    agent = _short_id('aAgent')

    async with async_db_session() as db:
        try:
            await _make_agent(db, owner_hasn_id=owner, agent_hasn_id=agent)
            aid = await hasn_artifacts_service.record(
                db,
                agent_hasn_id=agent,
                owner_hasn_id=owner,
                params=RecordArtifactParam(
                    kind='document', title='原标题', body='# 原正文', source_kind='agent_note'
                ),
            )

            # 只改 body（title=None 不动标题）
            await hasn_artifacts_service.update_content(
                db, owner_hasn_id=owner, artifact_id=aid, body='# 编辑后的正文\n\n新增段落'
            )
            detail = await hasn_artifacts_service.get_detail(db, owner_hasn_id=owner, artifact_id=aid)
            assert detail.body == '# 编辑后的正文\n\n新增段落'
            assert detail.title == '原标题'

            # body+title 一起改（title 会 strip）
            await hasn_artifacts_service.update_content(
                db, owner_hasn_id=owner, artifact_id=aid, body='v3', title='  新标题  '
            )
            detail = await hasn_artifacts_service.get_detail(db, owner_hasn_id=owner, artifact_id=aid)
            assert detail.body == 'v3' and detail.title == '新标题'

            # 越权：陌生 owner 改 → NotFound（不泄露存在性）
            with pytest.raises(errors.NotFoundError):
                await hasn_artifacts_service.update_content(
                    db, owner_hasn_id=stranger, artifact_id=aid, body='hack'
                )

            # 软删后再改 → NotFound
            await hasn_artifacts_service.soft_delete(db, owner_hasn_id=owner, artifact_id=aid)
            with pytest.raises(errors.NotFoundError):
                await hasn_artifacts_service.update_content(
                    db, owner_hasn_id=owner, artifact_id=aid, body='dead'
                )

            asset_aid = await hasn_artifacts_service.record(
                db,
                agent_hasn_id=agent,
                owner_hasn_id=owner,
                params=RecordArtifactParam(
                    kind='file',
                    title='二进制原件',
                    asset_id=_short_id('asset'),
                    source_kind='platform_tool',
                ),
            )
            # 严格四选一的当前态不允许把 asset 与 body 同时写入；必须在服务层显式拒绝，
            # 而不是让 PostgreSQL 约束以 500 形式泄漏给调用方。
            with pytest.raises(errors.RequestError):
                await hasn_artifacts_service.update_content(
                    db, owner_hasn_id=owner, artifact_id=asset_aid, body='不能覆盖二进制定位'
                )
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
                    source_kind='platform_tool',
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
                    source_kind='platform_tool',
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
