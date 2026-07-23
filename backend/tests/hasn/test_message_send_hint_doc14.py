"""分身发起会话因果闭环 S2 单测（doc14 §6.1 A 刀 · 返回体三态 hint）。

A 刀是**纯语义化**：把早就返回的 `conversation_id` 配上一句分身看得懂的行动指引，
三态（sent / suppressed / pending_confirmation）各一句。故本组用例只钉两件事：

1. **形状**：三态返回体都带 `hint`，且既有字段一个不少、一个不改（零行为变化）；
2. **内容**：hint 里含真实 conversation_id + `hasn.message.list` 用法，
   且明确「别轮询干等」（这正是 C 刀事件驱动回灌的前提，见设计 §4-5）。

R1-05 切片①后 direct 发送经 ImGateway port（ensure→send），故本组把三态桩重新指向新接缝——
patch `resolve_target`（认作 direct 人类目标）+ `get_im_gateway`（桩网关 send 产出指定
SendMessageResult 三态 / 抛 ImSendRejected），驱动工具映射逻辑 `_map_direct_send_result`
（与旧 route dict 直返逐字段一致，hint 语义不变）。零真实 DB 依赖。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from backend.app.hasn_im.application.errors import ImSendRejected
from backend.app.hasn_im.ports.dto import ConversationRef, DeliveryState, SendMessageResult
from backend.app.mcp.tools.message import MessageSendTool

_CONV = 'conv_01J8ABCDEF'

# doc02 既有返回体字段（A 刀之前）——逐态钉死，防「加 hint 顺手改了别的」。
_BASE_KEYS_SENT = {'message_id', 'conversation_id', 'delivered', 'reachable', 'status'}
_BASE_KEYS_PENDING = {'message_id', 'conversation_id', 'delivered', 'reachable', 'status', 'reason'}
_BASE_KEYS_SUPPRESSED = {
    'message_id', 'conversation_id', 'delivered', 'reachable', 'status', 'relation', 'pending_request_id', 'hint',
}


class _AgentCtx:
    def __init__(self) -> None:
        self.hasn_id = 'a_bot'
        self.agent_hasn_id = 'a_bot'
        self.agent_name = '小助手'
        self.owner_hasn_id = 'h_master'
        self.session_id: str | None = 'sess_work_01J8'


@asynccontextmanager
async def _fake_session():
    yield AsyncMock()


async def _drive(
    monkeypatch,
    *,
    send_result: SendMessageResult | None = None,
    rejected: ImSendRejected | None = None,
    to: str = 'h_peer',
) -> dict:
    """桩网关（send 产出指定三态/抛硬拒）驱动一次 direct message.send，返回工具响应。"""
    gw = AsyncMock()
    gw.ensure_direct_conversation = AsyncMock(return_value=ConversationRef(conversation_id=_CONV))
    if rejected is not None:
        gw.send_message = AsyncMock(side_effect=rejected)
    else:
        gw.send_message = AsyncMock(return_value=send_result)

    async def _fake_resolve(_db, target):
        return {'hasn_id': target, 'entity_type': 'human', 'name': target, 'owner_id': 'h_peer'}

    async def _fake_ensure_req(*_a, **_k):
        return 'req_1'

    import backend.app.mcp.tools.message as message_mod

    monkeypatch.setattr(message_mod.local_gateway, 'resolve_target', _fake_resolve)
    monkeypatch.setattr(message_mod, 'get_im_gateway', lambda: gw)
    monkeypatch.setattr(message_mod, '_ensure_first_contact_request', _fake_ensure_req)
    monkeypatch.setattr(message_mod, 'async_db_session', _fake_session)
    return await MessageSendTool().execute(_AgentCtx(), {'to': to, 'content': 'hi'})


@pytest.mark.asyncio
async def test_sent_returns_hint_with_handle(monkeypatch) -> None:
    """已送达态：hint 带真实会话 id + list 用法 + 「别轮询」。"""
    out = await _drive(
        monkeypatch,
        send_result=SendMessageResult(
            delivery_state=DeliveryState.ACCEPTED, conversation_id=_CONV, message_id=1001
        ),
    )

    assert set(out.keys()) == _BASE_KEYS_SENT | {'hint'}, '只多一个 hint，既有字段不增不减'
    assert out['delivered'] is True
    assert out['reachable'] is True
    assert out['conversation_id'] == _CONV
    assert _CONV in out['hint'], '句柄要渲染进 hint，分身才知道拿哪个 id 去下钻'
    assert 'hasn.message.list' in out['hint']
    assert '轮询' in out['hint'], '事件驱动前提：明确告诉分身不要干等（设计 §4-5）'


@pytest.mark.asyncio
async def test_pending_confirmation_returns_hint(monkeypatch) -> None:
    """待主人确认态（出站拦截）：hint 说清「等主人放行」，句柄照给。"""
    out = await _drive(
        monkeypatch,
        send_result=SendMessageResult(
            delivery_state=DeliveryState.PENDING_POLICY, conversation_id=_CONV, suppress_reason='需主人确认'
        ),
    )

    assert set(out.keys()) == _BASE_KEYS_PENDING | {'hint'}
    assert out['delivered'] is False
    assert out['reachable'] is True
    assert out['reason'] == '需主人确认'
    assert '主人' in out['hint'] and _CONV in out['hint']


@pytest.mark.asyncio
async def test_suppressed_hint_mentions_release_and_handle(monkeypatch) -> None:
    """暂存态：既有关系反馈文案保留，追加「对方放行后你会收到提示」+ 句柄（清单 S2）。"""
    out = await _drive(
        monkeypatch,
        send_result=SendMessageResult(
            delivery_state=DeliveryState.SUPPRESSED,
            conversation_id=_CONV,
            message_id=1002,
            relation={'level': 'stranger'},
            pending_request_id='req_1',
        ),
    )

    assert set(out.keys()) == _BASE_KEYS_SUPPRESSED
    assert out['delivered'] is False
    assert out['reachable'] is False, '修 B12 的既有语义不能被 A 刀带偏'
    assert out['pending_request_id'] == 'req_1'
    hint = out['hint']
    assert '已自动代发好友请求' in hint, '既有关系反馈文案保留'
    assert '放行后你会收到提示' in hint
    assert _CONV in hint


@pytest.mark.asyncio
async def test_error_branch_has_no_hint(monkeypatch) -> None:
    """不可达（无会话可追踪）→ 不编造句柄、不给 hint：诚实反馈原因即可。"""
    out = await _drive(monkeypatch, rejected=ImSendRejected(404, '对方不存在'), to='h_nobody')

    assert out['conversation_id'] is None
    assert 'hint' not in out
    assert out['reason'] == '对方不存在'


@pytest.mark.asyncio
async def test_hint_degrades_without_conversation_id(monkeypatch) -> None:
    """会话 id 缺失（理论上不该发生）→ hint 降级为不带 id 的通用句，绝不吐 None 进正文。"""
    out = await _drive(
        monkeypatch,
        send_result=SendMessageResult(
            delivery_state=DeliveryState.ACCEPTED, conversation_id=None, message_id=1003
        ),
    )

    assert 'None' not in out['hint']
    assert '{conversation_id}' not in out['hint'], '占位符必须被渲染掉'
    assert 'hasn.message.list' in out['hint']


def test_description_teaches_handle_semantics() -> None:
    """工具描述写明句柄语义（清单 S2 第 2 条）：弱模型不读代码，只读 description。"""
    desc = MessageSendTool().description
    assert 'conversation_id' in desc
    assert 'hasn.message.list' in desc
    assert '句柄' in desc
