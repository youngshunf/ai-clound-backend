"""社区工具经 MCP 直连面（AppTool shim → ai_native_runtime_gateway.call_tool）真实调通。

回归 d35b8903 引入的事故：A-P5 给中继网关 `call_tool` 加了
`request.headers.get('X-Capability-Ticket')`（无条件读请求头），但 MCP 直连面经
`backend/app/mcp/tools/app_tools.py` 的 `AppTool.execute` 构造的 `_Request` shim 只带
`state.agent`、没有 `headers` → 每次 Agent 经 `hasn.cloud.tool.call` 调社区工具都炸
`'_Request' object has no attribute 'headers'`（截图：create_post / create_article 两次被挡）。

既有 `test_app_instance_dispatch.py` 只打 `gateway._dispatch_tool`，绕过了 `call_tool`
读请求头那一层，故没抓到这个回归——这里补的就是「经 call_tool 的完整调用面」。

连真实本地 PostgreSQL，事务回滚隔离，零 Mock 零 Fake。
"""
from __future__ import annotations

import pytest

from backend.app.hasn.schema.ai_native_runtime import AiNativeToolCallRequest
from backend.app.hasn.service.ai_native_runtime_gateway import ai_native_runtime_gateway as gateway
from backend.app.mcp.auth import AgentContext
from backend.common.dataclasses import AgentTokenPayload
from tests.hasn_community.conftest import seed_agent, seed_human

def _agent_context(owner: dict, agent_row: dict) -> AgentContext:
    return AgentContext(
        hasn_id=agent_row['hasn_id'],
        owner_id=owner['user_id'],
        agent_status='active',
        metadata={},
        agent_name=agent_row['display_name'],
        owner_hasn_id=owner['hasn_id'],
        session_uuid='sess-mcp-dispatch-test',
        default_mode='allow',
        capability_modes={},
    )


def _mcp_face_shim(payload: AgentTokenPayload):
    """复刻 AppTool.execute 构造的 `_Request`：state.agent + mcp_face=True + 空 headers。

    这是生产 MCP 直连面真正递给网关的形状；本回归就锁这个形状能被网关安全消费。
    """

    class _Request:
        state = type('_State', (), {'agent': payload, 'mcp_face': True})()
        headers: dict[str, str] = {}

    return _Request()


@pytest.mark.asyncio
async def test_create_post_via_mcp_face_shim_succeeds(db):
    """MCP 直连面（mcp_face shim，无请求头）经 call_tool 调 community.create_post 真实建帖。

    修复前：call_tool line `request.headers.get('X-Capability-Ticket')` 直接抛
    AttributeError（shim 无 headers）。修复后：mcp_face=True → 跳过读头 + 跳过重复三态闸门，
    全链路落到 community 处理器真实建帖（status=pending_review）。
    """
    owner = await seed_human(db, nickname='直连面主人')
    agent_row = await seed_agent(db, owner_hasn_id=owner['hasn_id'], display_name='直连面分身')
    ctx = _agent_context(owner, agent_row)

    result = await gateway.call_tool(
        db,
        request=_mcp_face_shim(ctx.to_token_payload()),
        app_id='community',
        tool_id='community.create_post',
        body=AiNativeToolCallRequest(
            workspace={'kind': 'personal'},
            input={'content': 'MCP 直连面发帖回归测试——证明 _Request shim 不再炸 headers'},
            trace_id='trace_mcp_face_create_post',
        ),
    )

    # 真实放行（非 deny / 非 approval_required）+ 真实建帖（非 stub / 非空填充）。
    assert result['decision'] == 'allow', result
    assert result['app_id'] == 'community' and result['tool_id'] == 'community.create_post'
    inner = result['result']
    assert isinstance(inner, dict) and inner.get('post_id'), inner
    assert inner['status'] == 'pending_review'


@pytest.mark.asyncio
async def test_create_article_via_mcp_face_shim_succeeds(db):
    """同一回归覆盖 create_article（截图里第二个被挡的工具）。"""
    owner = await seed_human(db, nickname='直连面主人2')
    agent_row = await seed_agent(db, owner_hasn_id=owner['hasn_id'], display_name='直连面分身2')
    ctx = _agent_context(owner, agent_row)

    result = await gateway.call_tool(
        db,
        request=_mcp_face_shim(ctx.to_token_payload()),
        app_id='community',
        tool_id='community.create_article',
        body=AiNativeToolCallRequest(
            workspace={'kind': 'personal'},
            input={
                'title': 'MCP 直连面文章回归',
                'content': '## 小节\n\n- 证明 create_article 经 call_tool 不再炸 headers\n- 真实落库 pending_review',
            },
            trace_id='trace_mcp_face_create_article',
        ),
    )

    assert result['decision'] == 'allow', result
    inner = result['result']
    assert isinstance(inner, dict) and (inner.get('article_id') or inner.get('post_id')), inner


@pytest.mark.asyncio
async def test_app_tool_execute_end_to_end_builds_valid_shim(db, monkeypatch):
    """端到端打真正的 `AppTool.execute`：锁住生产 `_Request` shim 自带 headers + mcp_face。

    AppTool.execute 内部 `async with async_db_session()` 自开会话；测试用 savepoint 隔离的
    会话不可见其未提交种子，故把 async_db_session 临时指到本测试会话（不真正关闭它），
    让全链路在同一事务里跑、随用例回滚。若有人日后删了 shim 的 headers/mcp_face，此用例即红。
    """
    from backend.app.mcp.tools.app_tools import AppTool

    owner = await seed_human(db, nickname='AppTool主人')
    agent_row = await seed_agent(db, owner_hasn_id=owner['hasn_id'], display_name='AppTool分身')
    ctx = _agent_context(owner, agent_row)

    class _SessionCtx:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *exc):
            return False  # 绝不关闭测试会话（由 fixture 统一回滚）

    monkeypatch.setattr('backend.database.db.async_db_session', lambda: _SessionCtx())

    tool = AppTool(
        installation_id='manifest:community',
        app_id='community',
        app_namespace='community',
        tool_id='community.create_post',
        tool_name='create_post',
        action='create_post',
        tool_description='发布社区帖子',
        tool_input_schema={'type': 'object', 'properties': {'content': {'type': 'string'}}, 'required': ['content']},
        tool_required_scopes=['community:post'],
    )

    result = await tool.execute(ctx, {'content': 'AppTool.execute 端到端回归：真实建帖'})

    assert result['decision'] == 'allow', result
    assert result['result'].get('post_id'), result
