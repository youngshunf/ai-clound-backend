from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.crud.crud_outreach_message import outreach_message_dao
from backend.app.hasn_growth.model import OutreachMessage
from backend.app.hasn_growth.schema.outreach_message import CreateOutreachMessageParam, DeleteOutreachMessageParam, UpdateOutreachMessageParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class OutreachMessageService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> OutreachMessage:
        """
        获取获客触达消息（出/入双向，审批状态机核心表）

        :param db: 数据库会话
        :param pk: 获客触达消息（出/入双向，审批状态机核心表） ID
        :return:
        """
        outreach_message = await outreach_message_dao.get(db, pk)
        if not outreach_message:
            raise errors.NotFoundError(msg='获客触达消息（出/入双向，审批状态机核心表）不存在')
        return outreach_message

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取获客触达消息（出/入双向，审批状态机核心表）列表

        :param db: 数据库会话
        :return:
        """
        outreach_message_select = await outreach_message_dao.get_select()
        return await paging_data(db, outreach_message_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[OutreachMessage]:
        """
        获取所有获客触达消息（出/入双向，审批状态机核心表）

        :param db: 数据库会话
        :return:
        """
        outreach_message_list = await outreach_message_dao.get_all(db)
        return outreach_message_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateOutreachMessageParam) -> None:
        """
        创建获客触达消息（出/入双向，审批状态机核心表）

        :param db: 数据库会话
        :param obj: 创建获客触达消息（出/入双向，审批状态机核心表）参数
        :return:
        """
        await outreach_message_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateOutreachMessageParam) -> int:
        """
        更新获客触达消息（出/入双向，审批状态机核心表）

        :param db: 数据库会话
        :param pk: 获客触达消息（出/入双向，审批状态机核心表） ID
        :param obj: 更新获客触达消息（出/入双向，审批状态机核心表）参数
        :return:
        """
        count = await outreach_message_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteOutreachMessageParam) -> int:
        """
        删除获客触达消息（出/入双向，审批状态机核心表）

        :param db: 数据库会话
        :param obj: 获客触达消息（出/入双向，审批状态机核心表） ID 列表
        :return:
        """
        count = await outreach_message_dao.delete(db, obj.pks)
        return count


outreach_message_service: OutreachMessageService = OutreachMessageService()
