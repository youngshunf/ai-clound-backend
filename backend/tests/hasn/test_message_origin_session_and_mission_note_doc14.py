"""分身发起会话因果闭环 S1 单测（doc14 §6.2 B 刀 + §6.5 E 刀）。

覆盖：
- ``origin_session_id`` 事件受众分叉（发送方 owner 有、对端 owner 无——§7-1 隐私红线）；
- 无溯源时事件形状与 doc02 瘦事件**逐字节一致**（不多出 key）；
- ``mission_note`` 投影只对归属 owner 序列化，对端 owner / 无 viewer 一律裁剪；
- 入参不可伪造：``message.send`` schema 不出现 ``origin_session_id``，分身塞了也不认；
- ``mission_note`` 长度上限按字素簇计、超限报错不静默截断。

沿用本仓 message_router / conversation_projection 测试的 monkeypatch + dataclass stub 风格
（不依赖真 PG ORM：PostgreSQL JSONB 与 SQLite 不兼容，业务 ORM 不入内存库）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from backend.app.hasn.service import conversation_projection as cp
from backend.app.hasn.service import message_router as mr
from backend.app.mcp import trust_gate
from backend.app.mcp.tools.message import MessageSendTool

_SESSION = 'sess_work_01J8'


@dataclass
class _Conv:
    id: str = '00000000-0000-0000-0000-0000000000aa'
    type: str = 'direct'
    participant_a_id: str = 'a_bot'
    participant_a_type: str = 'agent'
    participant_b_id: str | None = 'h_peer'
    participant_b_type: str | None = 'human'
    group_id: str | None = None
    group_name: str | None = None
    group_avatar_url: str | None = None
    group_owner_id: str | None = None
    group_description: str | None = None
    agent_policy: str = 'free'
    revision: int = 1
    mission_note: str | None = None
    mission_note_owner_id: str | None = None
    created_time: datetime = datetime(2026, 7, 15, tzinfo=timezone.utc)
    updated_time: datetime = datetime(2026, 7, 15, tzinfo=timezone.utc)


@dataclass
class _Msg:
    id: int = 1001
    created_time: datetime = datetime(2026, 7, 15, tzinfo=timezone.utc)


# ─── 事件形状：带溯源 / 不带溯源 ───


class _FakeGw:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def _append_sync_event(self, db, **kwargs):
        self.calls.append(kwargs)
        return len(self.calls)


@pytest.mark.asyncio
async def test_event_carries_origin_session_id_when_present() -> None:
    gw = _FakeGw()
    await cp.append_message_new_event(
        gw,
        object(),
        owner_id='h_master',
        conversation_id='conv-1',
        message_id='1001',
        sender_hasn_id='a_bot',
        origin_node_id='node-1',
        content_type=1,
        content_body={'text': 'hi'},
        local_id=None,
        created_at=1700000000,
        origin_session_id=_SESSION,
    )
    payload = gw.calls[0]['payload']
    assert payload['origin_session_id'] == _SESSION


@pytest.mark.asyncio
async def test_event_shape_unchanged_without_origin_session_id() -> None:
    """无溯源 → key 直接缺席（不是 None）：对端 owner 的事件与 doc02 瘦事件逐字段一致。"""
    gw = _FakeGw()
    await cp.append_message_new_event(
        gw,
        object(),
        owner_id='h_peer',
        conversation_id='conv-1',
        message_id='1001',
        sender_hasn_id='a_bot',
        origin_node_id='node-1',
        content_type=1,
        content_body={'text': 'hi'},
        local_id=None,
        created_at=1700000000,
    )
    payload = gw.calls[0]['payload']
    assert set(payload.keys()) == {
        'conversation_id', 'message_id', 'sender_hasn_id', 'origin_node_id',
        'content_type', 'content_body', 'local_id', 'created_at',
    }
    assert 'origin_session_id' not in payload


# ─── §7-1 隐私红线：受众分叉 ───


@pytest.mark.asyncio
async def test_fanout_forks_origin_session_id_by_audience(monkeypatch) -> None:
    """发起溯源是发送方的执行细节：只有发送方 owner 的事件+推送带它，对端一律剥除。"""
    events: list[dict] = []

    async def _fake_append(gw, db, *, owner_id, origin_session_id=None, **kwargs):
        events.append({'owner_id': owner_id, 'origin_session_id': origin_session_id})
        return 1

    # a_bot 的主人是 h_master（发送方 owner）；h_peer 是对端 owner。
    monkeypatch.setattr(cp, 'compute_audience_owner_ids', AsyncMock(return_value=['h_master', 'h_peer']))
    monkeypatch.setattr(cp, '_resolve_owner_ids', AsyncMock(return_value={'a_bot': 'h_master'}))
    monkeypatch.setattr(cp, 'append_message_new_event', _fake_append)

    # R1-08 后：扇出不再内联 push，而是把待发推送收集进 deferred_pushes 返回（由 route_message
    # 在主链 commit 之后 _flush_pushes 发出）。§7-1 隐私红线在收集的推送载荷上同样成立。
    audience, deferred_pushes = await mr._fanout_message_new(
        object(),
        _FakeGw(),
        _Conv(),
        from_id='a_bot',
        msg=_Msg(),
        content={'text': '你好，想约个时间'},
        content_type=1,
        local_id=None,
        origin_node_id='node-1',
        origin_session_id=_SESSION,
    )

    assert audience == ['h_master', 'h_peer']
    by_owner = {e['owner_id']: e['origin_session_id'] for e in events}
    assert by_owner['h_master'] == _SESSION, '发送方 owner 应看到自己的发起溯源'
    assert by_owner['h_peer'] is None, '§7-1 红线：对端 owner 的事件绝不带 origin_session_id'

    push_by_owner = {owner: msg['params'] for owner, msg in deferred_pushes}
    assert push_by_owner['h_master']['origin_session_id'] == _SESSION
    assert 'origin_session_id' not in push_by_owner['h_peer'], '§7-1 红线：对端推送同样剥除'


@pytest.mark.asyncio
async def test_fanout_skips_owner_resolution_without_origin_session_id(monkeypatch) -> None:
    """无溯源 → 不解析发送方 owner（省一次查询），且两端事件都不带该字段。"""
    events: list[dict] = []

    async def _fake_append(gw, db, *, owner_id, origin_session_id=None, **kwargs):
        events.append({'owner_id': owner_id, 'origin_session_id': origin_session_id})
        return 1

    resolve_spy = AsyncMock(return_value={'a_bot': 'h_master'})
    monkeypatch.setattr(cp, 'compute_audience_owner_ids', AsyncMock(return_value=['h_master', 'h_peer']))
    monkeypatch.setattr(cp, '_resolve_owner_ids', resolve_spy)
    monkeypatch.setattr(cp, 'append_message_new_event', _fake_append)
    from backend.app.hasn.service.ws_router import ws_router

    monkeypatch.setattr(ws_router, 'push_to_owner', AsyncMock())

    await mr._fanout_message_new(
        object(),
        _FakeGw(),
        _Conv(),
        from_id='a_bot',
        msg=_Msg(),
        content={'text': 'hi'},
        content_type=1,
        local_id=None,
        origin_node_id='node-1',
    )
    resolve_spy.assert_not_awaited()
    assert all(e['origin_session_id'] is None for e in events)


# ─── E 刀：mission_note 投影裁剪 ───


def test_mission_note_visible_to_owning_master() -> None:
    conv = _Conv(mission_note='替主人约王工聊周五联调时间', mission_note_owner_id='h_master')
    proj = cp.build_conversation_projection(conv, viewer_owner_hasn_id='h_master')
    assert proj['mission_note'] == '替主人约王工聊周五联调时间'


def test_mission_note_cropped_for_peer_owner() -> None:
    conv = _Conv(mission_note='替主人约王工聊周五联调时间', mission_note_owner_id='h_master')
    proj = cp.build_conversation_projection(conv, viewer_owner_hasn_id='h_peer')
    assert 'mission_note' not in proj, '差事背景是发送方 owner 私有框定，对端不可见'


def test_mission_note_cropped_without_viewer() -> None:
    """fail-closed：拿不到 viewer 身份就不吐（宁可少给，不可错给）。"""
    conv = _Conv(mission_note='替主人约王工聊周五联调时间', mission_note_owner_id='h_master')
    assert 'mission_note' not in cp.build_conversation_projection(conv)


def test_no_mission_note_key_when_unset() -> None:
    assert 'mission_note' not in cp.build_conversation_projection(_Conv(), viewer_owner_hasn_id='h_master')


# ─── 入参不可伪造 ───


def test_send_schema_has_mission_note_but_never_origin_session_id() -> None:
    schema = MessageSendTool().input_schema
    props = schema['properties']
    assert 'mission_note' in props
    assert 'origin_session_id' not in props, 'doc14 §6.2：溯源 Server 侧自动填，绝不进入参 schema'
    assert trust_gate.RESERVED_SESSION_ID not in props


def test_trust_gate_strips_reserved_session_arg() -> None:
    cleaned, sid = trust_gate.pop_session_id({'to': 'h_peer', trust_gate.RESERVED_SESSION_ID: _SESSION})
    assert cleaned == {'to': 'h_peer'}, '保留参数必须剥离，工具体不该见到它'
    assert sid == _SESSION


# R1-05 切片①后：direct（人/分身）发送经 ImGateway port（ensure→send）。溯源经 principal、
# 差事背景经 ensure 命令流入——本组「入参不可伪造 / 溯源留 NULL / mission_note 透传」用例
# 改断言 port 收到的强类型入参（等价语义、更贴新契约），不再窥探 route_message kwargs。


class _SpyGateway:
    """桩 ImGateway：记录 ensure/send 收到的命令 + principal，send 返回已送达态。"""

    def __init__(self) -> None:
        self.ensure_cmd: object | None = None
        self.ensure_principal: object | None = None
        self.send_principal: object | None = None

    async def ensure_direct_conversation(self, command, principal):
        from backend.app.hasn_im.ports.dto import ConversationRef

        self.ensure_cmd = command
        self.ensure_principal = principal
        return ConversationRef(conversation_id='c1')

    async def send_message(self, command, principal):
        from backend.app.hasn_im.ports.dto import DeliveryState, SendMessageResult

        self.send_principal = principal
        return SendMessageResult(
            delivery_state=DeliveryState.ACCEPTED, conversation_id='c1', message_id=1
        )


async def _drive_send_via_port(monkeypatch, ctx, arguments) -> _SpyGateway:
    """用桩网关驱动一次 direct message.send，返回记录了入参的桩网关。"""
    gw = _SpyGateway()

    async def _fake_resolve(_db, target):
        return {'hasn_id': target, 'entity_type': 'human', 'name': target, 'owner_id': 'h_peer'}

    async def _fake_owner_ids(_db, hasn_ids):
        # 带 mission_note 时工具先解析发送方主人（走 conversation_projection），本组不碰库故替身接管。
        return {hid: 'h_master' for hid in hasn_ids}

    import backend.app.mcp.tools.message as message_mod

    monkeypatch.setattr(mr, 'resolve_target', _fake_resolve)
    monkeypatch.setattr(cp, '_resolve_owner_ids', _fake_owner_ids)
    monkeypatch.setattr(message_mod, 'get_im_gateway', lambda: gw)
    _patch_db_session(monkeypatch)
    await MessageSendTool().execute(ctx, arguments)
    return gw


@pytest.mark.asyncio
async def test_forged_origin_session_id_in_arguments_is_ignored(monkeypatch) -> None:
    """分身在入参里硬塞 origin_session_id → 工具只认 AgentContext，伪造值原样丢弃。"""
    ctx = _AgentCtx()
    ctx.session_id = _SESSION
    gw = await _drive_send_via_port(
        monkeypatch, ctx, {'to': 'h_peer', 'content': 'hi', 'origin_session_id': 'sess_受害者会话'}
    )
    # 溯源经 principal 流入 port（ensure/send 同一 principal），只认 AgentContext 真值。
    assert gw.send_principal.origin_session_id == _SESSION, '只认 AgentContext 的真值'


@pytest.mark.asyncio
async def test_mission_note_passed_through_and_length_capped(monkeypatch) -> None:
    tool = MessageSendTool()

    gw = await _drive_send_via_port(
        monkeypatch, _AgentCtx(), {'to': 'h_peer', 'content': 'hi', 'mission_note': '  替主人约时间  '}
    )
    # 差事背景经 ensure 命令流入 port（仅新建会话时落列），两侧空白裁掉。
    assert gw.ensure_cmd.mission_note == '替主人约时间', '两侧空白裁掉'

    # 全角中文按字素簇计数：501 字超限 → 报错，不静默截断（宁可让分身重写也不歪曲它的框定）
    _patch_db_session(monkeypatch)
    with pytest.raises(RuntimeError, match='mission_note 超长'):
        await tool.execute(_AgentCtx(), {'to': 'h_peer', 'content': 'hi', 'mission_note': '差' * 501})


@pytest.mark.asyncio
async def test_no_session_context_sends_null_origin(monkeypatch) -> None:
    """非派发路径直调（无会话上下文）→ 溯源留 NULL，发送照常（never over-block）。"""
    gw = await _drive_send_via_port(monkeypatch, _AgentCtx(), {'to': 'h_peer', 'content': 'hi'})
    assert gw.send_principal.origin_session_id is None
    assert gw.ensure_cmd.mission_note is None


# ─── 测试替身 ───


class _AgentCtx:
    """最小 AgentContext 替身：工具只读这几个字段。"""

    def __init__(self) -> None:
        self.hasn_id = 'a_bot'
        self.agent_hasn_id = 'a_bot'
        self.agent_name = '小助手'
        self.owner_hasn_id = 'h_master'
        self.session_id: str | None = None


def _patch_db_session(monkeypatch) -> None:
    """把工具内的 async_db_session 换成空壳（本组用例不碰库，路由/网关已被替身接管）。"""

    class _NullSession:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc):
            return False

    import backend.app.mcp.tools.message as message_mod

    monkeypatch.setattr(message_mod, 'async_db_session', lambda: _NullSession())
