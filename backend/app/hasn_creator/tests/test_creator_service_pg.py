"""创作运营服务 M3 真实 PG 验收（零 mock，事务末尾回滚不污染库）。

覆盖：全链路（建项目+1:1画像→设画像→加账号→选题→采纳建内容→阶段产出→状态机→提交发布→
批准→标记已发布→回填数据→复盘总览）、内容/发布状态机守卫、跨户隔离、企业 scope 三态裁剪、爆款搜索。
需要本地 PostgreSQL :15432（DATABASE_PORT）。
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_creator.model.viral_pattern import ViralPattern
from backend.app.hasn_creator.service.creator_service import creator_service
from backend.app.hasn_creator.service.scope_context import CreatorScope
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio

_UID_A = 920001
_UID_B = 920002
_HASN_A = 'hasn:test:creator-a'


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


def _personal_scope(uid: int = _UID_A) -> CreatorScope:
    return CreatorScope(user_id=uid, owner_hasn_id=_HASN_A)


async def test_full_pipeline_personal(session) -> None:
    """定位→创作→审核→发布→数据→复盘 全链路（个人模式）。"""
    scope = _personal_scope()
    # 1) 建项目（同时建 1:1 空画像）
    proj = await creator_service.create_project(
        session, user_id=_UID_A, scope=scope, name='家常菜美食号', primary_platform='xiaohongshu'
    )
    pid = proj['id']
    assert proj['owner_scope'] == 'personal'
    assert proj['assignee'] == _HASN_A
    assert proj['status'] == 'active'

    # get_project 带画像 + 账号 + 计数
    detail = await creator_service.get_project(session, user_id=_UID_A, scope=scope, project_id=pid)
    assert detail['profile'] is not None
    assert detail['content_count'] == 0

    # 2) 设画像
    prof = await creator_service.set_profile(
        session,
        user_id=_UID_A,
        scope=scope,
        project_id=pid,
        fields={
            'niche': '美食',
            'sub_niche': '家常菜',
            'tone': '温暖治愈',
            'content_pillars': ['食谱教程', '厨房好物', '探店'],
        },
    )
    assert prof['niche'] == '美食'
    assert prof['content_pillars'] == ['食谱教程', '厨房好物', '探店']

    # 3) 加平台账号
    acc = await creator_service.add_account(
        session,
        user_id=_UID_A,
        scope=scope,
        project_id=pid,
        platform='xiaohongshu',
        fields={
            'nickname': '家常菜小厨',
            'is_primary': True,
            'home_url': 'https://www.xiaohongshu.com/user/profile/a1',
        },
    )
    aid = acc['id']
    assert acc['platform'] == 'xiaohongshu'

    # 4) 竞品 + analyze
    await creator_service.log_competitor(
        session,
        user_id=_UID_A,
        scope=scope,
        project_id=pid,
        name='隔壁老王做饭',
        fields={
            'platform': 'xiaohongshu',
            'url': 'https://www.xiaohongshu.com/user/profile/w1',
            'follower_count': 50000,
        },
    )
    analyzed = await creator_service.analyze_profile(session, user_id=_UID_A, scope=scope, project_id=pid)
    assert len(analyzed['competitors']) == 1

    # 5) 选题（分身生成）→ 采纳建内容
    topics = await creator_service.suggest_topics(
        session,
        user_id=_UID_A,
        scope=scope,
        project_id=pid,
        topics=[{'title': '3步搞定红烧肉', 'reason': '教程类高互动', 'potential_score': 88}],
    )
    tid = topics[0]['id']
    assert topics[0]['status'] == 0

    content = await creator_service.create_content(
        session,
        user_id=_UID_A,
        scope=scope,
        project_id=pid,
        title='3步搞定红烧肉',
        content_tracks='article,video',
        topic_id=tid,
        created_by_agent_id='hasn:agent:x',
    )
    cid = content['id']
    assert content['status'] == 'idea'
    # 采纳后选题转已采纳 + 关联
    adopted = await creator_service.list_topics(session, user_id=_UID_A, scope=scope, project_id=pid, status=1)
    assert adopted and adopted[0]['content_id'] == cid

    # 6) 阶段产出（研究→大纲→终稿）
    await creator_service.save_stage(
        session, user_id=_UID_A, scope=scope, content_id=cid, stage='research', content_text='竞品都在做红烧肉'
    )
    await creator_service.save_stage(
        session, user_id=_UID_A, scope=scope, content_id=cid, stage='outline', content_text='钩子→步骤→收尾'
    )
    s_final = await creator_service.save_stage(
        session, user_id=_UID_A, scope=scope, content_id=cid, stage='final_draft', content_text='正文……'
    )
    assert s_final['version'] == 1
    # 同 stage 再存一版 → version bump
    s_final2 = await creator_service.save_stage(
        session, user_id=_UID_A, scope=scope, content_id=cid, stage='final_draft', content_text='正文 v2'
    )
    assert s_final2['version'] == 2

    # 7) 状态机推进 idea→drafting→reviewing
    await creator_service.update_content(session, user_id=_UID_A, scope=scope, content_id=cid, status='drafting')
    await creator_service.update_content(session, user_id=_UID_A, scope=scope, content_id=cid, status='reviewing')

    # 8) 提交发布 → pending_review（不绕审核）
    sub = await creator_service.submit_publish(
        session,
        user_id=_UID_A,
        scope=scope,
        content_id=cid,
        account_id=aid,
        publish_note='最佳时间晚8点，话题#家常菜',
    )
    assert sub['status'] == 'pending_review'
    pub_id = sub['publish_id']

    # 9) 主人审核内容通过 reviewing→ready
    await creator_service.update_content(
        session,
        user_id=_UID_A,
        scope=scope,
        content_id=cid,
        status='ready',
        review_status='approved',
        reviewer_user_id=_UID_A,
    )
    # 10) 批准发布 + 标记已发布（人工辅助回填 url）
    await creator_service.approve_publish(
        session, user_id=_UID_A, scope=scope, publish_id=pub_id, approval_user_id=_UID_A
    )
    pub = await creator_service.mark_published(
        session, user_id=_UID_A, scope=scope, publish_id=pub_id, publish_url='https://xhs/abc'
    )
    assert pub['status'] == 'published'
    assert pub['publish_url'] == 'https://xhs/abc'
    # 内容应转 published
    cdet = await creator_service.get_content(session, user_id=_UID_A, scope=scope, content_id=cid)
    assert cdet['status'] == 'published'
    assert len(cdet['stages']) == 4  # research/outline/final_draft x2
    assert len(cdet['publishes']) == 1

    # 11) 回填数据 → 内容转 analyzing
    await creator_service.update_metrics(
        session,
        user_id=_UID_A,
        scope=scope,
        publish_id=pub_id,
        metrics={'views': 12000, 'likes': 800, 'comments': 60, 'new_followers': 45},
    )
    cdet2 = await creator_service.get_content(session, user_id=_UID_A, scope=scope, content_id=cid)
    assert cdet2['status'] == 'analyzing'

    # 12) 复盘总览
    ov = await creator_service.report_overview(session, user_id=_UID_A, scope=scope, project_id=pid)
    assert ov['published_count'] == 1
    assert ov['metrics']['views'] == 12000
    assert ov['metrics']['new_followers'] == 45
    assert ov['content_total'] == 1


async def test_content_state_machine_guard(session) -> None:
    scope = _personal_scope()
    proj = await creator_service.create_project(session, user_id=_UID_A, scope=scope, name='测试号')
    content = await creator_service.create_content(
        session, user_id=_UID_A, scope=scope, project_id=proj['id'], title='t1'
    )
    # idea → published 非法
    with pytest.raises(errors.RequestError):
        await creator_service.update_content(
            session, user_id=_UID_A, scope=scope, content_id=content['id'], status='published'
        )


async def test_cross_user_isolation(session) -> None:
    """A 建项目，B 不可见/不可操作。"""
    scope_a = _personal_scope(_UID_A)
    proj = await creator_service.create_project(session, user_id=_UID_A, scope=scope_a, name='A的号')
    pid = proj['id']
    scope_b = CreatorScope(user_id=_UID_B, owner_hasn_id='hasn:test:creator-b')
    with pytest.raises(errors.NotFoundError):
        await creator_service.get_project(session, user_id=_UID_B, scope=scope_b, project_id=pid)
    # B 列表看不到 A 的
    b_list = await creator_service.list_projects(session, user_id=_UID_B, scope=scope_b)
    assert all(p['id'] != pid for p in b_list)


async def test_enterprise_scope_member_restrict(session) -> None:
    """企业模式：主编看全部、运营只看自己 assignee 的。"""
    eid = 930099
    mgr = CreatorScope(user_id=940001, owner_hasn_id='hasn:mgr', enterprise_id=eid, viewer_role='manager')
    member1 = CreatorScope(user_id=940002, owner_hasn_id='hasn:m1', enterprise_id=eid, viewer_role='member')
    member2 = CreatorScope(user_id=940003, owner_hasn_id='hasn:m2', enterprise_id=eid, viewer_role='member')

    # 两个运营各建一个企业项目（归各自 assignee）
    p1 = await creator_service.create_project(session, user_id=member1.user_id, scope=member1, name='号1')
    p2 = await creator_service.create_project(session, user_id=member2.user_id, scope=member2, name='号2')
    assert p1['owner_scope'] == 'enterprise'
    assert p1['enterprise_id'] == eid
    assert p1['assignee'] == 'hasn:m1'

    # 主编（view=team）看全部企业项目（含两条）
    mgr_list = await creator_service.list_projects(session, user_id=mgr.user_id, scope=mgr)
    ids = {p['id'] for p in mgr_list}
    assert p1['id'] in ids and p2['id'] in ids

    # 运营1 只看自己的
    m1_list = await creator_service.list_projects(session, user_id=member1.user_id, scope=member1)
    m1_ids = {p['id'] for p in m1_list}
    assert p1['id'] in m1_ids and p2['id'] not in m1_ids

    # 运营2 不可读运营1 的项目
    with pytest.raises(errors.NotFoundError):
        await creator_service.get_project(session, user_id=member2.user_id, scope=member2, project_id=p1['id'])


async def test_save_stage_local_asset_refs(session) -> None:
    """阶段产出 asset_refs 支持云端 + 本地引用（doc19 §5.5：reel 成片重资产本地优先不自动上云）。

    向后兼容：裸字符串 / 既有 cloud dict **原样保留**（不破坏现有 round-trip）；本地引用
    {kind:'local', path, node_id, uploaded} 严格校验并归一（补 uploaded 默认）。webui 据 kind=='local' 分流。
    """
    scope = _personal_scope()
    proj = await creator_service.create_project(session, user_id=_UID_A, scope=scope, name='短视频号')
    content = await creator_service.create_content(
        session, user_id=_UID_A, scope=scope, project_id=proj['id'], title='秋天热饮', content_tracks='video'
    )
    cid = content['id']

    # 1) 本地成片引用（reel 出片后 content_operator 调 content.stage.save 落本地路径 + node_id）。
    s_local = await creator_service.save_stage(
        session,
        user_id=_UID_A,
        scope=scope,
        content_id=cid,
        stage='final_draft',
        asset_refs=[{'kind': 'local', 'path': '/Users/x/reel/_work/t1/final-1.mp4', 'node_id': 'node-mac-1', 'uploaded': False}],
    )
    assert s_local['asset_refs'] == [
        {'kind': 'local', 'path': '/Users/x/reel/_work/t1/final-1.mp4', 'node_id': 'node-mac-1', 'uploaded': False}
    ]

    # 1b) 本地引用缺省 uploaded → 归一补 False。
    s_local2 = await creator_service.save_stage(
        session,
        user_id=_UID_A,
        scope=scope,
        content_id=cid,
        stage='voiceover',
        asset_refs=[{'kind': 'local', 'path': '/p/audio.mp3', 'node_id': 'node-mac-1'}],
    )
    assert s_local2['asset_refs'] == [{'kind': 'local', 'path': '/p/audio.mp3', 'node_id': 'node-mac-1', 'uploaded': False}]

    # 2) 历史裸字符串云端引用（封面图落私有桶）→ 原样保留（向后兼容）。
    s_cloud = await creator_service.save_stage(
        session, user_id=_UID_A, scope=scope, content_id=cid, stage='cover', asset_refs=['hasn://asset/cover-1.png']
    )
    assert s_cloud['asset_refs'] == ['hasn://asset/cover-1.png']

    # 3) 既有 cloud dict → 原样透传（不重写 shape，保护现有 round-trip）。
    s_clouddict = await creator_service.save_stage(
        session,
        user_id=_UID_A,
        scope=scope,
        content_id=cid,
        stage='storyboard',
        asset_refs=[{'kind': 'cloud', 'asset_uri': 'hasn://asset/legacy-1'}],
    )
    assert s_clouddict['asset_refs'] == [{'kind': 'cloud', 'asset_uri': 'hasn://asset/legacy-1'}]

    # 4) 混合一阶段多引用（本地成片 + 裸字符串云端封面）。
    s_mixed = await creator_service.save_stage(
        session,
        user_id=_UID_A,
        scope=scope,
        content_id=cid,
        stage='final_draft',  # 同 stage 再存 → version bump
        asset_refs=[
            {'kind': 'local', 'path': '/p/final-2.mp4', 'node_id': 'node-mac-1'},
            'hasn://asset/thumb',
        ],
    )
    assert s_mixed['version'] == 2
    assert s_mixed['asset_refs'] == [
        {'kind': 'local', 'path': '/p/final-2.mp4', 'node_id': 'node-mac-1', 'uploaded': False},
        'hasn://asset/thumb',
    ]


async def test_save_stage_rejects_invalid_asset_refs(session) -> None:
    """非法 asset_refs shape fail-fast 抛 RequestError（仅 local 引用强校验；非 str/非 dict / 空串也拒）。

    向后兼容下只对 kind=='local' 严格校验；非 local dict（含 {kind:'cloud'} 缺 asset_uri / 未知 kind / {}）
    一律透传不拒（保护历史数据），故不在此列。
    """
    scope = _personal_scope()
    proj = await creator_service.create_project(session, user_id=_UID_A, scope=scope, name='短视频号2')
    content = await creator_service.create_content(
        session, user_id=_UID_A, scope=scope, project_id=proj['id'], title='t', content_tracks='video'
    )
    cid = content['id']

    bad_refs = [
        [{'kind': 'local', 'path': '/p'}],  # local 缺 node_id
        [{'kind': 'local', 'node_id': 'n'}],  # local 缺 path
        [{'kind': 'local', 'path': '', 'node_id': 'n'}],  # local path 空串
        [{'kind': 'local', 'path': '/p', 'node_id': ''}],  # local node_id 空串
        [{'kind': 'local', 'path': '/p', 'node_id': 'n', 'uploaded': 'yes'}],  # uploaded 非 bool
        [123],  # 元素非 str 非 dict
        [''],  # 空字符串（不能作云端引用）
    ]
    for refs in bad_refs:
        with pytest.raises(errors.RequestError):
            await creator_service.save_stage(
                session, user_id=_UID_A, scope=scope, content_id=cid, stage='final_draft', asset_refs=refs
            )


async def test_search_patterns_builtin(session) -> None:
    """爆款搜索：命中全局内置 + 自己的。"""
    scope = _personal_scope()
    # 种一个全局内置 pattern + 一个自己的
    session.add(
        ViralPattern(
            name='3步搞定X',
            pattern_type='title',
            template='3步搞定{X}',
            source='builtin',
            is_builtin=True,
            owner_scope='personal',
            success_rate=88,
        )
    )
    session.add(
        ViralPattern(
            name='我的钩子',
            pattern_type='hook',
            user_id=_UID_A,
            owner_scope='personal',
            source='ai_extracted',
            success_rate=70,
        )
    )
    await session.flush()
    res = await creator_service.search_patterns(session, user_id=_UID_A, scope=scope)
    names = {r['name'] for r in res}
    assert '3步搞定X' in names
    assert '我的钩子' in names
    # 标题类过滤
    titles = await creator_service.search_patterns(session, user_id=_UID_A, scope=scope, pattern_type='title')
    assert all(r['pattern_type'] == 'title' for r in titles)
