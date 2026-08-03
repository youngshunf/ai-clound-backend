"""帖子详情 `visibility` 判权矩阵（service 层，真实 PG，零 mock）。

## 修的是什么

`community_service.get_post` 此前的可见性闸**只判 `status`**：只要 `status='published'`，
`visibility` 完全不看。于是任何登录用户（乃至走 `/api/v1/community/open/posts/{id}` 的
**未登录**访客）拿到 `post_id` 就能读到别人 `private` / `followers` / `circle` 帖的全文。

本文件守收紧后的判权矩阵：

| visibility | 作者本人 | 分身帖主人 | 关注者 | 非关注者 | 圈内成员 | 圈外人 | 未登录 |
|---|---|---|---|---|---|---|---|
| `public`     | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `followers`  | ✅ | ✅ | ✅ | ❌ | — | — | ❌ |
| `private`    | ✅ | ✅ | ❌ | ❌ | — | — | ❌ |
| `circle`（私密圈） | ✅ | ✅ | — | — | ✅ | ❌ | ❌ |
| `circle`（公开圈） | ✅ | ✅ | — | — | ✅ | ✅ | ✅ |
| 未知值（如遗留 `workspace_group`） | ✅ | ✅ | ❌ | ❌ | — | — | ❌ |

## 两条容易判错的边界（本文件专门守）

1. **`circle_id` 非空时，圈子闸优先于 `visibility`**——挂在私密圈里的帖子即使
   `visibility='public'` 也不能漏给圈外人，否则「私密圈」形同虚设。
2. **未知 `visibility` 取值 fail-closed**。线上真有第五个值 `workspace_group`
   （`2026-06-03-circle-columns.sql` 把旧的 `circle` 改名让位给新圈子概念）。
   `visibility` 列既无 CHECK 约束、入参也无白名单，任意字符串都能落库，
   所以「不认识就拒绝」是唯一安全的兜底。

## 返回码

详情接口一律 **404「帖子不存在」**，不回 403：它是主要的存在性探测面（尤其匿名 open 路由），
403 等于承认「这条帖子存在、只是你没权限」。翻译接口沿用 403 + 具体文案——它只有在详情
已放行后才够得着，不额外泄露存在性。两侧**判据**（能不能看）共用
`content_visibility` 一份实现，由本文件末尾的一致性用例守。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.service.content_translation_service import ContentTranslationService
from backend.app.hasn_community.model.hasn_circle_members import HasnCircleMembers
from backend.app.hasn_community.model.hasn_circles import HasnCircles
from backend.app.hasn_community.model.hasn_follows import HasnFollows
from backend.app.hasn_community.model.hasn_posts import HasnPosts
from backend.app.hasn_community.service.community_service import community_service
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio

# 未登录 viewer：get_post 的 user_id=None → _resolve_human_hasn_id 返回 None
_ANONYMOUS: int | None = None


def _uid() -> str:
    return uuid.uuid4().hex[:10]


def _next_user_id() -> int:
    """给测试 human 造互不相同的 user_id（真实库里 user_id 是 sys_user.id）。"""
    return 1_300_000_000 + int(uuid.uuid4().int % 900_000_000)


class _Ctx:
    """一组互相关联的测试主体：作者、主人、关注者、陌生人、圈内/圈外人。"""

    def __init__(self, sess) -> None:
        self.sess = sess
        # hasn_id → user_id 的映射，便于用例按角色取 user_id
        self.author_hasn = ''
        self.author_uid = 0
        self.owner_hasn = ''
        self.owner_uid = 0
        self.follower_hasn = ''
        self.follower_uid = 0
        self.stranger_hasn = ''
        self.stranger_uid = 0
        self.member_hasn = ''
        self.member_uid = 0
        self.outsider_hasn = ''
        self.outsider_uid = 0
        self.pending_hasn = ''
        self.pending_uid = 0
        self.private_circle_id = ''
        self.public_circle_id = ''


async def _add_human(sess, nickname: str) -> tuple[str, int]:
    hasn_id = f'h_{nickname}_{_uid()}'
    user_id = _next_user_id()
    sess.add(
        HasnHumans(
            hasn_id=hasn_id,
            star_id=f's_{_uid()}',
            user_id=user_id,
            nickname=nickname,
            status='active',
        )
    )
    await sess.flush()
    return hasn_id, user_id


@pytest_asyncio.fixture
async def ctx():
    """真实 PG；单 session + 末尾 rollback，不落脏数据。"""
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    sess = async_sessionmaker(engine, expire_on_commit=False)()
    c = _Ctx(sess)
    c.author_hasn, c.author_uid = await _add_human(sess, 'author')
    c.owner_hasn, c.owner_uid = await _add_human(sess, 'owner')
    c.follower_hasn, c.follower_uid = await _add_human(sess, 'follower')
    c.stranger_hasn, c.stranger_uid = await _add_human(sess, 'stranger')
    c.member_hasn, c.member_uid = await _add_human(sess, 'member')
    c.outsider_hasn, c.outsider_uid = await _add_human(sess, 'outsider')
    c.pending_hasn, c.pending_uid = await _add_human(sess, 'pending')

    # follower 关注 author（followers 可见性的判据）
    sess.add(
        HasnFollows(
            follower_hasn_id=c.follower_hasn,
            target_type='human',
            target_hasn_id=c.author_hasn,
        )
    )

    # 私密圈 + 公开圈
    c.private_circle_id = f'c_priv_{_uid()}'
    c.public_circle_id = f'c_pub_{_uid()}'
    sess.add(
        HasnCircles(
            circle_id=c.private_circle_id,
            name='私密圈',
            slug=f'priv-{_uid()}',
            owner_hasn_id=c.author_hasn,
            visibility='private',
            status='active',
        )
    )
    sess.add(
        HasnCircles(
            circle_id=c.public_circle_id,
            name='公开圈',
            slug=f'pub-{_uid()}',
            owner_hasn_id=c.author_hasn,
            visibility='public',
            status='active',
        )
    )
    # member 是私密圈的 active 成员；pending 只是待批（不应放行）
    sess.add(
        HasnCircleMembers(
            circle_id=c.private_circle_id,
            member_hasn_id=c.member_hasn,
            member_type='human',
            owner_hasn_id=c.member_hasn,
            role='member',
            status='active',
        )
    )
    sess.add(
        HasnCircleMembers(
            circle_id=c.private_circle_id,
            member_hasn_id=c.pending_hasn,
            member_type='human',
            owner_hasn_id=c.pending_hasn,
            role='member',
            status='pending',
        )
    )
    await sess.flush()

    try:
        yield c
    finally:
        await sess.rollback()
        await sess.close()
        await engine.dispose()


async def _add_post(
    c: _Ctx,
    *,
    visibility: str,
    status: str = 'published',
    circle_id: str | None = None,
    agent_authored: bool = False,
) -> str:
    """造一条帖子。

    `agent_authored=True` 时模拟分身发帖：author 是分身 hasn_id，owner 是主人（c.owner_hasn）。
    否则是真人发帖：author == owner == c.author_hasn。
    """
    post_id = f'p_{_uid()}'
    if agent_authored:
        author_hasn_id = f'a_{_uid()}'
        owner_hasn_id = c.owner_hasn
        author_type = 'agent'
    else:
        author_hasn_id = c.author_hasn
        owner_hasn_id = c.author_hasn
        author_type = 'human'
    c.sess.add(
        HasnPosts(
            post_id=post_id,
            author_type=author_type,
            author_hasn_id=author_hasn_id,
            owner_hasn_id=owner_hasn_id,
            content='[test] 可见性判权用帖子正文。',
            visibility=visibility,
            status=status,
            circle_id=circle_id,
        )
    )
    await c.sess.flush()
    return post_id


async def _can_see(c: _Ctx, post_id: str, user_id: int | None) -> bool:
    """走真实 get_post；能拿到详情即可见，404 即不可见。"""
    try:
        detail = await community_service.get_post(c.sess, post_id=post_id, user_id=user_id)
    except errors.NotFoundError:
        return False
    assert detail['post_id'] == post_id
    return True


# ==================== public：谁都能看 ====================


async def test_public_visible_to_everyone(ctx) -> None:
    """public 帖：作者、陌生人、未登录都能看（不因收紧而误伤公开内容）。"""
    c = ctx
    post_id = await _add_post(c, visibility='public')

    assert await _can_see(c, post_id, c.author_uid) is True
    assert await _can_see(c, post_id, c.stranger_uid) is True
    assert await _can_see(c, post_id, _ANONYMOUS) is True


# ==================== private：只有作者/主人 ====================


async def test_private_visible_only_to_author(ctx) -> None:
    """private 帖：作者本人能看。"""
    c = ctx
    post_id = await _add_post(c, visibility='private')
    assert await _can_see(c, post_id, c.author_uid) is True


async def test_private_hidden_from_stranger_follower_and_anonymous(ctx) -> None:
    """private 帖：陌生人、**关注者**、未登录一律 404（关注不解锁私密）。"""
    c = ctx
    post_id = await _add_post(c, visibility='private')

    assert await _can_see(c, post_id, c.stranger_uid) is False
    assert await _can_see(c, post_id, c.follower_uid) is False, '关注者也不该看到私密帖'
    assert await _can_see(c, post_id, _ANONYMOUS) is False, '未登录绝不能读私密帖'


async def test_private_agent_post_visible_to_owner(ctx) -> None:
    """分身发的 private 帖：主人能看（责任主体放行），其他人不能。"""
    c = ctx
    post_id = await _add_post(c, visibility='private', agent_authored=True)

    assert await _can_see(c, post_id, c.owner_uid) is True, '主人必须能看自己名下分身的帖'
    assert await _can_see(c, post_id, c.stranger_uid) is False
    assert await _can_see(c, post_id, _ANONYMOUS) is False


# ==================== followers：关注者才行 ====================


async def test_followers_visible_to_follower_and_author(ctx) -> None:
    """followers 帖：已关注作者的人 + 作者本人可见。"""
    c = ctx
    post_id = await _add_post(c, visibility='followers')

    assert await _can_see(c, post_id, c.follower_uid) is True
    assert await _can_see(c, post_id, c.author_uid) is True


async def test_followers_hidden_from_non_follower_and_anonymous(ctx) -> None:
    """followers 帖：没关注的人和未登录 404。"""
    c = ctx
    post_id = await _add_post(c, visibility='followers')

    assert await _can_see(c, post_id, c.stranger_uid) is False
    assert await _can_see(c, post_id, _ANONYMOUS) is False


async def test_followers_direction_matters(ctx) -> None:
    """关注方向不能反：author 关注 stranger 不等于 stranger 能看 author 的 followers 帖。"""
    c = ctx
    c.sess.add(
        HasnFollows(
            follower_hasn_id=c.author_hasn,
            target_type='human',
            target_hasn_id=c.stranger_hasn,
        )
    )
    await c.sess.flush()
    post_id = await _add_post(c, visibility='followers')

    assert await _can_see(c, post_id, c.stranger_uid) is False, '反向关注不该解锁'


# ==================== circle：圈成员才行 ====================


async def test_circle_post_in_private_circle_visible_to_active_member(ctx) -> None:
    """私密圈内帖：active 成员可见，作者可见。"""
    c = ctx
    post_id = await _add_post(c, visibility='circle', circle_id=c.private_circle_id)

    assert await _can_see(c, post_id, c.member_uid) is True
    assert await _can_see(c, post_id, c.author_uid) is True


async def test_circle_post_in_private_circle_hidden_from_outsider(ctx) -> None:
    """私密圈内帖：圈外人、未登录 404。"""
    c = ctx
    post_id = await _add_post(c, visibility='circle', circle_id=c.private_circle_id)

    assert await _can_see(c, post_id, c.outsider_uid) is False
    assert await _can_see(c, post_id, _ANONYMOUS) is False


async def test_circle_post_hidden_from_pending_member(ctx) -> None:
    """待批成员（status=pending）不是 active，看不到私密圈内容。"""
    c = ctx
    post_id = await _add_post(c, visibility='circle', circle_id=c.private_circle_id)

    assert await _can_see(c, post_id, c.pending_uid) is False, '仅 active 成员放行'


async def test_public_visibility_post_in_private_circle_still_gated(ctx) -> None:
    """**关键边界**：帖子写着 visibility=public，但挂在私密圈里 → 圈外人仍然看不到。

    圈子闸优先于 visibility，否则「私密圈」可被一条 public 帖直接击穿。
    """
    c = ctx
    post_id = await _add_post(c, visibility='public', circle_id=c.private_circle_id)

    assert await _can_see(c, post_id, c.outsider_uid) is False, '私密圈不能被 public 击穿'
    assert await _can_see(c, post_id, _ANONYMOUS) is False
    assert await _can_see(c, post_id, c.member_uid) is True


async def test_circle_post_in_public_circle_visible_to_all(ctx) -> None:
    """公开圈内帖：任何人（含未登录）可见——公开圈本就可浏览。"""
    c = ctx
    post_id = await _add_post(c, visibility='circle', circle_id=c.public_circle_id)

    assert await _can_see(c, post_id, c.outsider_uid) is True
    assert await _can_see(c, post_id, _ANONYMOUS) is True


async def test_circle_visibility_without_circle_id_is_denied(ctx) -> None:
    """脏数据：visibility=circle 却没挂 circle_id → 按最保守处理，非作者一律拒。"""
    c = ctx
    post_id = await _add_post(c, visibility='circle', circle_id=None)

    assert await _can_see(c, post_id, c.stranger_uid) is False
    assert await _can_see(c, post_id, _ANONYMOUS) is False
    assert await _can_see(c, post_id, c.author_uid) is True, '作者仍看得到自己的帖'


async def test_post_in_nonexistent_circle_is_denied(ctx) -> None:
    """挂了一个不存在的 circle_id → 拒（不能因为圈子查不到就放行）。"""
    c = ctx
    post_id = await _add_post(c, visibility='circle', circle_id=f'c_ghost_{_uid()}')

    assert await _can_see(c, post_id, c.stranger_uid) is False


async def test_post_in_blocked_circle_is_denied(ctx) -> None:
    """圈子被封禁（status=blocked）→ 圈内容对非作者不可见。"""
    c = ctx
    blocked_id = f'c_blocked_{_uid()}'
    c.sess.add(
        HasnCircles(
            circle_id=blocked_id,
            name='被封圈',
            slug=f'blocked-{_uid()}',
            owner_hasn_id=c.author_hasn,
            visibility='public',
            status='blocked',
        )
    )
    await c.sess.flush()
    post_id = await _add_post(c, visibility='circle', circle_id=blocked_id)

    assert await _can_see(c, post_id, c.stranger_uid) is False


# ==================== 未知 visibility：fail-closed ====================


async def test_legacy_workspace_group_is_fail_closed(ctx) -> None:
    """遗留取值 `workspace_group`（线上真实存在）：非作者一律拒，作者仍可见。

    它是 2026-06-03 迁移把旧 `circle` 改名的产物，语义**不是**公开。
    """
    c = ctx
    post_id = await _add_post(c, visibility='workspace_group')

    assert await _can_see(c, post_id, c.stranger_uid) is False
    assert await _can_see(c, post_id, _ANONYMOUS) is False
    assert await _can_see(c, post_id, c.author_uid) is True


async def test_unknown_visibility_value_is_fail_closed(ctx) -> None:
    """将来新增的、读取方还不认识的取值：默认拒绝，不能默默当公开。"""
    c = ctx
    post_id = await _add_post(c, visibility='some_future_scope')

    assert await _can_see(c, post_id, c.stranger_uid) is False
    assert await _can_see(c, post_id, _ANONYMOUS) is False


async def test_empty_visibility_is_fail_closed(ctx) -> None:
    """空串（ORM 侧 Python 默认值）同样 fail-closed。"""
    c = ctx
    post_id = await _add_post(c, visibility='')

    assert await _can_see(c, post_id, c.stranger_uid) is False


# ==================== status 回归：既有行为不被破坏 ====================


async def test_draft_still_visible_to_owner_regardless_of_visibility(ctx) -> None:
    """回归：分身待审草稿，主人仍能点开审核（status 闸先过，可见性闸不拦自己人）。"""
    c = ctx
    for visibility in ('public', 'private', 'followers', 'circle'):
        post_id = await _add_post(
            c, visibility=visibility, status='pending_review', agent_authored=True
        )
        assert await _can_see(c, post_id, c.owner_uid) is True, (
            f'主人必须能打开自己名下分身的待审帖（visibility={visibility}）'
        )


async def test_draft_still_hidden_from_others(ctx) -> None:
    """回归：他人草稿仍然 404，即使 visibility=public。"""
    c = ctx
    post_id = await _add_post(c, visibility='public', status='pending_review')

    assert await _can_see(c, post_id, c.stranger_uid) is False
    assert await _can_see(c, post_id, _ANONYMOUS) is False


async def test_deleted_still_hidden_from_everyone(ctx) -> None:
    """回归：deleted 一律 404，含作者本人，且不受 visibility 影响。"""
    c = ctx
    post_id = await _add_post(c, visibility='public', status='deleted')

    assert await _can_see(c, post_id, c.author_uid) is False
    assert await _can_see(c, post_id, c.stranger_uid) is False


async def test_missing_post_is_not_found(ctx) -> None:
    """回归：不存在的 post_id → 404（与「不可见」返回同一形状，不泄露存在性）。"""
    c = ctx
    assert await _can_see(c, f'p_missing_{_uid()}', c.stranger_uid) is False


# ==================== 与翻译接口的口径一致性 ====================


async def _translation_allows(c: _Ctx, post_id: str, viewer_hasn_id: str) -> bool:
    """翻译接口的判权面（`resolve_source` 只取原文+判权，不触发任何 LLM 调用）。"""
    service = ContentTranslationService()
    try:
        await service.resolve_source(
            c.sess,
            resource_kind='post',
            resource_id=post_id,
            fields=['content'],
            viewer_hasn_id=viewer_hasn_id,
        )
    except (errors.ForbiddenError, errors.NotFoundError):
        return False
    return True


async def test_detail_and_translation_verdicts_agree(ctx) -> None:
    """同一 viewer 对同一条已发布帖：详情能读 ⟺ 翻译能翻。

    这条守的就是「详情页能读、点翻译 403」那种错位不再复现。收紧前 private/followers
    帖在这里必然打架（详情放行、翻译拒绝）；收紧后两侧必须逐格一致。

    只比已发布帖：草稿两侧本就**故意**不同（详情让作者审草稿，翻译连作者都不给翻，
    理由是草稿反复编辑会把按需翻译变成按编辑次数翻译）。未登录也不比：翻译接口需要登录身份。
    """
    c = ctx
    viewers = [
        ('作者', c.author_uid, c.author_hasn),
        ('主人', c.owner_uid, c.owner_hasn),
        ('关注者', c.follower_uid, c.follower_hasn),
        ('陌生人', c.stranger_uid, c.stranger_hasn),
        ('圈内成员', c.member_uid, c.member_hasn),
        ('圈外人', c.outsider_uid, c.outsider_hasn),
    ]
    cases = [
        ('public', None, False),
        ('followers', None, False),
        ('private', None, False),
        ('circle', c.private_circle_id, False),
        ('circle', c.public_circle_id, False),
        ('public', c.private_circle_id, False),
        ('workspace_group', None, False),
        # 分身帖：校验「主人」这一路在两侧都放行
        ('private', None, True),
        ('followers', None, True),
    ]

    mismatches: list[str] = []
    for visibility, circle_id, agent_authored in cases:
        post_id = await _add_post(
            c, visibility=visibility, circle_id=circle_id, agent_authored=agent_authored
        )
        for label, user_id, hasn_id in viewers:
            detail_ok = await _can_see(c, post_id, user_id)
            translate_ok = await _translation_allows(c, post_id, hasn_id)
            if detail_ok != translate_ok:
                mismatches.append(
                    f'visibility={visibility} circle={"私密" if circle_id == c.private_circle_id else ("公开" if circle_id else "无")} '
                    f'agent={agent_authored} viewer={label}: 详情={detail_ok} 翻译={translate_ok}'
                )

    assert not mismatches, '详情与翻译口径不一致:\n' + '\n'.join(mismatches)
