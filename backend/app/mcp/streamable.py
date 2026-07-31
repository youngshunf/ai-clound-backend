"""
HASN 云端 MCP StreamableHTTP Server

使用 MCP SDK 的 StreamableHTTP 协议暴露云端工具
"""

import json
import logging

from contextvars import ContextVar
from time import monotonic
from typing import Any

import sqlalchemy as sa

from mcp import types
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.types import Receive, Scope, Send

from backend.app.hasn.model.hasn_sessions import HasnSessions
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
from backend.app.mcp.trust_gate import (
    allow_reserved_fields_in_schema,
    apply_session_tool_allowlist_header,
)
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

# ── 会话 → 平台项目 反查缓存（doc19 §6.2 项目记忆语境）──────────────────────────────
# streamable 是 stateless 的，每个 MCP 请求都重建 transport；若每请求都查一次 hasn_sessions，
# 等于给**每一次工具调用**平白加一次同步 DB 往返（发现面 list_tools 也算），性能会塌。
# 项目挂靠是低频事件：`hasn_sessions.project_id` 只在派发建会话时由已验证项目透传，会话存续期
# 内基本不变，因此用进程内短 TTL 缓存扛掉热路径。
#
# **失效策略：纯 TTL 到期（60 秒），不做主动失效 / 广播**。
# - 代价上界：挂靠变更后最多 60 秒内旧值仍被采信 → 这一窗口里新记的事实落到旧项目或落回兜底
#   作用域，检索的项目并集用旧项目。owner 隔离是另一层硬边界（service 层 owner_id 强制），
#   缓存陈旧**不会**把事实串到别人名下，最坏只是「短暂挂错自己的项目」，下一次读写即自愈；
# - 多 worker 各自持有独立副本是可接受的：都只是同一份低频数据的短暂视图，无一致性要求；
# - 负结果（会话不挂项目）同样入缓存——「非项目会话」是多数路径，不缓存等于每次白查一遍库。
_SESSION_PROJECT_TTL_SECONDS = 60.0
_SESSION_PROJECT_CACHE_MAX = 2048
_session_project_cache: dict[str, tuple[str | None, float]] = {}


async def resolve_session_project_id(session_id: str | None) -> str | None:
    """按会话 id 反查其平台项目挂靠 UUID（doc19 §6.2），带进程内短 TTL 缓存。

    返回云端权威项目 UUID 字符串；会话不存在 / 不挂项目 → None。
    反查失败（DB 瞬时异常）**绝不阻断本次调用**：退化为「无项目语境」——记忆照常落既有兜底
    作用域、检索不做项目并集收敛，属于 never over-block 的安全方向，不会把事实写进错项目。
    """
    if not session_id:
        return None
    now = monotonic()
    cached = _session_project_cache.get(session_id)
    if cached is not None and cached[1] > now:
        return cached[0]

    try:
        async with async_db_session() as db:
            raw = (
                await db.execute(
                    sa.select(HasnSessions.project_id).where(HasnSessions.session_id == session_id).limit(1)
                )
            ).scalar_one_or_none()
    except Exception as exc:
        # 不入缓存：瞬时故障不该被固化 60 秒，下次调用重试即可
        logger.warning(f'解析会话 {session_id} 的项目挂靠失败，本次按无项目语境处理: {exc!r}')
        return None

    project_id = str(raw) if raw else None
    if len(_session_project_cache) >= _SESSION_PROJECT_CACHE_MAX:
        # 无界增长防线：整体丢弃重建。会话 id 基数有限且 TTL 只有 60 秒，
        # 到顶本身就说明缓存里绝大多数条目已经过期，逐条 LRU 不值那个复杂度。
        _session_project_cache.clear()
    _session_project_cache[session_id] = (project_id, now + _SESSION_PROJECT_TTL_SECONDS)
    return project_id


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

                # 转换为 MCP types.Tool。schema 过一道 `allow_reserved_fields_in_schema`：
                # SDK 会拿这里给出的 inputSchema 去校验**未剥离**的 wire 入参（校验早于我们的
                # call_tool），不开口则声明 additionalProperties:false 的工具会把系统注入的
                # `_hasn_*` 判为非法入参直接拒掉。详见该函数 docstring。
                tools = [types.Tool(
                            name=tool_data['name'],
                            description=tool_data['description'],
                            inputSchema=allow_reserved_fields_in_schema(tool_data['input_schema']),
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

            # CLI runtime 直连云端 MCP 时，本地 per-dispatch key 不会经过 daemon 代理，必须由
            # daemon 组装的静态 header 把会话语境带入 AgentContext（会话轴分流，设计 02 §4.3）：
            # - `X-Hasn-Session-Id`（运行时会话）→ `session_id`：message.send 回灌定位等运行时消费；
            # - `X-Hasn-Work-Session-Id`（工作会话，仅真实工作会话派发才发）→ `work_session_id`：
            #   server.call_tool 优先据它落 register-on-write 的工作会话 ContextVar。
            session_header = headers.get(b'x-hasn-session-id')
            if session_header is not None:
                agent_context.session_id = session_header.decode('utf-8').strip() or None
            work_session_header = headers.get(b'x-hasn-work-session-id')
            if work_session_header is not None:
                agent_context.work_session_id = work_session_header.decode('utf-8').strip() or None

            # 项目语境（doc19 §6.2 项目记忆）：与会话语境同一通路落 `agent_context.project_id`，
            # 两级来源按优先级——
            # ① `X-Hasn-Project-Id` header：daemon 已解析出项目时直接带上，采信即可，零查库；
            # ② 缺 header 时由工作会话（缺省回落运行时会话）反查 `hasn_sessions.project_id`
            #    ——该列只由已验证派发项目透传，是云端权威 UUID。反查带 60 秒进程内缓存，
            #    避免给每次 MCP 请求加一趟同步 DB 往返（见 resolve_session_project_id）。
            # 分身无论如何都伪造不了它：header 由 daemon 组装、会话挂靠在云端库里。
            project_header = headers.get(b'x-hasn-project-id')
            if project_header is not None:
                agent_context.project_id = project_header.decode('utf-8').strip() or None
            if not agent_context.project_id:
                agent_context.project_id = await resolve_session_project_id(
                    agent_context.work_session_id or agent_context.session_id
                )

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

            # CLI runtime 的会话工具白名单走 per-dispatch Header；严格 JSON 数组解析，非法即
            # 失败关闭并由本方法统一返回 401。字段缺失保持既有不限制语义，空数组拒绝全部业务工具。
            apply_session_tool_allowlist_header(agent_context, headers)

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
