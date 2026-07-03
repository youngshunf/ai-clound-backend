from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_growth.model import OutreachMessage
from backend.app.hasn_growth.schema.outreach_message import CreateOutreachMessageParam, UpdateOutreachMessageParam


class CRUDOutreachMessage(CRUDPlus[OutreachMessage]):
    async def get(self, db: AsyncSession, pk: int) -> OutreachMessage | None:
        """
        获取获客触达消息（出/入双向，审批状态机核心表）

        :param db: 数据库会话
        :param pk: 获客触达消息（出/入双向，审批状态机核心表） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取获客触达消息（出/入双向，审批状态机核心表）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[OutreachMessage]:
        """
        获取所有获客触达消息（出/入双向，审批状态机核心表）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateOutreachMessageParam) -> None:
        """
        创建获客触达消息（出/入双向，审批状态机核心表）

        :param db: 数据库会话
        :param obj: 创建获客触达消息（出/入双向，审批状态机核心表）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateOutreachMessageParam) -> int:
        """
        更新获客触达消息（出/入双向，审批状态机核心表）

        :param db: 数据库会话
        :param pk: 获客触达消息（出/入双向，审批状态机核心表） ID
        :param obj: 更新 获客触达消息（出/入双向，审批状态机核心表）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除获客触达消息（出/入双向，审批状态机核心表）

        :param db: 数据库会话
        :param pks: 获客触达消息（出/入双向，审批状态机核心表） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


outreach_message_dao: CRUDOutreachMessage = CRUDOutreachMessage(OutreachMessage)
