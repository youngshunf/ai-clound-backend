from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


class _Base(DeclarativeBase):
    pass


class HumanStub(_Base):
    __tablename__ = 'hasn_humans'

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(sa.Integer, default=0)
    hasn_id: Mapped[str] = mapped_column(sa.String(40), default='')
    status: Mapped[str] = mapped_column(sa.String(20), default='active')


class CapturingRouter:
    def __init__(self) -> None:
        self.messages: list[tuple[str, dict[str, Any]]] = []

    async def push_message_to(self, target_hasn_id: str, payload: dict[str, Any]) -> bool:
        self.messages.append((target_hasn_id, payload))
        return True


@pytest_asyncio.fixture
async def sessionmaker(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    import backend.app.hasn.service.workspace_notification_subscriber as subscriber_mod

    monkeypatch.setattr(subscriber_mod, 'HasnHumans', HumanStub, raising=True)
    engine = create_async_engine('sqlite+aiosqlite:///:memory:', future=True)
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield maker
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_workspace_notification_subscriber_records_workspace_switch_payload() -> None:
    from backend.app.hasn.service.workspace_notification_subscriber import (
        RecordingWorkspaceNotificationActions,
        WorkspaceNotificationSubscriber,
    )

    actions = RecordingWorkspaceNotificationActions()
    subscriber = WorkspaceNotificationSubscriber(actions=actions)

    await subscriber.on_workspace_switched({
        'user_id': 7,
        'prev_workspace': {'kind': 'personal', 'enterprise_id': None},
        'next_workspace': {'kind': 'enterprise', 'enterprise_id': 42},
    })

    assert actions.calls == [
        (
            'notify_workspace_switched',
            {
                'user_id': 7,
                'prev_workspace': {'kind': 'personal', 'enterprise_id': None},
                'next_workspace': {'kind': 'enterprise', 'enterprise_id': 42},
            },
        )
    ]


@pytest.mark.asyncio
async def test_sqlalchemy_workspace_notification_pushes_to_active_human(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    from backend.app.hasn.service.workspace_notification_subscriber import (
        SqlAlchemyWorkspaceNotificationActions,
    )

    async with sessionmaker.begin() as db:
        db.add(HumanStub(user_id=7, hasn_id='h_owner_real', status='active'))

    router = CapturingRouter()
    actions = SqlAlchemyWorkspaceNotificationActions(sessionmaker=sessionmaker, router=router)

    await actions.notify_workspace_switched(
        user_id=7,
        prev_workspace={'kind': 'personal', 'enterprise_id': None},
        next_workspace={'kind': 'enterprise', 'enterprise_id': 42},
    )

    assert router.messages[0][0] == 'h_owner_real'
    payload = router.messages[0][1]
    if 'method' in payload and 'params' in payload:
        payload_params = payload['params']
        assert payload['method'] == 'WorkspaceSwitched'
    else:
        payload_params = payload
    assert payload_params['type'] == 'WorkspaceSwitched'
    assert payload_params['user_id'] == 7
    assert payload_params['prev_workspace'] == {'kind': 'personal', 'enterprise_id': None}
    assert payload_params['next_workspace'] == {'kind': 'enterprise', 'enterprise_id': 42}
    assert payload_params['created_time']
