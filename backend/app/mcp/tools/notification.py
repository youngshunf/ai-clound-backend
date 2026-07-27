"""平台工具 · notification 域（统一通知设计 §7，AI-Native App 发通知给主人）。

把分身的 `hasn.notifications.emit` 从 hasn-node 本地 hasn-mcp 迁到**云端 platform MCP 工具**
（不操作本地文件/数据 → 走云端，与 contact/message/plan 同范式）：分身经
`/api/v1/mcp/streamable` 直达云端，工具体直调云端权威 `notification_service.app_emit`
（in-process，**不再**经 daemon → `/api/v1/.../notifications/app-emit` HTTP relay）。

身份 owner 隔离：recipient 恒为本 Agent 的主人（`agent_context.owner_hasn_id`），绝不入请求体。
授权权威仍是云端 service：App 已发布 manifest 声明 `notifications.emit` + category 白名单 + 限频
（即便分身传任意已声明 App 的 id，最多只能限频地通知自己的主人，blast radius 极小）。

- 工具名 + input_schema 与原 hasn-mcp `NotificationEmitTool` **逐字段 1:1**（分身引用不变）。
- 与本地工具一致：**不声明 capability scope**（manifest 声明是服务端真权威）；三态闸门由
  `server.call_tool` 统一判定（出厂 Allow），工具体不二次校验。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.app.mcp.tools.base import BaseTool, require_owner_hasn_id
from backend.app.notification.service.notification_service import notification_service
from backend.database.db import async_db_session

if TYPE_CHECKING:
    from backend.app.mcp.auth import AgentContext


class NotificationEmitTool(BaseTool):
    """`hasn.notifications.emit`：以 App 身份给主人发通知（cloud-hosted，统一通知 §7）。"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def name(self) -> str:
        return 'hasn.notifications.emit'

    @property
    def namespace(self) -> str:
        return 'hasn.notifications'

    @property
    def risk_level(self) -> str:
        return 'low'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return (
            '以 App 身份给主人发通知（统一通知 §7）：落主人通知中心，并按 App manifest 的 '
            'card_message 开关 + 主人偏好投递卡片到该 App 的服务号会话。category 须在 App 白名单内，'
            '受云端限频。recipient 恒为本 Agent 主人。'
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'app_id': {
                    'type': 'string',
                    'description': '发通知的 App id（须已发布且其 manifest 声明 notifications.emit）',
                },
                'category': {
                    'type': 'string',
                    'enum': ['app', 'commerce', 'reminder', 'system'],
                    'description': '通知粗类（须在该 App manifest 白名单内，如 app/commerce/reminder）',
                },
                'type': {'type': 'string', 'description': '通知细类（自定义字符串，如 post_featured）'},
                'title': {'type': 'string', 'description': '通知标题（必填，<=200 字）'},
                'body': {'type': 'string', 'description': '通知正文（可选）'},
                'payload': {
                    'type': 'object',
                    'description': '{actor,target,preview,link,actions}（可选；link 决定卡片「查看」动作）',
                },
                'priority': {
                    'type': 'string',
                    'enum': ['critical', 'high', 'normal', 'low'],
                    'description': '优先级（可选，默认 normal）',
                },
                'card': {
                    'type': 'boolean',
                    'description': '是否请求卡片消息承载（默认 true；受 manifest card_message + 主人偏好收敛）',
                },
            },
            'required': ['app_id', 'category', 'type', 'title'],
        }

    @property
    def required_scopes(self) -> list[str]:
        # 与本地 hasn-mcp 工具 1:1：不声明独立 capability scope（manifest 声明是服务端真权威）。
        return []

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        """整形入参 → 云端 notification_service.app_emit（in-process，自动提交）。

        维度① 能力授权由 server.call_tool 三态 mode 统一判定（D3），工具内不二次校验；
        真权威是 service 内的 manifest 声明校验 + category 白名单 + 限频。
        """

        def _req(key: str) -> str:
            value = arguments.get(key)
            if not isinstance(value, str) or not value.strip():
                raise RuntimeError(f"notifications.emit: '{key}' 必填")
            return value.strip()

        app_id = _req('app_id')
        category = _req('category')
        notif_type = _req('type')
        title = _req('title')

        body = arguments.get('body')
        payload = arguments.get('payload') if isinstance(arguments.get('payload'), dict) else None
        priority = arguments.get('priority')
        card = arguments.get('card')

        # 写类 → .begin() 自动提交（service 只 flush 不 commit）。
        async with async_db_session.begin() as db:
            notification_id = await notification_service.app_emit(
                db,
                app_id=app_id,
                owner_hasn_id=require_owner_hasn_id(agent_context),
                category=category,
                type=notif_type,
                title=title,
                body=body if isinstance(body, str) else None,
                payload=payload,
                priority=priority if isinstance(priority, str) else None,
                want_card=card if isinstance(card, bool) else True,
            )
        return {'notification_id': notification_id}


NOTIFICATION_TOOLS: list[BaseTool] = [NotificationEmitTool()]
