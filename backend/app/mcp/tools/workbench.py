"""工作台 MCP 工具集（agent-scoped，供主脑调用）。

`hasn.workbench.briefing.publish`：主脑产出每日关注简报的**唯一**上行通道。入口**强校验**
BriefingDocument schema（设计 doc 04 §4），不合即返回校验错误让模型重试——绝不正则解析自由
文本拼简报（零 fake）。身份由认证决定：owner_id/agent_id 取自 Agent 凭证回填，不信任入参。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from backend.app.mcp.tools.base import BaseTool
from backend.common.log import log
from backend.database.db import async_db_session

if TYPE_CHECKING:
    from backend.app.mcp.auth import AgentContext


class PublishBriefingTool(BaseTool):
    """主脑发布每日关注简报（覆盖当日 period，写云端权威 hasn_workbench_briefing）。"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def namespace(self) -> str:
        return 'hasn.workbench'

    @property
    def name(self) -> str:
        return 'hasn.workbench.briefing.publish'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def risk_level(self) -> str:
        return 'low'

    @property
    def description(self) -> str:
        return (
            '发布今天的「每日关注简报」。你必须传入一份结构化 BriefingDocument（见 document schema），'
            '不要用自由文本——工作台只渲染这个结构。覆盖当日最新一份。'
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        action_schema = {
            'type': 'object',
            'properties': {
                'kind': {'type': 'string', 'enum': ['open_app', 'run_task', 'open_route', 'dismiss']},
                'label': {'type': 'string', 'description': '按钮文案'},
                'app_id': {'type': 'string', 'description': 'open_app：目标应用 id'},
                'deep_link': {'type': 'string', 'description': 'open_app：应用内深链'},
                'agent_id': {'type': 'string', 'description': 'run_task：执行分身（默认主脑）'},
                'prompt': {'type': 'string', 'description': 'run_task：派发提示词'},
                'skill_ids': {'type': 'array', 'items': {'type': 'string'}},
                'confirm': {'type': 'boolean', 'description': 'run_task：是否弹确认'},
                'route': {'type': 'string', 'description': 'open_route：客户端内部路由'},
            },
            'required': ['kind', 'label'],
        }
        focus_item_schema = {
            'type': 'object',
            'properties': {
                'item_id': {'type': 'string'},
                'category': {'type': 'string', 'enum': ['task', 'social', 'app', 'plan', 'risk']},
                'urgency': {'type': 'string', 'enum': ['high', 'medium', 'low']},
                'title': {'type': 'string'},
                'summary': {'type': 'string'},
                'source': {
                    'type': 'object',
                    'properties': {
                        'app_id': {'type': 'string'},
                        'ref': {'type': 'string'},
                        'deep_link': {'type': 'string'},
                    },
                },
                'evidence': {'type': 'array', 'items': {'type': 'string'}},
                'actions': {'type': 'array', 'items': action_schema},
            },
            'required': ['item_id', 'category', 'urgency', 'title'],
        }
        plan_item_schema = {
            'type': 'object',
            'properties': {
                'plan_id': {'type': 'string'},
                'title': {'type': 'string'},
                'horizon': {'type': 'string', 'enum': ['today', 'week']},
                'steps': {'type': 'array', 'items': {'type': 'string'}},
                'actions': {'type': 'array', 'items': action_schema},
            },
            'required': ['plan_id', 'title', 'horizon'],
        }
        return {
            'type': 'object',
            'properties': {
                'document': {
                    'type': 'object',
                    'description': 'BriefingDocument（owner_id/agent_id 由系统回填，无需填）',
                    'properties': {
                        'period': {'type': 'string', 'description': '覆盖周期 YYYY-MM-DD（缺省取当日）'},
                        'state': {'type': 'string', 'enum': ['generating', 'ready', 'failed'], 'default': 'ready'},
                        'summary': {'type': 'string', 'description': '一句话总览（Hero 副标题）'},
                        'focus_items': {'type': 'array', 'items': focus_item_schema},
                        'plans': {'type': 'array', 'items': plan_item_schema},
                    },
                    'required': ['summary', 'focus_items'],
                },
            },
            'required': ['document'],
        }

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        from backend.app.workbench.service.hasn_workbench_briefing_service import hasn_workbench_briefing_service

        document = arguments.get('document')
        if not isinstance(document, dict):
            return {'published': False, 'valid': False, 'reason': 'document 必填且须为对象'}

        async with async_db_session() as db:
            try:
                row = await hasn_workbench_briefing_service.publish(
                    db=db,
                    owner_hasn_id=agent_context.owner_hasn_id,
                    agent_hasn_id=agent_context.hasn_id,
                    document=document,
                )
            except ValidationError as exc:
                # schema 不合：返回结构化错误让模型重试（零 fake，不落库）。
                log.info(f'briefing publish schema invalid by {agent_context.hasn_id}: {exc.error_count()} errors')
                return {
                    'published': False,
                    'valid': False,
                    'reason': 'BriefingDocument 校验失败，请按 schema 修正后重试',
                    'errors': exc.errors(include_url=False)[:10],
                }
            # 会话关闭前抓出纯值（避免 detached ORM 实例访问报错）。
            result = {
                'published': True,
                'valid': True,
                'period': row.period,
                'state': row.state,
                'briefing_id': row.document_json.get('briefing_id'),
            }
            await db.commit()
        return result


WORKBENCH_TOOLS: list[type[BaseTool]] = [PublishBriefingTool]
