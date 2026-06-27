"""
HASN 云端 MCP Server

提供云端工具给 Agent Runtime
"""

import hashlib
import logging

from typing import Any

from backend.app.external_mcp.external_tool import load_external_mcp_tools_for_agent
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.errors import McpErrorCode, McpToolError
from backend.app.mcp.runtime_visibility import is_tool_hidden_for_runtime
from backend.app.mcp.tool_directory import ToolDirectoryService
from backend.app.mcp.tools.artifact import ARTIFACT_TOOLS
from backend.app.mcp.tools.asset import AssetCreateTool
from backend.app.mcp.tools.base import BaseTool
from backend.app.mcp.tools.contact import ContactListTool, ContactRequestTool, ContactSearchTool
from backend.app.mcp.tools.deck import DECK_TOOLS
from backend.app.mcp.tools.designsystem import DESIGNSYSTEM_TOOLS
from backend.app.mcp.tools.marketplace import MARKETPLACE_TOOLS
from backend.app.mcp.tools.message import MessageListTool, MessageSendTool
from backend.app.mcp.tools.notification import NOTIFICATION_TOOLS
from backend.app.mcp.tools.owner import OWNER_TOOLS
from backend.app.mcp.tools.plan import PLAN_TOOLS
from backend.app.mcp.tools.registry import ToolRegistry
from backend.app.mcp.tools.task import TASK_TOOLS
from backend.app.mcp.tools.tool_call import ToolCallTool
from backend.app.mcp.tools.tool_search import ToolSearchTool
from backend.app.mcp.tools.user import UserSearchTool
from backend.app.mcp.tools.workbench import WORKBENCH_TOOLS
from backend.app.mcp.tools.workflow import WORKFLOW_TOOLS
from backend.common.security.scope_policy import MODE_ASK, MODE_DENY

logger = logging.getLogger(__name__)

# 发现工具名（含迁移别名）。其 summary 级查询属"普通查询"，可降采样（04 §6）。
_DISCOVERY_TOOL_NAMES = frozenset({'hasn.cloud.tool.search', 'hasn.tool.search'})

# 调用元工具（03 §9）：透明转发，审计落**内层**工具，wrapper 自身不单独记，避免双记。
_DISPATCH_TOOL_NAMES = frozenset({'hasn.cloud.tool.call', 'hasn.local.tool.call'})

# 普通 summary/sources/apps 发现查询的审计采样率：约 1/N 落库，trace_id 仍全量保留聚合能力。
_SUMMARY_AUDIT_SAMPLE_RATE = 10


async def load_app_tools_for_agent(agent_id: str, owner_id: str) -> list[BaseTool]:  # noqa: RUF029
    """Agent 维度的 App 工具（P4-B）。

    当前 AI-Native App 的可见性按 workspace 已发布 manifest 投影（见
    load_app_tools_for_owner）；per-agent 安装层后续细化。此处返回空避免重复，
    workspace 可见集合由 owner 维度加载。零 fake：不造假。
    """
    return []


async def load_app_tools_for_owner(owner_id: str) -> list[BaseTool]:
    """Owner/workspace 维度的 App 工具（P4-B，Q1）：

    把已发布 AI-Native manifest（builtin community/knowledge + DB published）的
    capability 投影成 app-source 工具，闭合「App manifest 从未进 tool.search」的 GAP。
    零 fake：无已发布 manifest → 空 list。
    """
    from backend.app.mcp.tools.app_tool_loader import load_published_app_tools

    return await load_published_app_tools()


