"""平台工具 · 分身资源检索（artifact.list/search/get + asset.get）真实测试（禁 mock）。

设计：docs/Agent产物系统/01-分身资源检索与素材站工具设计.md 切片 A-P0-1 / A-P0-2 / A-P1。

契约（无需 DB）：三个 artifact 读工具 + asset.get 的名/命名空间/execution_location/scope；
input_schema 必填项 + 出参裁剪常量防漂移。

真实 PG 往返（需活体库 DATABASE_PORT=15432）：seed 一个主人 + 一个本人分身 + 若干产物，
经工具真调 service 落库读回，断言：
- list 出参**剥离 body 全文**（以 has_body 替代）、summary 截断、给 asset_uri；
- list 按 kind / session_id 过滤；
- search 关键词切词 **AND** 语义 + ILIKE 通配符转义（`%` 不全量匹配）；
- get 返回 body（>20000 截断 + body_truncated）+ asset_uri + 溯源；owner 隔离（同主人跨分身可读、他主人 NotFound）；
- asset.get 返回技术元数据 + owner 校验（无权/不存在统一「资产不存在」）+ transcript 截断。

无 DB 时跳过真实往返（不伪造）。
"""

from __future__ import annotations

import uuid

import pytest

from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.artifact import (
    ARTIFACT_TOOLS,
    _BODY_MAX,
    _LIST_SIZE_DEFAULT,
    _LIST_SIZE_MAX,
    _SUMMARY_MAX,
    ArtifactGetTool,
    ArtifactListTool,
    ArtifactSearchTool,
)
from backend.app.mcp.tools.asset import ASSET_TOOLS, AssetGetTool, _normalize_asset_id


def _agent_ctx(owner_hasn_id: str, agent_hasn_id: str) -> AgentContext:
    return AgentContext(
        hasn_id=agent_hasn_id,
        owner_id=1,
        agent_status='active',
        metadata={},
        owner_hasn_id=owner_hasn_id,
        session_uuid='amk_artifact_retrieval_test',
    )


async def _db_reachable() -> bool:
    try:
        from sqlalchemy import text

        from backend.database.db import async_db_session

        async with async_db_session() as db:
            await db.execute(text('SELECT 1'))
    except Exception:
        return False
    else:
        return True


# ── 契约（无需 DB）────────────────────────────────────────────────────────────
def test_artifact_tools_register_full_set() -> None:
    """artifact 域含 record + list + search + get 四工具；asset 域含 get 一工具。"""
    assert [t.name for t in ARTIFACT_TOOLS] == [
        'hasn.artifact.record',
        'hasn.artifact.list',
        'hasn.artifact.search',
        'hasn.artifact.get',
    ]
    assert [t.name for t in ASSET_TOOLS] == ['hasn.asset.get']


def test_read_tools_are_cloud_platform_no_scope() -> None:
    """读工具 source=platform、execution_location=cloud、命名空间正确、不声明 scope（出厂 Allow）。"""
    for tool in (ArtifactListTool(), ArtifactSearchTool(), ArtifactGetTool()):
        assert tool.source == 'platform'
        assert tool.execution_location == 'cloud'
        assert tool.namespace == 'hasn.artifact'
        assert tool.required_scopes == []
    asset = AssetGetTool()
    assert asset.source == 'platform'
    assert asset.execution_location == 'cloud'
    assert asset.namespace == 'hasn.asset'
    assert asset.required_scopes == []


def test_read_tools_required_fields() -> None:
    """search 必填 query；get 必填 artifact_id；asset.get 必填 asset_id；list 无必填。"""
    assert 'required' not in ArtifactListTool().input_schema
    assert ArtifactSearchTool().input_schema['required'] == ['query']
    assert ArtifactGetTool().input_schema['required'] == ['artifact_id']
    assert AssetGetTool().input_schema['required'] == ['asset_id']


def test_list_schema_filters() -> None:
    """list 支持 kind（enum）/session_id/page/size 过滤，kind enum 与产物类型一致。"""
    props = ArtifactListTool().input_schema['properties']
    assert set(props) >= {'kind', 'session_id', 'page', 'size'}
    assert props['kind']['enum'] == [
        'image',
        'voice',
        'video',
        'file',
        'document',
        'deck',
        'webpage',
        'dataset',
        'other',
    ]


def test_projection_constants_are_trim_bounds() -> None:
    """出参裁剪常量固定（agent 面收紧，防被改宽）。"""
    assert _LIST_SIZE_DEFAULT == 10
    assert _LIST_SIZE_MAX == 30
    assert _SUMMARY_MAX == 200
    assert _BODY_MAX == 20000


