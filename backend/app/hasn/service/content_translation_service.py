"""用户内容按需翻译（国际化轨道 B · 公共内容）。

设计事实源：`docs/hasn-node设计文档/国际化与多语言/00-国际化与多语言总体设计.md` §4。

三条不能妥协的性质，读代码前先记住：

1. **服务端自己取原文，不信客户端传的原文。** 否则这个接口就是一台免费翻译器，
   任何人都能拿它翻任意文本，成本由平台承担。客户端只能传「资源 ID + 字段名」。
2. **译文是视图。** 只写 `hasn_content_translations`，绝不回写 `hasn_posts.content`。
   缓存键含 `source_hash`，作者改了原文旧译文自然失效，不会出现译文对不上原文的鬼影。
3. **失败就是失败。** 网关挂了、结构校验没过，一律抛错让 UI 显示「翻译失败，重试」，
   **绝不返回原文冒充译文**（零 fake）。用户点了翻译却拿到一模一样的中文，比报错更糟。

判权口径比社区详情接口**更严**：只有「已发布 + 当前用户确实可见」的资源可翻，
草稿、私密帖、私密圈内容一律 403。这是设计 §4.6 明确要求的（"只有已发布且当前用户
可见的资源可翻"）。注意这意味着存在「详情接口能读到、翻译接口 403」的情形——因为
`community_service.get_post` 目前对 published 帖子完全不看 visibility，是既有实现的宽口径。
"""

from __future__ import annotations

import asyncio

from dataclasses import dataclass
from typing import Any, Callable, Coroutine, Final

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.model.hasn_content_translations import HasnContentTranslations
from backend.common.exception import errors
from backend.common.llm import LLMChatClient
from backend.common.log import log
from backend.common.response.response_code import StandardResponseCode
from backend.common.translation import (
    ContentTranslator,
    TranslationError,
    detect_language,
    is_same_language,
    normalize_language,
    source_hash,
)
from backend.core.conf import settings
from backend.database.db import async_db_session
from backend.database.redis import redis_client

# 资源类型 → 允许翻译的字段白名单。**不在白名单里的字段一律拒绝**，
# 防止有人拿 field 参数把任意列（如别人的 contact_policy）读出来当翻译输入。
RESOURCE_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    'post': ('content',),
    'article': ('title', 'summary', 'content'),
    'comment': ('content',),
    'circle': ('name', 'description'),
    'profile': ('bio',),
}

# 并发首译收敛锁的等待策略：拿不到锁的请求轮询缓存，等首译者写完直接读。
_LOCK_POLL_INTERVAL = 0.25
_LOCK_MAX_WAIT = 25.0


@dataclass(slots=True)
class ResolvedSource:
    """从权威表取到的、已判权通过的原文。"""

    #: 字段名 → 原文
    fields: dict[str, str]


@dataclass(slots=True)
class FieldTranslation:
    """单个字段的翻译结果。"""

    text: str
    cached: bool
    skipped: bool = False


@dataclass(slots=True)
class _FieldOutcome:
    """单字段翻译的内部返回：译文 + 检测到的源语言。"""

    translation: FieldTranslation
    source_lang: str


