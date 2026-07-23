from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

import sqlalchemy as sa

from backend.app.hasn.model import HasnHumans
from backend.app.hasn.service.enterprise_event_bus import EnterpriseEventBus, enterprise_event_bus
from backend.app.hasn_im.application.provider import get_realtime_gateway
from backend.app.hasn_im.ports import RealtimeFrame
from backend.database.db import async_db_session
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class WorkspaceNotificationActions(Protocol):
    async def notify_workspace_switched(
        self,
        *,
        user_id: int,
        prev_workspace: dict[str, Any],
        next_workspace: dict[str, Any],
    ) -> None: ...


class WorkspacePushRouter(Protocol):
    async def push_message_to(self, target_hasn_id: str, payload: dict[str, Any]) -> bool: ...


class _RealtimeWorkspacePushRouter:
    """通知面历史 `type` 帧的兼容桥接。

    当前阶段保持事件载荷语义不变：将历史 payload 按参数封装为 `WorkspaceSwitched` 帧透传。
    """

    async def push_message_to(self, target_hasn_id: str, payload: dict[str, Any]) -> bool:
        await get_realtime_gateway().push_to_owner(
            target_hasn_id,
            RealtimeFrame(method='WorkspaceSwitched', params=payload),
        )
        return True


@dataclass
class RecordingWorkspaceNotificationActions:
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def notify_workspace_switched(
        self,
        *,
        user_id: int,
        prev_workspace: dict[str, Any],
        next_workspace: dict[str, Any],
    ) -> None:
        self.calls.append((
            'notify_workspace_switched',
            {
                'user_id': user_id,
                'prev_workspace': prev_workspace,
                'next_workspace': next_workspace,
            },
        ))


class SqlAlchemyWorkspaceNotificationActions:
    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession] | None = None,
        router: WorkspacePushRouter | None = None,
    ) -> None:
        self.sessionmaker = sessionmaker or async_db_session
        self.router = router or _RealtimeWorkspacePushRouter()

    async def notify_workspace_switched(
        self,
        *,
        user_id: int,
        prev_workspace: dict[str, Any],
        next_workspace: dict[str, Any],
    ) -> None:
        async with self.sessionmaker() as db:
            human = (
                await db.execute(
                    sa.select(HasnHumans).where(
                        HasnHumans.user_id == user_id,
                        HasnHumans.status == 'active',
                    )
                )
            ).scalar_one_or_none()
        if human is None:
            return
        await self.router.push_message_to(
            human.hasn_id,
            {
                'type': 'WorkspaceSwitched',
                'user_id': user_id,
                'prev_workspace': prev_workspace,
                'next_workspace': next_workspace,
                'created_time': timezone.now().isoformat(),
            },
        )


class WorkspaceNotificationSubscriber:
    def __init__(self, actions: WorkspaceNotificationActions | None = None) -> None:
        self.actions = actions or SqlAlchemyWorkspaceNotificationActions()

    async def on_workspace_switched(self, payload: dict[str, Any]) -> None:
        await self.actions.notify_workspace_switched(
            user_id=int(payload['user_id']),
            prev_workspace=dict(payload.get('prev_workspace') or {}),
            next_workspace=dict(payload.get('next_workspace') or {}),
        )

    def register(self, bus: EnterpriseEventBus = enterprise_event_bus) -> None:
        bus.subscribe('on_workspace_switched', self.on_workspace_switched)


workspace_notification_subscriber = WorkspaceNotificationSubscriber()
workspace_notification_subscriber.register()