def test_preview_url_warning_in_descriptions() -> None:
    """三层防呆之一：list/search/get 描述都含 preview_url 禁入正文的警示。"""
    for tool in (ArtifactListTool(), ArtifactSearchTool(), ArtifactGetTool()):
        assert 'preview_url' in tool.description
        assert 'hasn://asset' in tool.description


def test_asset_id_normalizer() -> None:
    """asset_id 归一：裸 id 原样、hasn://asset/ 前缀剥离、两侧空白裁剪。"""
    assert _normalize_asset_id('ast_abc') == 'ast_abc'
    assert _normalize_asset_id('hasn://asset/ast_abc') == 'ast_abc'
    assert _normalize_asset_id('  hasn://asset/ast_abc  ') == 'ast_abc'


@pytest.mark.asyncio(loop_scope='module')
async def test_search_rejects_missing_query() -> None:
    """search 缺 query → RuntimeError（校验在打 DB 前）。"""
    with pytest.raises(RuntimeError, match='query'):
        await ArtifactSearchTool().execute(_agent_ctx('h_x', 'a_x'), {})


@pytest.mark.asyncio(loop_scope='module')
async def test_get_rejects_missing_artifact_id() -> None:
    """get 缺 artifact_id → RuntimeError（校验在打 DB 前）。"""
    with pytest.raises(RuntimeError, match='artifact_id'):
        await ArtifactGetTool().execute(_agent_ctx('h_x', 'a_x'), {})


@pytest.mark.asyncio(loop_scope='module')
async def test_asset_get_rejects_missing_id() -> None:
    """asset.get 缺 asset_id → RuntimeError（校验在打 DB 前）。"""
    with pytest.raises(RuntimeError, match='asset_id'):
        await AssetGetTool().execute(_agent_ctx('h_x', 'a_x'), {})


# ── 真实 PG 往返 ────────────────────────────────────────────────────────────────
async def _seed_owner_and_agent(owner: str, agent: str) -> None:
    """seed 主人 + 本人分身（list_by_agent 的 _owns_agent 归属校验需要）。"""
    from backend.app.hasn.model import HasnAgents, HasnHumans
    from backend.database.db import async_db_session

    tag = uuid.uuid4().hex[:8]
    uid = 970000 + int(uuid.uuid4().int % 9000)
    async with async_db_session.begin() as db:
        db.add_all([
            HasnHumans(hasn_id=owner, star_id=f's_{uid}', user_id=uid, nickname=f'主人{tag}', status='active'),
            HasnAgents(
                hasn_id=agent,
                star_id=f'sa_{tag}',
                owner_id=owner,
                display_name=f'我的分身{tag}',
                agent_name=f'mine{tag}',
                status='active',
            ),
        ])


async def _record(
    owner: str,
    agent: str,
    *,
    kind: str,
    title: str | None = None,
    summary: str | None = None,
    body: str | None = None,
    asset_id: str | None = None,
    session_id: str | None = None,
    source_kind: str = 'tool_output',
) -> str:
    """经 service 真登记一条产物，返回 artifact_id。"""
    from backend.app.hasn.schema.hasn_artifacts import RecordArtifactParam
    from backend.app.hasn.service.hasn_artifacts_service import hasn_artifacts_service
    from backend.database.db import async_db_session

    params = RecordArtifactParam(
        kind=kind,
        title=title,
        summary=summary,
        body=body,
        asset_id=asset_id,
        session_id=session_id,
        source_kind=source_kind,
        source_tool='hasn.image.generate' if kind == 'image' else 'hasn.artifact.record',
    )
    async with async_db_session.begin() as db:
        return await hasn_artifacts_service.record(db, agent_hasn_id=agent, owner_hasn_id=owner, params=params)


async def _cleanup(owner: str) -> None:
    from sqlalchemy import delete

    from backend.app.hasn.model import HasnAgents, HasnArtifacts, HasnHumans
    from backend.app.hasn.model.hasn_assets import HasnAssets
    from backend.database.db import async_db_session

    async with async_db_session.begin() as db:
        await db.execute(delete(HasnArtifacts).where(HasnArtifacts.owner_hasn_id == owner))
        await db.execute(delete(HasnAssets).where(HasnAssets.owner_hasn_id == owner))
        await db.execute(delete(HasnAgents).where(HasnAgents.owner_id == owner))
        await db.execute(delete(HasnHumans).where(HasnHumans.hasn_id == owner))


