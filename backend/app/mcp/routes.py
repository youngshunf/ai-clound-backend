"""
MCP 路由

提供 SSE 和 HTTP 端点
"""
import logging
from typing import Any
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from starlette.responses import Response

from backend.app.mcp.auth import AgentContextDep
from backend.app.mcp.context import set_current_agent_context, clear_agent_context
from backend.app.mcp.server import mcp_server
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["MCP"])
mcp_router = router  # 别名，方便导入


class ToolsListRequest(BaseModel):
    """工具列表请求"""
    namespace: str | None = None


class ToolsListResponse(BaseModel):
    """工具列表响应"""
    tools: list[dict[str, Any]]


class ToolCallRequest(BaseModel):
    """工具调用请求"""
    tool_name: str
    arguments: dict[str, Any]


class ToolCallResponse(BaseModel):
    """工具调用响应"""
    result: Any


@router.post("/tools/list", response_model=ToolsListResponse)
async def list_tools(
    request: ToolsListRequest,
    agent_context: AgentContextDep
):
    """列出可用工具"""
    try:
        tools = await mcp_server.list_tools(
            agent_context=agent_context,
            namespace=request.namespace
        )
        return ToolsListResponse(tools=tools)
    except Exception as e:
        logger.error(f"Failed to list tools: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tools/call", response_model=ToolCallResponse)
async def call_tool(
    request: ToolCallRequest,
    agent_context: AgentContextDep,
    http_request: Request,
):
    """调用工具"""
    from backend.app.mcp.context import set_capability_ticket

    # 一次性能力票据（A-P2 验票跳闸）：带 X-Capability-Ticket 的重试在 call_tool 的 ask 分支验票放行。
    set_capability_ticket(http_request.headers.get("x-capability-ticket"))
    try:
        result = await mcp_server.call_tool(
            agent_context=agent_context,
            tool_name=request.tool_name,
            arguments=request.arguments
        )
        return ToolCallResponse(result=result)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to call tool: {str(e)}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        set_capability_ticket(None)


@router.get("/sse")
async def mcp_sse_endpoint(
    request: Request,
    agent_context: AgentContextDep
):
    """
    MCP SSE 端点

    Agent Runtime 通过此端点建立 SSE 连接
    """
    logger.info(f"Agent {agent_context.hasn_id} connecting to MCP SSE")

    # 设置 Agent 上下文到 contextvars
    set_current_agent_context(agent_context)

    try:
        # TODO: 实现 SSE 流
        # 这里需要根据实际的 Python MCP SDK API 调整
        # 以下是占位实现
        async def event_generator():
            # 发送初始化消息
            yield {
                "event": "connected",
                "data": f"Agent {agent_context.hasn_id} connected"
            }

            # 等待客户端断开
            while True:
                if await request.is_disconnected():
                    break
                # 这里应该处理 MCP 协议消息
                # await asyncio.sleep(1)

        return EventSourceResponse(event_generator())

    except Exception as e:
        logger.error(f"SSE connection error: {str(e)}", exc_info=True)
        raise
    finally:
        # 清理上下文
        clear_agent_context()
        logger.info(f"Agent {agent_context.hasn_id} disconnected from MCP SSE")


@router.post("/message")
async def mcp_message_endpoint(
    request: Request,
    agent_context: AgentContextDep
):
    """
    MCP 消息端点（HTTP POST）

    备用方案：如果 SSE 不可用，可以使用 HTTP POST
    """
    set_current_agent_context(agent_context)

    try:
        body = await request.json()
        logger.info(f"Agent {agent_context.hasn_id} sent MCP message: {body.get('method')}")

        # TODO: 处理 MCP 消息
        # response = await mcp_server.handle_message(body)
        response = {"error": "Not implemented yet"}

        return response
    except Exception as e:
        logger.error(f"Message handling error: {str(e)}", exc_info=True)
        raise
    finally:
        clear_agent_context()




def register_mcp_routes(app):
    """将 MCP 路由注册到主应用"""
    app.include_router(router)

    # StreamableHTTP 端点作为原生 ASGI app 挂载，
    # 因为 session_manager.handle_request 直接操作 ASGI send/receive
    from starlette.routing import Route
    from backend.app.mcp.streamable import hasn_streamable_server

    class _StreamableASGI:
        async def __call__(self, scope, receive, send):
            await hasn_streamable_server.handle_request_with_auth(scope, receive, send)

    streamable_route = Route(
        "/api/v1/mcp/streamable",
        endpoint=_StreamableASGI(),
        methods=["GET", "POST", "DELETE"],
    )
    app.routes.insert(0, streamable_route)

    logger.info("MCP routes registered")
