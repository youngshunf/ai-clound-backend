"""分身命名体系重构 C2/C3：profession 落库 + display_name 全局唯一 + 人名查重。

测试策略：mirror onboarding 测试——monkeypatch register_hasn_agent 捕获 kwargs、
monkeypatch 全局唯一查重（避免真实 DB），断言：
  1. _merge_agent_create_payload 把 profession + display_name_candidates 透进 payload
  2. gateway.create_agent 撞名时按候选池挑首个空闲人名、profession 原样透传给 register
  3. resolve_unique_display_name 在候选池耗尽时退化为数字后缀
register 自身落库由 hasn_auth 真实 DB 集成 + E2E 覆盖。
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.hasn.service import hasn_auth as hasn_auth_module
from backend.app.hasn.service.hasn_agents_service import (
    SqlAlchemyAgentProfileGateway,
    _merge_agent_create_payload,
)
from backend.app.hasn.schema.hasn_agents import CloudCreateAgentRequest


def test_merge_payload_carries_profession_and_candidates() -> None:
    """请求显式 profession + 候选池 → 进 payload；profession 回退模板 name。"""
    request = CloudCreateAgentRequest(
        owner_id='h_owner_1',
        template_id='huanxing/agent/finance',
        display_name='明远',
        display_name_candidates=['明远', '思齐', '致远'],
        profession='金融专家',
    )
    payload = _merge_agent_create_payload(request, template=None)
    assert payload['profession'] == '金融专家'
    assert payload['display_name_candidates'] == ['明远', '思齐', '致远']

    # profession 缺省时回退 hasn_agent_templates.name
    request2 = CloudCreateAgentRequest(owner_id='h_owner_1', display_name='安然')
    payload2 = _merge_agent_create_payload(request2, template=SimpleNamespace(name='健康管理专家'))
    assert payload2['profession'] == '健康管理专家'


def _patch_uniqueness(monkeypatch, taken: set[str]) -> None:
    """monkeypatch 全局人名查重 + agent_name 唯一化（不打真实 DB）。"""

    async def _fake_taken(_db: Any, display_name: str) -> bool:
        return display_name in taken

    async def _fake_slug(self: Any, _db: Any, *, owner_id: str, base_slug: str) -> str:
        return base_slug

    monkeypatch.setattr(SqlAlchemyAgentProfileGateway, 'is_display_name_taken', staticmethod(_fake_taken))
    monkeypatch.setattr(SqlAlchemyAgentProfileGateway, '_ensure_unique_agent_name', _fake_slug)


class _FakeDB:
    """最小 db：仅 create_agent 末尾的 flush 会被调用（查重均已 monkeypatch）。"""

    async def flush(self) -> None:
        return None


def _capture_register(monkeypatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    async def _fake_register(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {'agent': SimpleNamespace(hasn_id='a_test', profile_revision=1), 'agent_key': 'k', 'already_exists': False}

    monkeypatch.setattr(hasn_auth_module, 'register_hasn_agent', _fake_register, raising=True)
    return captured


@pytest.mark.asyncio
async def test_create_agent_resolves_collision_via_candidate_pool(monkeypatch) -> None:
    """desired 撞名 → 按候选池挑首个空闲；profession 原样透传给 register。"""
    _patch_uniqueness(monkeypatch, taken={'明远'})  # 明远已被占用
    captured = _capture_register(monkeypatch)

    gateway = SqlAlchemyAgentProfileGateway()
    payload = {
        'owner_id': 'h_owner_1',
        'agent_name': 'finance',
        'display_name': '明远',
        'display_name_candidates': ['明远', '思齐', '致远'],
        'profession': '金融专家',
    }
    await gateway.create_agent(db=_FakeDB(), payload=payload)

    # 撞名 → 候选池下一个空闲 = 思齐
    assert captured['display_name'] == '思齐'
    assert captured['profession'] == '金融专家'
    assert captured['agent_name'] == 'finance'


@pytest.mark.asyncio
async def test_resolve_unique_display_name_falls_back_to_suffix(monkeypatch) -> None:
    """候选池全被占 → 退化为数字后缀。"""
    _patch_uniqueness(monkeypatch, taken={'明远', '思齐'})
    gateway = SqlAlchemyAgentProfileGateway()
    resolved = await gateway.resolve_unique_display_name(
        db=None, desired='明远', candidates=['明远', '思齐']
    )
    assert resolved == '明远2'


@pytest.mark.asyncio
async def test_resolve_unique_display_name_returns_desired_when_free(monkeypatch) -> None:
    """desired 未被占用 → 原样返回。"""
    _patch_uniqueness(monkeypatch, taken=set())
    gateway = SqlAlchemyAgentProfileGateway()
    resolved = await gateway.resolve_unique_display_name(db=None, desired='明远', candidates=['思齐'])
    assert resolved == '明远'
