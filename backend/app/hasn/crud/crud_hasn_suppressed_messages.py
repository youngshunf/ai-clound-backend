from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnSuppressedMessages
from backend.app.hasn.schema.hasn_suppressed_messages import (
    CreateHasnSuppressedMessagesParam,
    UpdateHasnSuppressedMessagesParam,
)


class CRUDHasnSuppressedMessages(CRUDPlus[HasnSuppressedMessages]):
    async def get(self, db: AsyncSession, pk: int) -> HasnSuppressedMessages | None:
        """
        获取HASN Runtime 抑制箱 / owner 可拉取消息

        :param db: 数据库会话
        :param pk: HASN Runtime 抑制箱 / owner 可拉取消息 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取HASN Runtime 抑制箱 / owner 可拉取消息列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnSuppressedMessages]:
        """
        获取所有HASN Runtime 抑制箱 / owner 可拉取消息

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnSuppressedMessagesParam) -> None:
        """
        创建HASN Runtime 抑制箱 / owner 可拉取消息

        :param db: 数据库会话
        :param obj: 创建HASN Runtime 抑制箱 / owner 可拉取消息参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnSuppressedMessagesParam) -> int:
        """
        更新HASN Runtime 抑制箱 / owner 可拉取消息

        :param db: 数据库会话
        :param pk: HASN Runtime 抑制箱 / owner 可拉取消息 ID
        :param obj: 更新 HASN Runtime 抑制箱 / owner 可拉取消息参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除HASN Runtime 抑制箱 / owner 可拉取消息

        :param db: 数据库会话
        :param pks: HASN Runtime 抑制箱 / owner 可拉取消息 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_suppressed_messages_dao: CRUDHasnSuppressedMessages = CRUDHasnSuppressedMessages(HasnSuppressedMessages)
