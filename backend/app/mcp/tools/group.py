"""平台工具 · group 域

- `hasn.group.join`：分身代主人向某群申请加入（doc22 身份名片分享 §6.5）。
  底层尊重群加入策略——`open` 自由加入群直接入群；`invite_only`/`approval` 邀请制则
  如实回「需审批/邀请」（零 fake：不伪造已提交申请）。
- `hasn.group.messages`：分身按本群 ID 拉取群聊历史（倒序，含发送人/时间/文本/附件）。
  鉴权维度换「群成员资格」（不是 owner_id）、查询维度换 conversation_id（doc12 切片1）。

入群主体是**分身本身**（以 `agent_hasn_id` 落名册），这样分身入群后才能在群内用
hasn.message.send 发言、参与群派发。「代主人」指授权来源是主人（分享者），不是让主人入群。

写类走 HasnGroupService.join_group 单一实现。零 mock。
"""

import logging

from typing import Any

from sqlalchemy import or_, select

from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_group_members import HasnGroupMembers
from backend.app.hasn.service import message_router
from backend.app.hasn.service.agent_message_read_service import agent_message_read_service
from backend.app.hasn.service.hasn_group_service import hasn_group_service
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.base import BaseTool
from backend.common.exception import errors
from backend.database.db import async_db_session

logger = logging.getLogger(__name__)


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


class GroupMessageListTool(BaseTool):
    """分身按本群 ID 拉取群聊历史（倒序，含发送人/时间/文本/附件）。

    换维度（doc12 切片1·决策①/⑤/⑥）：群消息在云端 hasn_messages 只单条存储、不为成员落
    owner_copy 副本行，owner 作用域的 hasn.message.list/conversation.list 看不到群历史。本工具
    先按**群成员资格**前置鉴权（主人本人或其名下任一分身在群内即放行），再按 **conversation_id**
    直接拉全群历史。只读、不动写热路径；入群前的历史也全量可读。
    """

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def name(self) -> str:
        return 'hasn.group.messages'

    @property
    def namespace(self) -> str:
        return 'hasn.group'

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def risk_level(self) -> str:
        return 'low'

    @property
    def description(self) -> str:
        return (
            '拉取本群聊天历史（group_id 为群公开 ID g:NNNNNN，取自画册注入的「本群ID」或群名片），'
            '**按时间倒序=最新在前**，含发送人/时间/文本/图片等附件，入群前的历史也可读。'
            '用于「查一下群里之前聊过什么」；用 cursor+limit 向更早翻页（cursor 传上一次返回的 next_cursor）。'
            '仅群成员可读（你或你的主人在群内），非成员会被拒绝。'
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            'type': 'object',
            'properties': {
                'group_id': {
                    'type': 'string',
                    'description': '群公开 ID（g:NNNNNN，取自画册注入的「本群ID」或群名片）',
                },
                'limit': {
                    'type': 'integer',
                    'description': '返回数量（默认 20，最大 100）',
                    'minimum': 1,
                    'maximum': 100,
                },
                'cursor': {
                    'type': 'string',
                    'description': '翻页游标：传上一次返回的 next_cursor 拉更早的一页；不传=从最新开始。',
                },
            },
            'required': ['group_id'],
        }

    @property
    def required_scopes(self) -> list[str]:
        return ['message:read']

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> dict[str, Any]:
        # 维度① 能力授权由 server.call_tool 三态 mode 统一判定（D3），工具内不二次校验；
        # 这里只做「群成员资格」这一读权限的业务前置校验。
        group_id = str(arguments.get('group_id') or '').strip()
        if not group_id:
            return {'ok': False, 'error': 'group_id 不能为空（群公开 ID g:NNNNNN）'}
        async with async_db_session() as db:
            # 解析群会话（g:NNNNNN → conversation_id），复用 message_router 现成实现，不把 uuid 暴露给分身。
            group = await message_router.get_group_conversation(db, group_id)
            if group is None:
                return {'ok': False, 'error': f'群不存在或已解散：{group_id}'}
            # 群成员资格鉴权：主人本人或其名下任一分身在群内即可读。
            # ⚠️ 读不受全员禁言影响，故不用 check_group_send_permission（那是发送门，mute_all 会误拦读）。
            owner_hasn_id = agent_context.owner_hasn_id or ''
            member_id = (
                await db.execute(
                    select(HasnGroupMembers.member_id)
                    .where(
                        HasnGroupMembers.conversation_id == group.id,
                        or_(
                            HasnGroupMembers.member_id == agent_context.agent_hasn_id,
                            HasnGroupMembers.member_id == owner_hasn_id,
                            HasnGroupMembers.member_id.in_(
                                select(HasnAgents.hasn_id).where(HasnAgents.owner_id == owner_hasn_id)
                            ),
                        ),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if member_id is None:
                # 非成员读群历史=权限不足（预期客户端侧状况，非服务端故障）→ 记 warn 不记 error；不静默返空。
                logger.warning(
                    '拒绝非成员读取群历史 group_id=%s agent=%s owner=%s',
                    group_id,
                    agent_context.agent_hasn_id,
                    owner_hasn_id,
                )
                return {'ok': False, 'error': '你或你的主人不是该群成员，无法查看群聊历史'}
            result = await agent_message_read_service.list_group_messages(
                db,
                str(group.id),
                limit=arguments.get('limit', 20),
                cursor=arguments.get('cursor'),
            )
            return {'ok': True, **result}
