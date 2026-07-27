from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_community.model import ImCommandOutbox
from backend.app.hasn_community.schema.im_command_outbox import CreateImCommandOutboxParam, UpdateImCommandOutboxParam


class CRUDImCommandOutbox(CRUDPlus[ImCommandOutbox]):
    async def get(self, db: AsyncSession, pk: int) -> ImCommandOutbox | None:
        """
        获取社区资源写入触发主人知情卡的事务命令队列

        :param db: 数据库会话
        :param pk: 社区资源写入触发主人知情卡的事务命令队列 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取社区资源写入触发主人知情卡的事务命令队列列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[ImCommandOutbox]:
        """
        获取所有社区资源写入触发主人知情卡的事务命令队列

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateImCommandOutboxParam) -> None:
        """
        创建社区资源写入触发主人知情卡的事务命令队列

        :param db: 数据库会话
        :param obj: 创建社区资源写入触发主人知情卡的事务命令队列参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateImCommandOutboxParam) -> int:
        """
        更新社区资源写入触发主人知情卡的事务命令队列

        :param db: 数据库会话
        :param pk: 社区资源写入触发主人知情卡的事务命令队列 ID
        :param obj: 更新 社区资源写入触发主人知情卡的事务命令队列参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除社区资源写入触发主人知情卡的事务命令队列

        :param db: 数据库会话
        :param pks: 社区资源写入触发主人知情卡的事务命令队列 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


im_command_outbox_dao: CRUDImCommandOutbox = CRUDImCommandOutbox(ImCommandOutbox)
