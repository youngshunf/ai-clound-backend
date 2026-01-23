"""
云端 Agent 执行器

在云端服务中执行 Graph，支持同步和异步执行。

@author Ysf
@date 2025-12-28
"""

import time
import uuid

from collections.abc import AsyncIterator
from typing import Any

from agent_core.graph.compiler import GraphCompiler
from agent_core.graph.loader import GraphLoader
from agent_core.llm import DirectLLMClient, LLMConfig
from agent_core.runtime.context import RuntimeContext
from agent_core.runtime.events import AgentEvent, EventType
from agent_core.runtime.interfaces import (
    ExecutionRequest,
    ExecutionResponse,
    ExecutorInterface,
    RuntimeType,
)
from agent_core.tools.builtin import LLMGenerateTool
from agent_core.tools.registry import ToolRegistry


class CloudExecutor(ExecutorInterface):
    """
    云端执行器

    在 FastAPI 后端中执行 Graph，支持：
    - 同步执行（短任务）
    - 异步执行（长任务，通过 Celery）
    - 流式执行（SSE 事件流）
    """

    runtime_type = RuntimeType.CLOUD

    def __init__(
        self,
        db,
        redis,
        config: dict[str, Any],
        browser_pool=None,
        llm_gateway=None,
    ) -> None:
        """
        初始化云端执行器

        Args:
            db: 数据库连接
            redis: Redis 连接
            config: 配置字典
            browser_pool: 浏览器池 (可选)
            llm_gateway: LLM 网关 (可选)
        """
        self.db = db
        self.redis = redis
        self.config = config
        self.browser_pool = browser_pool
        self.llm_gateway = llm_gateway

        # 初始化 Graph 加载器
        definitions_path = config.get("definitions_path", "agent-definitions")
        self.graph_loader = GraphLoader(definitions_path)

        # 初始化工具注册表
        self.tool_registry = ToolRegistry(RuntimeType.CLOUD)

        # 注册内置工具
        self._register_builtin_tools()

        # 初始化编译器
        self.compiler = GraphCompiler(self.tool_registry)

        # 初始化 LLM 客户端
        self.llm_client = None
        if llm_gateway:
            llm_config = LLMConfig(
                base_url="",
                api_token="",
                direct_mode=True,
            )
            self.llm_client = DirectLLMClient(llm_gateway, llm_config)

    def _register_builtin_tools(self) -> None:
        """注册内置工具"""
        # 注册 LLM 工具
        llm_tool = LLMGenerateTool()
        self.tool_registry.register_tool(llm_tool)

        # TODO: 注册更多云端工具
        # - cloud_browser_publish
        # - cloud_storage
        # 等

    async def execute(self, request: ExecutionRequest) -> ExecutionResponse:
        """
        执行 Graph（同步模式）

        Args:
            request: 执行请求

        Returns:
            执行响应
        """
        start_time = time.time()
        execution_id = str(uuid.uuid4())
        trace_id = request.trace_id or str(uuid.uuid4())

        try:
            # 1. 加载 Graph 定义
            graph_def = self.graph_loader.load(request.graph_name)

            # 2. 创建运行时上下文
            ctx = self._create_runtime_context(request, trace_id, execution_id)

            # 3. 编译 Graph
            compiled_graph = self.compiler.compile(graph_def, ctx)

            # 4. 准备初始状态
            initial_state = compiled_graph.initial_state_template.copy()
            initial_state.update(request.inputs)

            # 5. 执行 Graph
            final_state = await compiled_graph.graph.ainvoke(initial_state)

            # 6. 提取输出
            outputs = self._extract_outputs(graph_def, final_state)

            # 7. 计算执行时间
            execution_time = time.time() - start_time

            # 8. 记录执行日志
            await self._log_execution(
                execution_id=execution_id,
                trace_id=trace_id,
                user_id=request.user_id,
                graph_name=request.graph_name,
                success=True,
                execution_time=execution_time,
            )

            # 9. 返回成功响应
            return ExecutionResponse(
                success=True,
                outputs=outputs,
                execution_id=execution_id,
                trace_id=trace_id,
                execution_time=execution_time,
                runtime_type=RuntimeType.CLOUD,
                metadata={
                    "graph_name": request.graph_name,
                },
            )

        except Exception as e:
            execution_time = time.time() - start_time

            # 记录错误日志
            await self._log_execution(
                execution_id=execution_id,
                trace_id=trace_id,
                user_id=request.user_id,
                graph_name=request.graph_name,
                success=False,
                execution_time=execution_time,
                error=str(e),
            )

            return ExecutionResponse(
                success=False,
                outputs=None,
                error=str(e),
                execution_id=execution_id,
                trace_id=trace_id,
                execution_time=execution_time,
                runtime_type=RuntimeType.CLOUD,
            )

    async def execute_async(self, request: ExecutionRequest) -> str:
        """
        异步执行 Graph（提交到 Celery 队列）

        Args:
            request: 执行请求

        Returns:
            执行ID
        """
        execution_id = str(uuid.uuid4())
        trace_id = request.trace_id or str(uuid.uuid4())

        # 保存初始状态到 Redis
        await self.redis.hset(
            f"execution:{execution_id}",
            mapping={
                "status": "pending",
                "trace_id": trace_id,
                "graph_name": request.graph_name,
                "user_id": request.user_id,
                "created_at": str(time.time()),
            },
        )

        # 提交到 Celery 队列
        from backend.app.task.tasks.agent import execute_graph_task

        execute_graph_task.delay(
            execution_id=execution_id,
            trace_id=trace_id,
            graph_name=request.graph_name,
            inputs=request.inputs,
            user_id=request.user_id,
        )

        return execution_id

    async def execute_stream(
        self, request: ExecutionRequest
    ) -> AsyncIterator[AgentEvent]:
        """
        执行 Graph（流式模式）

        Args:
            request: 执行请求

        Yields:
            AgentEvent: 执行过程中的事件流
        """
        execution_id = str(uuid.uuid4())
        trace_id = request.trace_id or str(uuid.uuid4())

        try:
            # 发送开始事件
            yield AgentEvent(
                type=EventType.EXECUTION_START,
                data={
                    "graph_name": request.graph_name,
                    "execution_id": execution_id,
                    "trace_id": trace_id,
                },
            )

            # 1. 加载 Graph 定义
            graph_def = self.graph_loader.load(request.graph_name)

            # 2. 创建运行时上下文
            ctx = self._create_runtime_context(request, trace_id, execution_id)

            # 3. 编译 Graph
            compiled_graph = self.compiler.compile(graph_def, ctx)

            # 4. 准备初始状态
            initial_state = compiled_graph.initial_state_template.copy()
            initial_state.update(request.inputs)

            # 5. 流式执行 Graph
            async for event in compiled_graph.graph.astream_events(
                initial_state, version="v1"
            ):
                # 转换 LangGraph 事件为 AgentEvent
                agent_event = self._convert_langgraph_event(event, execution_id)
                if agent_event:
                    yield agent_event

            # 发送完成事件
            yield AgentEvent(
                type=EventType.EXECUTION_COMPLETE,
                data={
                    "execution_id": execution_id,
                    "trace_id": trace_id,
                },
            )

        except Exception as e:
            # 发送错误事件
            yield AgentEvent(
                type=EventType.EXECUTION_ERROR,
                data={
                    "execution_id": execution_id,
                    "trace_id": trace_id,
                    "error": str(e),
                },
            )

    async def get_status(self, execution_id: str) -> dict[str, Any]:
        """
        获取执行状态

        Args:
            execution_id: 执行ID

        Returns:
            状态字典
        """
        status = await self.redis.hgetall(f"execution:{execution_id}")
        if not status:
            return {"status": "not_found"}
        return {k.decode(): v.decode() for k, v in status.items()}

    async def cancel(self, execution_id: str) -> bool:
        """
        取消执行

        Args:
            execution_id: 执行ID

        Returns:
            是否成功取消
        """
        # 设置取消标志
        await self.redis.set(f"execution:{execution_id}:cancel", "1", ex=3600)
        return True

    async def health_check(self) -> bool:
        """
        健康检查

        Returns:
            True 表示健康，False 表示异常
        """
        try:
            # 检查 Redis 连接
            await self.redis.ping()

            # 检查 Graph 加载器
            self.graph_loader.list_graphs()

            return True
        except Exception:
            return False

    def _create_runtime_context(
        self, request: ExecutionRequest, trace_id: str, run_id: str
    ) -> RuntimeContext:
        """
        创建运行时上下文

        Args:
            request: 执行请求
            trace_id: 追踪 ID
            run_id: 运行 ID

        Returns:
            RuntimeContext: 运行时上下文
        """
        # 获取用户模型配置
        default_model = self._get_user_model(request.user_id)

        # 创建上下文
        ctx = RuntimeContext(
            runtime_type=RuntimeType.CLOUD,
            user_id=request.user_id,
            inputs=request.inputs,
            api_keys={},  # 云端不需要直接传递 API Key
            trace_id=trace_id,
            run_id=run_id,
            model_default=default_model,
            extra={
                "db": self.db,
                "redis": self.redis,
                "browser_pool": self.browser_pool,
                "llm_client": self.llm_client,
            },
        )

        return ctx

    def _get_user_model(self, user_id: str) -> str:
        """
        根据用户订阅级别获取默认模型

        Args:
            user_id: 用户ID

        Returns:
            模型名称
        """
        # TODO: 从数据库查询用户订阅级别
        # Pro 用户可用 claude-opus-4-20250514
        return "claude-sonnet-4-20250514"

    def _extract_outputs(
        self, graph_def: dict[str, Any], final_state: dict[str, Any]
    ) -> Any:
        """
        从最终状态中提取输出

        Args:
            graph_def: Graph 定义
            final_state: 最终状态

        Returns:
            输出数据
        """
        spec = graph_def.get("spec", {})
        outputs_def = spec.get("outputs", {})

        if not outputs_def:
            return final_state

        outputs = {}
        for key, expr in outputs_def.items():
            if isinstance(expr, str) and expr.startswith("${state."):
                state_var = expr[8:-1]
                outputs[key] = final_state.get(state_var)
            else:
                outputs[key] = expr

        return outputs

    def _convert_langgraph_event(
        self, event: dict[str, Any], execution_id: str
    ) -> AgentEvent | None:
        """
        转换 LangGraph 事件为 AgentEvent

        Args:
            event: LangGraph 事件
            execution_id: 执行 ID

        Returns:
            AgentEvent 或 None
        """
        event_type = event.get("event")

        if event_type == "on_chain_start":
            return AgentEvent(
                type=EventType.NODE_START,
                data={
                    "execution_id": execution_id,
                    "node_name": event.get("name"),
                },
            )
        if event_type == "on_chain_end":
            return AgentEvent(
                type=EventType.NODE_COMPLETE,
                data={
                    "execution_id": execution_id,
                    "node_name": event.get("name"),
                    "output": event.get("data", {}).get("output"),
                },
            )
        if event_type == "on_chain_error":
            return AgentEvent(
                type=EventType.NODE_ERROR,
                data={
                    "execution_id": execution_id,
                    "node_name": event.get("name"),
                    "error": str(event.get("data", {}).get("error")),
                },
            )

        return None

    async def _log_execution(
        self,
        execution_id: str,
        trace_id: str,
        user_id: str,
        graph_name: str,
        success: bool,
        execution_time: float,
        error: str | None = None,
    ) -> None:
        """
        记录执行日志

        Args:
            execution_id: 执行ID
            trace_id: 追踪ID
            user_id: 用户ID
            graph_name: Graph 名称
            success: 是否成功
            execution_time: 执行时间
            error: 错误信息
        """
        # 保存到 Redis
        await self.redis.hset(
            f"execution:{execution_id}",
            mapping={
                "status": "completed" if success else "failed",
                "trace_id": trace_id,
                "user_id": user_id,
                "graph_name": graph_name,
                "execution_time": str(execution_time),
                "error": error or "",
                "completed_at": str(time.time()),
            },
        )

        # 设置过期时间 (7天)
        await self.redis.expire(f"execution:{execution_id}", 7 * 24 * 3600)