class HasnCloudMcpServer:
    """HASN 云端 MCP Server"""

    def __init__(self) -> None:
        self.tool_registry = ToolRegistry()
        self.tool_directory = ToolDirectoryService(self.tool_registry)

        # 注册内置工具
        self._register_builtin_tools()

    def _register_builtin_tools(self) -> None:
        """注册内置工具"""
        self.tool_registry.register(ToolSearchTool(self.tool_directory))
        # 迁移别名：hasn.tool.search → hasn.cloud.tool.search（03 §3）。
        self.tool_registry.register_alias('hasn.tool.search', 'hasn.cloud.tool.search')

        # 通用调用元工具：转发任意 canonical 工具（03 §9），与 search 对称。
        self.tool_registry.register(ToolCallTool(self))

        # 用户搜索工具（平台 user 域）
        self.tool_registry.register(UserSearchTool())

        # 消息工具
        self.tool_registry.register(MessageSendTool())
        self.tool_registry.register(MessageListTool())

        # 资产工具：把分身自己的内容（SVG/base64 图片/文本…）上传成 hasn://asset，供消息附件引用。
        self.tool_registry.register(AssetCreateTool())

        # 联系人工具：列出 + 按昵称/唤星号搜索（含好友名下 agent）+ 代主人发起好友请求。
        # 打通"搜联系人→发消息"与"搜陌生人(user.search)→发起加好友"两条闭环。
        self.tool_registry.register(ContactListTool())
        self.tool_registry.register(ContactSearchTool())
        self.tool_registry.register(ContactRequestTool())

        # 规划工具（19-规划与目标管理）：goal/project/todo/event/habit CRUD + capture/triage/today/preference。
        # 这些「纯云端代理」工具从 hasn-node 本地 hasn-mcp 迁来（不操作本地文件/数据 → 走云端 platform tool）；
        # 仍留本地的是有真本地编排/计算的 decompose/briefing/review/schedule/reschedule/delegate/validate。
        for plan_tool in PLAN_TOOLS:
            self.tool_registry.register(plan_tool)

        # 主人画像工具（19-了解主人）：hasn.owner.coverage.get — 采访分身读「主人 5 维画像还缺哪几维」，
        # 定向采访（缺什么采访什么）。纯云端只读，直调 OwnerProfileCoverageService.assess_if_stale。
        for owner_tool in OWNER_TOOLS:
            self.tool_registry.register(owner_tool)

        # 通知工具（统一通知 §7）：AI-Native App 以 App 身份给主人发通知。
        # 从 hasn-node 本地 hasn-mcp 迁来（纯云端代理 → 走云端 platform tool）。
        for notification_tool in NOTIFICATION_TOOLS:
            self.tool_registry.register(notification_tool)

        # 产物工具（产物化 P6）：分身显式登记一条产物（文本/markdown 直接入库等）。
        # 从 hasn-node 本地 hasn-mcp 迁来（纯云端代理 → 走云端 platform tool；
        # audited 自动捕获机制仍留本地，因其拦截本地工具执行的副作用）。
        for artifact_tool in ARTIFACT_TOOLS:
            self.tool_registry.register(artifact_tool)

        # 设计系统工具（14-DS-P4 云端权威 4 工具）：import/save/list/get。
        # 从 hasn-node 本地 hasn-mcp 迁来（纯云端权威 → 走云端 platform tool；
        # 本地仅留确定性纯函数 compile_tokens/derive/validate/extract_components）。
        for designsystem_tool in DESIGNSYSTEM_TOOLS:
            self.tool_registry.register(designsystem_tool)

        # 任务工具（12-任务系统）：create/list/get/update/pause/resume/delete/run_now/list_runs/get_run/query_results。
        # 从 hasn-node 本地 hasn-mcp 迁来（不依赖本地操作 → 走云端 platform tool，TOOLMIG2）；
        # 执行 fire/调度仍由本地/云端 Runtime Host 基于 task sync mirror 自行 tick（中心不 tick）。
        for task_tool in TASK_TOOLS:
            self.tool_registry.register(task_tool)

        # 工作流工具（12-多任务编排 DAG）：create/list_agents/get/get_node_result/run/pause/cancel/list。
        # 从 hasn-node 本地 hasn-mcp 迁来（不依赖本地操作 → 走云端 platform tool，TOOLMIG2）；
        # add_node/add_edge 不在 agent 工具面（经 create 一次声明整图）；整图 fire 仍由 Runtime Host tick。
        for workflow_tool in WORKFLOW_TOOLS:
            self.tool_registry.register(workflow_tool)

        # 演示文稿工具（17-deck）：create/get/list/outline.set/page.write(_batch)/page.edit/page.delete/
        # page.reorder/delete/style.list/style.get（读 4 无 scope，写 8 = deck:manage）。
        # 从 hasn-node 本地 hasn-mcp 迁来（TOOLMIG2-P3，福仔选 B：完整迁 deck）；骨架校验 + 按 position
        # upsert 编排 + 整图重排在云端忠实复刻（page_skeleton.py / deck_service.reorder_pages），云端分身可完整创作。
        for deck_tool in DECK_TOOLS:
            self.tool_registry.register(deck_tool)

        # 技能市场工具（15-技能市场/11-doc）：浏览/装卸/发布 9 个云端工具。
        for tool_cls in MARKETPLACE_TOOLS:
            self.tool_registry.register(tool_cls())

        # 工作台工具（13-工作台/04-doc §5）：主脑发布每日关注简报。
        for tool_cls in WORKBENCH_TOOLS:
            self.tool_registry.register(tool_cls())

        logger.info(f'Registered {len(self.tool_registry.get_all_tools())} builtin tools')

    async def list_tools(self, agent_context: AgentContext, namespace: str | None = None) -> list[dict[str, Any]]:
        """
        列出可用工具

        Args:
            agent_context: Agent 上下文
            namespace: 可选的命名空间过滤

        Returns:
            工具列表
        """
        try:
            # 渐进式暴露（设计 03 §9）：默认只回 bootstrap 元工具（tool.search + tool.call）。
            # 这两个元工具在 __init__ 启动期已静态注册（registry.BOOTSTRAP_TOOL_NAMES），
            # 不依赖按 agent/owner 动态加载的 app 工具——故此处**不**调 _load_app_tools()，
            # 省掉 list_tools 路径上每次 keepalive 都白跑一遍的 DB/网络加载。
            # （hermes Runtime 每 180s 拿 list_tools 当连通性探测，调用量虽小但纯属浪费。）
            # 长尾工具不进清单，function-calling Runtime 经 hasn.cloud.tool.call 直调任意
            # canonical name；tool.call/tool.search 在各自路径里自行 _load_app_tools 加载目录。
            # `namespace` 仅兼容保留，不收窄。
            available_tools = self.tool_directory.list_bootstrap_tools(agent_context)
        except Exception as e:
            logger.error(f'Error listing tools: {e!s}', exc_info=True)
            raise
        else:
            # 例行 keepalive 探测，降到 DEBUG，避免刷屏（对齐 daemon 侧 hasn-mcp server.rs）。
            logger.debug(f'Agent {agent_context.hasn_id} listed {len(available_tools)} tools')
            return available_tools

    async def call_tool(self, agent_context: AgentContext, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """
        调用工具

        Args:
            agent_context: Agent 上下文
            tool_name: 工具名称
            arguments: 工具参数

        Returns:
            工具执行结果
        """
        try:
            logger.info(f'Agent {agent_context.hasn_id} calling tool: {tool_name}')

            await self._load_app_tools(agent_context)
            await self._load_external_mcp_tools(agent_context)

            # 解析工具并确定 source（P2）。未注册 → MCP_9209。
            tool, source = self._resolve_tool(tool_name)

            # 运行位置守卫（TOOLMIG2-P4）：本地分身在云端面不得调 deck/task/workflow（其用
            # 本地面那份本地优先引擎）。发现面已隐藏；此为执行面兜底，连 hasn.cloud.tool.call
            # 透传（委托回本方法重入）也一并拦住。见 runtime_visibility。
            if is_tool_hidden_for_runtime(tool_name, getattr(agent_context, 'runtime_location', 'cloud')):
                raise McpToolError(
                    McpErrorCode.TOOL_NOT_FOUND,
                    f'{tool_name} 在云端面对本地分身不可用：本地分身请使用本地工具面（hasn-local）的同名工具',
                )

            # 维度① 能力授权（D3 活取三态）：deny→拒；ask→挂起主人批准（P6）；allow→执行。
            # 维度② 对象可达性由社交工具 execute 内部 check_relation_permission 返回，与此正交。
            mode = agent_context.tool_mode(tool)
            if mode == MODE_DENY:
                raise PermissionError(f'Capability denied by owner for tool: {tool_name}')
            if mode == MODE_ASK:
                # 令牌重试（doc15 §3）：先验一次性票据——带有效票（agent/tool/args_hash 匹配且
                # jti 未用）→ 原子消费后**跳过闸门直接执行**；无票/票无效 → 云端不长挂，开一条审批
                # 请求并把 approval_required 信封作为工具结果返回，由 daemon 发卡片 + 换票 + 带票重试。
                if not await self._consume_capability_ticket(agent_context, tool_name, arguments):
                    from backend.app.mcp.ask_gate import ask_approval_gate

                    # 云端挂起(cloud-pend，福仔 2026-06-08 拍板，doc15 A1 修订)：分身经 cloud 直连面
                    # 调云端工具不经 daemon 中转，故云端**自己挂起**——发审批卡片给主人 + 阻塞轮询
                    # 裁决，对 agent 透明（agent 只等工具返回，不知道 ask 过程）。批准→落到下面真执行；
                    # 拒绝/超时→回**工具错误**（绝不把 approval_required 透传给 agent）。
                    verdict = await ask_approval_gate.request_and_wait(
                        agent_hasn_id=agent_context.agent_hasn_id,
                        owner_hasn_id=agent_context.owner_hasn_id,
                        tool_name=tool_name,
                        required_scopes=list(getattr(tool, 'required_scopes', []) or []),
                        default_mode=agent_context.default_mode,
                        capability_modes=agent_context.capability_modes,
                        arguments=arguments,
                    )
                    if verdict.get('decision') != 'approved':
                        denied = verdict.get('decision') == 'denied'
                        return {
                            'ok': False,
                            'error': 'approval_denied' if denied else 'approval_timeout',
                            'message': '主人已拒绝该操作' if denied else '审批超时：主人未在时限内确认',
                        }
                # 验票通过 / 主人已批准 → 落到下面 _dispatch_by_source 真正执行（工具体只在放行这次运行）。

            # 按 source 分发执行
            result = await self._dispatch_by_source(agent_context, tool, source, arguments)

            # 记录审计日志（04 §6）：真实工具调用 / schema 查询必审计；
            # 普通 summary 发现查询可降采样（trace_id 仍全量返回，保留聚合能力）。
            if self._should_audit_call(tool_name, arguments, success=True):
                await self._log_tool_call(agent_context, tool_name, arguments, result, success=True)
        except Exception as e:
            logger.error(f'Tool {tool_name} execution failed: {e!s}', exc_info=True)

            # 失败 / 拒绝一律审计（04 §6：Tool 拒绝 + scope/role 拒绝必审计）。
            # 调用元工具透明转发：失败也由内层工具自行审计，wrapper 自身不重复记。
            if tool_name not in _DISPATCH_TOOL_NAMES:
                try:
                    await self._log_tool_call(agent_context, tool_name, arguments, None, success=False, error=str(e))
                except Exception:
                    logger.exception('Failed to record tool-call denial audit')

            raise
        else:
            return result

    def _should_audit_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        success: bool,
    ) -> bool:
        """审计采样判定（04 §6）。

        必审计：失败 / 拒绝、真实工具调用、schema 查询。
        可降采样：普通 summary/sources/apps 发现查询——按 (tool, query) 稳定哈希
        约 1/N 落库；trace_id 仍随结果全量返回，聚合能力不丢失。
        """
        if tool_name in _DISPATCH_TOOL_NAMES:
            return False  # 透明转发：审计落内层工具，wrapper 自身不记
        if not success:
            return True
        if tool_name not in _DISCOVERY_TOOL_NAMES:
            return True
        detail = str((arguments or {}).get('detail', 'summary'))
        if detail == 'schema':
            return True
        query = str((arguments or {}).get('query', ''))
        digest = hashlib.sha256(f'{tool_name}|{query}'.encode()).hexdigest()
        return int(digest[:8], 16) % _SUMMARY_AUDIT_SAMPLE_RATE == 0

    async def _consume_capability_ticket(
        self, agent_context: AgentContext, tool_name: str, arguments: dict[str, Any]
    ) -> bool:
        """ask 分支验票跳闸（A-P2）。

        带一次性能力票（`X-Capability-Ticket`，由传输层落入 ContextVar）且其
        agent/tool/args_hash 与本次调用匹配、jti 未用 → **原子消费** + 标记审批请求 consumed →
        返回 True（跳过闸门执行）。无票 / 票无效 / 重放 → False（走 open_request 重新审批）。
        """
        from backend.app.mcp.context import get_capability_ticket

        ticket = get_capability_ticket()
        if not ticket:
            return False

        from backend.app.mcp.ask_gate import _canonical_args_hash, ask_approval_gate
        from backend.common.security.capability_ticket import consume_capability_ticket

        claims = await consume_capability_ticket(
            ticket,
            agent_hasn_id=agent_context.agent_hasn_id,
            tool_name=tool_name,
            args_hash=_canonical_args_hash(arguments),
        )
        if not claims:
            return False
        await ask_approval_gate.mark_consumed(str(claims.get('request_id') or ''))
        logger.info('Capability ticket consumed for agent %s tool %s', agent_context.hasn_id, tool_name)
        return True

    def _check_tool_permission(self, agent_context: AgentContext, tool: BaseTool) -> bool:
        """检查 Agent 是否有权限调用该工具（维度① 三态：deny→False，allow/ask→True）。"""
        return not agent_context.is_tool_denied(tool)

    def _resolve_tool(self, tool_name: str) -> tuple[BaseTool, str]:
        """解析工具名到 (tool, source)，未注册抛 MCP_9209（P2）。"""
        tool = self.tool_registry.get_tool(tool_name)
        if tool is None:
            raise McpToolError(McpErrorCode.TOOL_NOT_FOUND, f'Tool not found: {tool_name}')
        return tool, getattr(tool, 'source', 'platform')

    async def _dispatch_by_source(
        self,
        agent_context: AgentContext,
        tool: BaseTool,
        source: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """按 source 分发到对应 handler（04 §7）。

        platform / app 在云端 server 内由各自 BaseTool.execute 自路由其 handler
        （app → ai_native_runtime_gateway）。external（第三方 MCP，P7）由 ExternalMcpTool.execute
        委托 ExternalMcpGateway.proxy_call 代理到第三方 server（binding/health/secret/quota 全校验）。
        """
        if source == 'external':
            tool_name = getattr(tool, 'name', '')
            allowed = getattr(agent_context, 'external_allowed_tools', set()) or set()
            if tool_name not in allowed:
                # 防御性兜底：external 工具不在本 Agent 授权集合 → 拒绝（发现层已挡，此为执行层兜底）。
                raise McpToolError(
                    McpErrorCode.DIRECT_CALL_DENIED,
                    f'Agent 无权调用该外部 MCP 工具: {tool_name}',
                )
        return await tool.execute(agent_context, arguments)

    async def _load_app_tools(self, agent_context: AgentContext) -> None:
        try:
            agent_tools = await load_app_tools_for_agent(
                agent_id=agent_context.hasn_id,
                owner_id=agent_context.owner_id,
            )
            owner_tools = await load_app_tools_for_owner(owner_id=agent_context.owner_id)
            for tool in [*agent_tools, *owner_tools]:
                if self.tool_registry.get_tool(tool.name):
                    continue
                self.tool_registry.register(tool)
        except Exception as e:
            logger.error(f'Failed to load app tools: {e}', exc_info=True)

    async def _load_external_mcp_tools(self, agent_context: AgentContext) -> None:
        """P7：解析该 Agent 的第三方 MCP binding，投影成 source='external' 工具入注册表。

        - 工具实例全局共享、幂等注册（canonical 名全局唯一，server namespace 保证不撞）。
        - 发现/调用资格按 `agent_context.external_allowed_tools`（gate1 owner 启用 + gate2
          binding allowed_tools）per-request 过滤，工具实例**不**携带 agent 状态，杜绝串号。
        """
        try:
            tools = await load_external_mcp_tools_for_agent(agent_context)
            for tool in tools:
                if not self.tool_registry.get_tool(tool.name):
                    self.tool_registry.register(tool)
            agent_context.external_allowed_tools = {tool.name for tool in tools}
        except Exception as e:
            logger.error(f'Failed to load external MCP tools: {e}', exc_info=True)
            agent_context.external_allowed_tools = set()

    async def _log_tool_call(
        self,
        agent_context: AgentContext,
        tool_name: str,
        arguments: dict[str, Any],
        result: Any,
        *,
        success: bool,
        error: str | None = None,
    ) -> None:
        """记录工具调用审计日志"""
        try:
            from backend.app.hasn.service.hasn_audit_log_service import HasnAuditLogService
            from backend.database.db import async_db_session

            async with async_db_session() as db:
                audit_service = HasnAuditLogService()
                await audit_service.append(
                    db=db,
                    actor_type='agent',
                    actor_id=agent_context.hasn_id,
                    action='mcp_tool_call',
                    target_type='tool',
                    target_id=tool_name,
                    details={
                        'tool_name': tool_name,
                        'arguments': arguments,
                        'result': result if success else None,
                        'error': error,
                        'success': success,
                    },
                )
        except Exception as e:
            logger.error(f'Failed to log tool call: {e!s}')


# 全局 MCP Server 实例
mcp_server = HasnCloudMcpServer()
