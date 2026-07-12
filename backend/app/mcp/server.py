"""
HASN 云端 MCP Server

提供云端工具给 Agent Runtime
"""

import hashlib
import logging

from typing import Any, NoReturn

from backend.app.external_mcp.external_tool import load_external_mcp_tools_for_agent
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.errors import McpErrorCode, McpToolError
from backend.app.mcp.tool_directory import ToolDirectoryService
from backend.app.mcp.tool_exposure import (
    ACTION_ASK,
    REASON_EXTERNAL_NOT_BOUND,
    REASON_OWNER_DENIED,
    REASON_PRIVILEGED,
    REASON_ROLE_INSUFFICIENT,
    ExposureDecision,
    tool_exposure_policy,
)
from backend.app.mcp.tools.artifact import ARTIFACT_TOOLS
from backend.app.mcp.tools.asset import ASSET_TOOLS
from backend.app.mcp.tools.base import BaseTool
from backend.app.mcp.tools.contact import ContactListTool, ContactRequestTool, ContactSearchTool
from backend.app.mcp.tools.deck import DECK_TOOLS
from backend.app.mcp.tools.designsystem import DESIGNSYSTEM_TOOLS
from backend.app.mcp.tools.diag import DIAG_TOOLS
from backend.app.mcp.tools.group import GroupJoinTool
from backend.app.mcp.tools.marketplace import MARKETPLACE_TOOLS
from backend.app.mcp.tools.memory import MEMORY_TOOLS
from backend.app.mcp.tools.message import (
    ConversationListTool,
    MessageListTool,
    MessageSearchTool,
    MessageSendTool,
)
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

        # 消息工具：发送 + 读取（收件箱/会话详情，默认倒序 + keyset 翻页）+ 会话列表 + 关键词搜索。
        self.tool_registry.register(MessageSendTool())
        self.tool_registry.register(MessageListTool())
        self.tool_registry.register(ConversationListTool())
        self.tool_registry.register(MessageSearchTool())

        # 资产上传已收归本地工具 hasn.asset.upload(path)：分身只传路径，daemon 侧读盘上桶返
        # hasn://asset/{id}（铁律·禁二进制 base64 入参）。旧云端 hasn.asset.create(base64) 已删除。

        # 联系人工具：列出 + 按昵称/唤星号搜索（含好友名下 agent）+ 代主人发起好友请求。
        # 打通"搜联系人→发消息"与"搜陌生人(user.search)→发起加好友"两条闭环。
        self.tool_registry.register(ContactListTool())
        self.tool_registry.register(ContactSearchTool())
        self.tool_registry.register(ContactRequestTool())

        # 群工具：分身代主人加入某群（尊重群加入策略）。打通「群名片→加入群聊」闭环（doc22）。
        self.tool_registry.register(GroupJoinTool())

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

        # 产物 + 资产工具（产物化 P6 + 分身资源检索与素材站工具设计 A-P0/A-P1）：
        # - artifact 域：显式登记产物（record）+ 检索本分身产物（list/search/get，出参剥 body/防呆描述）。
        #   从 hasn-node 本地 hasn-mcp 迁来（纯云端代理；audited 自动捕获仍留本地拦本地工具副作用）。
        # - asset 域：hasn.asset.get 按 asset_id 取技术元数据，供分身校验手里的 hasn://asset/{id} 引用
        #   （owner 校验，无权/不存在同响应）。纯云端只读。
        for retrieval_tool in (*ARTIFACT_TOOLS, *ASSET_TOOLS):
            self.tool_registry.register(retrieval_tool)

        # 设计系统工具（14-DS-P4）：8 个云端工具全注册。
        # - 云端权威 4（操作云端数据）：import/save/list/get（TOOLMIG-4 从本地迁来）。
        # - 确定性纯函数 4（TOOLMIG：Python 移植 hasn_designsystem_core，云端分身可用）：
        #   compile_tokens/derive/validate/extract_components——本地 Rust 同名工具暂留待退役。
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
        # 一并注册错误诊断工具（21-可观测性 §8）：读类 diag:read:all、写类 diag:manage，
        # 两 scope 均命中特权前缀 diag: → G1 平台特权门（doc18 §4.1）统一判定：仅经 Admin 授予表
        # ∪ ENV bootstrap 拿到 diag:* 的「平台运维分析师」分身可见/可调；普通分身发现面隐身、
        # 执行面 TOOL_NOT_FOUND 泛化。跨 owner 全量读/处置（无隔离）。合并进同一 for 不拉高圈复杂度。
        for workflow_or_diag_tool in (*WORKFLOW_TOOLS, *DIAG_TOOLS):
            self.tool_registry.register(workflow_or_diag_tool)

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

        # 记忆工具（doc16 Phase C1：记忆迁云端权威）：save/search/recall/list 四工具，
        # 直调云端权威 semantic_fact_service 读写 hasn_memory.semantic_fact（单一云端记忆）。
        # save=memory:write（出厂 Allow，owner 三态可覆盖）；读类（search/recall/list）无 scope。
        for memory_tool in MEMORY_TOOLS:
            self.tool_registry.register(memory_tool)

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

    def _raise_for_blocked_decision(
        self, decision: ExposureDecision, tool_name: str, agent_context: AgentContext
    ) -> NoReturn:
        """暴露管线非放行决策 → 执行面错误映射（doc18 §3·U3 抽出，收敛 call_tool 分支）。

        VISIBLE_DENY（G3 应用权益门）与 HIDDEN（G1 特权 / 来源 / owner 三态 / 运行位置）分派到各自
        错误码，与发现面 _can_discover 同源、与网关付费墙 / 转发面兜底同码同文案。
        """
        if decision.is_visible_deny:
            # G3 应用权益门（doc18 §4.3·U3）：工具可见（发现面带 access_hint 引导购买/席位/切空间），
            # 但执行面按合并准入 reason 回结构化错误——对齐 AI-Native 网关 `_entitlement_denial`
            # 的付费墙形状（那里以 code=15030 reason=entitlement_denied 拒；此处 MCP 面用 TOOL_NOT_ALLOWED
            # + 具体 reason，令 daemon/分身能据 reason 引导主人完成购买/席位指派/切换企业空间）。
            logger.info(
                f'Tool call blocked by entitlement gate {decision.reason} '
                f'(app={decision.app_id}): {tool_name} (agent={agent_context.hasn_id})'
            )
            raise McpToolError(
                McpErrorCode.TOOL_NOT_ALLOWED,
                f'应用「{decision.app_id}」尚未准入（{decision.reason}）：'
                f'请在应用中心完成购买 / 席位指派 / 切换企业空间后再调用该工具',
            )
        logger.info(
            f'Tool call blocked by exposure gate {decision.gate}/{decision.reason}: '
            f'{tool_name} (agent={agent_context.hasn_id})'
        )
        if decision.reason == REASON_OWNER_DENIED:
            # 现状保持（103 §2）：三态 deny 在执行面是 PermissionError（403）。
            # 「deny 执行面也归 TOOL_NOT_FOUND」是行为变化，留 U5 与福仔拍板后统一。
            raise PermissionError(f'Capability denied by owner for tool: {tool_name}')
        if decision.reason == REASON_EXTERNAL_NOT_BOUND:
            # 与 _dispatch_by_source 兜底同码同文案（该兜底保留作防御纵深）。
            raise McpToolError(
                McpErrorCode.DIRECT_CALL_DENIED,
                f'Agent 无权调用该外部 MCP 工具: {tool_name}',
            )
        if decision.reason in (REASON_PRIVILEGED, REASON_ROLE_INSUFFICIENT):
            # G1 特权门 / G4 企业角色门（doc18 §4.1/§4.4）：两者都是**存在性隐藏**门——
            # 与 _resolve_tool 的「真·未注册」路径**逐字节同款**错误（同码 MCP_9209 + 同文案），
            # 令「存在但特权/角色隐身」与「根本不存在」对普通分身不可区分——否则攻击者比对措辞即可
            # 侧探 hasn.diag.* 运维工具、或「主人有无某企业角色」。回显调用方自己传入的 tool_name
            # 与真 404 一致、不构成额外泄漏；泄漏面是**措辞差异**。（G4 当前 inert，见 tool_exposure。）
            raise McpToolError(McpErrorCode.TOOL_NOT_FOUND, f'Tool not found: {tool_name}')
        # 兜底：其余任何 HIDDEN reason（理论不可达——上面已穷尽 G1/G2/G4/G5 的隐藏 reason；
        # 原「运行位置收口」按分身在本地/云端隐藏工具的门已于 2026-07-10 整体退役，见 tool_exposure）
        # 一律按存在性隐藏处理，绝不静默穿透返回 None。
        raise McpToolError(McpErrorCode.TOOL_NOT_FOUND, f'Tool not found: {tool_name}')

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

            # register-on-write（doc31/32 RC-P8 泛化）：剥离系统注入的工作会话 id（`_hasn_session_id`，
            # 分身不可伪造）→ agent_context.work_session_id，供 deck/app 写点把产物登记进「工作会话
            # 资源栏」。须在 trust gate / dispatch 前剥离（工具体永不见）。cloud 直连面（Hermes 出站
            # 打在入参）与 daemon 代理面（ai_native gateway 注入进 input）走同一提取点；缺省=主会话直调。
            from backend.app.mcp import trust_gate as _tg

            arguments, work_session_id = _tg.pop_session_id(arguments)
            if work_session_id:
                agent_context.work_session_id = work_session_id

            # L3 工具门（doc08 §4·RT3·云端半场）：先剥离系统注入的会话信任语境保留参数
            # （_hasn_is_external / _hasn_peer_id / _hasn_peer_trust，分身不可伪造），令下游
            # 暴露判定 / ask 验票 / dispatch / 审计都只见剥离后的干净入参；同时据其判档（对外
            # 会话按对端真实 trust 硬门控，不足即抛 MCP_9217 结构化拒绝）。
            arguments = await self._enforce_conversation_trust_gate(agent_context, tool, arguments)

            # 统一暴露管线（doc18 §3·实施/103 U1）：resolve 后一次 evaluate，执行面按决策映射。
            # 与发现面 _can_discover 同源（HIDDEN 即不可见），转发面 hasn.cloud.tool.call 重入
            # 本方法自动覆盖。维度② 对象可达性由工具 execute 内部返回，与管线正交。
            decision = tool_exposure_policy.evaluate(agent_context, tool)
            if decision.is_hidden or decision.is_visible_deny:
                # 非放行决策（HIDDEN / VISIBLE_DENY）统一映射为执行面错误（doc18 §3·U3）。
                self._raise_for_blocked_decision(decision, tool_name, agent_context)

            # G6 统一资源权限门（doc32 §5·doc33 S2-4）：确定性无权先拒，不进 ask 打扰主人审批。
            # 工具声明 resource_access 即判权，判过把已判权资源经 ContextVar 送达 handler。
            await self._enforce_resource_gate(agent_context, tool, arguments)

            if decision.action == ACTION_ASK:
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
                    # cloud-pend 复判（doc32 §5.2 TOCTOU 收口）：审批可等分钟级，等待期共享可能被撤销
                    # → 批准落回执行前重跑一次 G6（票据重试路径重进 call_tool 天然复判，不在此重跑）。
                    await self._enforce_resource_gate(agent_context, tool, arguments)
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
        finally:
            # G6 已判权资源随请求结束即清（doc33 S2-3/S2-4），防跨调用串味。
            from backend.app.mcp.context import clear_authorized_resources

            clear_authorized_resources()

    async def _enforce_resource_gate(
        self, agent_context: AgentContext, tool: BaseTool, arguments: dict[str, Any]
    ) -> None:
        """G6 统一资源权限门（doc32 §5·doc33 S2-4·MCP 直连面）：工具声明 `resource_access` 即判权。

        无声明工具零开销直返。判过把 `{param → AuthorizedResource}` 经 ContextVar 送达 handler。
        本面无现成 DB session，enforce 自开短 session（`ResourceMeta.row` 因此对 handler 是 detached
        只读快照，doc32 §4.1）。拒绝路径（doc33 S2-4）：
        - `NotFoundError`（不存在 / 无任何权限，存在性隐藏）→ `McpToolError(TOOL_NOT_FOUND)`；
        - `ForbiddenError`（有权但档位不足）→ `McpToolError(RESOURCE_PERMISSION_INSUFFICIENT)`（携当前/所需档）。
        """
        declarations = getattr(tool, 'resource_access', None)
        if not declarations:
            return
        from backend.app.hasn.service.authz import Subject, resource_gate
        from backend.app.mcp.context import set_authorized_resources
        from backend.common.exception import errors
        from backend.database.db import async_db_session

        subject = Subject.agent(agent_context.agent_hasn_id, agent_context.owner_hasn_id)
        try:
            async with async_db_session() as db:
                authorized = await resource_gate.enforce_declaration(db, subject, declarations, arguments)
        except errors.ForbiddenError as exc:
            # 档位不足：msg 已含当前档 + 所需档（gate 侧构造），分身据此礼貌说明权限不足。
            raise McpToolError(McpErrorCode.RESOURCE_PERMISSION_INSUFFICIENT, exc.msg or '对该资源权限不足') from exc
        except errors.NotFoundError as exc:
            # 存在性隐藏：不存在 / 无任何权限一律按「工具不存在」，不泄露资源是否存在。
            raise McpToolError(McpErrorCode.TOOL_NOT_FOUND, exc.msg or '资源不存在') from exc
        set_authorized_resources(authorized)

    async def _enforce_conversation_trust_gate(
        self, agent_context: AgentContext, tool: BaseTool, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """L3 工具门（doc08 §4·RT3·云端半场）：剥离会话信任语境保留参数 + 对外会话按档硬门控。

        1. 剥离系统注入的 ``_hasn_is_external`` / ``_hasn_peer_id`` / ``_hasn_peer_trust``
           （分身不可伪造），返回干净入参供下游 dispatch/审计（工具体永不见这些键）。
        2. 仅当**对外会话**（``is_external``）且工具声明了 ``min_trust_level`` 才判档：
           - 1:1 优先据 ``peer_id`` 从云端权威 ``hasn_contacts`` 解析对端**真实** trust
             （复用 RT1.5 ``effective_relation``）；解析不到再回落 daemon 预解析的 ``peer_trust``。
           - 群会话（无 ``peer_id``、daemon 填 roster 最低档）直接用 ``peer_trust``。
           - ``peer_trust`` 仍缺失 → 门内 fail-closed 当陌生人(1)。
        3. 主会话 / 主人自环（``is_external=False``）不受限；无对外门工具（``min_trust_level=None``）
           恒放行。判档不足抛 ``McpToolError`` TRUST_LEVEL_INSUFFICIENT（含当前档+所需档，
           分身据此礼貌回绝）。

        Raises:
            McpToolError: 对外会话里对端档低于工具 ``min_trust_level``（code=TRUST_LEVEL_INSUFFICIENT）。
        """
        from backend.app.mcp import trust_gate

        # 恒先剥离入参保留参数（工具体永不见 _hasn_*），得干净入参 + reserved-arg 兜底语境。
        cleaned, is_external, peer_id, peer_trust = trust_gate.pop_trust_context(arguments)
        # header 优先（CLI runtime 走 daemon 组装的 X-Hasn-* header；本半场闭合的主通道）；
        # 无 header 才回落上面的 reserved-arg（Hermes / 其它注入路径）。二者皆缺 = 主会话（放行）。
        header_ctx = trust_gate.read_header_trust_context()
        if header_ctx is not None:
            is_external, peer_id, peer_trust = header_ctx
        min_trust = getattr(tool, 'min_trust_level', None)
        # 主会话 / 无对外门 → 无需解析对端，直接放行（never over-block：缺语境即主会话）。
        if not is_external or min_trust is None:
            return cleaned
        # 对外 1:1：据 peer_id 解析云端权威真实档（解析不到才回落 daemon 预解析档）。
        if peer_id:
            try:
                from backend.database.db import async_db_session

                async with async_db_session() as db:
                    resolved = await trust_gate.resolve_conversation_peer_trust(
                        db, agent_context.owner_hasn_id, peer_id
                    )
            except Exception as exc:
                logger.warning('L3 trust gate peer resolve failed for %s: %r', peer_id, exc)
                resolved = None
            if resolved is not None:
                peer_trust = resolved
        trust_gate.evaluate_min_trust_level(min_trust, peer_trust, is_external=is_external)
        return cleaned

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