@pytest.mark.asyncio(loop_scope='module')
async def test_list_strips_body_and_projects_asset_uri_real_db() -> None:
    """真实 PG：list 剥离 body 全文（has_body 替代）、summary 截断、给 asset_uri。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')

    tag = uuid.uuid4().hex[:12]
    owner, agent = f'h_own_{tag}', f'a_mine_{tag}'
    try:
        await _seed_owner_and_agent(owner, agent)
        # 文本 document（长正文 + 长 summary）——检验 body 剥离 + summary 截断。
        long_summary = '要点' * 200  # 400 字符 > 200
        await _record(
            owner,
            agent,
            kind='document',
            title='竞品调研',
            summary=long_summary,
            body='# 竞品\n' + '正文很长' * 5000,
        )
        # image 产物（asset_id 指向占位资产，无需真存储）——检验 asset_uri 投影。
        await _record(owner, agent, kind='image', title='星空城市', asset_id='ast_placeholder_xyz')

        res = await ArtifactListTool().execute(_agent_ctx(owner, agent), {})
        assert res['total'] == 2
        assert res['page'] == 1 and res['size'] == _LIST_SIZE_DEFAULT
        items = {it['title']: it for it in res['items']}

        doc = items['竞品调研']
        assert 'body' not in doc  # 列表不回 body 全文
        assert doc['has_body'] is True
        assert doc['asset_uri'] is None
        assert len(doc['summary']) == _SUMMARY_MAX  # summary 截断到 200

        img = items['星空城市']
        assert img['has_body'] is False
        assert img['asset_uri'] == 'hasn://asset/ast_placeholder_xyz'  # 正文嵌图用它
        assert img['source_tool'] == 'hasn.image.generate'
        assert img['created_time']  # ISO 字符串
    finally:
        await _cleanup(owner)


@pytest.mark.asyncio(loop_scope='module')
async def test_list_filters_by_kind_and_session_real_db() -> None:
    """真实 PG：list 按 kind / session_id 过滤。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')

    tag = uuid.uuid4().hex[:12]
    owner, agent = f'h_own_{tag}', f'a_mine_{tag}'
    sess = f'sess_{tag}'
    try:
        await _seed_owner_and_agent(owner, agent)
        await _record(owner, agent, kind='image', title='图A', asset_id='ast_a', session_id=sess)
        await _record(owner, agent, kind='document', title='文B', body='b', session_id=sess)
        await _record(owner, agent, kind='image', title='图C', asset_id='ast_c')  # 别的会话

        by_kind = await ArtifactListTool().execute(_agent_ctx(owner, agent), {'kind': 'image'})
        assert {it['title'] for it in by_kind['items']} == {'图A', '图C'}

        by_session = await ArtifactListTool().execute(_agent_ctx(owner, agent), {'session_id': sess})
        assert {it['title'] for it in by_session['items']} == {'图A', '文B'}
    finally:
        await _cleanup(owner)


