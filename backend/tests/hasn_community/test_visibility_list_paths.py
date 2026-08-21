"""列表/聚合流路径的 `visibility` 判权（service 层，真实 PG，零 mock）。

## 修的是什么

详情接口（get_post）收紧后，这些**列表路径**此前完全不看 `visibility` 或判得太粗：

- `get_feed` 主流：只判 `status='published'` + `circle_id IS NULL`，私密/followers 帖
  直接漏进所有 feed_type——包括 open 匿名流（未登录也能刷到别人的私密帖）。
- `_get_articles_feed`：只排 `private`，followers 文和未知取值（如遗留
  `workspace_group`）照样漏给任何人。
- `get_profile_posts` / `get_profile_articles`：看别人主页时其私密/followers 内容全漏。
- `circle_service.get_circle_feed`：圈闸（私密圈限成员）之外完全不看内容自身的
  visibility——公开圈里的私密/followers 内容漏给所有读者。
- `topic_service.get_topic_feed`：`_APP_VIS` 直接放行 followers、不查关注，
  等于 followers 内容对全体登录用户公开。
- `community_cards.fetch_post_cards/fetch_article_cards`：按 id 批量取正文零判权，
  只能靠上游查询自觉。

收紧方式：全部改走 `content_visibility.content_visibility_sql`——与详情/翻译接口
`evaluate_content_visibility` **同一份判据的 SQL 投影**。本文件末尾的
`test_sql_predicate_matches_judge_row_by_row` 守的就是「投影与判官永不漂移」。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sqlalchemy import select

from backend.app.hasn_community.model.hasn_articles import HasnArticles
from backend.app.hasn_community.model.hasn_circle_members import HasnCircleMembers
from backend.app.hasn_community.model.hasn_circles import HasnCircles
from backend.app.hasn_community.model.hasn_content_topics import HasnContentTopics
from backend.app.hasn_community.model.hasn_posts import HasnPosts
from backend.app.hasn_community.model.hasn_topics import HasnTopics
from backend.app.hasn_community.service.circle_service import circle_service
from backend.app.hasn_community.service.community_cards import fetch_post_cards
from backend.app.hasn_community.service.community_service import community_service
from backend.app.hasn_community.service.content_visibility import (
    content_visibility_sql,
    evaluate_content_visibility,
)
from backend.app.hasn_community.service.topic_service import topic_service
from backend.common.exception import errors
from backend.tests.hasn_community.test_post_visibility import (
    _ANONYMOUS,
    _Ctx,
    _add_post,
    _uid,
    ctx,  # noqa: F401  # 复用详情判权文件的 fixture（同一套作者/主人/关注者/陌生人/圈内圈外主体）
)

pytestmark = pytest.mark.asyncio


async def _add_article(
    c: _Ctx,
    *,
    visibility: str,
    status: str = 'published',
    circle_id: str | None = None,
    agent_authored: bool = False,
) -> str:
    """造一篇文章（字段口径与 `_add_post` 对齐）。"""
    article_id = f'art_{_uid()}'
    if agent_authored:
        author_hasn_id = f'a_{_uid()}'
        owner_hasn_id = c.owner_hasn
        author_type = 'agent'
    else:
        author_hasn_id = c.author_hasn
        owner_hasn_id = c.author_hasn
        author_type = 'human'
    c.sess.add(
        HasnArticles(
            article_id=article_id,
            author_type=author_type,
            author_hasn_id=author_hasn_id,
            owner_hasn_id=owner_hasn_id,
            title='[test] 可见性判权用文章标题',
            content='[test] 可见性判权用文章正文。',
            visibility=visibility,
            status=status,
            circle_id=circle_id,
        )
    )
    await c.sess.flush()
    return article_id


async def _feed_ids(c: _Ctx, user_id: int | None, feed_type: str = 'recommend') -> set[str]:
    """主 feed 返回的帖子 ID 集合（主 feed 现在同时包含文章）。"""
    res = await community_service.get_feed(c.sess, user_id=user_id, feed_type=feed_type, limit=50)
    return {item['post_id'] for item in res['items'] if item['content_type'] == 'post'}


async def _articles_feed_ids(c: _Ctx, user_id: int | None) -> set[str]:
    res = await community_service.get_feed(c.sess, user_id=user_id, feed_type='articles', limit=50)
    return {item['article_id'] for item in res['items']}


# ==================== get_feed 主流 ====================


async def test_feed_hides_private_and_unknown_from_stranger(ctx) -> None:
    """主流对陌生人：public 可见；private / followers / workspace_group 一律不漏。"""
    c = ctx
    public_id = await _add_post(c, visibility='public')
    followers_id = await _add_post(c, visibility='followers')
    private_id = await _add_post(c, visibility='private')
    legacy_id = await _add_post(c, visibility='workspace_group')

    ids = await _feed_ids(c, c.stranger_uid)
    assert public_id in ids
    assert followers_id not in ids, 'followers 帖不能漏给非关注者'
    assert private_id not in ids, '私密帖不能漏进主流'
    assert legacy_id not in ids, '未知取值 fail-closed，不能当公开'


async def test_feed_shows_followers_post_to_follower(ctx) -> None:
    """主流对关注者：followers 帖可见；private 仍然不可见（关注不解锁私密）。"""
    c = ctx
    followers_id = await _add_post(c, visibility='followers')
    private_id = await _add_post(c, visibility='private')

    ids = await _feed_ids(c, c.follower_uid)
    assert followers_id in ids
    assert private_id not in ids


async def test_feed_shows_own_private_to_author(ctx) -> None:
    """作者刷主流能看到自己的私密帖（own 恒可见），陌生人看不到。"""
    c = ctx
    private_id = await _add_post(c, visibility='private')

    assert private_id in await _feed_ids(c, c.author_uid)
    assert private_id not in await _feed_ids(c, c.outsider_uid)


async def test_feed_anonymous_only_public(ctx) -> None:
    """open 匿名流：只剩 public——这是修复前漏得最狠的一面（未登录刷到私密帖）。"""
    c = ctx
    public_id = await _add_post(c, visibility='public')
    followers_id = await _add_post(c, visibility='followers')
    private_id = await _add_post(c, visibility='private')

    ids = await _feed_ids(c, _ANONYMOUS)
    assert ids & {public_id} == {public_id}
    assert followers_id not in ids and private_id not in ids


async def test_feed_following_respects_visibility(ctx) -> None:
    """following 流：关注者能看到关注作者的 followers 帖，但看不到其 private 帖。"""
    c = ctx
    followers_id = await _add_post(c, visibility='followers')
    private_id = await _add_post(c, visibility='private')

    ids = await _feed_ids(c, c.follower_uid, feed_type='following')
    assert followers_id in ids
    assert private_id not in ids, 'following 流不是私密帖的后门'


async def test_recommend_feed_mixes_posts_and_articles(ctx) -> None:
    """推荐流应按时间把帖子与文章放进同一页，而非只展示帖子。"""
    c = ctx
    post_id = await _add_post(c, visibility='public')
    article_id = await _add_article(c, visibility='public')

    result = await community_service.get_feed(c.sess, user_id=c.stranger_uid, feed_type='recommend', limit=50)

    items = result['items']
    assert post_id in {item['post_id'] for item in items if item['content_type'] == 'post'}
    assert article_id in {item['article_id'] for item in items if item['content_type'] == 'article'}
    assert {item['content_type'] for item in items} >= {'post', 'article'}


async def test_recommend_feed_mixed_cursor_keeps_content_types(ctx) -> None:
    """混排分页游标应能从文章继续到同排序键下的帖子，且不重复文章。"""
    c = ctx
    post_id = await _add_post(c, visibility='public')
    article_id = await _add_article(c, visibility='public')
    same_time = datetime.now(UTC)
    post = (await c.sess.execute(select(HasnPosts).where(HasnPosts.post_id == post_id))).scalar_one()
    article = (
        await c.sess.execute(select(HasnArticles).where(HasnArticles.article_id == article_id))
    ).scalar_one()
    post.published_time = same_time
    article.published_time = same_time
    await c.sess.flush()

    first = await community_service.get_feed(
        c.sess, user_id=c.stranger_uid, feed_type='recommend', limit=1
    )
    assert first['next_cursor']
    second = await community_service.get_feed(
        c.sess,
        user_id=c.stranger_uid,
        feed_type='recommend',
        cursor=first['next_cursor'],
        limit=1,
    )
    seen = [
        (item['content_type'], item.get('post_id') or item.get('article_id'))
        for item in [*first['items'], *second['items']]
    ]
    assert len(seen) == len(set(seen))
    assert {post_id, article_id} <= {item_id for _, item_id in seen}


# ==================== _get_articles_feed 文章流 ====================


async def test_articles_feed_hides_followers_and_unknown_from_stranger(ctx) -> None:
    """文章流对陌生人：只放 public。修复前 `visibility != 'private'` 会把
    followers 文与 workspace_group 文漏给任何人——本用例专守这两条。"""
    c = ctx
    public_id = await _add_article(c, visibility='public')
    followers_id = await _add_article(c, visibility='followers')
    legacy_id = await _add_article(c, visibility='workspace_group')

    ids = await _articles_feed_ids(c, c.stranger_uid)
    assert public_id in ids
    assert followers_id not in ids, 'followers 文不能漏给非关注者'
    assert legacy_id not in ids, '未知取值 fail-closed'


async def test_articles_feed_follower_sees_followers_article(ctx) -> None:
    c = ctx
    followers_id = await _add_article(c, visibility='followers')

    assert followers_id in await _articles_feed_ids(c, c.follower_uid)
    assert followers_id not in await _articles_feed_ids(c, _ANONYMOUS)


# ==================== 主页列表 ====================


async def test_profile_posts_stranger_sees_only_public(ctx) -> None:
    """看别人主页：私密/followers 帖不可见；关注者可见 followers 帖。"""
    c = ctx
    public_id = await _add_post(c, visibility='public')
    followers_id = await _add_post(c, visibility='followers')
    private_id = await _add_post(c, visibility='private')

    async def ids_for(viewer_uid: int) -> set[str]:
        res = await community_service.get_profile_posts(
            c.sess, hasn_id=c.author_hasn, viewer_user_id=viewer_uid, limit=50
        )
        return {item['post_id'] for item in res['items']}

    stranger_ids = await ids_for(c.stranger_uid)
    assert public_id in stranger_ids
    assert followers_id not in stranger_ids
    assert private_id not in stranger_ids

    follower_ids = await ids_for(c.follower_uid)
    assert followers_id in follower_ids
    assert private_id not in follower_ids

    author_ids = await ids_for(c.author_uid)
    assert {public_id, followers_id, private_id} <= author_ids, '作者看自己主页不受限'


async def test_profile_articles_stranger_sees_only_public(ctx) -> None:
    """主页文章列表同一判据（与 get_profile_posts 是孪生缺陷，一起修）。"""
    c = ctx
    public_id = await _add_article(c, visibility='public')
    followers_id = await _add_article(c, visibility='followers')
    private_id = await _add_article(c, visibility='private')

    async def ids_for(viewer_uid: int) -> set[str]:
        res = await community_service.get_profile_articles(
            c.sess, hasn_id=c.author_hasn, viewer_user_id=viewer_uid, limit=50
        )
        return {item['article_id'] for item in res['items']}

    stranger_ids = await ids_for(c.stranger_uid)
    assert public_id in stranger_ids
    assert followers_id not in stranger_ids
    assert private_id not in stranger_ids
    assert followers_id in await ids_for(c.follower_uid)
    assert private_id in await ids_for(c.author_uid)


# ==================== get_article 详情 ====================


async def _can_see_article(c: _Ctx, article_id: str, viewer_hasn: str) -> bool:
    try:
        detail = await community_service.get_article(
            c.sess, user_id=0, hasn_id=viewer_hasn, article_id=article_id
        )
    except errors.NotFoundError:
        return False
    assert detail['article_id'] == article_id
    return True


async def test_get_article_private_hidden_from_stranger(ctx) -> None:
    """文章详情：private 对陌生人 404（此前零判权，拿到 id 就能读全文）。"""
    c = ctx
    article_id = await _add_article(c, visibility='private')

    assert await _can_see_article(c, article_id, c.author_hasn) is True
    assert await _can_see_article(c, article_id, c.stranger_hasn) is False
    assert await _can_see_article(c, article_id, c.follower_hasn) is False


async def test_get_article_draft_hidden_from_others_visible_to_author(ctx) -> None:
    """草稿：作者/主人能看（审核入口），其他人 404——此前连他人草稿都能读。"""
    c = ctx
    draft_id = await _add_article(c, visibility='public', status='draft')
    agent_draft_id = await _add_article(c, visibility='public', status='pending_review', agent_authored=True)

    assert await _can_see_article(c, draft_id, c.author_hasn) is True
    assert await _can_see_article(c, draft_id, c.stranger_hasn) is False
    assert await _can_see_article(c, agent_draft_id, c.owner_hasn) is True, '主人必须能审名下分身的待审文'
    assert await _can_see_article(c, agent_draft_id, c.stranger_hasn) is False


async def test_get_article_followers_visible_to_follower_only(ctx) -> None:
    c = ctx
    article_id = await _add_article(c, visibility='followers')

    assert await _can_see_article(c, article_id, c.follower_hasn) is True
    assert await _can_see_article(c, article_id, c.stranger_hasn) is False


# ==================== 圈子内容流 ====================


async def _add_circle(c: _Ctx, *, visibility: str, member: bool = False) -> str:
    """造一个 `cir_` 前缀的圈子（`CircleService._get` 只认此前缀按 circle_id 查，否则按 slug）。

    member=True 时把 c.member_hasn 放成 active 成员。
    """
    circle_id = f'cir_{visibility[:3]}_{_uid()}'
    c.sess.add(
        HasnCircles(
            circle_id=circle_id,
            name=f'测试{visibility}圈',
            slug=f'{visibility}-{_uid()}',
            owner_hasn_id=c.author_hasn,
            visibility=visibility,
            status='active',
        )
    )
    if member:
        c.sess.add(
            HasnCircleMembers(
                circle_id=circle_id,
                member_hasn_id=c.member_hasn,
                member_type='human',
                owner_hasn_id=c.member_hasn,
                role='member',
                status='active',
            )
        )
    await c.sess.flush()
    return circle_id


async def _circle_feed_post_ids(c: _Ctx, circle_id: str, viewer: str | None, public_only: bool = False) -> set[str]:
    res = await circle_service.get_circle_feed(
        c.sess, circle_id, viewer_hasn_id=viewer, public_only=public_only, limit=50
    )
    return {item['post_id'] for item in res['items'] if item['content_type'] == 'post'}


async def _add_circle_post(c: _Ctx, *, visibility: str, circle_id: str) -> str:
    """造一条进圈子流的帖子：圈子流只收 `published_time` 非空的内容，共享 fixture 的
    `_add_post` 不设发布时间，这里补上（真实发布路径会写该字段）。"""
    from datetime import UTC, datetime

    post_id = await _add_post(c, visibility=visibility, circle_id=circle_id)
    row = (
        await c.sess.execute(select(HasnPosts).where(HasnPosts.post_id == post_id))
    ).scalar_one()
    row.published_time = datetime.now(UTC)
    await c.sess.flush()
    return post_id


async def test_circle_feed_per_content_visibility(ctx) -> None:
    """公开圈里的内容仍按自身 visibility 判：圈成员/路人看不到他人的私密与 followers 帖，
    但能看到 public 与 circle 帖（圈语义：圈内公开内容对圈可读）。"""
    c = ctx
    pub_circle = await _add_circle(c, visibility='public')
    public_id = await _add_circle_post(c, visibility='public', circle_id=pub_circle)
    circle_vis_id = await _add_circle_post(c, visibility='circle', circle_id=pub_circle)
    followers_id = await _add_circle_post(c, visibility='followers', circle_id=pub_circle)
    private_id = await _add_circle_post(c, visibility='private', circle_id=pub_circle)

    stranger_ids = await _circle_feed_post_ids(c, pub_circle, c.stranger_hasn)
    assert {public_id, circle_vis_id} <= stranger_ids
    assert followers_id not in stranger_ids, '圈内 followers 帖仍要求关注作者'
    assert private_id not in stranger_ids, '圈内私密帖只有作者/主人可见'

    follower_ids = await _circle_feed_post_ids(c, pub_circle, c.follower_hasn)
    assert followers_id in follower_ids
    assert private_id not in follower_ids

    author_ids = await _circle_feed_post_ids(c, pub_circle, c.author_hasn)
    assert {public_id, circle_vis_id, followers_id, private_id} <= author_ids


async def test_circle_feed_public_only_anonymous(ctx) -> None:
    """open 面（public_only）：公开圈里只剩 public 与 circle 帖，followers/private 不放行。"""
    c = ctx
    pub_circle = await _add_circle(c, visibility='public')
    public_id = await _add_circle_post(c, visibility='public', circle_id=pub_circle)
    circle_vis_id = await _add_circle_post(c, visibility='circle', circle_id=pub_circle)
    followers_id = await _add_circle_post(c, visibility='followers', circle_id=pub_circle)

    ids = await _circle_feed_post_ids(c, pub_circle, None, public_only=True)
    assert {public_id, circle_vis_id} <= ids
    assert followers_id not in ids


async def test_circle_feed_private_circle_still_member_only(ctx) -> None:
    """回归：私密圈的圈闸不因内容判据收紧而松动——圈外人 403，成员可读 circle 帖。"""
    c = ctx
    priv_circle = await _add_circle(c, visibility='private', member=True)
    circle_vis_id = await _add_circle_post(c, visibility='circle', circle_id=priv_circle)

    with pytest.raises(errors.ForbiddenError):
        await circle_service.get_circle_feed(c.sess, priv_circle, viewer_hasn_id=c.outsider_hasn)

    member_ids = await _circle_feed_post_ids(c, priv_circle, c.member_hasn)
    assert circle_vis_id in member_ids


# ==================== 话题聚合流 ====================


async def _add_topic_with_posts(c: _Ctx, post_ids: list[str]) -> str:
    topic_id = f'tpc_{_uid()}'
    c.sess.add(
        HasnTopics(
            topic_id=topic_id,
            name=f'测试话题{_uid()}',
            slug=f'topic-{_uid()}',
            status='active',
        )
    )
    for pid in post_ids:
        c.sess.add(HasnContentTopics(topic_id=topic_id, content_type='post', content_id=pid))
    await c.sess.flush()
    return topic_id


async def test_topic_feed_followers_requires_follow(ctx) -> None:
    """话题流：followers 帖只给关注者（修复前 _APP_VIS 对全体登录用户放行）。"""
    c = ctx
    public_id = await _add_post(c, visibility='public')
    followers_id = await _add_post(c, visibility='followers')
    private_id = await _add_post(c, visibility='private')
    topic_id = await _add_topic_with_posts(c, [public_id, followers_id, private_id])

    async def ids_for(viewer: str | None, public_only: bool = False) -> set[str]:
        res = await topic_service.get_topic_feed(
            c.sess, topic_id, viewer_hasn_id=viewer, public_only=public_only, limit=50
        )
        return {item['post_id'] for item in res['items']}

    stranger_ids = await ids_for(c.stranger_hasn)
    assert public_id in stranger_ids
    assert followers_id not in stranger_ids
    assert private_id not in stranger_ids

    assert followers_id in await ids_for(c.follower_hasn)
    assert followers_id not in await ids_for(None, public_only=True), 'open 面只剩 public'


# ==================== cards 批量取数 ====================


async def test_fetch_post_cards_filters_when_viewer_given(ctx) -> None:
    """cards 按 id 取正文：传 viewer（含 None=匿名）即按判据过滤；默认不过滤是给
    管理面（审核队列必须看到待审内容）留的显式语义。"""
    c = ctx
    public_id = await _add_post(c, visibility='public')
    private_id = await _add_post(c, visibility='private')

    cards = await fetch_post_cards(c.sess, [public_id, private_id], viewer_hasn_id=c.stranger_hasn)
    assert public_id in cards
    assert private_id not in cards, '越权 id 不该出现在结果里'

    unfiltered = await fetch_post_cards(c.sess, [public_id, private_id])
    assert {public_id, private_id} <= set(unfiltered), '管理面默认不过滤'


# ==================== SQL 谓词与判官的逐行一致性 ====================


async def test_sql_predicate_matches_judge_row_by_row(ctx) -> None:
    """`content_visibility_sql` 是 `evaluate_content_visibility` 的 SQL 投影——
    判据只能有一份。本用例对「visibility × 圈子 × 真人/分身」矩阵逐行比对：
    SQL 放行的行当且仅当判官放行。任何一边改了判据没同步另一边，这里立刻红。
    """
    c = ctx
    cases = [
        # (visibility, circle_id, agent_authored)
        ('public', None, False),
        ('followers', None, False),
        ('private', None, False),
        ('workspace_group', None, False),
        ('', None, False),
        ('circle', c.private_circle_id, False),
        ('circle', c.public_circle_id, False),
        ('public', c.private_circle_id, False),
        ('followers', c.private_circle_id, False),
        ('private', c.public_circle_id, False),
        ('circle', None, False),  # 脏数据：circle 可见性却没挂圈
        ('private', None, True),
        ('followers', None, True),
        ('circle', c.private_circle_id, True),
    ]
    seeded: list[tuple[str, str, str, str, str | None]] = []  # (post_id, visibility, author, owner, circle_id)
    for visibility, circle_id, agent_authored in cases:
        post_id = await _add_post(c, visibility=visibility, circle_id=circle_id, agent_authored=agent_authored)
        row = (
            await c.sess.execute(select(HasnPosts).where(HasnPosts.post_id == post_id))
        ).scalar_one()
        seeded.append((post_id, row.visibility, row.author_hasn_id, row.owner_hasn_id, row.circle_id))

    viewers: list[tuple[str, str | None]] = [
        ('作者', c.author_hasn),
        ('主人', c.owner_hasn),
        ('关注者', c.follower_hasn),
        ('陌生人', c.stranger_hasn),
        ('圈内成员', c.member_hasn),
        ('圈外人', c.outsider_hasn),
        ('待批成员', c.pending_hasn),
        ('未登录', None),
    ]
    all_ids = [pid for pid, *_ in seeded]

    mismatches: list[str] = []
    for viewer_label, viewer in viewers:
        sql_visible = set(
            (
                await c.sess.execute(
                    select(HasnPosts.post_id).where(
                        HasnPosts.post_id.in_(all_ids),
                        content_visibility_sql(HasnPosts, viewer_hasn_id=viewer),
                    )
                )
            )
            .scalars()
            .all()
        )
        for post_id, visibility, author, owner, circle_id in seeded:
            decision = await evaluate_content_visibility(
                c.sess,
                visibility=visibility,
                author_hasn_id=author,
                owner_hasn_id=owner,
                circle_id=circle_id,
                viewer_hasn_id=viewer,
            )
            if (post_id in sql_visible) != decision.allowed:
                mismatches.append(
                    f'visibility={visibility!r} circle={circle_id} viewer={viewer_label}: '
                    f'SQL={post_id in sql_visible} 判官={decision.allowed}'
                )

    assert not mismatches, 'SQL 谓词与判官判定不一致：\n' + '\n'.join(mismatches)
