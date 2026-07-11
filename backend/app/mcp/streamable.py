"""
HASN 云端 MCP StreamableHTTP Server

使用 MCP SDK 的 StreamableHTTP 协议暴露云端工具
"""

import json
import logging

from contextvars import ContextVar
from typing import Any

import sqlalchemy as sa

from mcp import types
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.types import Receive, Scope, Send

from backend.app.hasn.service.hasn_agent_mcp_keys_service import (
    KEY_PREFIX as AGENT_MCP_KEY_PREFIX,
)
from backend.app.hasn.service.hasn_agent_mcp_keys_service import (
    hasn_agent_mcp_keys_service,
)
from backend.app.hasn_core import HasnHumans, hasn_agents_dao
from backend.app.mcp.auth import AgentContext, inject_app_access
from backend.app.mcp.context import set_capability_ticket, set_trust_context_header
from backend.app.mcp.json_encoding import json_default
from backend.app.mcp.server import mcp_server
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.common.security.agent_jwt import get_agent_scopes_cached, get_privileged_grants_cached, verify_agent_token
from backend.database.db import async_db_session
from backend.utils.timezone import timezone

logger = logging.getLogger(__name__)

# Agent MCP Key 的 Bearer 前缀（与 service.KEY_PREFIX 对齐）；据此与 JWT 分流
_AGENT_MCP_KEY_BEARER_PREFIX = f'{AGENT_MCP_KEY_PREFIX}_'

# 使用 ContextVar 在异步上下文中传递 AgentContext
_streamable_agent_context: ContextVar[AgentContext | None] = ContextVar('streamable_agent_context', default=None)


