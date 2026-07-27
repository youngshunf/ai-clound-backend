from collections.abc import Sequence
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_conversations import hasn_conversations_dao
from backend.app.hasn.model import HasnConversations
from backend.app.hasn.schema.hasn_conversations import (
    CreateHasnConversationsParam,
    DeleteHasnConversationsParam,
    UpdateHasnConversationsParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnConversationsService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnConversations:
        """
        获取HASN 会话

        :param db: 数据库会话
        :param pk: HASN 会话 ID
        :return:
        """
        hasn_conversations = await hasn_conversations_dao.get(db, pk)
        if not hasn_conversations:
            raise errors.NotFoundError(msg='HASN 会话不存在')
        return hasn_conversations

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取HASN 会话列表

        :param db: 数据库会话
        :return:
        """
        hasn_conversations_select = await hasn_conversations_dao.get_select()
        return await paging_data(db, hasn_conversations_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnConversations]:
        """
        获取所有HASN 会话

        :param db: 数据库会话
        :return:
        """
        hasn_conversationss = await hasn_conversations_dao.get_all(db)
        return hasn_conversationss

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnConversationsParam) -> None:
        """
        创建HASN 会话

        :param db: 数据库会话
        :param obj: 创建HASN 会话参数
        :return:
        """
        await hasn_conversations_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnConversationsParam) -> int:
        """
        更新HASN 会话

        :param db: 数据库会话
        :param pk: HASN 会话 ID
        :param obj: 更新HASN 会话参数
        :return:
        """
        count = await hasn_conversations_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnConversationsParam) -> int:
        """
        删除HASN 会话

        :param db: 数据库会话
        :param obj: HASN 会话 ID 列表
        :return:
        """
        count = await hasn_conversations_dao.delete(db, obj.pks)
        return count

    @staticmethod
    async def ensure_conversation(
        *,
        db: AsyncSession,
        caller_hasn_id: str,
        peer_hasn_id: str,
        relation_type: str = 'social',
    ) -> HasnConversations:
        """
        确保会话存在（如果不存在则创建）

        用于 1:1 会话的幂等创建。**单一去重收口**：按排序后参与者对建
        `uq_hasn_conversations_direct` 唯一会话，配套事务级 advisory lock
        避免并发重复创建，关系类型仅作为新建初值写入，不参与去重键。

        :param db: 数据库会话
        :param caller_hasn_id: 调用者的 HASN ID
        :param peer_hasn_id: 对方的 HASN ID
        :param relation_type: 关系类型，默认 'social'（仅新建初值，不参与去重键）
        :return: 会话对象
        """
        # 确定参与者类型（h_ 开头是 human，a_ 开头是 agent）
        caller_type = _participant_type(caller_hasn_id)
        peer_type = _participant_type(peer_hasn_id)

        return await _get_or_create_direct_conversation(
            db,
            caller_hasn_id,
            caller_type,
            peer_hasn_id,
            peer_type,
            relation_type=relation_type,
        )


def _participant_type(hasn_id: str) -> str:
    """按 has_id 前缀推断会话参与者类型。"""
    if hasn_id.startswith('h_'):
        return 'human'
    if hasn_id.startswith('a_'):
        return 'agent'
    raise errors.RequestError(msg=f'无效的 HASN ID 格式: {hasn_id}')


async def _get_or_create_direct_conversation(
    db: AsyncSession,
    participant_a_id: str,
    participant_a_type: str,
    participant_b_id: str,
    participant_b_type: str,
    relation_type: str = 'social',
) -> HasnConversations:
    """获取或创建单聊会话（同 message_router 直连实现，保持并发幂等不变量）。"""
    if participant_a_id > participant_b_id:
        participant_a_id, participant_b_id = participant_b_id, participant_a_id
        participant_a_type, participant_b_type = participant_b_type, participant_a_type

    await db.execute(
        text('SELECT pg_advisory_xact_lock(hashtext(:pair_key))'),
        {'pair_key': f'hasn_conv_direct:{participant_a_id}:{participant_b_id}'},
    )

    result = await db.execute(
        select(HasnConversations)
        .where(
            HasnConversations.type == 'direct',
            HasnConversations.participant_a_id == participant_a_id,
            HasnConversations.participant_b_id == participant_b_id,
        )
        .order_by(HasnConversations.created_time.asc())
    )
    conv = result.scalars().first()
    if conv:
        return conv

    conv = HasnConversations(
        type='direct',
        relation_type=relation_type,
        participant_a_id=participant_a_id,
        participant_b_id=participant_b_id,
        participant_a_type=participant_a_type,
        participant_b_type=participant_b_type,
        agent_policy='free',
        join_policy='',
        max_members=2,
        allow_invite=False,
        mute_all=False,
        member_count=2,
        message_count=0,
        status='active',
    )
    db.add(conv)
    await db.flush()
    return conv


hasn_conversations_service: HasnConversationsService = HasnConversationsService()
