"""平台工具 · group 域

- `hasn.group.join`：分身代主人向某群申请加入（doc22 身份名片分享 §6.5）。
  底层尊重群加入策略——`open` 自由加入群直接入群；`invite_only`/`approval` 邀请制则
  如实回「需审批/邀请」（零 fake：不伪造已提交申请）。

入群主体是**分身本身**（以 `agent_hasn_id` 落名册），这样分身入群后才能在群内用
hasn.message.send 发言、参与群派发。「代主人」指授权来源是主人（分享者），不是让主人入群。

写类走 HasnGroupService.join_group 单一实现。零 mock。
"""

from typing import Any

from backend.app.hasn.service.hasn_group_service import hasn_group_service
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.base import BaseTool
from backend.common.exception import errors
from backend.database.db import async_db_session


class GroupJoinTool(BaseTool):
    """分身代主人加入某群（尊重群加入策略）。"""

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def name(self) -> str:
        return 'hasn.group.join'

    @property
    def namespace(self) -> str:
        return 'hasn.group'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return (
            '代主人加入某群聊（group_id 为群公开 ID g:NNNNNN，可取自群名片）。'
            '尊重群加入策略：自由加入群直接入群，邀请制群则需群主/管理员邀请或审批后加入。'
            '入群后分身可在群内用 hasn.message.send 发言、参与群协作。'
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'group_id': {
                    'type': 'string',
                    'description': '群公开 ID（g:NNNNNN，取自群名片的 share_subject.group_id）',
                },
            },
            'required': ['group_id'],
        }

    @property
    def required_scopes(self) -> list[str]:
        return ['group:join']

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        # 维度① 能力授权由 server.call_tool 三态 mode 统一判定（D3），工具内不二次校验。
        group_id = str(arguments.get('group_id') or '').strip()
        if not group_id:
            return {'ok': False, 'error': 'group_id 不能为空（群公开 ID g:NNNNNN）'}
        async with async_db_session.begin() as db:
            try:
                # 入群主体是分身本身（agent_hasn_id），入群后可在群内发言/协作。
                result = await hasn_group_service.join_group(
                    db, applicant_hasn_id=agent_context.agent_hasn_id, group_id=group_id
                )
            except errors.NotFoundError as e:
                return {'ok': False, 'error': e.msg}
            return {'ok': True, **result}