@pytest.mark.asyncio(loop_scope='module')
async def test_search_keyword_and_semantics_and_escape_real_db() -> None:
    """真实 PG：search 切词 AND 语义 + ILIKE 通配符转义。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')

    tag = uuid.uuid4().hex[:12]
    owner, agent = f'h_own_{tag}', f'a_mine_{tag}'
    try:
        await _seed_owner_and_agent(owner, agent)
        await _record(owner, agent, kind='image', title='星空下的城市夜景', asset_id='ast_1')
        await _record(owner, agent, kind='image', title='海边的日落', summary='城市海岸线', asset_id='ast_2')

        # 单词命中 title。
        r1 = await ArtifactSearchTool().execute(_agent_ctx(owner, agent), {'query': '星空'})
        assert {it['title'] for it in r1['items']} == {'星空下的城市夜景'}

        # 多词 AND：两词都要命中同一条（「星空」「城市」都在第一条）。
        r2 = await ArtifactSearchTool().execute(_agent_ctx(owner, agent), {'query': '星空 城市'})
        assert {it['title'] for it in r2['items']} == {'星空下的城市夜景'}

        # AND 语义：「星空」在第一条、「日落」在第二条，无一条同时含两词 → 空。
        r3 = await ArtifactSearchTool().execute(_agent_ctx(owner, agent), {'query': '星空 日落'})
        assert r3['total'] == 0

        # summary 也参与匹配（「城市」在第二条的 summary）。
        r4 = await ArtifactSearchTool().execute(_agent_ctx(owner, agent), {'query': '城市'})
        assert {it['title'] for it in r4['items']} == {'星空下的城市夜景', '海边的日落'}

        # ILIKE 通配符转义：`%` 被转义为字面量 → 不匹配任何 title（没有含字面 % 的）。
        r5 = await ArtifactSearchTool().execute(_agent_ctx(owner, agent), {'query': '%'})
        assert r5['total'] == 0
    finally:
        await _cleanup(owner)


@pytest.mark.asyncio(loop_scope='module')
async def test_get_body_truncation_and_owner_isolation_real_db() -> None:
    """真实 PG：get 返回 body（>20000 截断 + body_truncated）；owner 隔离（同主人跨分身可读、他主人 NotFound）。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')

    from backend.common.exception.errors import NotFoundError

    tag = uuid.uuid4().hex[:12]
    owner, agent_a, agent_b = f'h_own_{tag}', f'a_mine_a_{tag}', f'a_mine_b_{tag}'
    other_owner, other_agent = f'h_other_{tag}', f'a_other_{tag}'
    try:
        await _seed_owner_and_agent(owner, agent_a)
        # 同主人第二个分身（get 按 owner 隔离，不限本分身）。
        from backend.app.hasn.model import HasnAgents
        from backend.database.db import async_db_session

        async with async_db_session.begin() as db:
            db.add(
                HasnAgents(
                    hasn_id=agent_b,
                    star_id=f'sb_{tag}',
                    owner_id=owner,
                    display_name='分身B',
                    agent_name=f'mineb{tag}',
                    status='active',
                )
            )
        await _seed_owner_and_agent(other_owner, other_agent)

        # 分身 B 产的超长文本产物。
        huge = '超长正文段落。' * 4000  # 远超 20000 字符
        aid = await _record(owner, agent_b, kind='document', title='长报告', body=huge)

        # 分身 A 用同主人身份 get 分身 B 的产物 → 可读（跨分身复用）。
        detail = await ArtifactGetTool().execute(_agent_ctx(owner, agent_a), {'artifact_id': aid})
        assert detail['artifact_id'] == aid
        assert detail['kind'] == 'document'
        assert detail['body_truncated'] is True
        assert len(detail['body']) == _BODY_MAX  # 截断到 20000
        assert detail['asset_uri'] is None
        assert detail['title'] == '长报告'

        # 他主人的分身 get 本产物 → NotFound（owner 隔离）。
        with pytest.raises(NotFoundError):
            await ArtifactGetTool().execute(_agent_ctx(other_owner, other_agent), {'artifact_id': aid})
    finally:
        await _cleanup(owner)
        await _cleanup(other_owner)


@pytest.mark.asyncio(loop_scope='module')
async def test_asset_get_metadata_owner_check_and_transcript_trim_real_db() -> None:
    """真实 PG：asset.get 返回技术元数据 + owner 校验（无权/不存在统一「资产不存在」）+ transcript 截断。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')

    from backend.app.hasn.model.hasn_assets import HasnAssets
    from backend.database.db import async_db_session

    tag = uuid.uuid4().hex[:12]
    owner, agent = f'h_own_{tag}', f'a_mine_{tag}'
    other_owner, other_agent = f'h_other_{tag}', f'a_other_{tag}'
    asset_id = f'ast_{tag}'
    long_transcript = '语音转写文本。' * 500  # > 2000 字符
    try:
        await _seed_owner_and_agent(owner, agent)
        await _seed_owner_and_agent(other_owner, other_agent)
        async with async_db_session.begin() as db:
            db.add(
                HasnAssets(
                    asset_id=asset_id,
                    owner_hasn_id=owner,
                    access='private',
                    kind='voice',
                    mime='audio/mpeg',
                    size_bytes=123456,
                    duration_ms=42000,
                    transcript=long_transcript,
                    extract_status='done',
                )
            )

        # 本主人取：返回技术元数据，transcript 截断到 2000。
        got = await AssetGetTool().execute(_agent_ctx(owner, agent), {'asset_id': f'hasn://asset/{asset_id}'})
        assert got['asset_id'] == asset_id
        assert got['kind'] == 'voice'
        assert got['mime'] == 'audio/mpeg'
        assert got['size_bytes'] == 123456
        assert got['duration_ms'] == 42000
        assert len(got['transcript']) == 2000

        # 他主人取本资产 → 「资产不存在」（不区分无权/不存在）。
        with pytest.raises(RuntimeError, match='资产不存在'):
            await AssetGetTool().execute(_agent_ctx(other_owner, other_agent), {'asset_id': asset_id})

        # 不存在的资产 → 同样「资产不存在」。
        with pytest.raises(RuntimeError, match='资产不存在'):
            await AssetGetTool().execute(_agent_ctx(owner, agent), {'asset_id': 'ast_no_such_asset'})
    finally:
        from sqlalchemy import delete

        async with async_db_session.begin() as db:
            await db.execute(delete(HasnAssets).where(HasnAssets.asset_id == asset_id))
        await _cleanup(owner)
        await _cleanup(other_owner)
