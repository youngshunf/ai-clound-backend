"""创作运营云端 MCP 工具 handler M4 真实 PG 验收（零 mock，事务末尾回滚）。

直接走 `creator_tool_handlers` 的 handler 层——即云端 gateway_internal 的真实执行路径
（去掉 HTTP/MCP-key 外壳）：handler 从 Agent JWT claims 取身份 → resolve_creator_scope →
直调 creator service。证明设计 §6.1 的 28 工具（含 S4 账号/竞品扩充）经 manifest→注册表→handler 全链可调，且
身份恒取自 JWT（不读入参身份）、个人模式 scope 隔离生效。

需要本地 PostgreSQL :15432（DATABASE_PORT）。
"""

from __future__ import annotations

import datetime

from uuid import uuid4

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_core.app_platform import ai_native_runtime_gateway as gateway
from backend.app.hasn_creator.manifest import CREATOR_AI_NATIVE_MANIFEST
from backend.app.hasn_creator.service import creator_tool_handlers as H
from backend.app.hasn_creator.service import project_linkage as _creator_project_linkage  # noqa: F401
from backend.app.hasn_project.model.hasn_project import HasnProject
from backend.app.hasn_project.service.project_linkage_registry import project_linkage_registry
from backend.app.mcp.context import clear_current_project_id, set_current_project_id
from backend.common.dataclasses import AgentTokenPayload
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


def _agent(
    uid: int = 921001, hasn: str = 'hasn:agent:creator-x', owner: str = 'hasn:owner:creator-a'
) -> AgentTokenPayload:
    return AgentTokenPayload(
        agent_hasn_id=hasn,
        agent_name='创作分身',
        owner_hasn_id=owner,
        owner_user_id=uid,
        session_uuid='sess-creator-test',
        expire_time=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1),
    )


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    sess = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield sess
    finally:
        await sess.rollback()
        await sess.close()
        await engine.dispose()


async def test_manifest_handlers_all_registered() -> None:
    """每个 manifest tool.handler 必在 gateway 内部 handler 注册表中（28 工具全可调）。"""
    reg = gateway._internal_handlers()
    tools = CREATOR_AI_NATIVE_MANIFEST['tools']
    # manifest 已扩到 38 工具（S4 账号/竞品 + 后续平台/素材/草稿等扩充），全部须在注册表可调。
    assert len(tools) == 38
    missing = [t['handler'] for t in tools if t['handler'] not in reg]
    assert missing == [], f'未注册 handler: {missing}'
    # 每个 handler 是 async callable
    for t in tools:
        assert callable(reg[t['handler']])


async def test_creator_project_inherits_current_platform_project_and_registers_container(session) -> None:
    """项目会话创建的创作运营容器继承平台项目，且适配器进入统一注册表。"""
    agent = _agent(owner=f'h_creator_context_{uuid4().hex[:16]}')
    platform_project = HasnProject(owner_id=agent.owner_hasn_id, name='内容增长战役')
    session.add(platform_project)
    await session.flush()

    set_current_project_id(str(platform_project.id))
    try:
        created = await H.handle_project_create(session, agent, {'name': '增长内容号'})
    finally:
        clear_current_project_id()

    assert created['platform_project_id'] == str(platform_project.id)
    filtered = await H.handle_project_list(
        session,
        agent,
        {'platform_project_id': str(platform_project.id)},
    )
    assert [item['id'] for item in filtered['items']] == [created['id']]
    adapter = project_linkage_registry.get('creator/projects')
    assert adapter is not None
    assert adapter.attach_column == 'platform_project_id'
    assert adapter.is_container is True


