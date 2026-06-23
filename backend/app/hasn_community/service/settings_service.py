"""社区个人设置与黑名单服务。

从 god-class CommunityService 拆出的「§个人社区设置 + 黑名单」子域：owner 读取/部分更新
自己的社区偏好（评论政策/可见性/通知开关，默认值与已存配置合并）+ 黑名单增删查（幂等）。
无跨子域调用、纯独立切片。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from backend.app.hasn_community.model import HasnCommunityBlocks
from backend.app.hasn_core import HasnHumans
from backend.common.exception import errors

if TYPE_CHECKING:
    from typing import Any

    from sqlalchemy.ext.asyncio import AsyncSession


DEFAULT_COMMUNITY_SETTINGS: dict[str, Any] = {
    'show_profile': True,
    'searchable': True,
    'allow_follow': True,
    'default_comment_policy': 'all',
    # 主人是否要求审核名下分身的社区内容（发帖/发文/评论）后才公开。
    # 出厂 True = 维持「分身内容默认进 pending_review 待主人审核」的既有行为。
    'agent_post_review': True,
    'notify': {'like': True, 'comment': True, 'follow': True, 'collect': True},
}


class CommunitySettingsService:
    """owner 个人社区设置与黑名单（设置合并、拉黑/解除拉黑，幂等）。"""

    @staticmethod
    async def get_community_settings(db: AsyncSession, *, hasn_id: str) -> dict[str, Any]:
        """读取个人社区设置（默认值与已存配置合并），doc-13 §2.3.1。"""
        human = (
            await db.execute(select(HasnHumans).where(HasnHumans.hasn_id == hasn_id))
        ).scalar_one_or_none()
        if not human:
            raise errors.NotFoundError(msg='用户 HASN 身份不存在')
        stored = human.community_settings if isinstance(human.community_settings, dict) else {}
        merged = dict(DEFAULT_COMMUNITY_SETTINGS)
        merged.update({k: v for k, v in stored.items() if k != 'notify'})
        notify = dict(DEFAULT_COMMUNITY_SETTINGS['notify'])
        if isinstance(stored.get('notify'), dict):
            notify.update(stored['notify'])
        merged['notify'] = notify
        return merged

    @staticmethod
    async def update_community_settings(
        db: AsyncSession, *, hasn_id: str, patch: dict[str, Any]
    ) -> dict[str, Any]:
        """部分更新个人社区设置，doc-13 §3.3。"""
        human = (
            await db.execute(select(HasnHumans).where(HasnHumans.hasn_id == hasn_id))
        ).scalar_one_or_none()
        if not human:
            raise errors.NotFoundError(msg='用户 HASN 身份不存在')
        current = dict(human.community_settings) if isinstance(human.community_settings, dict) else {}
        for k, v in patch.items():
            if k == 'notify' and isinstance(v, dict):
                cur_notify = dict(current.get('notify') or {})
                cur_notify.update(v)
                current['notify'] = cur_notify
            else:
                current[k] = v
        human.community_settings = current
        await db.flush()
        return await CommunitySettingsService.get_community_settings(db, hasn_id=hasn_id)

    @staticmethod
    async def get_agent_post_review(db: AsyncSession, *, owner_hasn_id: str) -> bool:
        """主人是否要求审核名下分身的社区内容（发帖/发文/评论）后才公开。

        默认 True（出厂维持「分身内容进 pending_review 待审核」）。查不到主人身份时
        保守返回 True——宁可多一道审核，绝不因取设置失败而把分身内容直接放出去。
        """
        human = (
            await db.execute(select(HasnHumans).where(HasnHumans.hasn_id == owner_hasn_id))
        ).scalar_one_or_none()
        if not human:
            return True
        stored = human.community_settings if isinstance(human.community_settings, dict) else {}
        value = stored.get('agent_post_review')
        if isinstance(value, bool):
            return value
        return bool(DEFAULT_COMMUNITY_SETTINGS['agent_post_review'])

    @staticmethod
    async def list_blocks(db: AsyncSession, *, blocker_hasn_id: str) -> dict[str, Any]:
        """黑名单列表，doc-13 §3.3。"""
        rows = (
            await db.execute(
                select(HasnCommunityBlocks)
                .where(HasnCommunityBlocks.blocker_hasn_id == blocker_hasn_id)
                .order_by(HasnCommunityBlocks.created_time.desc())
            )
        ).scalars().all()
        return {
            'items': [
                {
                    'blocked_hasn_id': b.blocked_hasn_id,
                    'blocked_type': b.blocked_type,
                    'reason': b.reason,
                    'created_time': b.created_time.isoformat() if b.created_time else None,
                }
                for b in rows
            ]
        }

    @staticmethod
    async def add_block(
        db: AsyncSession,
        *,
        blocker_hasn_id: str,
        blocked_hasn_id: str,
        blocked_type: str = 'human',
        reason: str | None = None,
    ) -> dict[str, Any]:
        """拉黑（幂等），doc-13 §3.3。"""
        if blocked_hasn_id == blocker_hasn_id:
            raise errors.RequestError(msg='不能拉黑自己')
        existing = (
            await db.execute(
                select(HasnCommunityBlocks).where(
                    HasnCommunityBlocks.blocker_hasn_id == blocker_hasn_id,
                    HasnCommunityBlocks.blocked_hasn_id == blocked_hasn_id,
                )
            )
        ).scalar_one_or_none()
        if existing:
            return {'blocked_hasn_id': blocked_hasn_id, 'blocked': True}
        db.add(
            HasnCommunityBlocks(
                blocker_hasn_id=blocker_hasn_id,
                blocked_hasn_id=blocked_hasn_id,
                blocked_type=blocked_type,
                reason=reason,
            )
        )
        await db.flush()
        return {'blocked_hasn_id': blocked_hasn_id, 'blocked': True}

    @staticmethod
    async def remove_block(
        db: AsyncSession, *, blocker_hasn_id: str, blocked_hasn_id: str
    ) -> dict[str, Any]:
        """解除拉黑，doc-13 §3.3。"""
        block = (
            await db.execute(
                select(HasnCommunityBlocks).where(
                    HasnCommunityBlocks.blocker_hasn_id == blocker_hasn_id,
                    HasnCommunityBlocks.blocked_hasn_id == blocked_hasn_id,
                )
            )
        ).scalar_one_or_none()
        if block:
            await db.delete(block)
            await db.flush()
        return {'blocked_hasn_id': blocked_hasn_id, 'blocked': False}


community_settings_service = CommunitySettingsService()
