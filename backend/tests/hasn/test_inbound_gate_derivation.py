"""doc08 RT1.5-A（修 B9）：入站门控接主人派生的三分叉接线 + A2H 接收侧解析（§4.1.3）。

不依赖真实 PG：patch 掉 gatekeeper 的 DB 读（_load_agent/_load_contact/_agent_owner）与副作用
（_materialize_edge/_auto_first_contact_request），验证 evaluate_inbound / evaluate_a2h_inbound
在「无直连边 + 发送方是分身」时按主人边档位正确分叉。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.hasn.service import inbound_gatekeeper as ig

_RECEIVER = {'hasn_id': 'a_a_agent', 'owner_id': 'h_a'}
_FROM_AGENT = 'a_b_agent'
_OWNER_B = 'h_b'


def _owner_edge(trust: int | None):
    if trust is None:
        return None
    return SimpleNamespace(
        trust_level=trust,
        status='blocked' if trust == 0 else 'connected',
        relation_type='social',
    )


async def _load_contact_factory(direct_contact, owner_edge):
    async def _fake(db, owner_id, peer_id):
        if peer_id == _FROM_AGENT:  # 直连实体边
            return direct_contact
        if peer_id == _OWNER_B:  # 主人边
            return owner_edge
        return None
    return _fake


async def _run_agent_gate(*, owner_trust, direct_contact=None):
    agent = SimpleNamespace(status='active', social_enabled=True, inbound_policy='auto')
    fake_load = await _load_contact_factory(direct_contact, _owner_edge(owner_trust))
    with (
        patch.object(ig, '_load_agent', AsyncMock(return_value=agent)),
        patch.object(ig, '_load_contact', AsyncMock(side_effect=fake_load)),
        patch.object(ig, '_agent_owner', AsyncMock(return_value=_OWNER_B)),
        patch.object(ig, '_materialize_edge', AsyncMock()) as mat,
        patch.object(ig, '_auto_first_contact_request', AsyncMock(return_value=555)) as req,
    ):
        outcome = await ig.evaluate_inbound(
            AsyncMock(), from_id=_FROM_AGENT, agent_info=_RECEIVER, relation_type='social',
        )
    return outcome, mat, req


# ── A2A 入站门控三分叉（修 B9）──


@pytest.mark.asyncio
async def test_friend_owner_materializes_and_allows() -> None:
    """主人边=3 好友 → 物化 A→分身 边（继承 trust=3）+ 放行。"""
    outcome, mat, req = await _run_agent_gate(owner_trust=3)
    assert outcome.action == ig.ALLOW
    mat.assert_awaited_once()
    assert mat.await_args.kwargs['trust_level'] == 3
    req.assert_not_awaited()


@pytest.mark.asyncio
async def test_normal_friend_owner_requests_and_suppresses() -> None:
    """主人边=2 普通朋友 → 代发好友请求 + 暂存拦截箱（snapshot 关联 request_id）。"""
    outcome, mat, req = await _run_agent_gate(owner_trust=2)
    assert outcome.action == ig.SUPPRESS
    assert outcome.snapshot.get('pending_request_id') == 555
    req.assert_awaited_once()
    mat.assert_not_awaited()


@pytest.mark.asyncio
async def test_stranger_owner_falls_through_to_gate() -> None:
    """主人边≤1 / 无边 → 派生未命中，落回现状陌生人门控（矩阵 DENY → SUPPRESS）。"""
    outcome, mat, req = await _run_agent_gate(owner_trust=1)
    assert outcome.action == ig.SUPPRESS
    assert outcome.snapshot.get('pending_request_id') is None
    mat.assert_not_awaited()
    req.assert_not_awaited()


@pytest.mark.asyncio
async def test_blocked_owner_rejects_silent() -> None:
    """主人边黑名单（trust=0）→ 静默拒（不进箱）。"""
    outcome, _, _ = await _run_agent_gate(owner_trust=0)
    assert outcome.action == ig.REJECT_SILENT


@pytest.mark.asyncio
async def test_direct_edge_skips_derivation() -> None:
    """已有直连边（≥2）→ 走现状矩阵放行，不做主人派生。"""
    direct = SimpleNamespace(trust_level=3, status='connected', relation_type='social')
    outcome, mat, req = await _run_agent_gate(owner_trust=5, direct_contact=direct)
    assert outcome.action == ig.ALLOW
    mat.assert_not_awaited()
    req.assert_not_awaited()


# ── A2H 接收侧解析（§4.1.3 修 B12 后半）──


async def _run_a2h(*, owner_trust, direct_contact=None):
    fake_load = await _load_contact_factory(direct_contact, _owner_edge(owner_trust))
    with (
        patch.object(ig, '_load_contact', AsyncMock(side_effect=fake_load)),
        patch.object(ig, '_agent_owner', AsyncMock(return_value=_OWNER_B)),
        patch.object(ig, '_materialize_edge', AsyncMock()) as mat,
        patch.object(ig, '_auto_first_contact_request', AsyncMock(return_value=666)) as req,
    ):
        outcome = await ig.evaluate_a2h_inbound(
            AsyncMock(), from_agent=_FROM_AGENT, human_id='h_a', relation_type='social',
        )
    return outcome, mat, req


@pytest.mark.asyncio
async def test_a2h_friend_allows() -> None:
    """A2H：主人边=4 密友 → 物化 + ALLOW（覆盖出站关系门 DENY）。"""
    outcome, mat, _ = await _run_a2h(owner_trust=4)
    assert outcome.action == ig.ALLOW
    mat.assert_awaited_once()


@pytest.mark.asyncio
async def test_a2h_stranger_suppresses_not_bare_2002() -> None:
    """A2H：陌生人 → 暂存拦截箱（主人可见），不再裸 2002。"""
    outcome, _, _ = await _run_a2h(owner_trust=None)
    assert outcome.action == ig.SUPPRESS


@pytest.mark.asyncio
async def test_a2h_normal_friend_requests() -> None:
    """A2H：主人边=2 普通朋友 → 代发请求 + 暂存。"""
    outcome, _, req = await _run_a2h(owner_trust=2)
    assert outcome.action == ig.SUPPRESS
    assert outcome.snapshot.get('pending_request_id') == 666
    req.assert_awaited_once()