class ContentTranslationService:
    """内容翻译服务（单例，模块级 `content_translation_service` 暴露）。"""

    def __init__(self) -> None:
        self._translator: ContentTranslator | None = None

    # ------------------------------------------------------------------
    # LLM 客户端
    # ------------------------------------------------------------------
    def _get_translator(self) -> ContentTranslator:
        """惰性构造翻译器（避免 import 期就读 settings）。"""
        if self._translator is None:
            self._translator = ContentTranslator(
                LLMChatClient(model=settings.CONTENT_TRANSLATION_MODEL, timeout=180.0)
            )
        return self._translator

    @property
    def engine_version(self) -> str:
        return settings.CONTENT_TRANSLATION_ENGINE_VERSION

    # ------------------------------------------------------------------
    # 限速
    # ------------------------------------------------------------------
    async def check_rate_limit(self, viewer_hasn_id: str) -> None:
        """单主人 N 次/分钟。超限 429 并记 `warn`（4xx 可自愈，不是 error）。

        Redis 抖动时 fail-open：限速是防滥用，不是判权，不该因为缓存抖动就拒绝正常用户。
        """
        limit = settings.CONTENT_TRANSLATION_RATE_LIMIT_PER_MIN
        try:
            key = f'content_translate:rl:{viewer_hasn_id}'
            count = await redis_client.incr(key)
            if count == 1:
                await redis_client.expire(key, 60)
            if count > limit:
                log.warning(f'[content-translate] 主人 {viewer_hasn_id} 触发限速（{count}/{limit} 每分钟）')
                raise errors.RequestError(
                    code=StandardResponseCode.HTTP_429,
                    msg=f'翻译请求过于频繁（每分钟上限 {limit} 次），请稍后再试',
                )
        except errors.RequestError:
            raise
        except Exception as exc:
            log.warning(f'[content-translate] 限速计数异常，fail-open 放行: {exc}')

    # ------------------------------------------------------------------
    # 取原文 + 判权
    # ------------------------------------------------------------------
    async def resolve_source(
        self,
        db: AsyncSession,
        *,
        resource_kind: str,
        resource_id: str,
        fields: list[str],
        viewer_hasn_id: str,
    ) -> ResolvedSource:
        """按 `resource_kind` 取权威原文并判权；读不到/看不到一律抛错。"""
        allowed = RESOURCE_FIELDS.get(resource_kind)
        if allowed is None:
            raise errors.RequestError(msg=f'不支持的资源类型: {resource_kind}')

        requested = [field for field in fields if field]
        if not requested:
            raise errors.RequestError(msg='fields 不能为空')
        invalid = [field for field in requested if field not in allowed]
        if invalid:
            raise errors.RequestError(
                msg=f'{resource_kind} 不支持翻译字段 {invalid}（可选: {list(allowed)}）'
            )

        resolver = _RESOLVERS[resource_kind]
        return await resolver(self, db, resource_id, requested, viewer_hasn_id)

    # -- 各资源的 resolver --------------------------------------------

    async def _resolve_post(
        self, db: AsyncSession, resource_id: str, fields: list[str], viewer_hasn_id: str
    ) -> ResolvedSource:
        from backend.app.hasn_community.model.hasn_posts import HasnPosts

        post = (
            await db.execute(select(HasnPosts).where(HasnPosts.post_id == resource_id))
        ).scalar_one_or_none()
        if post is None:
            raise errors.NotFoundError(msg='帖子不存在')
        await self._assert_content_visible(
            db,
            status=post.status,
            visibility=post.visibility,
            author_hasn_id=post.author_hasn_id,
            owner_hasn_id=post.owner_hasn_id,
            circle_id=post.circle_id,
            viewer_hasn_id=viewer_hasn_id,
            not_found_msg='帖子不存在',
        )
        return ResolvedSource(fields={field: getattr(post, field) or '' for field in fields})

    async def _resolve_article(
        self, db: AsyncSession, resource_id: str, fields: list[str], viewer_hasn_id: str
    ) -> ResolvedSource:
        from backend.app.hasn_community.model.hasn_articles import HasnArticles

        article = (
            await db.execute(select(HasnArticles).where(HasnArticles.article_id == resource_id))
        ).scalar_one_or_none()
        if article is None:
            raise errors.NotFoundError(msg='文章不存在')
        await self._assert_content_visible(
            db,
            status=article.status,
            visibility=article.visibility,
            author_hasn_id=article.author_hasn_id,
            owner_hasn_id=article.owner_hasn_id,
            circle_id=article.circle_id,
            viewer_hasn_id=viewer_hasn_id,
            not_found_msg='文章不存在',
        )
        # summary 取库里的值，不取 effective_summary 的正文抽取兜底：抽取值会随正文漂，
        # 拿它算 source_hash 会让「摘要没改却因为正文改了而失效」，白烧一次翻译。
        return ResolvedSource(fields={field: getattr(article, field) or '' for field in fields})

    async def _resolve_comment(
        self, db: AsyncSession, resource_id: str, fields: list[str], viewer_hasn_id: str
    ) -> ResolvedSource:
        from backend.app.hasn_community.model.hasn_comments import HasnComments

        comment = (
            await db.execute(select(HasnComments).where(HasnComments.comment_id == resource_id))
        ).scalar_one_or_none()
        if comment is None:
            raise errors.NotFoundError(msg='评论不存在')
        if comment.status != 'visible':
            raise errors.NotFoundError(msg='评论不存在')
        # 评论没有自己的可见性，**继承宿主**：看不到帖子就不该翻它下面的评论。
        host_kind = 'post' if comment.target_type == 'post' else 'article'
        host_resolver = _RESOLVERS[host_kind]
        await host_resolver(self, db, comment.target_id, [], viewer_hasn_id)
        return ResolvedSource(fields={field: getattr(comment, field) or '' for field in fields})

    async def _resolve_circle(
        self, db: AsyncSession, resource_id: str, fields: list[str], viewer_hasn_id: str
    ) -> ResolvedSource:
        from backend.app.hasn_community.model.hasn_circles import HasnCircles

        circle = (
            await db.execute(select(HasnCircles).where(HasnCircles.circle_id == resource_id))
        ).scalar_one_or_none()
        if circle is None or circle.status == 'blocked':
            raise errors.NotFoundError(msg='圈子不存在')
        if circle.visibility == 'private' and not await self._is_active_circle_member(
            db, circle.circle_id, viewer_hasn_id
        ):
            raise errors.ForbiddenError(msg='私密圈内容仅成员可见')
        return ResolvedSource(fields={field: getattr(circle, field) or '' for field in fields})

    async def _resolve_profile(
        self, db: AsyncSession, resource_id: str, fields: list[str], viewer_hasn_id: str
    ) -> ResolvedSource:
        from backend.app.hasn.model.hasn_humans import HasnHumans

        human = (
            await db.execute(select(HasnHumans).where(HasnHumans.hasn_id == resource_id))
        ).scalar_one_or_none()
        if human is None or human.status == 'deleted':
            raise errors.NotFoundError(msg='用户不存在')
        return ResolvedSource(fields={field: getattr(human, field) or '' for field in fields})

    # -- 可见性闸 ------------------------------------------------------

    async def _assert_content_visible(
        self,
        db: AsyncSession,
        *,
        status: str,
        visibility: str,
        author_hasn_id: str,
        owner_hasn_id: str,
        circle_id: str | None,
        viewer_hasn_id: str,
        not_found_msg: str,
    ) -> None:
        """帖子/文章的可见性闸：已发布 + 当前用户确实看得到，才准翻。"""
        # 作者与责任主体永远看得到自己的东西（但仍要求已发布，草稿不给翻——见下）。
        is_own = viewer_hasn_id in {author_hasn_id, owner_hasn_id}

        if status != 'published':
            # 草稿/待审/隐藏/已删：一律不给翻，连作者自己也不行。
            # 理由是成本——草稿会被反复编辑，每改一次 source_hash 就变、缓存全失效，
            # 等于把「按需翻译」变成「按编辑次数翻译」。发布后再翻。
            raise errors.NotFoundError(msg=not_found_msg)

        if is_own:
            return

        if circle_id:
            await self._assert_circle_readable(db, circle_id, viewer_hasn_id)

        if visibility == 'public':
            return
        if visibility == 'followers':
            if not await self._is_following(db, follower=viewer_hasn_id, target=author_hasn_id):
                raise errors.ForbiddenError(msg='该内容仅作者的关注者可见')
            return
        if visibility == 'private':
            raise errors.ForbiddenError(msg='该内容为私密内容')
        if visibility == 'circle':
            # visibility=circle 但没挂 circle_id 属于脏数据，按最保守处理。
            if not circle_id:
                raise errors.ForbiddenError(msg='该内容为圈子内容')
            return
        # 未知 visibility 一律拒绝（fail-closed，别让新枚举值默默变成公开）。
        raise errors.ForbiddenError(msg='该内容不可见')

    async def _assert_circle_readable(
        self, db: AsyncSession, circle_id: str, viewer_hasn_id: str
    ) -> None:
        from backend.app.hasn_community.model.hasn_circles import HasnCircles

        circle = (
            await db.execute(select(HasnCircles).where(HasnCircles.circle_id == circle_id))
        ).scalar_one_or_none()
        if circle is None or circle.status == 'blocked':
            raise errors.NotFoundError(msg='圈子不存在')
        if circle.visibility == 'private' and not await self._is_active_circle_member(
            db, circle_id, viewer_hasn_id
        ):
            raise errors.ForbiddenError(msg='私密圈内容仅成员可见')

    @staticmethod
    async def _is_active_circle_member(db: AsyncSession, circle_id: str, viewer_hasn_id: str) -> bool:
        from backend.app.hasn_community.model.hasn_circle_members import HasnCircleMembers

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

    @staticmethod
    async def _is_following(db: AsyncSession, *, follower: str, target: str) -> bool:
        from backend.app.hasn_community.model.hasn_follows import HasnFollows

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

    # ------------------------------------------------------------------
    # 缓存
    # ------------------------------------------------------------------
    async def _read_cache(
        self,
        db: AsyncSession,
        *,
        resource_kind: str,
        resource_id: str,
        field: str,
        target_lang: str,
        text_hash: str,
    ) -> HasnContentTranslations | None:
        return (
            await db.execute(
                select(HasnContentTranslations).where(
                    HasnContentTranslations.resource_kind == resource_kind,
                    HasnContentTranslations.resource_id == resource_id,
                    HasnContentTranslations.field == field,
                    HasnContentTranslations.target_lang == target_lang,
                    HasnContentTranslations.source_hash == text_hash,
                    HasnContentTranslations.engine_version == self.engine_version,
                )
            )
        ).scalar_one_or_none()

    async def _bump_hit_count(self, row_id: int) -> None:
        """命中计数 +1。**独立事务、best-effort**：这是观测指标，不该拖垮读路径。"""
        try:
            async with async_db_session.begin() as db:
                await db.execute(
                    update(HasnContentTranslations)
                    .where(HasnContentTranslations.id == row_id)
                    .values(hit_count=HasnContentTranslations.hit_count + 1)
                )
        except Exception as exc:
            log.warning(f'[content-translate] hit_count 自增失败（不影响返回译文）: {exc}')

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    async def translate_resource(
        self,
        db: AsyncSession,
        *,
        resource_kind: str,
        resource_id: str,
        fields: list[str],
        target_lang: str,
        viewer_hasn_id: str,
    ) -> dict[str, Any]:
        """翻译一个资源的若干字段，返回 API 契约形状的 dict。"""
        target = normalize_language(target_lang)
        if not target:
            raise errors.RequestError(msg='target_lang 不能为空')

        await self.check_rate_limit(viewer_hasn_id)

        resolved = await self.resolve_source(
            db,
            resource_kind=resource_kind,
            resource_id=resource_id,
            fields=fields,
            viewer_hasn_id=viewer_hasn_id,
        )

        max_chars = settings.CONTENT_TRANSLATION_MAX_CHARS
        total_chars = sum(len(text) for text in resolved.fields.values())
        if total_chars > max_chars:
            raise errors.RequestError(msg=f'待翻文本过长（{total_chars} 字，上限 {max_chars} 字）')

        results: dict[str, FieldTranslation] = {}
        detected_langs: list[str] = []
        for field, text in resolved.fields.items():
            outcome = await self._translate_field(
                db,
                resource_kind=resource_kind,
                resource_id=resource_id,
                field=field,
                text=text,
                target_lang=target,
            )
            results[field] = outcome.translation
            if outcome.source_lang:
                detected_langs.append(outcome.source_lang)

        # 整体 skipped：所有字段都因「源语言==目标语言」被跳过。
        all_skipped = bool(results) and all(item.skipped for item in results.values())
        return {
            'resource_kind': resource_kind,
            'resource_id': resource_id,
            'target_lang': target,
            'source_lang': detected_langs[0] if detected_langs else '',
            'cached': bool(results) and all(item.cached or item.skipped for item in results.values()),
            'skipped': all_skipped,
            'fields': {field: item.text for field, item in results.items()},
            'engine': settings.CONTENT_TRANSLATION_MODEL,
            'engine_version': self.engine_version,
            'charge_policy': settings.CONTENT_TRANSLATION_CHARGE_POLICY,
        }

    async def _translate_field(
        self,
        db: AsyncSession,
        *,
        resource_kind: str,
        resource_id: str,
        field: str,
        text: str,
        target_lang: str,
    ) -> _FieldOutcome:
        """单字段：空 → 原样；同语言 → 跳过；查缓存 → 命中即返回；未命中 → 锁 + 翻 + 写缓存。"""
        if not text.strip():
            # 空字段没什么可翻的，原样回，也不写缓存（省得给空串占一行）。
            return _FieldOutcome(FieldTranslation(text=text, cached=False, skipped=True), '')

        detected = detect_language(text)
        if detected and is_same_language(detected, target_lang):
            # 源语言就是目标语言：直接回原文并标 skipped，不调 LLM。
            # 注意这里 `skipped=True` 明确告诉客户端「没翻，因为不用翻」，
            # 与「翻译失败回落原文」是完全不同的语义（后者我们根本不做）。
            return _FieldOutcome(FieldTranslation(text=text, cached=False, skipped=True), detected)

        text_hash = source_hash(text)
        cached = await self._read_cache(
            db,
            resource_kind=resource_kind,
            resource_id=resource_id,
            field=field,
            target_lang=target_lang,
            text_hash=text_hash,
        )
        if cached is not None:
            await self._bump_hit_count(cached.id)
            return _FieldOutcome(
                FieldTranslation(text=cached.translated_text, cached=True), cached.source_lang
            )

        return await self._translate_and_cache(
            db,
            resource_kind=resource_kind,
            resource_id=resource_id,
            field=field,
            text=text,
            text_hash=text_hash,
            source_lang=detected,
            target_lang=target_lang,
        )

    async def _translate_and_cache(
        self,
        db: AsyncSession,
        *,
        resource_kind: str,
        resource_id: str,
        field: str,
        text: str,
        text_hash: str,
        source_lang: str,
        target_lang: str,
    ) -> _FieldOutcome:
        """缓存未命中：抢 Redis 短锁，抢到的真翻，没抢到的等首译者写完读缓存。

        这一步是「热帖被 N 个人同时点开翻译」时唯一挡住 N 次付费调用的地方，两个细节
        决定它到底管不管用（都是实测踩出来的，别改）：

        1. **拿到锁后必须再查一次缓存**（double-checked locking）。并发请求不是同一瞬间
           到达锁的：先到者翻完、释放锁之后，后到者才刚开始抢锁，于是抢到一把空闲的锁，
           又翻一遍。实测 10 并发有 8 个走到这条路径，收敛完全失效。补上二次查缓存后，
           无论到达顺序如何都只翻一次。
        2. **锁必须持有到译文写进缓存之后再放**。锁的语义是「有人正在生产这条译文」，
           在译文可见之前就放锁，等于告诉后来者「没人在做」，前一条的问题会再次出现。
        """
        lock_key = f'content_translate:lock:{text_hash}:{target_lang}'
        acquired = await self._acquire_lock(lock_key)

        if not acquired:
            waited = await self._wait_for_peer_translation(
                db,
                resource_kind=resource_kind,
                resource_id=resource_id,
                field=field,
                target_lang=target_lang,
                text_hash=text_hash,
            )
            if waited is not None:
                await self._bump_hit_count(waited.id)
                return _FieldOutcome(
                    FieldTranslation(text=waited.translated_text, cached=True), waited.source_lang
                )
            # 首译者超时也没写出来（多半它自己失败了）：自己翻一次，别把用户干等死。
            log.warning(f'[content-translate] 等待首译超时，改为自行翻译 key={lock_key}')

        try:
            # 二次查缓存：可能在我们抢锁的间隙里，先到者已经翻完并写好了。
            existing = await self._reread_cache(
                db,
                resource_kind=resource_kind,
                resource_id=resource_id,
                field=field,
                target_lang=target_lang,
                text_hash=text_hash,
            )
            if existing is not None:
                await self._bump_hit_count(existing.id)
                return _FieldOutcome(
                    FieldTranslation(text=existing.translated_text, cached=True), existing.source_lang
                )

            try:
                outcome = await self._get_translator().translate_markdown(
                    text, source_lang=source_lang, target_lang=target_lang
                )
            except TranslationError as exc:
                # 显式失败：把它变成 502，UI 显示「翻译失败，重试」。**不回落原文。**
                log.warning(f'[content-translate] {resource_kind}/{resource_id}.{field} 翻译失败: {exc}')
                raise errors.GatewayError(msg=f'翻译失败：{exc}') from exc

            await self._write_cache(
                resource_kind=resource_kind,
                resource_id=resource_id,
                field=field,
                source_lang=source_lang,
                target_lang=target_lang,
                text_hash=text_hash,
                translated_text=outcome.text,
                engine=outcome.engine,
                token_usage=outcome.token_usage,
            )
        finally:
            # 写完缓存才放锁：译文可见之前放锁，后来者会以为没人在做而重复翻译。
            if acquired:
                await self._release_lock(lock_key)

        return _FieldOutcome(FieldTranslation(text=outcome.text, cached=False), source_lang)

    async def _reread_cache(
        self,
        db: AsyncSession,
        *,
        resource_kind: str,
        resource_id: str,
        field: str,
        target_lang: str,
        text_hash: str,
    ) -> HasnContentTranslations | None:
        """结束当前事务快照后再查一次缓存（READ COMMITTED 下才看得见别人刚提交的行）。"""
        try:
            await db.rollback()
            return await self._read_cache(
                db,
                resource_kind=resource_kind,
                resource_id=resource_id,
                field=field,
                target_lang=target_lang,
                text_hash=text_hash,
            )
        except Exception as exc:
            # 查不了就当没命中，最坏情况是多翻一次，不该因此让用户拿不到译文。
            log.warning(f'[content-translate] 二次查缓存异常，按未命中处理: {exc}')
            return None

    async def _write_cache(
        self,
        *,
        resource_kind: str,
        resource_id: str,
        field: str,
        source_lang: str,
        target_lang: str,
        text_hash: str,
        translated_text: str,
        engine: str,
        token_usage: int,
    ) -> None:
        """写译文缓存（独立事务）。撞唯一键说明别人刚写过同一条，忽略即可。"""
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        try:
            async with async_db_session.begin() as db:
                await db.execute(
                    pg_insert(HasnContentTranslations)
                    .values(
                        resource_kind=resource_kind,
                        resource_id=resource_id,
                        field=field,
                        source_lang=source_lang,
                        target_lang=target_lang,
                        source_hash=text_hash,
                        translated_text=translated_text,
                        engine=engine,
                        engine_version=self.engine_version,
                        token_usage=token_usage,
                        hit_count=0,
                    )
                    # 唯一键是并发首译的最后一道防线：撞了就当对方赢，不覆盖也不报错。
                    .on_conflict_do_nothing(index_elements=[
                        'resource_kind', 'resource_id', 'field', 'target_lang', 'source_hash', 'engine_version',
                    ])
                )
        except Exception as exc:
            # 译文已经翻出来了，缓存没落库只是下次要重翻——按日志规范这是 warn 不是 error。
            log.warning(f'[content-translate] 译文缓存写入失败（本次仍正常返回译文）: {exc}')

    # ------------------------------------------------------------------
    # 并发首译收敛
    # ------------------------------------------------------------------
    @staticmethod
    async def _acquire_lock(key: str) -> bool:
        """抢短锁。Redis 不可用时返回 True（fail-open：宁可多翻一次，不能不给翻）。"""
        try:
            got = await redis_client.set(key, '1', ex=settings.CONTENT_TRANSLATION_LOCK_TTL, nx=True)
            return bool(got)
        except Exception as exc:
            log.warning(f'[content-translate] 抢锁异常，fail-open 直接翻译: {exc}')
            return True

    @staticmethod
    async def _release_lock(key: str) -> None:
        try:
            await redis_client.delete(key)
        except Exception as exc:
            log.warning(f'[content-translate] 释放锁失败（会随 TTL 自动过期）: {exc}')

    async def _wait_for_peer_translation(
        self,
        db: AsyncSession,
        *,
        resource_kind: str,
        resource_id: str,
        field: str,
        target_lang: str,
        text_hash: str,
    ) -> HasnContentTranslations | None:
        """没抢到锁：轮询缓存，等首译者写完。超时返回 None。

        **复用调用方的会话，不另开连接。** 这一点很要命：等待发生的时刻正是「热帖被一群人
        同时点开」的时刻，此时每个等待者再多占一条连接，会直接把连接池抽干
        （生产 pool_size 只有个位数），于是所有等待者都拿不到连接、退化成各自翻一遍——
        恰好是这把锁本来要防的事。

        每轮先 `rollback()` 结束当前事务快照再读：PostgreSQL 默认 READ COMMITTED，
        新事务才看得见首译者刚提交的那行。

        读异常只记 warn 并**继续轮询**，不提前 return——提前退出等于放弃收敛、多付一次翻译。
        """
        elapsed = 0.0
        while elapsed < _LOCK_MAX_WAIT:
            await asyncio.sleep(_LOCK_POLL_INTERVAL)
            elapsed += _LOCK_POLL_INTERVAL
            try:
                # 结束上一轮的事务快照，否则始终读到进入等待那一刻的旧视图。
                await db.rollback()
                row = await self._read_cache(
                    db,
                    resource_kind=resource_kind,
                    resource_id=resource_id,
                    field=field,
                    target_lang=target_lang,
                    text_hash=text_hash,
                )
                if row is not None:
                    return row
            except Exception as exc:
                log.warning(f'[content-translate] 等待首译时读缓存异常（继续等待）: {exc}')
        return None


#: `resource_kind` → resolver。放在类外是为了让 resolver 表本身可被测试直接检查覆盖面。
_RESOLVERS: Final[
    dict[str, Callable[..., Coroutine[Any, Any, ResolvedSource]]]
] = {
    'post': ContentTranslationService._resolve_post,
    'article': ContentTranslationService._resolve_article,
    'comment': ContentTranslationService._resolve_comment,
    'circle': ContentTranslationService._resolve_circle,
    'profile': ContentTranslationService._resolve_profile,
}

content_translation_service = ContentTranslationService()
