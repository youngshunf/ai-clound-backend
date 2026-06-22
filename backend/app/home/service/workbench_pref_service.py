"""主人工作台偏好服务（主脑指针 + 每日简报偏好）。

云端权威：daemon 经 BackendGateway 拉取/写回，本地仅镜像。

主脑解析（resolve）优先级：
  1. 主人显式设置且仍有效（归属本人 + 活跃）的 `primary_agent_id`
  2. 主人名下 `role='primary'` 的活跃分身（onboarding 默认分身）
  3. 主人名下最早创建的活跃分身
  4. 无任何活跃分身 → None（工作台显示"先创建一个分身"引导，零 fake）

设计事实源：docs/hasn-node设计文档/13-工作台/04-...设计.md §2.2 / §2.3。
"""

from __future__ import annotations

import re

from typing import TYPE_CHECKING

import sqlalchemy as sa

from backend.app.hasn_core import HasnAgents
from backend.app.home.crud.crud_hasn_owner_workbench_pref import hasn_owner_workbench_pref_dao
from backend.app.home.schema.hasn_owner_workbench_pref import WorkbenchPrefResponse
from backend.common.exception import errors

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.app.home.model import HasnOwnerWorkbenchPref

_BRIEFING_TIME_RE = re.compile(r'^([01]\d|2[0-3]):[0-5]\d$')
_DEFAULT_BRIEFING_TIME = '08:00'
_DEFAULT_BRIEFING_SOURCES = ['task', 'social', 'app', 'plan']
_ALLOWED_SOURCES = {'task', 'social', 'app', 'plan'}
# 分身被视为"可作主脑"的活跃状态（空字符串是历史存量未回填状态，按活跃对待）
_ACTIVE_AGENT_STATUSES = ('active', '')


class WorkbenchPrefService:
    """工作台偏好读写 + 主脑解析。"""

    @staticmethod
    async def _agent_is_owned_active(db: AsyncSession, *, owner_hasn_id: str, agent_id: str) -> bool:
        """该 agent 是否归属本主人且活跃（可作主脑）。"""
        row = (
            await db.execute(
                sa.select(HasnAgents.hasn_id).where(
                    HasnAgents.hasn_id == agent_id,
                    HasnAgents.owner_id == owner_hasn_id,
                    HasnAgents.deleted_at.is_(None),
                    HasnAgents.status.in_(_ACTIVE_AGENT_STATUSES),
                )
            )
        ).scalar_one_or_none()
        return row is not None

    @staticmethod
    async def _default_primary_agent(db: AsyncSession, owner_hasn_id: str) -> str | None:
        """回落主脑：优先 role='primary' 活跃分身，否则最早创建的活跃分身。"""
        primary = (
            await db.execute(
                sa.select(HasnAgents.hasn_id)
                .where(
                    HasnAgents.owner_id == owner_hasn_id,
                    HasnAgents.role == 'primary',
                    HasnAgents.deleted_at.is_(None),
                    HasnAgents.status.in_(_ACTIVE_AGENT_STATUSES),
                )
                .order_by(HasnAgents.created_time.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if primary:
            return primary
        return (
            await db.execute(
                sa.select(HasnAgents.hasn_id)
                .where(
                    HasnAgents.owner_id == owner_hasn_id,
                    HasnAgents.deleted_at.is_(None),
                    HasnAgents.status.in_(_ACTIVE_AGENT_STATUSES),
                )
                .order_by(HasnAgents.created_time.asc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def resolve_primary_agent(
        self, db: AsyncSession, owner_hasn_id: str, explicit_id: str | None
    ) -> tuple[str | None, bool]:
        """解析有效主脑，返回 (agent_id, explicit)。explicit=True 表示主人显式设置且仍有效。"""
        if explicit_id and await self._agent_is_owned_active(db, owner_hasn_id=owner_hasn_id, agent_id=explicit_id):
            return explicit_id, True
        return await self._default_primary_agent(db, owner_hasn_id), False

    async def get_or_init_pref(self, db: AsyncSession, owner_hasn_id: str) -> WorkbenchPrefResponse:
        """取偏好（含解析后的有效主脑）；无行时返回默认值，不强制落库。"""
        row: HasnOwnerWorkbenchPref | None = await hasn_owner_workbench_pref_dao.get_by_owner(db, owner_hasn_id)
        explicit_id = row.primary_agent_id if row else None
        primary_agent_id, explicit = await self.resolve_primary_agent(db, owner_hasn_id, explicit_id)
        return WorkbenchPrefResponse(
            owner_hasn_id=owner_hasn_id,
            primary_agent_id=primary_agent_id,
            primary_agent_explicit=explicit,
            briefing_enabled=row.briefing_enabled if row else True,
            briefing_time=row.briefing_time if row else _DEFAULT_BRIEFING_TIME,
            briefing_sources=list(row.briefing_sources) if row and row.briefing_sources else list(_DEFAULT_BRIEFING_SOURCES),
        )

    async def set_primary_agent(self, db: AsyncSession, owner_hasn_id: str, agent_id: str) -> WorkbenchPrefResponse:
        """设主脑：校验归属本人且活跃，落库（云端权威）。"""
        agent_id = (agent_id or '').strip()
        if not agent_id:
            raise errors.RequestError(msg='primary_agent_id 必填')
        if not await self._agent_is_owned_active(db, owner_hasn_id=owner_hasn_id, agent_id=agent_id):
            raise errors.ForbiddenError(msg='该分身不存在、非本人所有或非活跃，无法设为主脑')
        await hasn_owner_workbench_pref_dao.upsert_by_owner(
            db, owner_hasn_id=owner_hasn_id, values={'primary_agent_id': agent_id}
        )
        await db.commit()
        return await self.get_or_init_pref(db, owner_hasn_id)

    async def update_pref(
        self,
        db: AsyncSession,
        owner_hasn_id: str,
        *,
        primary_agent_id: str | None = None,
        briefing_enabled: bool | None = None,
        briefing_time: str | None = None,
        briefing_sources: list[str] | None = None,
    ) -> WorkbenchPrefResponse:
        """部分更新偏好（仅传入字段）；校验主脑归属与时刻格式。"""
        values: dict = {}
        if primary_agent_id is not None:
            agent_id = primary_agent_id.strip()
            if not agent_id:
                raise errors.RequestError(msg='primary_agent_id 不能为空字符串')
            if not await self._agent_is_owned_active(db, owner_hasn_id=owner_hasn_id, agent_id=agent_id):
                raise errors.ForbiddenError(msg='该分身不存在、非本人所有或非活跃，无法设为主脑')
            values['primary_agent_id'] = agent_id
        if briefing_enabled is not None:
            values['briefing_enabled'] = briefing_enabled
        if briefing_time is not None:
            if not _BRIEFING_TIME_RE.match(briefing_time):
                raise errors.RequestError(msg='briefing_time 必须为 HH:MM（24 小时制）')
            values['briefing_time'] = briefing_time
        if briefing_sources is not None:
            invalid = [s for s in briefing_sources if s not in _ALLOWED_SOURCES]
            if invalid:
                raise errors.RequestError(msg=f'briefing_sources 含非法项：{invalid}（仅允许 task/social/app/plan）')
            values['briefing_sources'] = briefing_sources
        if values:
            await hasn_owner_workbench_pref_dao.upsert_by_owner(db, owner_hasn_id=owner_hasn_id, values=values)
            await db.commit()
        return await self.get_or_init_pref(db, owner_hasn_id)


workbench_pref_service = WorkbenchPrefService()
