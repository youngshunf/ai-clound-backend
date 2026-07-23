"""doc08 RT1.5-C（修 B12）：hasn.message.send 结构化关系反馈 + 无关系自动代发好友请求。

R1-05 切片①后 direct 发送经 ImGateway port（ensure→send）：本单测把三态桩重新指向新接缝——
patch `resolve_target`（认作 direct 分身目标）+ `get_im_gateway`（返回桩网关，send_message 产出
指定 SendMessageResult 三态 / 抛 ImSendRejected）+ `_ensure_first_contact_request`（避免真发请求），
驱动 MessageSendTool.execute 断言工具映射：被暂存时 reachable=false、status=pending_contact_approval、
带 pending_request_id/hint；正常送达 reachable=true；硬拒 reachable=false 且原因透传。聚焦工具的
返回体映射逻辑（`_map_direct_send_result`），零真实 DB 依赖。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.hasn_im.application.errors import ImSendRejected
from backend.app.hasn_im.ports.dto import (
    ConversationRef,
    DeliveryState,
    SendMessageResult,
)
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools import message as message_module
from backend.app.mcp.tools.message import MessageSendTool


def _agent_ctx() -> AgentContext:
    return AgentContext(
        hasn_id='a_sender_agent',
        owner_id=1,
        agent_status='active',
        metadata={},
        agent_name='测试分身',
        owner_hasn_id='h_sender_owner',
        session_uuid='amk_test',
    )


@asynccontextmanager
async def _fake_session():
    yield AsyncMock()


async def _run(
    send_result: SendMessageResult | None = None,
    *,
    rejected: ImSendRejected | None = None,
    ensure_return: int | None = None,
    conversation_id: str = 'conv-x',
):
    """用桩 ImGateway（send 产出指定三态/抛硬拒）驱动一次 direct message.send，返回工具响应。"""
    fake_gateway = AsyncMock()
    fake_gateway.ensure_direct_conversation = AsyncMock(
        return_value=ConversationRef(conversation_id=conversation_id)
    )
    if rejected is not None:
        fake_gateway.send_message = AsyncMock(side_effect=rejected)
    else:
        fake_gateway.send_message = AsyncMock(return_value=send_result)

    async def _fake_resolve(_db, target):
        # 认作 direct 分身目标（切片① is_direct 分叉），port 承接发送。
        return {'hasn_id': target, 'entity_type': 'agent', 'name': target, 'owner_id': 'h_other'}

    with (
        patch('backend.app.mcp.tools.message.async_db_session', _fake_session),
        patch.object(message_module.local_gateway, 'resolve_target', AsyncMock(side_effect=_fake_resolve)),
        patch.object(message_module, 'get_im_gateway', lambda: fake_gateway),
        patch.object(
            message_module, '_ensure_first_contact_request',
            AsyncMock(return_value=ensure_return),
        ) as ensure_mock,
    ):
        resp = await MessageSendTool().execute(_agent_ctx(), {'to': 'a_receiver_agent', 'content': '你好'})
    return resp, ensure_mock


# ── 一、被暂存 + 门控已代发请求 → 复用其 request_id，不再重复代发 ──


@pytest.mark.asyncio
async def test_suppressed_with_pending_request_is_honest_and_reuses() -> None:
    send_result = SendMessageResult(
        delivery_state=DeliveryState.SUPPRESSED,
        conversation_id='conv-1',
        message_id=123,
        pending_request_id=777,
        relation={'relation_type': 'social', 'trust_level': 2, 'label': '普通联系人'},
    )
    resp, ensure_mock = await _run(send_result)

    assert resp['reachable'] is False  # 修 B12：不再误报 reachable=true
    assert resp['delivered'] is False
    assert resp['status'] == 'pending_contact_approval'
    assert resp['pending_request_id'] == 777
    assert resp['relation']['trust_level'] == 2
    assert resp.get('hint')
    ensure_mock.assert_not_awaited()  # 已带请求 → 幂等复用，不重复代发


# ── 二、被暂存 + 无关系（陌生人，门控未代发）→ 工具兜底自动代发请求 ──


@pytest.mark.asyncio
async def test_suppressed_without_request_autocreates() -> None:
    send_result = SendMessageResult(
        delivery_state=DeliveryState.SUPPRESSED,
        conversation_id='conv-2',
        message_id=124,
        pending_request_id=None,
        relation=None,
    )
    resp, ensure_mock = await _run(send_result, ensure_return=999)

    assert resp['reachable'] is False
    assert resp['status'] == 'pending_contact_approval'
    assert resp['pending_request_id'] == 999  # 工具兜底代发
    ensure_mock.assert_awaited_once()


# ── 三、正常送达 → reachable/delivered True ──


@pytest.mark.asyncio
async def test_sent_is_reachable_and_delivered() -> None:
    send_result = SendMessageResult(
        delivery_state=DeliveryState.ACCEPTED,
        conversation_id='conv-3',
        message_id=200,
    )
    resp, _ = await _run(send_result)

    assert resp['reachable'] is True
    assert resp['delivered'] is True
    assert resp['status'] == 'sent'


# ── 四、硬不可达（协议级硬拒 ImSendRejected）→ reachable False + 原因透传 ──


@pytest.mark.asyncio
async def test_error_is_unreachable() -> None:
    resp, _ = await _run(rejected=ImSendRejected(2002, '对方已将你屏蔽'))

    assert resp['reachable'] is False
    assert resp['delivered'] is False
    assert resp['message_id'] is None
    assert resp['reason'] == '对方已将你屏蔽'
    assert resp['code'] == 2002
