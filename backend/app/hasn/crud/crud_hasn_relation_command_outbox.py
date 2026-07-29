from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnRelationCommandOutbox
from backend.app.hasn.schema.hasn_relation_command_outbox import CreateHasnRelationCommandOutboxParam, UpdateHasnRelationCommandOutboxParam


class CRUDHasnRelationCommandOutbox(CRUDPlus[HasnRelationCommandOutbox]):
    async def get(self, db: AsyncSession, pk: int) -> HasnRelationCommandOutbox | None:
        """
        获取身份事实投影为 IM 关系的可靠命令队列

        :param db: 数据库会话
        :param pk: 身份事实投影为 IM 关系的可靠命令队列 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取身份事实投影为 IM 关系的可靠命令队列列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnRelationCommandOutbox]:
        """
        获取所有身份事实投影为 IM 关系的可靠命令队列

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnRelationCommandOutboxParam) -> None:
        """
        创建身份事实投影为 IM 关系的可靠命令队列

        :param db: 数据库会话
        :param obj: 创建身份事实投影为 IM 关系的可靠命令队列参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnRelationCommandOutboxParam) -> int:
        """
        更新身份事实投影为 IM 关系的可靠命令队列

        :param db: 数据库会话
        :param pk: 身份事实投影为 IM 关系的可靠命令队列 ID
        :param obj: 更新 身份事实投影为 IM 关系的可靠命令队列参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除身份事实投影为 IM 关系的可靠命令队列

        :param db: 数据库会话
        :param pks: 身份事实投影为 IM 关系的可靠命令队列 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_relation_command_outbox_dao: CRUDHasnRelationCommandOutbox = CRUDHasnRelationCommandOutbox(HasnRelationCommandOutbox)
