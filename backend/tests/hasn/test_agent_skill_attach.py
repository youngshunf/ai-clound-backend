"""HASN Agent 技能装配/卸载（云端权威）契约测试。

覆盖 `HasnAgentProfileService.attach_skill_cloud_first / detach_skill_cloud_first`：
- 装配：归一去重并入 skills、bump profile_revision、append `agent.updated` 同步事件；
- 幂等：已在/不在清单中时不改动、不 bump、不发事件；
- 校验：技能未 published/public → 404；owner 不归属 → 403；Agent 不存在 → 404；
- 归一：dict / list[dict] 历史形态统一成 list[str]。

不依赖真实 DB：用最小 fake session（execute→scalar_one_or_none、flush）+ monkeypatch
市场技能 DAO 的公开查找，专注 service 业务语义。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock

import pytest

from backend.common.exception import errors


@dataclass
class _Agent:
    hasn_id: str = 'a_created'
    owner_id: str = 'h_owner'
    agent_name: str = 'agent'
    display_name: str = '云端 Agent'
    skills: Any = field(default_factory=list)
    profile_revision: int = 1
    status: str = 'active'


class _Gateway:
    """复用 profile sync 测试的 fake：owns_owner 仅认 h_owner/100，记录同步事件。"""

    def __init__(self) -> None:
        self.sync_events: list[dict[str, Any]] = []

    async def owns_owner(self, _db: Any, *, owner_id: str, user_id: int) -> bool:
        return owner_id == 'h_owner' and user_id == 100

    async def append_agent_sync_event(self, _db: Any, *, owner_id: str, agent: _Agent, event_type: str) -> None:
        self.sync_events.append({'owner_id': owner_id, 'agent_id': agent.hasn_id, 'event_type': event_type})


class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _FakeSession:
    """最小 AsyncSession 替身：execute 恒返回预置 agent；flush 计数。"""

    def __init__(self, agent: _Agent | None) -> None:
        self._agent = agent
        self.flush_count = 0

    async def execute(self, _stmt: Any) -> _Result:
        return _Result(self._agent)

    async def flush(self) -> None:
        self.flush_count += 1


def _patch_skill_lookup(monkeypatch: pytest.MonkeyPatch, *, found: bool) -> None:
    import backend.app.marketplace.crud.crud_marketplace_skill as skill_crud

    sentinel = object() if found else None
    monkeypatch.setattr(
        skill_crud.marketplace_skill_dao,
        'get_by_namespace_slug_public',
        AsyncMock(return_value=sentinel),
    )


def _service(gateway: Any) -> Any:
    from backend.app.hasn.service.hasn_agents_service import HasnAgentProfileService

    return HasnAgentProfileService(gateway=gateway)


@pytest.mark.asyncio
async def test_attach_skill_appends_normalizes_and_bumps_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_skill_lookup(monkeypatch, found=True)
    gateway = _Gateway()
    agent = _Agent(skills=[], profile_revision=4)
    service = _service(gateway)

    response = await service.attach_skill_cloud_first(
        _FakeSession(agent),
        owner_id='h_owner',
        hasn_id='a_created',
        skill_id='huanxing/translator-pro',
        user_id=100,
    )

    assert agent.skills == ['huanxing/translator-pro']
    assert agent.profile_revision == 5
    assert response.agent.skills == ['huanxing/translator-pro']
    assert response.agent.profile_revision == 5
    assert gateway.sync_events == [{'owner_id': 'h_owner', 'agent_id': 'a_created', 'event_type': 'agent.updated'}]


@pytest.mark.asyncio
async def test_attach_skill_normalizes_dict_form_to_list(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_skill_lookup(monkeypatch, found=True)
    gateway = _Gateway()
    # 历史形态：{skill_id: version} —— 归一为保序 list[str] 后再追加新技能。
    agent = _Agent(skills={'old/skill': '1.0'}, profile_revision=1)
    service = _service(gateway)

    await service.attach_skill_cloud_first(
        _FakeSession(agent),
        owner_id='h_owner',
        hasn_id='a_created',
        skill_id='huanxing/translator-pro',
        user_id=100,
    )

    assert agent.skills == ['old/skill', 'huanxing/translator-pro']
    assert agent.profile_revision == 2


@pytest.mark.asyncio
async def test_attach_skill_is_idempotent_when_already_present(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_skill_lookup(monkeypatch, found=True)
    gateway = _Gateway()
    agent = _Agent(skills=['huanxing/translator-pro'], profile_revision=7)
    session = _FakeSession(agent)
    service = _service(gateway)

    await service.attach_skill_cloud_first(
        session,
        owner_id='h_owner',
        hasn_id='a_created',
        skill_id='huanxing/translator-pro',
        user_id=100,
    )

    assert agent.skills == ['huanxing/translator-pro']
    assert agent.profile_revision == 7  # 不 bump
    assert session.flush_count == 0  # 不落库
    assert gateway.sync_events == []  # 不发事件


@pytest.mark.asyncio
async def test_attach_skill_rejects_unpublished_skill(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_skill_lookup(monkeypatch, found=False)
    gateway = _Gateway()
    agent = _Agent(skills=[], profile_revision=1)
    service = _service(gateway)

    with pytest.raises(errors.NotFoundError):
        await service.attach_skill_cloud_first(
            _FakeSession(agent),
            owner_id='h_owner',
            hasn_id='a_created',
            skill_id='huanxing/translator-pro',
            user_id=100,
        )

    assert agent.skills == []  # 未改动
    assert agent.profile_revision == 1
    assert gateway.sync_events == []


@pytest.mark.asyncio
async def test_attach_skill_denies_non_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_skill_lookup(monkeypatch, found=True)
    gateway = _Gateway()
    agent = _Agent(skills=[], profile_revision=1)
    service = _service(gateway)

    with pytest.raises(errors.AuthorizationError):
        await service.attach_skill_cloud_first(
            _FakeSession(agent),
            owner_id='h_owner',
            hasn_id='a_created',
            skill_id='huanxing/translator-pro',
            user_id=999,  # 非该 owner
        )

    assert agent.skills == []


@pytest.mark.asyncio
async def test_attach_skill_404_when_agent_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_skill_lookup(monkeypatch, found=True)
    gateway = _Gateway()
    service = _service(gateway)

    with pytest.raises(errors.NotFoundError):
        await service.attach_skill_cloud_first(
            _FakeSession(None),  # 查不到 Agent
            owner_id='h_owner',
            hasn_id='a_missing',
            skill_id='huanxing/translator-pro',
            user_id=100,
        )


@pytest.mark.asyncio
async def test_detach_skill_removes_and_bumps_revision() -> None:
    gateway = _Gateway()
    agent = _Agent(skills=['huanxing/translator-pro', 'x/y'], profile_revision=3)
    service = _service(gateway)

    response = await service.detach_skill_cloud_first(
        _FakeSession(agent),
        owner_id='h_owner',
        hasn_id='a_created',
        skill_id='huanxing/translator-pro',
        user_id=100,
    )

    assert agent.skills == ['x/y']
    assert agent.profile_revision == 4
    assert response.agent.skills == ['x/y']
    assert gateway.sync_events == [{'owner_id': 'h_owner', 'agent_id': 'a_created', 'event_type': 'agent.updated'}]


@pytest.mark.asyncio
async def test_detach_skill_is_idempotent_when_absent() -> None:
    gateway = _Gateway()
    agent = _Agent(skills=['x/y'], profile_revision=5)
    session = _FakeSession(agent)
    service = _service(gateway)

    await service.detach_skill_cloud_first(
        session,
        owner_id='h_owner',
        hasn_id='a_created',
        skill_id='huanxing/translator-pro',
        user_id=100,
    )

    assert agent.skills == ['x/y']
    assert agent.profile_revision == 5
    assert session.flush_count == 0
    assert gateway.sync_events == []
