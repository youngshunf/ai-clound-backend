"""Phase 7: permission_engine.evaluate 中央四态判决单元测试 (RESEARCH §B2)。

被测目标：PermissionEngine.evaluate(db, sender, receiver, envelope) →
- 命中 iron_laws 则返回该 DecisionResult
- iron_laws None → 灰度 route_guard.check_permission diff log → 矩阵默认 ALLOW
- 内部异常 → Fail-closed DENY (D-03)
- 所有路径都尝试 _audit_safe (失败仅 log.warning 不阻断)

依赖隔离：iron_laws.check_iron_laws / route_guard.check_permission /
hasn_audit_log_service.append 均通过 monkeypatch 替换为 AsyncMock。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.app.hasn.constants import ALLOW, CONFIRM, DENY, SCOPE_LTD

pytestmark = pytest.mark.asyncio


def _patch_engine_deps(monkeypatch, *, iron_result=None, legacy_ok=True, audit_raises=False):
    """统一打桩 evaluate 依赖。"""
    from backend.app.hasn.service import permission_engine as eng_mod

    iron_mock = AsyncMock(return_value=iron_result)
    monkeypatch.setattr(eng_mod, 'check_iron_laws', iron_mock)

    monkeypatch.setattr(
        eng_mod.route_guard, 'check_permission',
        AsyncMock(return_value=legacy_ok),
        raising=False,
    )

    audit_mock = AsyncMock(side_effect=RuntimeError('audit DB down')) if audit_raises else AsyncMock(return_value=None)
    monkeypatch.setattr(
        eng_mod.hasn_audit_log_service, 'append', audit_mock, raising=False,
    )

    return {'iron': iron_mock, 'audit': audit_mock}


def _entities():
    sender = {'hasn_id': 'h_sender', 'entity_type': 'human'}
    receiver = {'hasn_id': 'h_receiver', 'entity_type': 'human'}
    envelope = {
        'msg_type': 'message', 'content': {'body': 'x'},
        'relation_type': 'social', 'metadata': {}, 'from_entity_type': 'human',
    }
    return sender, receiver, envelope


# ── Test 1: Fail-closed on internal exception ──
async def test_evaluate_fail_closed_on_exception(monkeypatch) -> None:
    from backend.app.hasn.service import permission_engine as eng_mod

    # check_iron_laws 抛异常 → evaluate 必须 Fail-closed DENY
    monkeypatch.setattr(
        eng_mod, 'check_iron_laws', AsyncMock(side_effect=RuntimeError('boom')),
    )
    audit_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(
        eng_mod.hasn_audit_log_service, 'append', audit_mock, raising=False,
    )

    sender, receiver, envelope = _entities()
    result = await eng_mod.permission_engine.evaluate(
        None, sender=sender, receiver=receiver, envelope=envelope,
    )
    assert result.decision == DENY
    assert result.matched_rule == 'exception'
    assert result.error_code == 2099
    audit_mock.assert_called()  # _audit_safe 被尝试调用


# ── Test 2: iron_laws 返回 DENY 直接透传 + audit warning ──
async def test_evaluate_passes_through_iron_law_deny(monkeypatch) -> None:
    from backend.app.hasn.service.iron_laws import DecisionResult

    iron_deny = DecisionResult(
        decision=DENY, reason='iron law violation',
        matched_rule='iron_law_5', error_code=2014,
    )
    mocks = _patch_engine_deps(monkeypatch, iron_result=iron_deny)

    from backend.app.hasn.service.permission_engine import permission_engine

    sender, receiver, envelope = _entities()
    result = await permission_engine.evaluate(
        None, sender=sender, receiver=receiver, envelope=envelope,
    )

    assert result is iron_deny
    assert result.decision == DENY
    mocks['audit'].assert_called_once()
    audit_kwargs = mocks['audit'].call_args.kwargs
    assert audit_kwargs['action'] == 'permission_decision'
    assert audit_kwargs['severity'] == 'warning'  # DENY 默认 severity=warning


# ── Test 3: iron_laws None + 灰度 route_guard 允许 → matrix ALLOW ──
async def test_evaluate_matrix_allow_when_legacy_agrees(monkeypatch) -> None:
    mocks = _patch_engine_deps(monkeypatch, iron_result=None, legacy_ok=True)

    from backend.app.hasn.service.permission_engine import permission_engine

    sender, receiver, envelope = _entities()
    result = await permission_engine.evaluate(
        None, sender=sender, receiver=receiver, envelope=envelope,
    )
    assert result.decision == ALLOW
    assert result.matched_rule == 'matrix'
    mocks['audit'].assert_called_once()


# ── Test 4: iron_laws None + H2H route_guard 拒绝 → DENY（C1 陌生人门控，Core/04 §1）──
async def test_evaluate_h2h_stranger_denied(monkeypatch) -> None:
    _patch_engine_deps(monkeypatch, iron_result=None, legacy_ok=False)

    from backend.app.hasn.service.permission_engine import permission_engine

    sender, receiver, envelope = _entities()  # 双方 human（H2H）
    result = await permission_engine.evaluate(
        None, sender=sender, receiver=receiver, envelope=envelope,
    )
    # H2H 且无关系（route_guard False）→ DENY（不再灰度放行；修复陌生人直发）
    assert result.decision == DENY
    assert result.matched_rule == 'matrix_h2h_relation'
    assert result.error_code == 2002


# ── Test 4c: receiver=Agent 时不套用 H2H 门控（to=Agent 由 inbound_gatekeeper 管）──
async def test_evaluate_non_h2h_not_relation_gated(monkeypatch) -> None:
    # route_guard 即使 False，receiver=agent 也不应被 H2H 门控拦截 → 维持矩阵 ALLOW，
    # 避免误伤 service→Agent / 跨 owner Agent 路径（它们有各自门控）。
    _patch_engine_deps(monkeypatch, iron_result=None, legacy_ok=False)

    from backend.app.hasn.service.permission_engine import permission_engine

    sender = {'hasn_id': 'h_sender', 'entity_type': 'human'}
    receiver = {'hasn_id': 'a_agent', 'entity_type': 'agent', 'owner_id': 'h_other'}
    envelope = {
        'msg_type': 'message', 'content': {'body': 'x'},
        'relation_type': 'social', 'metadata': {}, 'from_entity_type': 'human',
    }
    result = await permission_engine.evaluate(
        None, sender=sender, receiver=receiver, envelope=envelope,
    )
    assert result.decision == ALLOW
    assert result.matched_rule == 'matrix'


# ── Test 5: snake_case decision 字面量与 Rust PermissionDecision 字节对齐 ──
async def test_decision_literals_byte_aligned_with_rust() -> None:
    """断言 constants 字面量与 07-01 Rust 侧 serde rename_all='snake_case' 输出对齐。"""
    assert ALLOW == 'allow'
    assert DENY == 'deny'
    assert CONFIRM == 'confirm_required'
    assert SCOPE_LTD == 'scope_limited'

    from backend.app.hasn.service.iron_laws import DecisionResult

    # 构造四态各一个，确认 .decision 字段就是上述四个 snake_case 字面量
    for lit in (ALLOW, DENY, CONFIRM, SCOPE_LTD):
        d = DecisionResult(decision=lit, reason='t', matched_rule='t')
        assert d.decision == lit
        assert d.decision in {'allow', 'deny', 'confirm_required', 'scope_limited'}


# ── J-L0：A2H 出站关系门（doc07 §8-1）──
class _FakeResult:
    """模拟 db.execute(...) 返回值，仅实现 scalar_one_or_none。"""

    def __init__(self, obj) -> None:
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class _FakeDb:
    """按调用顺序依次返回预置结果（先 HasnAgents，再 HasnContacts）。"""

    def __init__(self, results) -> None:
        self._results = list(results)

    async def execute(self, *_a, **_k):
        return _FakeResult(self._results.pop(0) if self._results else None)


def _a2h_entities(receiver_id='h_stranger'):
    sender = {'hasn_id': 'a_agent', 'entity_type': 'agent'}
    receiver = {'hasn_id': receiver_id, 'entity_type': 'human'}
    envelope = {
        'msg_type': 'message', 'content': {'body': 'x'},
        'relation_type': 'social', 'metadata': {}, 'from_entity_type': 'agent',
    }
    return sender, receiver, envelope


async def test_a2h_outbound_stranger_denied(monkeypatch) -> None:
    """分身 → 无关系陌生人类 → DENY（关系矩阵 send_message=DENY）。"""
    _patch_engine_deps(monkeypatch, iron_result=None)
    from backend.app.hasn.service.permission_engine import permission_engine

    sender, receiver, envelope = _a2h_entities()
    # 第一次 execute 返回分身（owner=h_owner），第二次返回 None（无联系人=陌生人）
    db = _FakeDb([SimpleNamespace(owner_id='h_owner'), None])
    result = await permission_engine.evaluate(
        db, sender=sender, receiver=receiver, envelope=envelope,
    )
    assert result.decision == DENY
    assert result.matched_rule == 'matrix_a2h_relation'
    assert result.error_code == 2002


async def test_a2h_outbound_friend_allowed(monkeypatch) -> None:
    """分身 → 主人的好友（social trust=3，send_message=ALLOW）→ 放行到矩阵 ALLOW。"""
    _patch_engine_deps(monkeypatch, iron_result=None)
    from backend.app.hasn.service.permission_engine import permission_engine

    sender, receiver, envelope = _a2h_entities(receiver_id='h_friend')
    contact = SimpleNamespace(relation_type='social', trust_level=3, status='connected')
    db = _FakeDb([SimpleNamespace(owner_id='h_owner'), contact])
    result = await permission_engine.evaluate(
        db, sender=sender, receiver=receiver, envelope=envelope,
    )
    assert result.decision == ALLOW
    assert result.matched_rule == 'matrix'


async def test_a2h_outbound_to_own_owner_allowed(monkeypatch) -> None:
    """分身 → 自己主人 → 豁免（owner loopback 双保险），矩阵 ALLOW。"""
    _patch_engine_deps(monkeypatch, iron_result=None)
    from backend.app.hasn.service.permission_engine import permission_engine

    sender, receiver, envelope = _a2h_entities(receiver_id='h_owner')
    # 只需返回分身（owner=h_owner）；receiver==owner 命中豁免后不再查联系人
    db = _FakeDb([SimpleNamespace(owner_id='h_owner')])
    result = await permission_engine.evaluate(
        db, sender=sender, receiver=receiver, envelope=envelope,
    )
    assert result.decision == ALLOW
    assert result.matched_rule == 'matrix'


async def test_a2h_outbound_blocked_contact_denied(monkeypatch) -> None:
    """分身 → 主人已拉黑的人类（status=blocked）→ DENY。"""
    _patch_engine_deps(monkeypatch, iron_result=None)
    from backend.app.hasn.service.permission_engine import permission_engine

    sender, receiver, envelope = _a2h_entities(receiver_id='h_blocked')
    contact = SimpleNamespace(relation_type='social', trust_level=2, status='blocked')
    db = _FakeDb([SimpleNamespace(owner_id='h_owner'), contact])
    result = await permission_engine.evaluate(
        db, sender=sender, receiver=receiver, envelope=envelope,
    )
    assert result.decision == DENY
    assert result.matched_rule == 'matrix_a2h_relation'


# ── Test 6: audit 失败不阻断主流程 (Rule 2 / D-03 partial) ──
async def test_audit_failure_does_not_break_flow(monkeypatch) -> None:
    _patch_engine_deps(
        monkeypatch, iron_result=None, legacy_ok=True, audit_raises=True,
    )

    from backend.app.hasn.service.permission_engine import permission_engine

    sender, receiver, envelope = _entities()
    # audit append 抛异常，但 evaluate 仍应返回正常 ALLOW
    result = await permission_engine.evaluate(
        None, sender=sender, receiver=receiver, envelope=envelope,
    )
    assert result.decision == ALLOW
    assert result.matched_rule == 'matrix'
