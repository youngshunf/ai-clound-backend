"""联系人模块 P-A: 好友请求 / 通过 走 WS push（拆 hasn_contact_requests 表后）.

覆盖:
- POST /contacts/request 通过 realtime gateway 推送 target_owner: hasn.contact.request_received
- PUT /contacts/requests/{id}/respond accept 通过 realtime gateway 推送 from_id: hasn.contact.connected
- reject 不推 ws
- WS push 失败时 HTTP 仍然成功 (best-effort, 不阻塞主链路)

请求落 hasn_contact_requests，accept 经 upsert_connected 建双向边。WS 事件 schema 对 daemon 保持不变。
DAO 全部 mock 以保持 SQLite 跑得起来 (HasnContacts/HasnContactRequests 是 JSONB).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.hasn.api.v1.app.contacts import (
    respond_to_request,
    send_contact_request,
)
from backend.app.hasn.schema.hasn_contacts_business import (
    HasnContactRequestReq,
    HasnContactRespondReq,
)

SENDER = "h_aaaaaaaaaaaaaaaaaa"
RECEIVER = "h_bbbbbbbbbbbbbbbbbb"
RECEIVER_AGENT = "a_bbbbbbbbbbbbbbbbbb"
SENDER_STAR = "100001"
RECEIVER_STAR = "100002"
RECEIVER_AGENT_STAR = "100002#helper"


def _human(hasn_id: str, star_id: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(hasn_id=hasn_id, star_id=star_id, name=name)


def _agent(hasn_id: str, star_id: str, name: str, owner_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        hasn_id=hasn_id,
        star_id=star_id,
        display_name=name,
        name=name,
        owner_id=owner_id,
    )


def _request(
    req_id: int, from_id: str, to_id: str, message: str = '',
    *, to_type: str = 'human', to_owner_id: str | None = None,
) -> SimpleNamespace:
    """hasn_contact_requests 行。"""
    return SimpleNamespace(
        id=req_id,
        from_id=from_id,
        to_id=to_id,
        to_type=to_type,
        to_owner_id=to_owner_id or to_id,
        relation_type='social',
        requested_trust_level=2,
        status='pending',
        message=message,
        channel_source='manual',
        add_source='other',
        created_time=None,
    )


def _humans_lookup(*humans: SimpleNamespace) -> AsyncMock:
    """按 hasn_id 返回对应 human 的 get_by_hasn_id mock（端点会查多个身份）。"""
    table = {h.hasn_id: h for h in humans}

    async def _lookup(_db, hasn_id):  # noqa: ANN001
        return table.get(hasn_id)

    return AsyncMock(side_effect=_lookup)


class _GatewayStub:
    """给 contacts 单测注入的最小 realtime gateway 替身。"""

    def __init__(self, push_to_owner: AsyncMock) -> None:
        self.push_to_owner = push_to_owner


@pytest.mark.asyncio
async def test_send_request_pushes_request_received_to_target() -> None:
    """A 发好友请求给 B → 后端推 hasn.contact.request_received 给 B."""
    receiver = _human(RECEIVER, RECEIVER_STAR, 'Bob')
    sender = _human(SENDER, SENDER_STAR, 'Alice')
    push = AsyncMock(return_value=True)

    with patch(
        'backend.app.hasn.api.v1.app.contacts._resolve_star_id',
        new=AsyncMock(return_value=(receiver, 'human')),
    ), patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_contacts_dao.get_relation',
        new=AsyncMock(return_value=None),
    ), patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_contact_requests_dao.get_active_pending',
        new=AsyncMock(return_value=None),
    ), patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_contact_requests_dao.create_request',
        new=AsyncMock(return_value=_request(42, SENDER, RECEIVER, 'hi')),
    ) as create_request, patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_humans_dao.get_by_hasn_id',
        new=_humans_lookup(sender, receiver),
    ), patch(
        'backend.app.hasn.service.hasn_contacts_service._realtime_gateway',
        new=_GatewayStub(push),
    ):
        db = AsyncMock()
        await send_contact_request(
            obj_in=HasnContactRequestReq(target_star_id=RECEIVER_STAR, message='hi'),
            db=db,
            auth={'hasn_id': SENDER},
        )

    create_request.assert_awaited_once()
    assert create_request.await_args.kwargs['from_id'] == SENDER
    assert create_request.await_args.kwargs['to_id'] == RECEIVER
    assert create_request.await_args.kwargs['channel_source'] == 'manual'
    push.assert_awaited_once()
    target, frame = push.await_args.args
    assert target == RECEIVER
    assert frame.method == 'hasn.contact.request_received'
    assert frame.params['owner_id'] == RECEIVER  # daemon 用 owner_id 路由 ws sink
    assert frame.params['request_id'] == 42
    assert frame.params['from_peer']['hasn_id'] == SENDER
    assert frame.params['from_peer']['star_id'] == SENDER_STAR
    assert frame.params['message'] == 'hi'


@pytest.mark.asyncio
async def test_send_agent_request_keeps_agent_target_and_pushes_to_owner() -> None:
    """A 请求添加 B 的『分身』→ 目标保持分身本体（to_type=agent），审批人=主人 B，推给 B."""
    receiver_agent = _agent(RECEIVER_AGENT, RECEIVER_AGENT_STAR, 'Bob Helper', RECEIVER)
    sender = _human(SENDER, SENDER_STAR, 'Alice')
    push = AsyncMock(return_value=True)

    with patch(
        'backend.app.hasn.api.v1.app.contacts._resolve_star_id',
        new=AsyncMock(return_value=(receiver_agent, 'agent')),
    ), patch(
        # 无既有 agent 边 / 未被主人拉黑 / 主人 trust 缺省 → 落 trust=2
        'backend.app.hasn.api.v1.app.contacts.hasn_contacts_dao.get_relation',
        new=AsyncMock(return_value=None),
    ), patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_contact_requests_dao.get_active_pending',
        new=AsyncMock(return_value=None),
    ), patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_contact_requests_dao.create_request',
        new=AsyncMock(return_value=_request(
            43, SENDER, RECEIVER_AGENT, 'hi agent', to_type='agent', to_owner_id=RECEIVER,
        )),
    ) as create_request, patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_humans_dao.get_by_hasn_id',
        new=_humans_lookup(sender),
    ), patch(
        'backend.app.hasn.service.hasn_contacts_service._realtime_gateway',
        new=_GatewayStub(push),
    ):
        db = AsyncMock()
        await send_contact_request(
            obj_in=HasnContactRequestReq(target_star_id=RECEIVER_AGENT_STAR, message='hi agent'),
            db=db,
            auth={'hasn_id': SENDER},
        )

    create_request.assert_awaited_once()
    # 目标保持分身本体；审批人=主人 B；信任与主人一致（缺省 2）。
    assert create_request.await_args.kwargs['to_id'] == RECEIVER_AGENT
    assert create_request.await_args.kwargs['to_owner_id'] == RECEIVER
    assert create_request.await_args.kwargs['to_type'] == 'agent'
    assert create_request.await_args.kwargs['requested_trust_level'] == 2

    push.assert_awaited_once()
    target, frame = push.await_args.args
    assert target == RECEIVER  # 推给分身主人（审批人）
    assert frame.method == 'hasn.contact.request_received'
    assert frame.params['owner_id'] == RECEIVER
    assert frame.params['request_id'] == 43
    assert frame.params['from_peer']['hasn_id'] == SENDER
    assert frame.params['target']['hasn_id'] == RECEIVER_AGENT
    assert frame.params['target']['type'] == 'agent'


@pytest.mark.asyncio
async def test_respond_accept_pushes_connected_to_original_sender() -> None:
    """B 通过 A 的请求 → 后端推 hasn.contact.connected 给 A (from_id)."""
    request = _request(42, SENDER, RECEIVER)
    acceptor = _human(RECEIVER, RECEIVER_STAR, 'Bob')
    push = AsyncMock(return_value=True)

    with patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_contact_requests_dao.get',
        new=AsyncMock(return_value=request),
    ), patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_contacts_dao.upsert_connected',
        new=AsyncMock(return_value=SimpleNamespace(id=900)),
    ) as upsert, patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_contact_requests_dao.mark_accepted',
        new=AsyncMock(),
    ) as mark_accepted, patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_humans_dao.get_by_hasn_id',
        new=AsyncMock(return_value=acceptor),
    ), patch(
        'backend.app.hasn.api.v1.app.contacts._realtime_gateway',
        new=_GatewayStub(push),
    ):
        db = AsyncMock()
        await respond_to_request(
            request_id=42,
            obj_in=HasnContactRespondReq(action='accept'),
            db=db,
            auth={'hasn_id': RECEIVER},
        )

    # 双向边各 UPSERT 一次
    assert upsert.await_count == 2
    # resulting_contact_id 回填为发起方→目标那条边的 id
    mark_accepted.assert_awaited_once()
    assert mark_accepted.await_args.kwargs['resulting_contact_id'] == 900
    push.assert_awaited_once()
    target, frame = push.await_args.args
    assert target == SENDER  # 推给原发起方,不是 acceptor
    assert frame.method == 'hasn.contact.connected'
    assert frame.params['owner_id'] == SENDER  # daemon 用 owner_id 路由 ws sink
    assert frame.params['request_id'] == 42
    assert frame.params['peer']['hasn_id'] == RECEIVER
    assert frame.params['peer']['name'] == 'Bob'
    assert frame.params['trust_level'] == 2


@pytest.mark.asyncio
async def test_respond_reject_does_not_push() -> None:
    """B 拒绝 → 不推任何 ws (rejected 状态发起方下次轮询自然能查到)."""
    request = _request(42, SENDER, RECEIVER)
    push = AsyncMock(return_value=True)
    with patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_contact_requests_dao.get',
        new=AsyncMock(return_value=request),
    ), patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_contact_requests_dao.mark_rejected',
        new=AsyncMock(),
    ) as mark_rejected, patch(
        'backend.app.hasn.api.v1.app.contacts._realtime_gateway',
        new=_GatewayStub(push),
    ):
        db = AsyncMock()
        await respond_to_request(
            request_id=42,
            obj_in=HasnContactRespondReq(action='reject'),
            db=db,
            auth={'hasn_id': RECEIVER},
        )
    mark_rejected.assert_awaited_once()
    push.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_request_succeeds_even_if_ws_push_fails() -> None:
    """WS push 抛异常时 HTTP 仍然 success (fail-open, 不阻塞主链路)."""
    receiver = _human(RECEIVER, RECEIVER_STAR, 'Bob')
    sender = _human(SENDER, SENDER_STAR, 'Alice')

    with patch(
        'backend.app.hasn.api.v1.app.contacts._resolve_star_id',
        new=AsyncMock(return_value=(receiver, 'human')),
    ), patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_contacts_dao.get_relation',
        new=AsyncMock(return_value=None),
    ), patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_contact_requests_dao.get_active_pending',
        new=AsyncMock(return_value=None),
    ), patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_contact_requests_dao.create_request',
        new=AsyncMock(return_value=_request(42, SENDER, RECEIVER, 'hi')),
    ), patch(
        'backend.app.hasn.api.v1.app.contacts.hasn_humans_dao.get_by_hasn_id',
        new=_humans_lookup(sender, receiver),
    ), patch(
        'backend.app.hasn.service.hasn_contacts_service._realtime_gateway',
        new=_GatewayStub(AsyncMock(side_effect=RuntimeError('redis unreachable'))),
    ):
        db = AsyncMock()
        resp = await send_contact_request(
            obj_in=HasnContactRequestReq(target_star_id=RECEIVER_STAR, message='hi'),
            db=db,
            auth={'hasn_id': SENDER},
        )

    # response_base.success 包了一层. 关键: 没炸即可.
    assert resp.data['request_id'] == 42
    assert resp.data['status'] == 'pending'
