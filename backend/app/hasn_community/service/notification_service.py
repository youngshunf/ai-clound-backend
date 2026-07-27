"""社区通知服务（doc-13 §2.1 / §3.1）——统一通知服务 emit() 的薄封装。

2026-06-02 统一通知服务（§5）落地：本模块不再直写 hasn_notifications，改为转调
通用 backend.app.notification.service.notification_service.emit()。社区触发矩阵
（actor/target/preview/link/relay_from + 自互动抑制）逻辑保留在此，承载/存储/读时
聚合统一由通用服务负责。读取/已读接口直接委派通用服务。

- 复用共享表 hasn_notifications（target_id = 接收方 hasn_id，data = 结构化负载）。
- 触发模型：互动写库成功后，按 §2.1.3 矩阵 emit 通知；Agent 内容/被关注额外 relay 给主人。
- 去重抑制：自己互动自己的内容不写通知。
- category：社区互动归 social；分身草稿待确认归 agent（D5）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from backend.app.hasn_community.service.settings_service import community_settings_service
from backend.app.hasn_core import HasnAgents, HasnHumans
from backend.app.notification.service.notification_service import notification_service as _unified

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# type → 标题模板后缀（按内容类型）
_CONTENT_NOUN = {'post': '帖子', 'article': '文章', 'comment': '评论'}

# actor.type → NotificationSource.kind
_ACTOR_KIND = {'human': 'user', 'agent': 'agent'}

# 社区互动通知类型 → 收件人通知开关键（community_settings.notify.*）。
# 不在表内的类型（如 community_draft_pending 分身草稿待主人审核）不受开关约束，始终送达。
_NOTIFY_KEY_BY_TYPE = {
    'community_like': 'like',
    'community_comment': 'comment',
    'community_collect': 'collect',
    'community_follow': 'follow',
}


class NotificationService:
    """社区通知服务（emit 封装 + 读取委派）"""

    @staticmethod
    async def _resolve_actor(db: AsyncSession, hasn_id: str) -> dict[str, Any]:
        """解析触发者展示信息（human/agent）。"""
        human = (
            await db.execute(select(HasnHumans).where(HasnHumans.hasn_id == hasn_id))
        ).scalar_one_or_none()
        if human:
            return {
                'hasn_id': hasn_id,
                'type': 'human',
                'display_name': human.nickname or hasn_id,
                'avatar': human.avatar or '',
            }
        agent = (
            await db.execute(select(HasnAgents).where(HasnAgents.hasn_id == hasn_id))
        ).scalar_one_or_none()
        if agent:
            return {
                'hasn_id': hasn_id,
                'type': 'agent',
                'display_name': agent.display_name or hasn_id,
                'avatar': agent.avatar or '',
            }
        return {'hasn_id': hasn_id, 'type': 'unknown', 'display_name': hasn_id, 'avatar': ''}

    @staticmethod
    def _actor_source(actor: dict[str, Any]) -> dict[str, Any]:
        """actor → NotificationSource（社区互动 source 即触发者）。"""
        actor_type = actor.get('type')
        return {
            'kind': _ACTOR_KIND.get(actor_type, 'system') if isinstance(actor_type, str) else 'system',
            'id': actor.get('hasn_id'),
            'display_name': actor.get('display_name', ''),
            'avatar': actor.get('avatar', ''),
        }

    @classmethod
    async def _emit(
        cls,
        db: AsyncSession,
        *,
        recipient_hasn_id: str,
        source: dict[str, Any],
        ntype: str,
        category: str,
        title: str,
        data: dict[str, Any],
    ) -> None:
        # 收件人通知开关把关：关闭了对应互动类型的提醒则不落库（draft_pending 等不在表内的类型恒发）。
        notify_key = _NOTIFY_KEY_BY_TYPE.get(ntype)
        if notify_key is not None and not await community_settings_service.get_notify_enabled(
            db, recipient_hasn_id=recipient_hasn_id, notify_key=notify_key
        ):
            return
        await _unified.emit(
            db,
            recipient_id=recipient_hasn_id,
            source=source,
            category=category,
            type=ntype,
            title=title,
            payload=data,
        )

    @staticmethod
    def _content_link(content_type: str, content_id: str) -> str:
        if content_type == 'article':
            return f'/community/articles/{content_id}'
        return f'/community/posts/{content_id}'

    @classmethod
    async def notify_content_interaction(
        cls,
        db: AsyncSession,
        *,
        ntype: str,
        actor_hasn_id: str,
        content_type: str,
        content_id: str,
        author_hasn_id: str,
        author_type: str,
        owner_hasn_id: str | None,
        preview: str | None = None,
        extra_recipient_hasn_id: str | None = None,
    ) -> None:
        """点赞/评论/收藏：通知内容作者；Agent 内容 relay 给主人；可选额外接收方（被回复者）。"""
        actor = await cls._resolve_actor(db, actor_hasn_id)
        source = cls._actor_source(actor)
        noun = _CONTENT_NOUN.get(content_type, '内容')
        verb = {
            'community_like': '赞了你的',
            'community_comment': '评论了你的',
            'community_collect': '收藏了你的',
        }.get(ntype, '互动了你的')
        link = cls._content_link(content_type, content_id)
        base_data = {
            'actor': actor,
            'target': {'type': content_type, 'id': content_id},
            'preview': (preview or '')[:80],
            'link': link,
        }

        # 1) 通知内容作者（自己互动自己跳过）
        if author_hasn_id and author_hasn_id != actor_hasn_id:
            await cls._emit(
                db,
                recipient_hasn_id=author_hasn_id,
                source=source,
                ntype=ntype,
                category='social',
                title=f'{actor["display_name"]}{verb}{noun}',
                data=dict(base_data),
            )
            # 2) Agent 内容 relay 给主人
            if author_type == 'agent' and owner_hasn_id and owner_hasn_id not in (actor_hasn_id, author_hasn_id):
                relay_data = dict(base_data)
                relay_data['relay_from'] = author_hasn_id
                await cls._emit(
                    db,
                    recipient_hasn_id=owner_hasn_id,
                    source=source,
                    ntype=ntype,
                    category='social',
                    title=f'{actor["display_name"]}{verb}你的分身的{noun}',
                    data=relay_data,
                )

        # 3) 额外接收方（评论回复 → 父评论作者）
        if extra_recipient_hasn_id and extra_recipient_hasn_id not in (actor_hasn_id, author_hasn_id):
            reply_data = dict(base_data)
            await cls._emit(
                db,
                recipient_hasn_id=extra_recipient_hasn_id,
                source=source,
                ntype=ntype,
                category='social',
                title=f'{actor["display_name"]}回复了你的评论',
                data=reply_data,
            )

    @classmethod
    async def notify_follow(
        cls,
        db: AsyncSession,
        *,
        actor_hasn_id: str,
        target_hasn_id: str,
        target_type: str,
        target_owner_hasn_id: str | None = None,
    ) -> None:
        """被关注：通知被关注者；Agent 被关注 relay 给主人。"""
        if target_hasn_id == actor_hasn_id:
            return
        actor = await cls._resolve_actor(db, actor_hasn_id)
        source = cls._actor_source(actor)
        data = {
            'actor': actor,
            'target': {'type': target_type, 'id': target_hasn_id},
            'link': f'/community/profiles/{actor_hasn_id}',
        }
        await cls._emit(
            db,
            recipient_hasn_id=target_hasn_id,
            source=source,
            ntype='community_follow',
            category='contact',  # doc `01` §2.3-B / §7：关注归「联系人」面，非「社交」
            title=f'{actor["display_name"]}关注了你',
            data=dict(data),
        )
        if target_type == 'agent' and target_owner_hasn_id and target_owner_hasn_id not in (actor_hasn_id, target_hasn_id):
            relay = dict(data)
            relay['relay_from'] = target_hasn_id
            await cls._emit(
                db,
                recipient_hasn_id=target_owner_hasn_id,
                source=source,
                ntype='community_follow',
                category='contact',  # doc `01` §2.3-B / §7：关注归「联系人」面
                title=f'{actor["display_name"]}关注了你的分身',
                data=relay,
            )

    @classmethod
    async def notify_draft_pending(
        cls,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        agent_hasn_id: str,
        content_type: str,
        content_id: str,
        preview: str | None = None,
    ) -> None:
        """Agent 草稿待确认：通知主人（D5：category=agent，分身审批项）。"""
        if not owner_hasn_id:
            return
        actor = await cls._resolve_actor(db, agent_hasn_id)
        noun = _CONTENT_NOUN.get(content_type, '内容')
        # 待确认草稿的复核入口：文章进编辑器确认/发布，帖子进社区草稿箱 tab
        # （与 WebUI CommunitySidebar「待确认」区一致；不存在 /community/drafts 路由）。
        link = (
            f'/community/articles/{content_id}/edit'
            if content_type == 'article'
            else '/community?tab=drafts'
        )
        await cls._emit(
            db,
            recipient_hasn_id=owner_hasn_id,
            source=cls._actor_source(actor),
            ntype='community_draft_pending',
            category='agent',
            title=f'你的分身 {actor["display_name"]} 有一篇{noun}待确认',
            data={
                'actor': actor,
                'target': {'type': content_type, 'id': content_id},
                'preview': (preview or '')[:80],
                'link': link,
                'relay_from': agent_hasn_id,
            },
        )

    # ==================== 读取 / 已读（委派通用服务） ====================

    @staticmethod
    async def list_notifications(
        db: AsyncSession,
        *,
        recipient_hasn_id: str,
        types: list[str] | None = None,
        unread_only: bool = False,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return await _unified.list_notifications(
            db,
            recipient_hasn_id=recipient_hasn_id,
            types=types,
            unread_only=unread_only,
            cursor=cursor,
            limit=limit,
        )

    @staticmethod
    async def unread_count(db: AsyncSession, *, recipient_hasn_id: str) -> dict[str, Any]:
        return await _unified.unread_count(db, recipient_hasn_id=recipient_hasn_id)

    @staticmethod
    async def mark_read(db: AsyncSession, *, recipient_hasn_id: str, notification_id: int) -> None:
        await _unified.mark_read(db, recipient_hasn_id=recipient_hasn_id, notification_id=notification_id)

    @staticmethod
    async def mark_all_read(
        db: AsyncSession, *, recipient_hasn_id: str, types: list[str] | None = None
    ) -> int:
        return await _unified.mark_all_read(db, recipient_hasn_id=recipient_hasn_id, types=types)


notification_service = NotificationService()
