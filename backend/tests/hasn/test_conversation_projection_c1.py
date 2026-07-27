"""会话一等实体重构 C1 单测（doc02 §3.1–§3.4）。

覆盖：会话对象投影形状、受众计算、详情读鉴权矩阵（本人/分身主人/群成员/无关者/不存在）、
message.new 瘦事件形状（不含 relation_view/peer/conversation_type/group_name）、conversation.updated。

沿用本仓 message_router 测试的 monkeypatch + dataclass stub 风格（不依赖真 PG ORM）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest

from backend.app.hasn.service import conversation_projection as cp


@dataclass
class _ConvImpl:
    id: str = '00000000-0000-0000-0000-0000000000aa'
    type: str = 'direct'
    participant_a_id: str = 'h_alice'
    participant_a_type: str = 'human'
    participant_b_id: str | None = 'a_bot'
    participant_b_type: str | None = 'agent'
    group_id: str | None = None
    group_name: str | None = None
    group_avatar_url: str | None = None
    group_owner_id: str | None = None
    group_description: str | None = None
    agent_policy: str = 'free'
    revision: int = 3
    created_time: datetime = datetime(2026, 7, 14, tzinfo=timezone.utc)
    updated_time: datetime = datetime(2026, 7, 14, tzinfo=timezone.utc)


_Conv: Any = _ConvImpl


@dataclass
class _MemberImpl:
    member_id: str
    member_type: str = 'human'
    role: str = 'member'


_Member: Any = _MemberImpl
TEST_DB: Any = object()


# ─── 纯函数：投影形状 ───


def test_direct_projection_shape() -> None:
    proj = cp.build_conversation_projection(_Conv())
    assert proj['conversation_id'] == '00000000-0000-0000-0000-0000000000aa'
    assert proj['type'] == 'direct'
    assert proj['group'] is None
    assert proj['revision'] == 3
    assert proj['participants'] == [
        {'hasn_id': 'h_alice', 'hasn_type': 'human', 'role': 'member'},
        {'hasn_id': 'a_bot', 'hasn_type': 'agent', 'role': 'member'},
    ]


def test_group_projection_shape() -> None:
    conv = _Conv(
        id='00000000-0000-0000-0000-0000000000bb',
        type='group',
        participant_a_id='h_owner',
        participant_b_id=None,
        group_id='g:500001',
        group_name='测试群',
        group_owner_id='h_owner',
    )
    members = [_Member('h_owner', 'human', 'owner'), _Member('a_bot', 'agent', 'member')]
    proj = cp.build_conversation_projection(conv, members=members)
    assert proj['type'] == 'group'
    assert proj['group']['group_id'] == 'g:500001'
    assert proj['group']['name'] == '测试群'
    assert proj['participants'] == [
        {'hasn_id': 'h_owner', 'hasn_type': 'human', 'role': 'owner'},
        {'hasn_id': 'a_bot', 'hasn_type': 'agent', 'role': 'member'},
    ]


def test_content_type_to_mime() -> None:
    assert cp.content_type_to_mime(1) == 'text'
    assert cp.content_type_to_mime(2) == 'image/*'
    assert cp.content_type_to_mime(5) == 'application/x.card+json'
    assert cp.content_type_to_mime(99) == 'text'


# ─── 受众计算 ───


@pytest.mark.asyncio
async def test_audience_direct_agent_resolves_to_owner(monkeypatch) -> None:
    # a_bot 的主人是 h_owner；受众 = {h_alice(人本身), h_owner(分身主人)}
    monkeypatch.setattr(
        cp, '_resolve_owner_ids', AsyncMock(return_value={'h_alice': 'h_alice', 'a_bot': 'h_owner'})
    )
    audience = await cp.compute_audience_owner_ids(TEST_DB, _Conv())
    assert audience == ['h_alice', 'h_owner']


@pytest.mark.asyncio
async def test_audience_a2a_two_owners(monkeypatch) -> None:
    # A2A：两个分身各自解析主人 → 两个主人都在受众（A2AFIRST 补推补丁天然消失）
    conv = _Conv(participant_a_id='a_x', participant_a_type='agent', participant_b_id='a_y', participant_b_type='agent')
    monkeypatch.setattr(cp, '_resolve_owner_ids', AsyncMock(return_value={'a_x': 'h_m1', 'a_y': 'h_m2'}))
    audience = await cp.compute_audience_owner_ids(TEST_DB, conv)
    assert audience == ['h_m1', 'h_m2']


# ─── 详情读鉴权矩阵 ───


def _patch_load(monkeypatch, conv, members, owner_map) -> None:
    monkeypatch.setattr(cp, '_fetch_conversation', AsyncMock(return_value=conv))
    monkeypatch.setattr(cp, '_load_group_members', AsyncMock(return_value=members or []))
    monkeypatch.setattr(cp, '_resolve_owner_ids', AsyncMock(return_value=owner_map))


@pytest.mark.asyncio
async def test_load_participant_human_allowed(monkeypatch) -> None:
    _patch_load(monkeypatch, _Conv(), None, {'h_alice': 'h_alice', 'a_bot': 'h_owner'})
    proj = await cp.load_conversation_object(TEST_DB, _Conv().id, viewer_owner_hasn_id='h_alice')
    assert proj is not None and proj['type'] == 'direct'


@pytest.mark.asyncio
async def test_load_agent_owner_allowed(monkeypatch) -> None:
    # 主人拉「自己分身参与的 A2A/A2H 会话」——这是主人通道的权限本体
    _patch_load(monkeypatch, _Conv(), None, {'h_alice': 'h_alice', 'a_bot': 'h_owner'})
    proj = await cp.load_conversation_object(TEST_DB, _Conv().id, viewer_owner_hasn_id='h_owner')
    assert proj is not None


@pytest.mark.asyncio
async def test_load_group_member_allowed(monkeypatch) -> None:
    conv = _Conv(type='group', participant_b_id=None, group_id='g:1', group_owner_id='h_owner')
    members = [_Member('h_owner', 'human', 'owner'), _Member('a_bot', 'agent')]
    _patch_load(monkeypatch, conv, members, {'h_owner': 'h_owner', 'a_bot': 'h_owner'})
    proj = await cp.load_conversation_object(TEST_DB, conv.id, viewer_owner_hasn_id='h_owner')
    assert proj is not None and proj['type'] == 'group'


@pytest.mark.asyncio
async def test_load_unrelated_owner_denied(monkeypatch) -> None:
    # 无关 owner 不在受众 → None（端点回 403/404）
    _patch_load(monkeypatch, _Conv(), None, {'h_alice': 'h_alice', 'a_bot': 'h_owner'})
    proj = await cp.load_conversation_object(TEST_DB, _Conv().id, viewer_owner_hasn_id='h_stranger')
    assert proj is None


@pytest.mark.asyncio
async def test_load_not_found(monkeypatch) -> None:
    monkeypatch.setattr(cp, '_fetch_conversation', AsyncMock(return_value=None))
    proj = await cp.load_conversation_object(TEST_DB, 'nope', viewer_owner_hasn_id='h_alice')
    assert proj is None


# ─── 瘦事件形状 ───


def test_message_new_event_shape() -> None:
    envelope = cp.build_message_new_envelope(
        owner_id='h_owner',
        conversation_id='conv-1',
        message_id='1001',
        sender_hasn_id='a_bot',
        origin_node_id='node-xyz',
        content_type=1,
        content_body={'text': 'hi'},
        local_id='lid-1',
        created_at=1700000000,
    )
    assert envelope.event_type == 'message.new' == cp.MESSAGE_NEW_EVENT_TYPE
    assert envelope.aggregate_type == 'message'
    assert envelope.aggregate_id == '1001'
    assert envelope.producer == 'hasn_im'
    assert envelope.source_event_id == 'message:1001'
    payload = envelope.payload
    # 瘦事件只带这 8 个字段，绝不含旧框定字段
    assert set(payload.keys()) == {
        'conversation_id', 'message_id', 'sender_hasn_id', 'origin_node_id',
        'content_type', 'content_body', 'local_id', 'created_at',
    }
    assert payload['content_type'] == 'text'
    assert payload['origin_node_id'] == 'node-xyz'
    for forbidden in ('relation_view', 'peer', 'conversation_type', 'group_name', 'direction'):
        assert forbidden not in payload


def test_conversation_updated_emits_per_owner() -> None:
    envelopes = cp.build_conversation_updated_envelopes(
        conversation_id='conv-1',
        revision=7,
        owner_ids=['h_b', 'h_a', 'h_a'],
    )
    # 去重 + 排序：h_a、h_b 各一条
    assert [item.payload['conversation_id'] for item in envelopes] == [
        'conv-1',
        'conv-1',
    ]
    assert [item.owner_id for item in envelopes] == ['h_a', 'h_b']
    assert all(item.event_type == 'conversation.updated' for item in envelopes)
    assert all(item.payload['revision'] == 7 for item in envelopes)
    assert len({item.source_event_id for item in envelopes}) == 1