async def test_full_pipeline_via_handlers(session) -> None:
    """全链路经 handler 层（个人模式）：建项目→画像→账号→选题→内容→阶段→提交发布→批准→数据→洞察→总览。"""
    agent = _agent()
    # 1) 建项目（handler 落 personal 归属，assignee=主人 hasn_id）
    proj = await H.handle_project_create(session, agent, {'name': '美食号', 'primary_platform': 'xiaohongshu'})
    pid = proj['id']
    assert proj['owner_scope'] == 'personal'
    assert proj['assignee'] == agent.owner_hasn_id

    # 项目列表（含 scope meta）
    listing = await H.handle_project_list(session, agent, {})
    assert any(p['id'] == pid for p in listing['items'])
    assert listing['scope']['owner_scope'] == 'personal'

    # 2) 画像
    prof = await H.handle_profile_set(
        session, agent, {'project_id': pid, 'fields': {'niche': '美食', 'content_pillars': ['食谱', '探店']}}
    )
    assert prof['niche'] == '美食'
    got_prof = await H.handle_profile_get(session, agent, {'project_id': pid})
    assert got_prof['content_pillars'] == ['食谱', '探店']

    # 3) 账号 + 竞品 + analyze
    acc = await H.handle_account_add(
        session,
        agent,
        {
            'project_id': pid,
            'platform': 'xiaohongshu',
            'fields': {
                'nickname': '小厨',
                'is_primary': True,
                'home_url': 'https://www.xiaohongshu.com/user/profile/h1',
            },
        },
    )
    aid = acc['id']
    await H.handle_competitor_log(
        session,
        agent,
        {
            'project_id': pid,
            'name': '隔壁老王',
            'fields': {
                'platform': 'xiaohongshu',
                'url': 'https://www.xiaohongshu.com/user/profile/w1',
                'follower_count': 50000,
            },
        },
    )
    analyzed = await H.handle_profile_analyze(session, agent, {'project_id': pid})
    assert len(analyzed['competitors']) == 1
    accts = await H.handle_account_list(session, agent, {'project_id': pid})
    assert len(accts['items']) == 1

    # 4) 选题 → 采纳建内容
    topics = await H.handle_topic_suggest(
        session, agent, {'project_id': pid, 'topics': [{'title': '3步红烧肉', 'potential_score': 88}]}
    )
    tid = topics['items'][0]['id']
    content = await H.handle_content_create(
        session, agent, {'project_id': pid, 'title': '3步红烧肉', 'content_tracks': 'article,video', 'topic_id': tid}
    )
    cid = content['id']
    assert content['status'] == 'idea'
    assert content['created_by_agent_id'] == agent.agent_hasn_id  # 身份取自 JWT

    # 5) 阶段产出 + 状态机推进
    await H.handle_content_stage_save(
        session, agent, {'content_id': cid, 'stage': 'outline', 'content_text': '钩子→步骤→收尾'}
    )
    await H.handle_content_stage_save(
        session, agent, {'content_id': cid, 'stage': 'final_draft', 'content_text': '正文'}
    )
    await H.handle_content_update(session, agent, {'content_id': cid, 'status': 'drafting'})
    await H.handle_content_update(session, agent, {'content_id': cid, 'status': 'reviewing'})

    # 6) 提交发布 → pending_review（不绕审核）
    sub = await H.handle_publish_submit(
        session, agent, {'content_id': cid, 'account_id': aid, 'publish_note': '晚8点发'}
    )
    assert sub['status'] == 'pending_review'
    pub_id = sub['publish_id']

    # 7) 主人审内容通过 → 批准发布 → 标记已发布（mark_published 在 service 层；此处经 update_metrics 验数据回填）
    await H.handle_content_update(session, agent, {'content_id': cid, 'status': 'ready', 'review_status': 'approved'})
    # 批准 + 标记已发布走 service（owner 审核动作），这里直接用 service 完成发布闭环以便回填数据
    from backend.app.hasn_creator.service.creator_service import creator_service
    from backend.app.hasn_creator.service.scope_context import CreatorScope

    scope = CreatorScope(user_id=agent.owner_user_id, owner_hasn_id=agent.owner_hasn_id)
    await creator_service.approve_publish(
        session, user_id=agent.owner_user_id, scope=scope, publish_id=pub_id, approval_user_id=agent.owner_user_id
    )
    await creator_service.mark_published(
        session, user_id=agent.owner_user_id, scope=scope, publish_id=pub_id, publish_url='https://xhs/abc'
    )

    # 8) 回填数据（经 handler）→ 内容转 analyzing
    await H.handle_publish_update_metrics(
        session, agent, {'publish_id': pub_id, 'metrics': {'views': 12000, 'likes': 800, 'new_followers': 45}}
    )
    cdet = await H.handle_content_get(session, agent, {'content_id': cid})
    assert cdet['status'] == 'analyzing'
    assert len(cdet['stages']) == 2
    assert len(cdet['publishes']) == 1

    # 发布记录列表
    pubs = await H.handle_publish_list(session, agent, {'project_id': pid})
    assert any(p['id'] == pub_id for p in pubs['items'])

    # 9) 沉淀洞察（M4 仅落库；回写在 M5）
    insight = await H.handle_insight_log(
        session,
        agent,
        {
            'project_id': pid,
            'insight_type': 'pillar_performance',
            'summary': '食谱类互动最高',
            'proposed_action': {'pillar_weight_delta': {'食谱': 0.2}},
        },
    )
    assert insight['insight_type'] == 'pillar_performance'
    assert insight['created_by_agent_id'] == agent.agent_hasn_id
    assert insight['evidence_json'].get('proposed_action')  # M5 待消费

    # 10) 总览
    ov = await H.handle_report_overview(session, agent, {'project_id': pid})
    assert ov['published_count'] == 1
    assert ov['metrics']['views'] == 12000


