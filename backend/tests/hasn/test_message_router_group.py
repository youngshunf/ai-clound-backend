"""HASN 群消息路由测试。

覆盖缺口 4：群组目标 g:* 可正常路由；Agent 群成员在 Runtime 缺失/不在线时，Owner 在线节点也会收到消息，保证纯 IM 客户端可用。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest


@dataclass
class _Group:
    id: str = '00000000-0000-0000-0000-000000000001'
    group_id: str = 'g:500001'
    group_name: str = '测试群'
    group_owner_id: str = 'h_owner'
    mute_all: bool = False
    agent_policy: str = 'free'


@dataclass
class _Member:
    member_id: str
    member_type: str = 'human'
    role: str = 'member'
    member_name: str = ''
    member_star_id: str = ''


@dataclass
class _Msg:
    id: int = 1001
    from_type: int = 1
    to_type: int = 4
    created_time: datetime = datetime(2026, 4, 27, tzinfo=timezone.utc)


class _DB:
    commits = 0

    async def commit(self) -> None:
        self.commits += 1


@pytest.mark.asyncio
async def test_group_message_fans_out_to_human_and_agent_owner(monkeypatch) -> None:
    from backend.app.hasn.service import message_router as mr

    group = _Group()
    pushed = []

    monkeypatch.setattr(
        mr,
        'resolve_target',
        AsyncMock(return_value={
            'hasn_id': group.group_id,
            'entity_type': 'group',
            'conversation_id': group.id,
            'owner_id': group.group_owner_id,
        }),
    )
    monkeypatch.setattr(mr, 'get_group_conversation', AsyncMock(return_value=group))
    monkeypatch.setattr(mr, 'check_group_send_permission', AsyncMock(return_value={'allowed': True}))
    monkeypatch.setattr(mr, 'persist_message', AsyncMock(return_value=_Msg()))
    monkeypatch.setattr(
        mr,
        'list_group_members',
        AsyncMock(return_value=[
            _Member('h_sender'),
            _Member('h_peer'),
            _Member('a_peer', member_type='agent'),
        ]),
    )
    monkeypatch.setattr(mr, '_agent_owner_id', AsyncMock(return_value='h_agent_owner'))
    monkeypatch.setattr(mr, 'increment_unread_for', AsyncMock(return_value=None))
    monkeypatch.setattr(
        mr,
        '_push_message_to',
        AsyncMock(side_effect=lambda target, payload: pushed.append((target, payload))),
    )
    # G2-b：捕获群离线回放 sync_event。
    from backend.app.hasn.service import hasn_sync_service as sync_service_module

    sync_calls: list[dict] = []
    monkeypatch.setattr(
        sync_service_module.SqlAlchemySyncGateway,
        '_append_sync_event',
        AsyncMock(side_effect=lambda _self_db, **kw: sync_calls.append(kw)),
    )

    result = await mr.route_message(
        _DB(),
        from_id='h_sender',
        to_target='g:500001',
        content={'text': 'hello group'},
    )

    assert result['error'] is False
    assert result['conversation_id'] == group.id
    # Agent Runtime 与 Owner 节点都在投递列表中；即使 Runtime 缺失，Owner 仍可作为纯 IM 客户端收信。
    assert result['delivered_to'] == ['a_peer', 'h_agent_owner', 'h_peer']
    assert [target for target, _ in pushed] == ['a_peer', 'h_agent_owner', 'h_peer']
    assert pushed[0][1]['method'] == 'hasn.message.received'
    assert pushed[0][1]['params']['message']['to_entity_type'] == 'group'
    assert pushed[0][1]['params']['message']['to_type'] == 4
    # G2-b 离线回放：发送方 message.sent + 两个不同 owner 的 message.received（h_peer / a_peer 的 owner h_agent_owner）。
    by_owner = {(c['owner_id'], c['event_type']) for c in sync_calls}
    assert ('h_sender', 'message.sent') in by_owner
    assert ('h_peer', 'message.received') in by_owner
    assert ('h_agent_owner', 'message.received') in by_owner
    assert all(c['payload']['group_id'] == 'g:500001' for c in sync_calls)


@pytest.mark.asyncio
async def test_group_route_rejects_non_member(monkeypatch) -> None:
    from backend.app.hasn.service import message_router as mr

    monkeypatch.setattr(mr, 'resolve_target', AsyncMock(return_value={'hasn_id': 'g:500001', 'entity_type': 'group'}))
    monkeypatch.setattr(mr, 'get_group_conversation', AsyncMock(return_value=_Group()))
    monkeypatch.setattr(mr, 'check_group_send_permission', AsyncMock(return_value={'allowed': False, 'reason': '不是该群成员'}))

    result = await mr.route_message(_DB(), from_id='h_outsider', to_target='g:500001', content={'text': 'x'})

    assert result == {'error': True, 'code': 2002, 'message': '不是该群成员'}


def test_entity_type_int_supports_group() -> None:
    from backend.app.hasn.service.message_router import _entity_type_int

    assert _entity_type_int('g:500001') == 4


@pytest.mark.asyncio
async def test_group_envelope_carries_policy_mentions_and_sender(monkeypatch) -> None:
    """G2：群 envelope 富化——agent_policy + mentions + mention_all + 发言人展示名/唤星号。

    这些是 daemon(G4) group_participation_gate 的权威数据（决定唤醒哪些分身）与接收侧
    名册/"本条来自 X"标签的渲染来源。
    """
    from backend.app.hasn.service import message_router as mr

    group = _Group(agent_policy='mention_only')
    pushed = []

    monkeypatch.setattr(
        mr,
        'resolve_target',
        AsyncMock(return_value={
            'hasn_id': group.group_id,
            'entity_type': 'group',
            'conversation_id': group.id,
            'owner_id': group.group_owner_id,
        }),
    )
    monkeypatch.setattr(mr, 'get_group_conversation', AsyncMock(return_value=group))
    monkeypatch.setattr(mr, 'check_group_send_permission', AsyncMock(return_value={'allowed': True}))
    monkeypatch.setattr(mr, 'persist_message', AsyncMock(return_value=_Msg()))
    monkeypatch.setattr(
        mr,
        'list_group_members',
        AsyncMock(return_value=[
            _Member('h_sender', member_name='发送者甲', member_star_id='100#me'),
            _Member('a_peer', member_type='agent'),
        ]),
    )
    monkeypatch.setattr(mr, '_agent_owner_id', AsyncMock(return_value='h_agent_owner'))
    monkeypatch.setattr(mr, 'increment_unread_for', AsyncMock(return_value=None))
    monkeypatch.setattr(
        mr,
        '_push_message_to',
        AsyncMock(side_effect=lambda target, payload: pushed.append((target, payload))),
    )
    from backend.app.hasn.service import hasn_sync_service as sync_service_module

    monkeypatch.setattr(
        sync_service_module.SqlAlchemySyncGateway,
        '_append_sync_event',
        AsyncMock(return_value=None),
    )

    mentions = [{'hasn_id': 'a_peer', 'entity_type': 'agent'}]
    result = await mr.route_message(
        _DB(),
        from_id='h_sender',
        to_target='g:500001',
        content={'text': '@分身 在吗'},
        context={'mentions': mentions, 'mention_all': False},
    )

    assert result['error'] is False
    env = pushed[0][1]['params']['message']
    assert env['agent_policy'] == 'mention_only'
    assert env['group']['agent_policy'] == 'mention_only'
    assert env['mentions'] == mentions
    assert env['mention_all'] is False
    assert env['from_display_name'] == '发送者甲'
    assert env['from_star_id'] == '100#me'
