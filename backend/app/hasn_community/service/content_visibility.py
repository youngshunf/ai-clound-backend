"""社区内容（帖子 / 文章）可见性判权的**唯一实现**。

## 为什么要有这个模块

`visibility` 列在每条创建路径上都写，却几乎没有读取方真正判它。此前只有三处读了：
搜索路径、话题流（且判得很粗）、以及 Agent 资源编解码器。而**帖子详情
`community_service.get_post` 对 `published` 帖完全不看 `visibility`**——任何人拿到
`post_id` 就能读到别人的 `private` / `followers` / `circle` 帖全文，未登录走
`/api/v1/community/open/posts/{post_id}` 同样能读。这是真实越权读取漏洞。

与此同时 `content_translation_service` 写了一套严格判权（`private`→403、`followers`
→查关注、`circle`→查圈成员），于是出现「详情页能读、点翻译 403」的错位。**错位的成因
是详情接口太宽，不是翻译接口太严。**

本模块把那套严格判据抽出来作为唯一事实源，详情接口与翻译接口都调它，**判据只有一份**，
不会再次漂移。

- 单条详情/写路径：调 :func:`evaluate_content_visibility`（或其抛错版
  :func:`assert_content_visible`）。
- 列表/聚合流路径：在查询层叠加 :func:`content_visibility_sql`——逐行调判官是 N+1，
  但判据仍只有一份，SQL 谓词是判官逻辑的投影，两边由一致性测试守着同步。

## 判据（对**已发布**内容而言；`status` 闸由各调用方自己把，见下）

| visibility | 谁能读 |
|---|---|
| `public` | 任何人（含未登录） |
| `followers` | 作者本人 /（分身帖的）主人 + 已关注作者的人 |
| `private` | 仅作者本人 /（分身帖的）主人 |
| `circle` | 作者本人 / 主人 + 该圈成员（圈私密时校验成员资格） |
| 其他值 | **一律拒绝**（fail-closed） |

另外：只要 `circle_id` 非空，无论 `visibility` 取值，都要先过「圈子可读」闸——
圈内容不该因为 `visibility` 恰好写着 `public` 就漏到圈外。

## 为什么 `status` 闸留在调用方

两侧对 `status` 的要求本就不同，**不能**合并：

- 详情接口：作者 / 主人**要能**打开自己的草稿、待审帖（分身发帖默认 `pending_review`，
  主人得点进去审核）。
- 翻译接口：草稿一律不给翻，**连作者自己也不行**——草稿反复编辑，每改一次
  `source_hash` 就变、缓存全失效，等于把「按需翻译」变成「按编辑次数翻译」。

强行合并会让其中一侧的语义被另一侧带偏，所以这里只管 `visibility`。

## fail-closed 的现实后果（有真实存量数据）

线上存在**第五个** `visibility` 取值 `workspace_group`：`2026-06-03-circle-columns.sql`
把旧的 `circle`（工作区组范围）改名让位给新的圈子概念，改名后没有任何读取方处理它。
它**不是** `public`，所以按 fail-closed 拒绝——这与翻译接口既有行为一致（那边也判 403）。
`visibility` 列既无 CHECK 约束也无入参白名单，任意字符串都能落库，fail-closed 是唯一
安全的兜底：别让一个没人认识的新枚举值默默变成「公开」。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, false, or_, select

from backend.app.hasn_community.model.hasn_circle_members import HasnCircleMembers
from backend.app.hasn_community.model.hasn_circles import HasnCircles
from backend.app.hasn_community.model.hasn_follows import HasnFollows
from backend.common.exception import errors
from backend.common.log import log

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql.elements import ColumnElement


class VisibilityDenialReason(StrEnum):
    """不可见的具体原因。

    调用方据此决定对外返回什么——**故意**不在本模块里硬编码 HTTP 状态码：
    详情接口统一 404（不泄露存在性），翻译接口沿用既有的 403 + 具体文案。
    """

    # 挂了 circle_id 但圈子不存在 / 已封禁
    CIRCLE_NOT_FOUND = 'circle_not_found'
    # 私密圈，viewer 不是 active 成员
    CIRCLE_NOT_MEMBER = 'circle_not_member'
    # visibility=followers，viewer 没关注作者
    FOLLOWERS_ONLY = 'followers_only'
    # visibility=private
    PRIVATE = 'private'
    # visibility=circle 却没挂 circle_id（脏数据，按最保守处理）
    CIRCLE_WITHOUT_ID = 'circle_without_id'
    # 不认识的 visibility 取值（如遗留的 workspace_group）
    UNKNOWN_VISIBILITY = 'unknown_visibility'


# 各拒绝原因对应的对外文案。翻译接口沿用这些文案（保持其既有行为逐字不变）；
# 详情接口不用，它一律回「帖子不存在」。
_DENIAL_MESSAGES: dict[VisibilityDenialReason, str] = {
    VisibilityDenialReason.CIRCLE_NOT_FOUND: '圈子不存在',
    VisibilityDenialReason.CIRCLE_NOT_MEMBER: '私密圈内容仅成员可见',
    VisibilityDenialReason.FOLLOWERS_ONLY: '该内容仅作者的关注者可见',
    VisibilityDenialReason.PRIVATE: '该内容为私密内容',
    VisibilityDenialReason.CIRCLE_WITHOUT_ID: '该内容为圈子内容',
    VisibilityDenialReason.UNKNOWN_VISIBILITY: '该内容不可见',
}

# 「资源不存在」类拒绝：调用方若要区分 404/403，这些算 404。
_NOT_FOUND_REASONS = frozenset({VisibilityDenialReason.CIRCLE_NOT_FOUND})


@dataclass(frozen=True, slots=True)
class ContentVisibilityDecision:
    """可见性判定结果。`allowed=True` 时 `reason` 为 None。"""

    allowed: bool
    reason: VisibilityDenialReason | None = None

    @property
    def message(self) -> str:
        """对外文案（仅在拒绝时有意义）。"""
        if self.reason is None:
            return ''
        return _DENIAL_MESSAGES[self.reason]

    @property
    def is_not_found(self) -> bool:
        """该拒绝原因是否属于「资源不存在」（对应 404）。"""
        return self.reason in _NOT_FOUND_REASONS


_VISIBLE = ContentVisibilityDecision(allowed=True)


def is_own_content(
    *, viewer_hasn_id: str | None, author_hasn_id: str | None, owner_hasn_id: str | None
) -> bool:
    """viewer 是否是内容的作者本人或（分身内容的）责任主体主人。

    必须先判 viewer 非空再比对：`owner_hasn_id` / `author_hasn_id` 在脏数据里可能是空串，
    而未登录 viewer 解析出来也是 None/空串，直接做集合命中会把匿名用户判成作者。
    """
    if not viewer_hasn_id:
        return False
    return viewer_hasn_id in {author_hasn_id, owner_hasn_id}


async def is_following(db: AsyncSession, *, follower: str | None, target: str | None) -> bool:
    """follower 是否关注了 target。

    `hasn_follows` 无 `follower_type` 列，故只按 id 对匹配（与既有各处判法一致）。
    """
    if not follower or not target:
        return False
    hit = (
        await db.execute(
            select(HasnFollows.id).where(
                HasnFollows.follower_hasn_id == follower,
                HasnFollows.target_hasn_id == target,
            )
        )
    ).scalar_one_or_none()
    return hit is not None


async def is_active_circle_member(
    db: AsyncSession, *, circle_id: str, viewer_hasn_id: str | None
) -> bool:
    """viewer 是否是该圈的 active 成员（pending/banned/left 都不算）。"""
    if not viewer_hasn_id:
        return False
    member = (
        await db.execute(
            select(HasnCircleMembers.status).where(
                HasnCircleMembers.circle_id == circle_id,
                HasnCircleMembers.member_hasn_id == viewer_hasn_id,
            )
        )
    ).scalar_one_or_none()
    return member == 'active'


async def _evaluate_circle_readable(
    db: AsyncSession, *, circle_id: str, viewer_hasn_id: str | None
) -> ContentVisibilityDecision:
    """圈子可读闸：圈不存在/已封禁 → 不存在；私密圈非成员 → 拒绝。"""
    circle = (
        await db.execute(select(HasnCircles).where(HasnCircles.circle_id == circle_id))
    ).scalar_one_or_none()
    if circle is None or circle.status == 'blocked':
        return ContentVisibilityDecision(False, VisibilityDenialReason.CIRCLE_NOT_FOUND)
    if circle.visibility == 'private' and not await is_active_circle_member(
        db, circle_id=circle_id, viewer_hasn_id=viewer_hasn_id
    ):
        return ContentVisibilityDecision(False, VisibilityDenialReason.CIRCLE_NOT_MEMBER)
    return _VISIBLE


async def evaluate_content_visibility(
    db: AsyncSession,
    *,
    visibility: str,
    author_hasn_id: str | None,
    owner_hasn_id: str | None,
    circle_id: str | None,
    viewer_hasn_id: str | None,
) -> ContentVisibilityDecision:
    """判定 viewer 能否看到这条**已发布**内容。

    调用方须**自行**把好 `status` 闸（草稿/删除等）后再调本函数，理由见模块文档。

    :param visibility: 内容的 `visibility` 列取值
    :param author_hasn_id: 作者 hasn_id
    :param owner_hasn_id: 责任主体 hasn_id（分身内容 = 主人；真人内容 = 作者自己）
    :param circle_id: 所属圈子 circle_id，NULL 表示主社区/公共流
    :param viewer_hasn_id: 当前查看者的 human hasn_id；未登录传 None
    :return: 判定结果
    """
    # 作者与责任主体永远看得到自己的东西，且不受圈子闸约束（退圈后仍能看自己发的）。
    if is_own_content(
        viewer_hasn_id=viewer_hasn_id,
        author_hasn_id=author_hasn_id,
        owner_hasn_id=owner_hasn_id,
    ):
        return _VISIBLE

    # 圈内容先过圈子闸：不能因为 visibility 恰好写着 public 就漏到圈外。
    if circle_id:
        circle_decision = await _evaluate_circle_readable(
            db, circle_id=circle_id, viewer_hasn_id=viewer_hasn_id
        )
        if not circle_decision.allowed:
            return circle_decision

    if visibility == 'public':
        return _VISIBLE
    if visibility == 'followers':
        if await is_following(db, follower=viewer_hasn_id, target=author_hasn_id):
            return _VISIBLE
        return ContentVisibilityDecision(False, VisibilityDenialReason.FOLLOWERS_ONLY)
    if visibility == 'private':
        return ContentVisibilityDecision(False, VisibilityDenialReason.PRIVATE)
    if visibility == 'circle':
        # visibility=circle 但没挂 circle_id 属于脏数据，按最保守处理。
        # 挂了 circle_id 的已在上面过完圈子闸，此处放行。
        if not circle_id:
            return ContentVisibilityDecision(False, VisibilityDenialReason.CIRCLE_WITHOUT_ID)
        return _VISIBLE

    # 不认识的取值一律拒绝（fail-closed），别让新枚举值默默变成公开。
    # 记 warn 而非 error：这是数据漂移信号（如遗留的 workspace_group），
    # 请求本身已被安全地拒掉，服务可继续。
    log.warning(
        f'[community-visibility] 未知 visibility 取值，已 fail-closed 拒绝: '
        f'visibility={visibility!r} author={author_hasn_id!r} circle_id={circle_id!r}'
    )
    return ContentVisibilityDecision(False, VisibilityDenialReason.UNKNOWN_VISIBILITY)


def content_visibility_sql(
    content_model: type[Any],
    *,
    viewer_hasn_id: str | None,
) -> ColumnElement[bool]:
    """与 :func:`evaluate_content_visibility` 同一判据的 SQL 谓词，供列表/聚合流在查询层过滤。

    列表路径逐行调判官是 N+1（feed 一页 20 条就是 20+ 次额外查询），但**判据仍必须只有
    一份**——本函数是判官逻辑向 SQL 的投影，任何一边改动必须同步另一边，
    `test_post_visibility.py` 里有逐行一致性用例守着。

    判据投影（与判官逐条对应）：

    - 作者本人 / 责任主体（`owner_hasn_id`）恒可见，且不受圈闸约束；
    - 挂了 `circle_id` 的内容先过圈闸：圈存在、未封禁、私密圈限 active 成员；
    - `public` 任何人可见；`circle` 挂在圈内即可（圈闸已过）；`followers` 需 viewer 已关注作者；
    - `private` 与任何不认识的取值一律排除（fail-closed）；
    - 匿名 viewer（None）：只剩 `public` + 公开圈里的 `circle` 内容。

    :param content_model: 带 `author_hasn_id`/`owner_hasn_id`/`visibility`/`circle_id` 列的模型
    :param viewer_hasn_id: 当前查看者的 human hasn_id；未登录传 None
    :return: 可直接 `stmt.where(...)` 的 SQL 条件
    """
    # 圈闸：未挂圈（NULL 或空串，判官以真值判定）或 圈可读。
    # 私密圈的成员子查询只在 viewer 非空时有意义，匿名直接 false()。
    circle_gate_visibility: ColumnElement[bool] = (
        HasnCircles.circle_id.in_(
            select(HasnCircleMembers.circle_id).where(
                HasnCircleMembers.member_hasn_id == viewer_hasn_id,
                HasnCircleMembers.status == 'active',
            )
        )
        if viewer_hasn_id
        else false()
    )
    readable_circles = select(HasnCircles.circle_id).where(
        HasnCircles.status != 'blocked',
        or_(
            HasnCircles.visibility != 'private',
            circle_gate_visibility,
        ),
    )
    circle_gate = or_(
        content_model.circle_id.is_(None),
        content_model.circle_id == '',
        content_model.circle_id.in_(readable_circles),
    )

    # 可见性闸：public 任何人；circle 需真实挂圈（空串/NULL 是脏数据，排除，对齐判官）。
    visibility_gate: ColumnElement[bool] = or_(
        content_model.visibility == 'public',
        and_(
            content_model.visibility == 'circle',
            content_model.circle_id.is_not(None),
            content_model.circle_id != '',
        ),
    )
    own: ColumnElement[bool] = false()
    if viewer_hasn_id:
        followed_authors = select(HasnFollows.target_hasn_id).where(
            HasnFollows.follower_hasn_id == viewer_hasn_id
        )
        visibility_gate = or_(
            visibility_gate,
            and_(
                content_model.visibility == 'followers',
                content_model.author_hasn_id.in_(followed_authors),
            ),
        )
        own = or_(
            content_model.author_hasn_id == viewer_hasn_id,
            content_model.owner_hasn_id == viewer_hasn_id,
        )

    # private 与不认识的取值不在任何分支里——fail-closed 与判官一致。
    return or_(own, and_(circle_gate, visibility_gate))


async def assert_content_visible(
    db: AsyncSession,
    *,
    visibility: str,
    author_hasn_id: str | None,
    owner_hasn_id: str | None,
    circle_id: str | None,
    viewer_hasn_id: str | None,
) -> None:
    """`evaluate_content_visibility` 的抛错版：不可见时抛 404/403。

    「资源不存在」类原因抛 `NotFoundError`，其余抛 `ForbiddenError` 并带具体文案。
    翻译接口用这个入口（保持其既有 403 + 文案逐字不变）；帖子详情**不用**它，
    详情一律回 404「帖子不存在」以免变成存在性探测器。
    """
    decision = await evaluate_content_visibility(
        db,
        visibility=visibility,
        author_hasn_id=author_hasn_id,
        owner_hasn_id=owner_hasn_id,
        circle_id=circle_id,
        viewer_hasn_id=viewer_hasn_id,
    )
    if decision.allowed:
        return
    if decision.is_not_found:
        raise errors.NotFoundError(msg=decision.message)
    raise errors.ForbiddenError(msg=decision.message)
