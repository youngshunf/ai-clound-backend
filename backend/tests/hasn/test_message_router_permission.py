"""Phase 7 → 会话一等实体（doc02 §3.3）：route_message 的 A 路线四态判决 × 统一受众扇出。

被测目标：
- mock permission_engine.evaluate → ALLOW/DENY/CONFIRM/SCOPE_LTD，验证判决出口。
- ALLOW → 落库 + `_fanout_message_new` 扇出：每受众 owner 一条 `hasn.message.new` 瘦事件推送。
- DENY → 不投递，返回 error；CONFIRM → 调 _stash_pending_commitment 不投递；
  SCOPE_LTD → mask content 后扇出（content_body 只留 allowed_fields）。
- check_relation_permission 已不被 route_message 调用（legacy 仅保留 def）。

会话一等实体后：投递不再有 envelope/permission 子对象/entity 直推/owner exclude-fanout——
统一走 `push_to_owner(owner, message.new)`（8 字段瘦事件）。permission 决定的是**投递什么**
（masked content_body）与**是否投递**，不再作为 envelope 的 rider 字段。

依赖隔离：resolve_target / get_or_create_conversation / persist_message / permission_engine.evaluate
/ compute_audience_owner_ids / _stash_pending_commitment / ws_router.push_to_owner 全部 mock。
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
    audience=('h_receiver', 'h_sender'),
):
    """统一 mock route_message 的下游依赖（含统一受众扇出）。"""
    from backend.app.hasn.service import conversation_projection as cp
    from backend.app.hasn_im.application import message_service as mr
    from backend.app.hasn_im.application.node_session_service import node_session_service as ws_router

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

    # 会话 + 持久化（轻量 stub；conv 带 type/participants 供受众计算读，虽然下面直接 mock 掉受众）
    fake_conv = SimpleNamespace(
        id=42, type='direct',
        participant_a_id='h_sender', participant_a_type='human',
        participant_b_id='h_receiver', participant_b_type='human',
    )
    monkeypatch.setattr(
        mr, 'get_or_create_conversation',
        AsyncMock(return_value=fake_conv),
    )
    fake_msg = SimpleNamespace(
        id=1001, from_type=1, to_type=1, created_time=datetime(2026, 4, 19),
    )
    monkeypatch.setattr(mr, 'persist_message', AsyncMock(return_value=fake_msg))

    # 受众计算隔离：固定返回，聚焦 permission 出口（受众计算本体在 C1 单测覆盖）。
    monkeypatch.setattr(cp, 'compute_audience_owner_ids', AsyncMock(return_value=list(audience)))

    # 捕获瘦事件 sync feed 落库。
    from backend.app.hasn.service import hasn_sync_service as sync_service_module

    sync_calls: list[dict] = []
    monkeypatch.setattr(
        sync_service_module.SqlAlchemySyncGateway,
        '_append_sync_event',
        AsyncMock(side_effect=lambda _self_db, **kw: (sync_calls.append(kw), 1)[1]),
        raising=False,
    )

    # _stash_pending_commitment（CONFIRM 分支）。
    stash_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(mr, '_stash_pending_commitment', stash_mock, raising=False)

    # 统一受众扇出实时推送出口：push_to_owner(owner_id, message.new payload)。
    pushed: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        ws_router, 'push_to_owner',
        AsyncMock(side_effect=lambda owner_id, payload: pushed.append((owner_id, payload))),
        raising=False,
    )

    legacy_mock = AsyncMock(return_value={'allowed': False})  # 应不被调用
    monkeypatch.setattr(mr, 'check_relation_permission', legacy_mock)

    return {
        'evaluate': eval_mock,
        'stash': stash_mock,
        'pushed': pushed,
        'sync_calls': sync_calls,
        'legacy': legacy_mock,
    }


def _fake_db():
    db = MagicMock()
    db.commit = AsyncMock(return_value=None)
    return db


def _first_message_new_params(mocks) -> dict:
    """从 push_to_owner(owner_id, payload) 捕获里取第一条 message.new 的 params。"""
    assert mocks['pushed'], '预期至少一条 message.new 推送'
    owner_id, payload = mocks['pushed'][0]
    assert payload['method'] == 'hasn.message.new'
    return payload['params']


# ── Test 1: ALLOW → 扇出 message.new 到受众 owner ──
async def test_allow_fans_out_message_new(monkeypatch) -> None:
    mocks = _patch_router_pipeline(monkeypatch, perm_decision=ALLOW)
    from backend.app.hasn_im.application.message_service import route_message

    result = await route_message(
        db=_fake_db(), from_id='h_sender', to_target='h_receiver',
        content={'body': 'hi'}, msg_type='message',
    )
    assert result.get('error') is False
    assert result['status'] == 'sent'
    # 受众两个 owner 都收到 message.new。
    assert {owner for owner, _ in mocks['pushed']} == {'h_receiver', 'h_sender'}
    params = _first_message_new_params(mocks)
    assert params['sender_hasn_id'] == 'h_sender'
    assert params['content_body'] == {'body': 'hi'}
    # 瘦事件严格 8 字段，无 permission/envelope rider。
    assert set(params.keys()) == {
        'conversation_id', 'message_id', 'sender_hasn_id', 'origin_node_id',
        'content_type', 'content_body', 'local_id', 'created_at',
    }
    assert result['delivered_to'] == ['h_receiver', 'h_sender']


async def test_agent_target_fans_out_to_agent_owner(monkeypatch) -> None:
    """发给 Agent 的消息不依赖 Runtime 在线——Agent 的主人 owner 在受众里，其在线设备收 message.new。

    会话一等实体后无「entity 节点直推 + owner exclude-fanout」：统一受众扇出让 Agent 主人本就在
    受众集合，其设备收到后由 dispatch_here（§3.8）在绑定节点唤醒 Runtime。
    """
    mocks = _patch_router_pipeline(monkeypatch, perm_decision=ALLOW, audience=('h_owner', 'h_sender'))
    from backend.app.hasn.service import conversation_projection as cp
    from backend.app.hasn_im.application import message_service as mr
    from backend.app.hasn_im.application.message_service import route_message

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
    # 受众 = {发送方 h_sender, 分身主人 h_owner}。
    monkeypatch.setattr(cp, 'compute_audience_owner_ids', AsyncMock(return_value=['h_owner', 'h_sender']))
    # to=Agent 走 INGATE 入站门控；本用例只验证 permission 出口，放行入站门控。
    monkeypatch.setattr(
        mr, 'evaluate_inbound',
        AsyncMock(return_value=SimpleNamespace(action='allow')),
    )

    result = await route_message(
        db=_fake_db(), from_id='h_sender', to_target='a_receiver',
        content={'body': 'hi agent'}, msg_type='message',
    )

    assert result.get('error') is False
    # Agent 主人 h_owner 在受众里，收到 message.new（Runtime 缺失/离线也照收）。
    pushed_owners = {owner for owner, _ in mocks['pushed']}
    assert 'h_owner' in pushed_owners
    assert pushed_owners == {'h_owner', 'h_sender'}
    params = _first_message_new_params(mocks)
    assert params['content_body'] == {'body': 'hi agent'}


# ── Test 2: DENY → 不投递，返回 error ──
async def test_deny_returns_error_no_delivery(monkeypatch) -> None:
    mocks = _patch_router_pipeline(
        monkeypatch, perm_decision=DENY, perm_reason='blocked', error_code=2002,
    )
    from backend.app.hasn_im.application.message_service import route_message

    result = await route_message(
        db=_fake_db(), from_id='h_sender', to_target='h_receiver',
        content={'body': 'hi'}, msg_type='message',
    )
    assert result.get('error') is True
    assert result['code'] == 2002
    assert result['message'] == 'blocked'
    assert not mocks['pushed']


# ── Test 3: CONFIRM → 调 _stash_pending_commitment，不投递 ──
async def test_confirm_stashes_no_delivery(monkeypatch) -> None:
    mocks = _patch_router_pipeline(
        monkeypatch, perm_decision=CONFIRM, perm_reason='need confirm',
    )
    from backend.app.hasn_im.application.message_service import route_message

    result = await route_message(
        db=_fake_db(), from_id='h_sender', to_target='h_receiver',
        content={'body': 'hi'}, msg_type='commitment',
    )

    assert result.get('error') is False
    assert result['status'] == 'pending_confirmation'
    mocks['stash'].assert_called_once()
    assert not mocks['pushed']


# ── Test 4: SCOPE_LTD → mask content_body 仅保留 allowed_fields ──
async def test_scope_limited_masks_content_body(monkeypatch) -> None:
    mocks = _patch_router_pipeline(
        monkeypatch, perm_decision=SCOPE_LTD, allowed_fields=['body'],
    )
    from backend.app.hasn_im.application.message_service import route_message

    await route_message(
        db=_fake_db(), from_id='h_sender', to_target='h_receiver',
        content={'body': 'visible', 'payment_amount': 100},
        msg_type='message',
    )
    params = _first_message_new_params(mocks)
    # 只保留 allowed_fields，payment_amount 被 mask 掉，不进 content_body。
    assert params['content_body'] == {'body': 'visible'}


# ── Test 5: ALLOW & SCOPE_LTD 都扇出 message.new（permission 已不作 rider）──
async def test_allow_and_scope_limited_both_deliver(monkeypatch) -> None:
    for decision, fields, expect_body in [
        (ALLOW, None, {'body': 'x'}),
        (SCOPE_LTD, ['body'], {'body': 'x'}),
    ]:
        mocks = _patch_router_pipeline(
            monkeypatch, perm_decision=decision, allowed_fields=fields,
        )
        from backend.app.hasn_im.application.message_service import route_message

        await route_message(
            db=_fake_db(), from_id='h_sender', to_target='h_receiver',
            content={'body': 'x'}, msg_type='message',
        )
        params = _first_message_new_params(mocks)
        assert params['content_body'] == expect_body


# ── Test 6: legacy check_relation_permission 已不被 route_message 调用 ──
async def test_legacy_check_relation_permission_not_called(monkeypatch) -> None:
    mocks = _patch_router_pipeline(monkeypatch, perm_decision=ALLOW)
    from backend.app.hasn_im.application.message_service import route_message

    await route_message(
        db=_fake_db(), from_id='h_sender', to_target='h_receiver',
        content={'body': 'x'}, msg_type='message',
    )
    mocks['legacy'].assert_not_called()


# ── Test 7: R1-08 事务收口守卫——直连（1:1）路径主链单 commit ──
async def test_direct_route_commits_exactly_once(monkeypatch) -> None:
    """persist_message + 扇出 sync feed 事件同一事务、只 commit 一次。

    删了原扇出前的中间 commit（它把消息落库但 feed 尚未写、crash 即半状态）；实时 push 移到
    commit 之后的 _flush_pushes，不再夹在事务里。await_count > 1 即回归。
    """
    _patch_router_pipeline(monkeypatch, perm_decision=ALLOW)
    from backend.app.hasn_im.application.message_service import route_message

    db = _fake_db()
    result = await route_message(
        db=db, from_id='h_sender', to_target='h_receiver',
        content={'body': 'once'}, msg_type='message',
    )
    assert result.get('error') is False
    assert db.commit.await_count == 1, f'直连路径主链应恰好 commit 一次，实际 {db.commit.await_count} 次（半状态回归）'
