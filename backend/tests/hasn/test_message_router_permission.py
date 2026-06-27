"""Phase 7: message_router.route_message 的 A 路线四态判决出口测试 (RESEARCH §B3)。

被测目标：
- mock permission_engine.evaluate → ALLOW/DENY/CONFIRM/SCOPE_LTD
- 验证 envelope JSON 顶层含 permission 子对象 {decision, reason, allowed_fields}
- 验证 DENY 直接返回 error，CONFIRM 调 _stash_pending_commitment 不 push，
  SCOPE_LTD 应用 mask 后 push，ALLOW 正常 push
- 验证 check_relation_permission 已不被 route_message 调用 (legacy 仅保留 def)

依赖隔离：resolve_target / get_or_create_conversation / persist_message /
_push_message_to / permission_engine.evaluate / _stash_pending_commitment 全部 mock。
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.hasn.constants import ALLOW, CONFIRM, DENY, SCOPE_LTD

pytestmark = pytest.mark.asyncio


def _make_iron_result(decision: str, **extra):
    """复用 iron_laws.DecisionResult 构造一个判决对象。"""
    from backend.app.hasn.service.iron_laws import DecisionResult

    base = {
        'decision': decision,
        'reason': f'test {decision}',
        'matched_rule': 'test',
    }
    base.update(extra)
    return DecisionResult(**base)


def _patch_router_pipeline(
    monkeypatch,
    *,
    perm_decision: str,
    perm_reason: str = 'test',
    allowed_fields=None,
    error_code=None,
):
    """统一 mock route_message 的下游依赖。"""
    from backend.app.hasn.service import message_router as mr

    # 目标解析：人类收件人
    monkeypatch.setattr(
        mr, 'resolve_target',
        AsyncMock(return_value={
            'hasn_id': 'h_receiver',
            'star_id': '100002',
            'entity_type': 'human',
            'name': 'receiver',
        }),
    )

    # permission_engine.evaluate 返回指定四态
    perm_result = _make_iron_result(
        perm_decision,
        reason=perm_reason,
        allowed_fields=allowed_fields,
        error_code=error_code,
    )
    eval_mock = AsyncMock(return_value=perm_result)
    monkeypatch.setattr(mr.permission_engine, 'evaluate', eval_mock, raising=False)

    # 会话 + 持久化（轻量 stub）
    fake_conv = SimpleNamespace(id=42)
    monkeypatch.setattr(
        mr, 'get_or_create_conversation',
        AsyncMock(return_value=fake_conv),
    )
    fake_msg = SimpleNamespace(
        id=1001, from_type=1, to_type=1, created_time=datetime(2026, 4, 19),
    )
    monkeypatch.setattr(mr, 'persist_message', AsyncMock(return_value=fake_msg))

    # 这些用例只验证 permission 分支，避免被同步事件落库路径干扰。
    from backend.app.hasn.service import hasn_sync_service as sync_service_module

    monkeypatch.setattr(
        sync_service_module.SqlAlchemySyncGateway,
        '_append_sync_event',
        AsyncMock(return_value=None),
        raising=False,
    )

    # _stash_pending_commitment / _push_message_to / db.commit
    stash_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(mr, '_stash_pending_commitment', stash_mock, raising=False)

    push_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(mr, '_push_message_to', push_mock, raising=False)

    # Owner 透明 fanout：fc401e7 起改走 ws_router.push_to_owner_excluding_agent_node
    # （排除 Agent 实体节点，避免 Agent 跑在主人 daemon 上时同一节点收两遍），
    # 不再用 _push_message_to 二次投递。单测需 mock 该单例方法。
    from backend.app.hasn.service.ws_router import ws_router as _ws_router

    owner_fanout_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(
        _ws_router, 'push_to_owner_excluding_agent_node', owner_fanout_mock, raising=False,
    )

    legacy_mock = AsyncMock(return_value={'allowed': False})  # 应不被调用
    monkeypatch.setattr(mr, 'check_relation_permission', legacy_mock)

    return {
        'evaluate': eval_mock,
        'stash': stash_mock,
        'push': push_mock,
        'owner_fanout': owner_fanout_mock,
        'legacy': legacy_mock,
    }


def _fake_db():
    db = MagicMock()
    db.commit = AsyncMock(return_value=None)
    return db


def _extract_pushed_envelope(push_mock) -> dict:
    """从 _push_message_to(to_id, payload) mock 调用中取出 envelope。"""
    args, kwargs = push_mock.call_args
    payload = args[1] if len(args) > 1 else (kwargs.get('payload') or kwargs.get('message'))
    return payload['params']['message']


# ── Test 1: ALLOW → push + envelope 含 permission ──
async def test_allow_pushes_with_permission(monkeypatch) -> None:
    mocks = _patch_router_pipeline(monkeypatch, perm_decision=ALLOW)
    from backend.app.hasn.service.message_router import route_message

    result = await route_message(
        db=_fake_db(), from_id='h_sender', to_target='h_receiver',
        content={'body': 'hi'}, msg_type='message',
    )
    assert result.get('error') is False
    assert result['status'] == 'sent'

    mocks['push'].assert_called_once()
    envelope = _extract_pushed_envelope(mocks['push'])
    assert 'permission' in envelope
    assert envelope['permission']['decision'] == 'allow'


async def test_agent_target_also_pushes_to_owner_without_runtime(monkeypatch) -> None:
    """发给 Agent 的消息不依赖 Runtime 在线；Owner 在线节点也收到同一 IM 消息。"""
    mocks = _patch_router_pipeline(monkeypatch, perm_decision=ALLOW)
    from backend.app.hasn.service import message_router as mr
    from backend.app.hasn.service.message_router import route_message

    monkeypatch.setattr(
        mr,
        'resolve_target',
        AsyncMock(return_value={
            'hasn_id': 'a_receiver',
            'star_id': '200002',
            'entity_type': 'agent',
            'name': 'receiver-agent',
            'owner_id': 'h_owner',
        }),
    )
    # to=Agent 走 INGATE 入站门控；本用例只验证 permission 出口，放行入站门控
    # （否则 _fake_db() MagicMock 喂给 evaluate_inbound 的真实查询会 fail-closed suppress）。
    monkeypatch.setattr(
        mr, 'evaluate_inbound',
        AsyncMock(return_value=SimpleNamespace(action='allow')),
    )

    result = await route_message(
        db=_fake_db(), from_id='h_sender', to_target='a_receiver',
        content={'body': 'hi agent'}, msg_type='message',
    )

    assert result.get('error') is False
    # Agent 实体节点经 _push_message_to 收到一次（Runtime 缺失/离线也照投，本地 IM 透明）。
    assert [call.args[0] for call in mocks['push'].call_args_list] == ['a_receiver']
    envelope = _extract_pushed_envelope(mocks['push'])
    assert envelope['to_owner_id'] == 'h_owner'
    # Owner 透明 fanout 经 ws_router 排除 Agent 实体节点投递一次：(owner_id, agent_id, payload)。
    mocks['owner_fanout'].assert_called_once()
    fanout_args = mocks['owner_fanout'].call_args.args
    assert fanout_args[0] == 'h_owner'
    assert fanout_args[1] == 'a_receiver'
    assert fanout_args[2]['params']['message']['to_owner_id'] == 'h_owner'


# ── Test 2: DENY → 不 push，返回 error ──
async def test_deny_returns_error_no_push(monkeypatch) -> None:
    mocks = _patch_router_pipeline(
        monkeypatch, perm_decision=DENY, perm_reason='blocked', error_code=2002,
    )
    from backend.app.hasn.service.message_router import route_message

    result = await route_message(
        db=_fake_db(), from_id='h_sender', to_target='h_receiver',
        content={'body': 'hi'}, msg_type='message',
    )
    assert result.get('error') is True
    assert result['code'] == 2002
    assert result['message'] == 'blocked'
    mocks['push'].assert_not_called()


# ── Test 3: CONFIRM → 调 _stash_pending_commitment，不 push ──
async def test_confirm_stashes_no_push(monkeypatch) -> None:
    mocks = _patch_router_pipeline(
        monkeypatch, perm_decision=CONFIRM, perm_reason='need confirm',
    )
    from backend.app.hasn.service.message_router import route_message

    result = await route_message(
        db=_fake_db(), from_id='h_sender', to_target='h_receiver',
        content={'body': 'hi'}, msg_type='commitment',
    )

    assert result.get('error') is False
    assert result['status'] == 'pending_confirmation'
    mocks['stash'].assert_called_once()
    mocks['push'].assert_not_called()


# ── Test 4: SCOPE_LTD → mask content 仅保留 allowed_fields ──
async def test_scope_limited_applies_mask(monkeypatch) -> None:
    mocks = _patch_router_pipeline(
        monkeypatch, perm_decision=SCOPE_LTD, allowed_fields=['body'],
    )
    from backend.app.hasn.service.message_router import route_message

    await route_message(
        db=_fake_db(), from_id='h_sender', to_target='h_receiver',
        content={'body': 'visible', 'payment_amount': 100},
        msg_type='message',
    )
    mocks['push'].assert_called_once()
    envelope = _extract_pushed_envelope(mocks['push'])
    assert envelope['content'] == {'body': 'visible'}
    assert envelope['permission']['decision'] == 'scope_limited'


# ── Test 5: ALLOW & SCOPE_LTD 都带 permission 子对象 ──
async def test_envelope_contains_permission_always(monkeypatch) -> None:
    for decision, fields in [(ALLOW, None), (SCOPE_LTD, ['body'])]:
        mocks = _patch_router_pipeline(
            monkeypatch, perm_decision=decision, allowed_fields=fields,
        )
        from backend.app.hasn.service.message_router import route_message

        await route_message(
            db=_fake_db(), from_id='h_sender', to_target='h_receiver',
            content={'body': 'x'}, msg_type='message',
        )
        envelope = _extract_pushed_envelope(mocks['push'])
        assert 'permission' in envelope
        assert envelope['permission']['decision'] == decision


# ── Test 6: legacy check_relation_permission 已不被 route_message 调用 ──
async def test_legacy_check_relation_permission_not_called(monkeypatch) -> None:
    mocks = _patch_router_pipeline(monkeypatch, perm_decision=ALLOW)
    from backend.app.hasn.service.message_router import route_message

    await route_message(
        db=_fake_db(), from_id='h_sender', to_target='h_receiver',
        content={'body': 'x'}, msg_type='message',
    )
    mocks['legacy'].assert_not_called()