class HasnMcpStreamableServer:
    """HASN MCP StreamableHTTP Server"""

    def __init__(self) -> None:
        self.server = Server('hasn-cloud-mcp')
        self.session_manager: StreamableHTTPSessionManager | None = None

        # 注册处理器
        self._register_handlers()

    def _register_handlers(self) -> None:
        """注册 MCP 协议处理器"""

        @self.server.list_tools()
        async def handle_list_tools() -> list[types.Tool]:
            """列出可用工具"""
            try:
                # 从 ContextVar 获取 AgentContext
                agent_context = _streamable_agent_context.get()
                if agent_context is None:
                    raise RuntimeError('AgentContext not found in request context')

                # 调用现有的 HasnCloudMcpServer
                tools_data = await mcp_server.list_tools(agent_context)

                # 转换为 MCP types.Tool
                tools = [types.Tool(
                            name=tool_data['name'],
                            description=tool_data['description'],
                            inputSchema=tool_data['input_schema'],
                        ) for tool_data in tools_data]

                # 无状态 MCP 下每个请求都会走一遍 list_tools（stateless=True 每请求新建 transport），
                # 用 INFO 会把单进程 dev 后端日志刷屏（冷启动批量拉起分身时尤甚），降到 DEBUG。
                logger.debug(f'Listed {len(tools)} tools for agent {agent_context.hasn_id}')
                return tools

            except Exception as e:
                logger.error(f'Error listing tools: {e}', exc_info=True)
                raise

        @self.server.call_tool()
        async def handle_call_tool(
            name: str, arguments: dict[str, Any]
        ) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
            """调用工具"""
            try:
                # 从 ContextVar 获取 AgentContext
                agent_context = _streamable_agent_context.get()
                if agent_context is None:
                    raise RuntimeError('AgentContext not found in request context')

                logger.info(f'Agent {agent_context.hasn_id} calling tool: {name}')

                # 调用现有的 HasnCloudMcpServer
                result = await mcp_server.call_tool(agent_context, name, arguments)

                # 转换为 MCP TextContent（default=json_default：兜底 datetime/Decimal，序列化边界绝不崩）
                return [
                    types.TextContent(
                        type='text',
                        text=json.dumps(result, ensure_ascii=False, indent=2, default=json_default),
                    )
                ]

            except Exception as e:
                logger.error(f'Error calling tool {name}: {e}', exc_info=True)
                raise

    async def _authenticate_from_headers(self, headers: dict[bytes, bytes]) -> AgentContext:
        """
        从 HTTP headers 中提取并验证 AgentContext。

        按 Bearer 前缀分流（设计 12-Agent接入凭证设计.md §6）：
        - `hasn_amk_*` → 稳定可吊销的 Agent MCP Key，查表解析身份（key 自识别，
          无需 X-User-Id / X-HASN-Agent-ID）；
        - 其余 → 原 Agent JWT 兼容路（verify_agent_token + Redis 会话，仍用 X-HASN-Agent-ID）。

        Raises:
            ValueError: 认证失败（统一转 401）
        """
        auth_header = headers.get(b'authorization')
        if not auth_header:
            raise ValueError('Missing Authorization header')

        auth_str = auth_header.decode('utf-8')
        if not auth_str.startswith('Bearer '):
            raise ValueError('Invalid Authorization header format')

        token = auth_str[7:]  # 移除 "Bearer " 前缀

        if token.startswith(_AGENT_MCP_KEY_BEARER_PREFIX):
            return await self._authenticate_with_key(token, headers)
        return await self._authenticate_with_jwt(token, headers)

    async def _authenticate_with_key(self, token: str, headers: dict[bytes, bytes]) -> AgentContext:
        """Agent MCP Key 路：哈希查表解析身份，构造 AgentContext（key 自识别身份）。"""
        # node 绑定校验头（默认开：签发即绑 node；空 header 时若 key 绑了 node 则 verify 拒）
        node_header = headers.get(b'x-node-id')
        node_id = node_header.decode('utf-8') if node_header else None

        async with async_db_session() as db:
            try:
                record = await hasn_agent_mcp_keys_service.verify(db, presented_key=token, node_id=node_id)
            except (errors.AuthorizationError, errors.TokenError) as e:
                raise ValueError(f'Invalid Agent MCP Key: {e}')

            # 可选防御性核对：带了 X-HASN-Agent-ID 才比对，非鉴权必需（§11.4）
            agent_id_header = headers.get(b'x-hasn-agent-id')
            if agent_id_header and agent_id_header.decode('utf-8') != record.agent_hasn_id:
                raise ValueError('Agent ID mismatch')

            agent = await hasn_agents_dao.get_by_hasn_id(db, hasn_id=record.agent_hasn_id)
            if not agent:
                raise ValueError('Agent not found')
            if agent.status != 'active':
                raise ValueError(f'Agent is {agent.status}')

            owner_user_id = record.owner_user_id
            if owner_user_id is None:
                # 兜底：签发时一般已落 owner_user_id；缺失则按 owner_hasn_id 反查
                owner_user_id = (
                    await db.execute(
                        sa.select(HasnHumans.user_id).where(HasnHumans.hasn_id == record.owner_hasn_id).limit(1)
                    )
                ).scalar_one_or_none()
            if owner_user_id is None:
                raise ValueError('Owner not resolvable for Agent MCP Key')

            # 合成稳定会话标识：key 即长期会话，amk_ 前缀与 JWT 会话天然可区分，
            # 供下游 to_token_payload()/审计按签发 key 归因（设计 §11 实施前待排查的解法）。
            payload = AgentTokenPayload(
                agent_hasn_id=record.agent_hasn_id,
                agent_name=agent.agent_name or '',
                owner_hasn_id=record.owner_hasn_id,
                owner_user_id=int(owner_user_id),
                session_uuid=f'amk_{record.id}',
                expire_time=record.expire_time or timezone.now(),
            )
            context = AgentContext.from_token_payload(
                payload,
                agent_status=agent.status,
                runtime_location=getattr(agent, 'runtime_location', 'cloud') or 'cloud',
            )
            # D3 消费时活取：不用 key 上冻结的 scopes 判定，用 agent_hasn_id 现查三态策略。
            policy = await get_agent_scopes_cached(record.agent_hasn_id, db)
            context.apply_policy(policy)
            # G1 特权授予同处活取（Admin 授予表 ∪ ENV bootstrap，doc18 §4.1）
            context.granted_privileged_scopes = await get_privileged_grants_cached(record.agent_hasn_id, db)
            # G3 应用权益门 per-request 预取（doc18 §4.3·U3）
            await inject_app_access(context, db)
            return context

    async def _authenticate_with_jwt(self, token: str, headers: dict[bytes, bytes]) -> AgentContext:
        """Agent JWT 兼容路：verify_agent_token（JWT + 可吊销 Redis 会话）。"""
        agent_id_header = headers.get(b'x-hasn-agent-id')
        if not agent_id_header:
            raise ValueError('Missing X-HASN-Agent-ID header')

        hasn_id = agent_id_header.decode('utf-8')

        try:
            payload = await verify_agent_token(token)
        except errors.TokenError as e:
            raise ValueError(f'Invalid token: {e}')

        if payload.agent_hasn_id != hasn_id:
            raise ValueError('Agent ID mismatch')

        async with async_db_session() as db:
            agent = await hasn_agents_dao.get_by_hasn_id(db, hasn_id=hasn_id)
            if not agent:
                raise ValueError('Agent not found')
            if agent.status != 'active':
                raise ValueError(f'Agent is {agent.status}')

            context = AgentContext.from_token_payload(
                payload,
                agent_status=agent.status,
                runtime_location=getattr(agent, 'runtime_location', 'cloud') or 'cloud',
            )
            # D3 消费时活取：JWT scopes 仅审计快照，三态判定现查 DB。
            policy = await get_agent_scopes_cached(hasn_id, db)
            context.apply_policy(policy)
            # G1 特权授予同处活取（Admin 授予表 ∪ ENV bootstrap，doc18 §4.1）
            context.granted_privileged_scopes = await get_privileged_grants_cached(hasn_id, db)
            # G3 应用权益门 per-request 预取（doc18 §4.3·U3）
            await inject_app_access(context, db)
            return context

    async def handle_request_with_auth(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """
        处理 ASGI 请求，先进行认证，然后委托给 session_manager

        Args:
            scope: ASGI scope
            receive: ASGI receive function
            send: ASGI send function
        """
        try:
            # 从 ASGI scope 中提取 headers
            headers = dict(scope.get('headers', []))

            # 认证并获取 AgentContext
            agent_context = await self._authenticate_from_headers(headers)

            # 将 AgentContext 存储到 ContextVar
            _streamable_agent_context.set(agent_context)

            # 一次性能力票据（A-P2 验票跳闸）：带 X-Capability-Ticket 的重试在 call_tool 的 ask 分支验票放行。
            ticket_header = headers.get(b'x-capability-ticket')
            set_capability_ticket(ticket_header.decode('utf-8') if ticket_header else None)

            # 会话信任语境 header（L3 工具门云端半场·doc08 §4·RT3）：daemon 为本次派发的 CLI runtime
            # 组装云端 MCP server 时戳进 X-Hasn-*（分身不可伪造），call_tool 的 L3 门据此判档（header
            # 优先、工具入参保留参数兜底）。以 X-Hasn-Is-External 的存在与否作「本次是否带信任语境」信号
            # ——缺失 → 传 None → 回落工具入参保留参数（never over-block：无语境即主会话放行）。
            is_external_header = headers.get(b'x-hasn-is-external')
            if is_external_header is not None:
                peer_id_header = headers.get(b'x-hasn-peer-id')
                peer_trust_header = headers.get(b'x-hasn-peer-trust')
                set_trust_context_header((
                    is_external_header.decode('utf-8'),
                    peer_id_header.decode('utf-8') if peer_id_header else None,
                    peer_trust_header.decode('utf-8') if peer_trust_header else None,
                ))
            else:
                set_trust_context_header(None)

            logger.debug(f'Authenticated agent {agent_context.hasn_id} for MCP request')

            # 委托给 session_manager 处理实际的 MCP 请求
            if self.session_manager is None:
                raise RuntimeError('Session manager not initialized')

            await self.session_manager.handle_request(scope, receive, send)

        except Exception as e:
            logger.error(f'Authentication failed: {e}', exc_info=True)
            # 返回 401 错误
            await send({
                'type': 'http.response.start',
                'status': 401,
                'headers': [[b'content-type', b'application/json']],
            })
            await send({
                'type': 'http.response.body',
                'body': b'{"error": "Authentication failed"}',
            })
        finally:
            # 清理 ContextVar
            _streamable_agent_context.set(None)
            set_capability_ticket(None)
            set_trust_context_header(None)

    def create_session_manager(self) -> StreamableHTTPSessionManager:
        """创建 StreamableHTTP 会话管理器"""
        if self.session_manager is None:
            # stateless=True 是刻意选择，勿改成有状态：生产多 worker（gunicorn/uvicorn workers）
            # 下，有状态会话会绑定到首次命中的那个 worker 进程，后续请求被负载均衡打到别的
            # worker 就找不到会话而失败。无状态模式每请求自建临时 transport、用完即 terminate
            #（日志里的 "Terminating session: None" 是无状态的正常行为，非错误）。
            self.session_manager = StreamableHTTPSessionManager(
                self.server,
                stateless=True,  # 无状态：每请求新建 transport（多 worker 安全，勿改成有状态）
            )
        return self.session_manager


# 全局实例
hasn_streamable_server = HasnMcpStreamableServer()
