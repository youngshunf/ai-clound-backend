"""话题 / 圈子 / 文档系统 真实 PG 集成测试（实施/95 Phase 3，零 mock）。

打本地真实库（端口 15432），验证设计 15/16/17 的验收口径；每用例独立 engine（NullPool）
+ 末尾 rollback，绝不污染真实库。tag/名称带唯一后缀，独立于已有数据。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn_community.model import HasnContentTopics, HasnDocNodes
from backend.app.hasn_community.service.circle_service import circle_service
from backend.app.hasn_community.service.community_service import community_service
from backend.app.hasn_community.service.doc_service import doc_service
from backend.app.hasn_community.service.topic_service import topic_service
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def pg():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


# ==================== 话题 ====================
async def test_topic_normalize_dedup(pg) -> None:
    """同义异写 tag 归一到同一话题（lower(name) 唯一）。"""
    base = f'Topic{_uid()}'
    resolved = await topic_service.resolve_topics(pg, [base.upper(), base.lower(), f'  {base}  '], created_by='h_owner')
    assert len({r['topic_id'] for r in resolved}) == 1, '同义异写应归一为一个话题'


async def test_publish_links_topics_and_feed(pg) -> None:
    """发文带 tag → 话题聚合流出现该内容。"""
    owner = f'h_{_uid()}'
    tag = f'Rust{_uid()}'
    res = await community_service.create_post(pg, user_id=1, hasn_id=owner, content='hello rust', tags=[tag])
    pid = res['post_id']
    # 关联写入
    links = (await pg.execute(select(HasnContentTopics).where(HasnContentTopics.content_id == pid))).scalars().all()
    assert len(links) == 1
    topic_id = links[0].topic_id
    feed = await topic_service.get_topic_feed(pg, topic_id, public_only=True)
    assert any(i.get('post_id') == pid for i in feed['items']), '发文应进话题聚合流'


async def test_trending_topics_schema_qualified(pg) -> None:
    """热门话题聚合裸 SQL 必须按 hasn_community schema 全限定（回归：曾用非限定 hasn_posts/hasn_articles → UndefinedTableError）。"""
    owner = f'h_{_uid()}'
    tag = f'Trend{_uid()}'
    # 发一篇带唯一 tag 的已发布帖（human 主社区 → published）
    res = await community_service.create_post(pg, user_id=1, hasn_id=owner, content='trending probe', tags=[tag])
    assert res['status'] == 'published'
    # 关键：不应抛 ProgrammingError(relation does not exist)，返回结构化话题列表
    topics = await community_service.get_trending_topics(pg, limit=200, days=7)
    assert isinstance(topics, list)
    assert all({'topic', 'post_count', 'trend'} <= set(t) for t in topics)
    assert any(t['topic'] == tag for t in topics), '刚发布的 tag 应出现在热门话题聚合中'


async def test_follow_topic_rename_no_break(pg) -> None:
    """关注话题后改名，关注关系不断链（关注的是 topic_id）。"""
    owner = f'h_{_uid()}'
    t = await topic_service.create_topic(pg, name=f'Orig{_uid()}', description=None, cover_url=None, created_by_hasn_id=owner)
    await topic_service.follow_topic(pg, follower_hasn_id=owner, topic_id=t['topic_id'], following=True)
    await topic_service.update_topic(pg, ident=t['topic_id'], name=f'Renamed{_uid()}')
    following = await topic_service.get_following(pg, follower_hasn_id=owner)
    assert any(x['topic_id'] == t['topic_id'] for x in following), '改名后关注不断链'


# ==================== 圈子 ====================
async def test_circle_approval_join_and_feed_isolation(pg) -> None:
    """approval 加入→pending→审批；圈内发帖进圈子流、不串主 feed。"""
    owner = f'h_{_uid()}'
    member = f'h_{_uid()}'
    c = await circle_service.create_circle(pg, owner_hasn_id=owner, owner_user_id=1, name=f'Circle{_uid()}', join_policy='approval', post_policy='members', visibility='public')
    cid = c['circle_id']
    # 申请加入 → pending
    j = await circle_service.join_circle(pg, ident=cid, member_hasn_id=member, member_type='human', owner_hasn_id=member)
    assert j['status'] == 'pending'
    # 非成员发帖被拒
    with pytest.raises(errors.ForbiddenError):
        await circle_service.assert_can_post(pg, circle_id=cid, actor_hasn_id=member)
    # 审批通过
    await circle_service.moderate_member(pg, ident=cid, target_hasn_id=member, actor_hasn_id=owner, action='approve')
    # 圈内发帖（members 策略 → published）
    res = await community_service.create_post(pg, user_id=2, hasn_id=member, content='in circle', tags=[], circle_id=cid)
    assert res['status'] == 'published' and res['circle_id'] == cid
    cpid = res['post_id']
    # 圈子流可见
    cfeed = await circle_service.get_circle_feed(pg, cid, viewer_hasn_id=member)
    assert any(i.get('post_id') == cpid for i in cfeed['items'])
    # 不串主 feed
    main = await community_service.get_feed(pg, user_id=2, feed_type='recommend', limit=50)
    assert all(i.get('post_id') != cpid for i in main['items']), '圈子内容不应进主 feed'


async def test_circle_private_only_members(pg) -> None:
    """私密圈内容仅成员可见（非成员读流 Forbidden）。"""
    owner = f'h_{_uid()}'
    outsider = f'h_{_uid()}'
    c = await circle_service.create_circle(pg, owner_hasn_id=owner, owner_user_id=1, name=f'Priv{_uid()}', visibility='private', join_policy='invite')
    with pytest.raises(errors.ForbiddenError):
        await circle_service.get_circle_feed(pg, c['circle_id'], viewer_hasn_id=outsider)


async def test_circle_post_policy_approval_pending(pg) -> None:
    """post_policy=approval：普通成员发帖进 pending_review。"""
    owner = f'h_{_uid()}'
    member = f'h_{_uid()}'
    c = await circle_service.create_circle(pg, owner_hasn_id=owner, owner_user_id=1, name=f'Appr{_uid()}', join_policy='open', post_policy='approval', visibility='public')
    await circle_service.join_circle(pg, ident=c['circle_id'], member_hasn_id=member, member_type='human', owner_hasn_id=member)
    res = await community_service.create_post(pg, user_id=2, hasn_id=member, content='needs review', tags=[], circle_id=c['circle_id'])
    assert res['status'] == 'pending_review'


# ==================== 文档系统 ====================
async def test_doc_tree_move_recompute(pg) -> None:
    """建多级目录 → move 后子树 path/depth 正确。"""
    owner = f'h_{_uid()}'
    s = await doc_service.create_space(pg, owner_hasn_id=owner, author_type='human', author_hasn_id=owner, owner_user_id=1, title=f'Space{_uid()}')
    sid = s['space_id']
    a = await doc_service.create_node(pg, space_id=sid, actor_hasn_id=owner, node_type='directory', title='A')
    b = await doc_service.create_node(pg, space_id=sid, actor_hasn_id=owner, node_type='directory', title='B', parent_node_id=a['node_id'])
    leaf = await doc_service.create_node(pg, space_id=sid, actor_hasn_id=owner, node_type='directory', title='C', parent_node_id=b['node_id'])
    assert leaf['depth'] == 2 and leaf['path'] == f"{b['path']}/{leaf['node_id']}"
    # move B 到根 → B depth 0，C 跟随
    await doc_service.move_node(pg, node_id=b['node_id'], actor_hasn_id=owner, new_parent_node_id=None)
    rows = {n.node_id: n for n in (await pg.execute(select(HasnDocNodes).where(HasnDocNodes.space_id == sid))).scalars().all()}
    assert rows[b['node_id']].depth == 0 and rows[b['node_id']].path == f"/{b['node_id']}"
    assert rows[leaf['node_id']].depth == 1 and rows[leaf['node_id']].path == f"/{b['node_id']}/{leaf['node_id']}"


async def test_doc_effective_visibility_and_pruning(pg) -> None:
    """有效可见性继承+覆盖；私有子树对非 owner 裁剪不泄露标题。"""
    owner = f'h_{_uid()}'
    viewer = f'h_{_uid()}'
    s = await doc_service.create_space(pg, owner_hasn_id=owner, author_type='human', author_hasn_id=owner, owner_user_id=1, title=f'Doc{_uid()}', default_visibility='private')
    pub = await doc_service.create_node(pg, space_id=s['space_id'], actor_hasn_id=owner, node_type='directory', title='公开手册', visibility='public')
    await doc_service.create_node(pg, space_id=s['space_id'], actor_hasn_id=owner, node_type='directory', title='继承公开', parent_node_id=pub['node_id'])
    await doc_service.create_node(pg, space_id=s['space_id'], actor_hasn_id=owner, node_type='directory', title='私密内部', visibility='private', parent_node_id=pub['node_id'])
    # 非 owner 看到公开子树，但看不到私密标题
    tree = await doc_service.get_tree(pg, space_ident=s['space_id'], viewer_hasn_id=viewer, public_only=True)
    titles = []

    def _collect(nodes) -> None:
        for n in nodes:
            titles.append(n.get('title'))
            _collect(n.get('children', []))

    _collect(tree['tree'])
    assert '公开手册' in titles and '继承公开' in titles
    assert '私密内部' not in titles, '私有子树标题不应泄露'


async def test_doc_password_unlock_and_pwd_version(pg) -> None:
    """密码节点 unlock→grant_token；改密 bump pwd_version 使旧令牌失效。"""
    owner = f'h_{_uid()}'
    viewer = f'h_{_uid()}'
    s = await doc_service.create_space(pg, owner_hasn_id=owner, author_type='human', author_hasn_id=owner, owner_user_id=1, title=f'Pwd{_uid()}', default_visibility='public')
    locked = await doc_service.create_node(pg, space_id=s['space_id'], actor_hasn_id=owner, node_type='directory', title='付费章节', visibility='password', password='s3cret')
    child = await doc_service.create_node(pg, space_id=s['space_id'], actor_hasn_id=owner, node_type='directory', title='高级调优', parent_node_id=locked['node_id'])
    # 未解锁：非 owner 看到锁定占位，不见子标题
    t0 = await doc_service.get_tree(pg, space_ident=s['space_id'], viewer_hasn_id=viewer, public_only=True)
    locked_node = next(n for n in t0['tree'] if n['node_id'] == locked['node_id'])
    assert locked_node['locked'] is True and not locked_node['children']
    # 错误密码拒绝
    with pytest.raises(errors.ForbiddenError):
        await doc_service.unlock(pg, node_id=locked['node_id'], password='wrong')
    # 正确密码 → grant_token，凭它子树解锁
    grant = await doc_service.unlock(pg, node_id=locked['node_id'], password='s3cret')
    t1 = await doc_service.get_tree(pg, space_ident=s['space_id'], viewer_hasn_id=viewer, public_only=True, grant_tokens=[grant['grant_token']])
    unlocked = next(n for n in t1['tree'] if n['node_id'] == locked['node_id'])
    assert unlocked['locked'] is False
    assert any(ch['node_id'] == child['node_id'] for ch in unlocked['children']), '解锁后子节点可见'
    # 改密 bump pwd_version → 旧 grant 失效
    await doc_service.update_node(pg, node_id=locked['node_id'], actor_hasn_id=owner, password='newpass')
    t2 = await doc_service.get_tree(pg, space_ident=s['space_id'], viewer_hasn_id=viewer, public_only=True, grant_tokens=[grant['grant_token']])
    relocked = next(n for n in t2['tree'] if n['node_id'] == locked['node_id'])
    assert relocked['locked'] is True, '改密后旧 grant_token 应失效'


async def test_list_mine_enriches_agent_author(pg) -> None:
    """「我的」列表必须富化作者：文集作者可能是主人名下的分身，只回 hasn_id 前端就没有昵称头像。"""
    owner = f'h_{_uid()}'
    agent = f'a_{_uid()}'
    pg.add(
        HasnAgents(
            hasn_id=agent,
            star_id=f'ag{_uid()}',
            owner_id=owner,
            display_name='砚白',
            avatar='https://cdn.example.com/yanbai.png',
            status='active',
        )
    )
    await pg.flush()
    await doc_service.create_space(
        pg, owner_hasn_id=owner, author_type='agent', author_hasn_id=agent, owner_user_id=1, title=f'Agent{_uid()}'
    )

    items = await doc_service.list_mine(pg, owner_hasn_id=owner)

    assert len(items) == 1
    author = items[0]['author']
    assert author['hasn_id'] == agent
    assert author['type'] == 'agent'
    # 富化缺失时 _author_info 会把 display_name 回落成 hasn_id，断言真名才能证伪。
    assert author['display_name'] == '砚白'
    assert author['avatar'] == 'https://cdn.example.com/yanbai.png'


async def test_publish_article_doc_placement(pg) -> None:
    """一次发文同时落话题关联 + doc_placement（建多级目录落位）。"""
    owner = f'h_{_uid()}'
    tag = f'Guide{_uid()}'
    res = await community_service.create_article(
        pg, user_id=1, hasn_id=owner, title='落位文章', content='正文', tags=[tag],
        doc_placement={'new_space': {'title': f'NS{_uid()}'}, 'new_dirs': ['指南', '进阶']},
    )
    assert res['doc_placement'] and res['doc_placement']['node_id'].startswith('dn_')
    # 文章叶子在树中、路径两级目录下
    space_id = res['doc_placement']['space_id']
    nodes = (await pg.execute(select(HasnDocNodes).where(HasnDocNodes.space_id == space_id))).scalars().all()
    article_node = next(n for n in nodes if n.node_type == 'article')
    assert article_node.article_id == res['article_id']
    assert article_node.depth == 2, '文章应挂在两级目录之下'
