"""真删除分身 (DELAGENT) 云端 service 契约测试。

覆盖：
  1. `delete_profile_cloud_first` 按 (hasn_id, owner_id) **物理 DELETE**（不是改 status=archived 软归档）、
     owner 隔离、不存在 404。
  2. `update_profile_cloud_first` 的 `status='disabled'` **真落库** + 递增 profile_revision + 发
     `agent.updated` 同步事件 —— 这是「点击停用没反应」根因排查的云端侧回归：证明停用持久化在云端
     是好的，问题在前端 `window.confirm` 被桌面 WebView 抑制（已换公共 ConfirmDialog）。

测试策略 mirror `test_agent_profile_sync.py`：service 级 + 最小 fake DB/gateway（纯控制流，不依赖真实 PG）；
物理删库本身由 hasn_auth 真实 DB 集成 + 全栈 E2E 覆盖。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import pytest

from backend.common.exception import errors

if TYPE_CHECKING:
    from backend.app.hasn.service.hasn_agents_service import HasnAgentProfileService


@dataclass
class _Agent:
    id: int = 1
    hasn_id: str = 'a_target'
    star_id: str = '100001#agent'
    owner_id: str = 'h_owner'
    agent_name: str = 'agent'
    display_name: str = '云端 Agent'
    description: str | None = '简介'
    avatar: str | None = 'https://cdn.example.com/user.png'
    type: str = 'desktop'
    role: str = 'specialist'
    node_id: str | None = 'n_local'
    capabilities: dict[str, Any] | None = None
    template_id: str | None = 'tpl_assistant'
    skills: dict[str, Any] | None = field(default_factory=lambda: {'enabled': ['chat']})
    soul_md: str | None = '# SOUL'
    user_md: str | None = '# USER'
    profile_revision: int = 3
    status: str = 'active'
    created_via: str = 'client'
    created_time: datetime = datetime(2026, 5, 1, tzinfo=timezone.utc)
    updated_time: datetime | None = None


class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _FakeDB:
    """最小 async db：select 恒返预置 agent（None 表示不存在）；记录 delete/flush 调用。"""

    def __init__(self, agent: _Agent | None) -> None:
        self.agent = agent
        self.deleted: list[Any] = []
        self.flushed = 0

    async def execute(self, *_a: Any, **_k: Any) -> _Result:
        return _Result(self.agent)

    async def delete(self, instance: Any) -> None:
        self.deleted.append(instance)

    async def flush(self) -> None:
        self.flushed += 1


class _Gateway:
    def __init__(self, *, owns: bool = True) -> None:
        self._owns = owns
        self.sync_events: list[dict[str, Any]] = []

    async def owns_owner(self, _db: Any, *, owner_id: str, user_id: int) -> bool:
        return self._owns

    async def append_agent_sync_event(self, _db: Any, *, owner_id: str, agent: Any, event_type: str) -> None:
        self.sync_events.append({'owner_id': owner_id, 'agent_id': agent.hasn_id, 'event_type': event_type})


def _service(gateway: _Gateway) -> HasnAgentProfileService:
    from backend.app.hasn.service.hasn_agents_service import HasnAgentProfileService

    return HasnAgentProfileService(gateway=gateway)


@pytest.mark.asyncio
async def test_delete_profile_cloud_first_resolves_by_hasn_id_and_physically_deletes() -> None:
    agent = _Agent()
    db = _FakeDB(agent)
    service = _service(_Gateway())

    result = await service.delete_profile_cloud_first(db, owner_id='h_owner', hasn_id='a_target', user_id=100)

    assert result == 'a_target'
    # 物理 DELETE 走 db.delete(agent)，不是把 status 改成 archived（软归档）。
    assert db.deleted == [agent]
    assert db.flushed == 1


@pytest.mark.asyncio
async def test_delete_profile_cloud_first_404_when_agent_missing() -> None:
    db = _FakeDB(None)
    service = _service(_Gateway())

    with pytest.raises(errors.NotFoundError):
        await service.delete_profile_cloud_first(db, owner_id='h_owner', hasn_id='a_missing', user_id=100)

    assert db.deleted == []


@pytest.mark.asyncio
async def test_delete_profile_cloud_first_forbidden_when_owner_mismatch() -> None:
    db = _FakeDB(_Agent())
    service = _service(_Gateway(owns=False))

    with pytest.raises(errors.AuthorizationError):
        await service.delete_profile_cloud_first(db, owner_id='h_other', hasn_id='a_target', user_id=999)

    # owner 校验在取 agent 之前跳闸，绝不删别人的分身。
    assert db.deleted == []


@pytest.mark.asyncio
async def test_update_profile_persists_disabled_status_and_emits_sync_event() -> None:
    """停用持久化回归：PATCH status=disabled 真改 agent.status + bump revision + 发 agent.updated。"""
    from backend.app.hasn.schema.hasn_agents import UpdateAgentProfileRequest

    agent = _Agent(status='active', profile_revision=3)
    db = _FakeDB(agent)
    gateway = _Gateway()
    service = _service(gateway)

    response = await service.update_profile_cloud_first(
        db,
        owner_id='h_owner',
        hasn_id='a_target',
        request=UpdateAgentProfileRequest(status='disabled'),
        user_id=100,
    )

    assert agent.status == 'disabled'  # 真落库
    assert agent.profile_revision == 4  # revision bump → daemon WSPUSH/重拉
    assert response.agent.status == 'disabled'
    assert gateway.sync_events == [
        {'owner_id': 'h_owner', 'agent_id': 'a_target', 'event_type': 'agent.updated'}
    ]
