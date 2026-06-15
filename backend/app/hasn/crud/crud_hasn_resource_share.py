from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnResourceShare
from backend.app.hasn.schema.hasn_resource_share import CreateHasnResourceShareParam, UpdateHasnResourceShareParam


class CRUDHasnResourceShare(CRUDPlus[HasnResourceShare]):
    async def get(self, db: AsyncSession, pk: int) -> HasnResourceShare | None:
        """
        获取通用产物共享表（平台级显式协作授权）

        :param db: 数据库会话
        :param pk: 通用产物共享表（平台级显式协作授权） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取通用产物共享表（平台级显式协作授权）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnResourceShare]:
        """
        获取所有通用产物共享表（平台级显式协作授权）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnResourceShareParam) -> None:
        """
        创建通用产物共享表（平台级显式协作授权）

        :param db: 数据库会话
        :param obj: 创建通用产物共享表（平台级显式协作授权）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnResourceShareParam) -> int:
        """
        更新通用产物共享表（平台级显式协作授权）

        :param db: 数据库会话
        :param pk: 通用产物共享表（平台级显式协作授权） ID
        :param obj: 更新 通用产物共享表（平台级显式协作授权）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除通用产物共享表（平台级显式协作授权）

        :param db: 数据库会话
        :param pks: 通用产物共享表（平台级显式协作授权） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_resource_share_dao: CRUDHasnResourceShare = CRUDHasnResourceShare(HasnResourceShare)
