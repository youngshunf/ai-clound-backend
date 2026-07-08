"""doc08 RT1.5-C（修 B12）：hasn.message.send 结构化关系反馈 + 无关系自动代发好友请求。

策略：patch 掉 message_router.route_message（返回 suppressed / sent / error 三态）+ async_db_session
（纯文本路径不真触 DB）+ _ensure_first_contact_request（避免真发请求），驱动 MessageSendTool.execute
断言：被暂存时 reachable=false、status=pending_contact_approval、带 pending_request_id/hint；
有关系正常送达时 reachable=true。零真实 DB 依赖。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

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


async def _run(route_result: dict, ensure_return: int | None = None):
    """用给定 route_message 结果驱动一次 message.send，返回工具响应。"""
    with (
        patch.object(message_module.async_db_session, '__call__', _fake_session),
        patch('backend.app.mcp.tools.message.async_db_session', _fake_session),
        patch.object(
            message_module.message_router, 'route_message',
            AsyncMock(return_value=route_result),
        ),
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
    route_result = {
        'error': False,
        'status': 'suppressed',
        'suppressed': True,
        'msg_id': 123,
        'conversation_id': 'conv-1',
        'pending_request_id': 777,
        'relation': {'relation_type': 'social', 'trust_level': 2, 'label': '普通联系人'},
    }
    resp, ensure_mock = await _run(route_result)

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
    route_result = {
        'error': False,
        'status': 'suppressed',
        'suppressed': True,
        'msg_id': 124,
        'conversation_id': 'conv-2',
        'pending_request_id': None,
        'relation': None,
    }
    resp, ensure_mock = await _run(route_result, ensure_return=999)

    assert resp['reachable'] is False
    assert resp['status'] == 'pending_contact_approval'
    assert resp['pending_request_id'] == 999  # 工具兜底代发
    ensure_mock.assert_awaited_once()


# ── 三、正常送达 → reachable/delivered True ──


@pytest.mark.asyncio
async def test_sent_is_reachable_and_delivered() -> None:
    route_result = {'error': False, 'status': 'sent', 'msg_id': 200, 'conversation_id': 'conv-3'}
    resp, _ = await _run(route_result)

    assert resp['reachable'] is True
    assert resp['delivered'] is True
    assert resp['status'] == 'sent'


# ── 四、硬不可达（error）→ reachable False + 原因透传 ──


@pytest.mark.asyncio
async def test_error_is_unreachable() -> None:
    route_result = {'error': True, 'code': 2002, 'message': '对方已将你屏蔽'}
    resp, _ = await _run(route_result)

    assert resp['reachable'] is False
    assert resp['delivered'] is False
    assert resp['message_id'] is None
    assert resp['reason'] == '对方已将你屏蔽'