async def test_stage_save_backfills_created_by_agent_id(session) -> None:
    """主人建内容壳（无创作分身）→ 分身写阶段 → 回填 created_by_agent_id；已有作者不被覆盖。

    还原真实场景：主人经 owner 路径建内容壳（不带 agent 身份，created_by_agent_id=NULL），
    分身随后经工具面写阶段正文，此时应把创作分身 hasn_id 回填到内容上（诚实标注作者分身）。
    """
    from backend.app.hasn_creator.service.creator_service import creator_service
    from backend.app.hasn_creator.service.scope_context import CreatorScope

    agent = _agent()
    proj = await H.handle_project_create(session, agent, {'name': '回填号', 'primary_platform': 'xiaohongshu'})
    pid = proj['id']
    scope = CreatorScope(user_id=agent.owner_user_id, owner_hasn_id=agent.owner_hasn_id)

    # 主人路径建内容壳：不传 created_by_agent_id → 应为 NULL
    shell = await creator_service.create_content(
        session, user_id=agent.owner_user_id, scope=scope, project_id=pid, title='主人建的壳'
    )
    cid = shell['id']
    assert shell['created_by_agent_id'] is None

    # 分身写阶段 → 回填创作分身身份
    await H.handle_content_stage_save(
        session, agent, {'content_id': cid, 'stage': 'final_draft', 'content_text': '分身写的正文'}
    )
    got = await H.handle_content_get(session, agent, {'content_id': cid})
    assert got['created_by_agent_id'] == agent.agent_hasn_id

    # 同主人另一个分身再写阶段 → 不覆盖已有作者（回填只在 NULL 时发生）
    other = _agent(hasn='hasn:agent:creator-y')
    await H.handle_content_stage_save(
        session, other, {'content_id': cid, 'stage': 'final_draft', 'content_text': '别的分身改'}
    )
    got2 = await H.handle_content_get(session, agent, {'content_id': cid})
    assert got2['created_by_agent_id'] == agent.agent_hasn_id  # 仍是首个作者分身


async def test_cross_owner_isolation_via_handlers(session) -> None:
    """两个主人的分身互不可见（个人模式 user_id 隔离，handler 经 JWT 身份）。"""
    agent_a = _agent(uid=921001, hasn='hasn:agent:a', owner='hasn:owner:a')
    agent_b = _agent(uid=921002, hasn='hasn:agent:b', owner='hasn:owner:b')
    proj = await H.handle_project_create(session, agent_a, {'name': 'A的号'})
    pid = proj['id']
    # B 列表看不到 A 的
    b_list = await H.handle_project_list(session, agent_b, {})
    assert all(p['id'] != pid for p in b_list['items'])
    # B 取 A 的项目 → 不存在
    from backend.common.exception import errors

    with pytest.raises(errors.NotFoundError):
        await H.handle_project_get(session, agent_b, {'project_id': pid})
