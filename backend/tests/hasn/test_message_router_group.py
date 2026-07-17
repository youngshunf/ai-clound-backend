"""HASN 群消息路由测试（会话一等实体·doc02 §3.3）。

群消息经**统一受众扇出**（``_fanout_message_new``）投递：受众 = 群名册每个成员解析出的 owner
集合（去重稳定排序），对每个受众 owner 写一条 ``message.new`` 瘦事件 + 实时 ``push_to_owner``。
- 不再有 message.sent/received 双事件、``_grp_sync_event`` 双写、``hasn.message.received`` 直推；
- Agent 群成员的 owner 也在受众里——即使 Runtime 缺失/离线，owner 仍作纯 IM 客户端收信；
- @提及折进瘦事件 ``content_body``（daemon 群派发闸从会话对象镜像读 agent_policy、从
  content_body 取 mentions，事件本身不带 agent_policy/mentions 顶层字段）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest


@dataclass
class _Group:
    id: str = '00000000-0000-0000-0000-000000000001'
    type: str = 'group'  # compute_audience_owner_ids 据此走名册展开
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


def _patch_group_common(monkeypatch, mr, group, members, owner_map):
    """群路由 fanout 前的公共桩：目标解析/建群/权限/落库/名册/受众解析/实时推送。"""
    from backend.app.hasn.service import conversation_projection as cp
    from backend.app.hasn.service import hasn_sync_service as sync_service_module
    from backend.app.hasn.service.ws_router import ws_router

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
    monkeypatch.setattr(mr, 'list_group_members', AsyncMock(return_value=members))
    monkeypatch.setattr(mr, 'increment_unread_for', AsyncMock(return_value=None))
    # 受众解析：群名册成员 hasn_id → owner（agent 解析到其主人）。
    monkeypatch.setattr(cp, '_resolve_owner_ids', AsyncMock(return_value=owner_map))

    sync_calls: list[dict] = []
    monkeypatch.setattr(
        sync_service_module.SqlAlchemySyncGateway,
        '_append_sync_event',
        AsyncMock(side_effect=lambda _self_db, **kw: (sync_calls.append(kw), 1)[1]),
    )
    pushed: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        ws_router,
        'push_to_owner',
        AsyncMock(side_effect=lambda owner_id, payload: pushed.append((owner_id, payload))),
    )
    return sync_calls, pushed


@pytest.mark.asyncio
async def test_group_message_fans_out_to_member_owners(monkeypatch) -> None:
    """群受众 = 名册每个成员的 owner（agent→主人），去重稳定排序；每 owner 一条 message.new + push。"""
    from backend.app.hasn.service import message_router as mr

    group = _Group()
    members = [
        _Member('h_sender'),
        _Member('h_peer'),
        _Member('a_peer', member_type='agent'),
    ]
    owner_map = {'h_sender': 'h_sender', 'h_peer': 'h_peer', 'a_peer': 'h_agent_owner'}
    sync_calls, pushed = _patch_group_common(monkeypatch, mr, group, members, owner_map)

    result = await mr.route_message(
        _DB(),
        from_id='h_sender',
        to_target='g:500001',
        content={'text': 'hello group'},
    )

    assert result['error'] is False
    assert result['conversation_id'] == group.id
    # 受众 = {h_sender, h_peer, h_agent_owner}（去重 + 排序）。发送方 owner 本就在受众——
    # 「补推发送方」补丁天然消失；agent 成员的 owner 也在，Runtime 离线也照收（纯 IM 客户端）。
    assert result['delivered_to'] == ['h_agent_owner', 'h_peer', 'h_sender']
    # 每个受众 owner 收到一条实时 message.new 推送，且是唯一投递方法（无 message.received）。
    assert [owner for owner, _ in pushed] == ['h_agent_owner', 'h_peer', 'h_sender']
    assert all(p['method'] == 'hasn.message.new' for _, p in pushed)
    first_params = pushed[0][1]['params']
    assert set(first_params.keys()) == {
        'conversation_id', 'message_id', 'sender_hasn_id', 'origin_node_id',
        'content_type', 'content_body', 'local_id', 'created_at',
    }
    assert first_params['conversation_id'] == group.id
    assert first_params['sender_hasn_id'] == 'h_sender'
    # sync feed 也是每 owner 一条 message.new 瘦事件（离线补拉来源），无 message.sent/received。
    by_owner = {(c['owner_id'], c['event_type']) for c in sync_calls}
    assert by_owner == {
        ('h_agent_owner', 'message.new'),
        ('h_peer', 'message.new'),
        ('h_sender', 'message.new'),
    }
    assert all(c['aggregate_type'] == 'message' for c in sync_calls)
    for c in sync_calls:
        assert 'relation_view' not in c['payload'] and 'group_name' not in c['payload']


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
async def test_group_mentions_folded_into_content_body(monkeypatch) -> None:
    """@提及 + mention_all 折进 message.new 的 content_body（供 daemon 群派发闸读取）。

    瘦事件顶层严格 8 字段、不带 mentions/agent_policy——mentions 进 content_body、
    agent_policy 由 daemon 从会话对象镜像（group_meta.agent_policy）读，事件不携带。
    """
    from backend.app.hasn.service import message_router as mr

    group = _Group(agent_policy='mention_only')
    members = [
        _Member('h_sender', member_name='发送者甲', member_star_id='100#me'),
        _Member('a_peer', member_type='agent'),
    ]
    owner_map = {'h_sender': 'h_sender', 'a_peer': 'h_agent_owner'}
    _sync_calls, pushed = _patch_group_common(monkeypatch, mr, group, members, owner_map)

    mentions = [{'hasn_id': 'a_peer', 'entity_type': 'agent'}]
    result = await mr.route_message(
        _DB(),
        from_id='h_sender',
        to_target='g:500001',
        content={'text': '@分身 在吗'},
        context={'mentions': mentions, 'mention_all': False},
    )

    assert result['error'] is False
    params = pushed[0][1]['params']
    # 顶层严格 8 字段——无 mentions/mention_all/agent_policy 顶层键。
    assert set(params.keys()) == {
        'conversation_id', 'message_id', 'sender_hasn_id', 'origin_node_id',
        'content_type', 'content_body', 'local_id', 'created_at',
    }
    body = params['content_body']
    assert body['text'] == '@分身 在吗'
    assert body['mentions'] == mentions
    assert body['mention_all'] is False


@pytest.mark.asyncio
async def test_group_route_commits_exactly_once(monkeypatch) -> None:
    """R1-08 事务收口守卫：群路径主链**单 commit**。

    persist_message + 未读自增 + 扇出 sync feed 事件同一事务、只 commit 一次——删了原扇出前
    的中间 commit（它会把消息落库但 feed 尚未写、crash 即半状态）。实时 push 移到 commit 之后
    的 _flush_pushes，不再夹在事务里。commit 计数 > 1 即回归。
    """
    from backend.app.hasn.service import message_router as mr

    group = _Group()
    members = [_Member('h_sender'), _Member('h_peer')]
    owner_map = {'h_sender': 'h_sender', 'h_peer': 'h_peer'}
    _patch_group_common(monkeypatch, mr, group, members, owner_map)

    db = _DB()
    result = await mr.route_message(db, from_id='h_sender', to_target='g:500001', content={'text': 'once'})

    assert result['error'] is False
    assert db.commits == 1, f'群路径主链应恰好 commit 一次，实际 {db.commits} 次（半状态回归）'
