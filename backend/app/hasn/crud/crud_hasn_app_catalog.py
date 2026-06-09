from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnAppCatalog
from backend.app.hasn.schema.hasn_app_catalog import CreateHasnAppCatalogParam, UpdateHasnAppCatalogParam


class CRUDHasnAppCatalog(CRUDPlus[HasnAppCatalog]):
    async def get(self, db: AsyncSession, pk: int) -> HasnAppCatalog | None:
        """
        获取AI-Native 应用目录（云端权威）

        :param db: 数据库会话
        :param pk: AI-Native 应用目录（云端权威） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取AI-Native 应用目录（云端权威）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnAppCatalog]:
        """
        获取所有AI-Native 应用目录（云端权威）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnAppCatalogParam) -> None:
        """
        创建AI-Native 应用目录（云端权威）

        :param db: 数据库会话
        :param obj: 创建AI-Native 应用目录（云端权威）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnAppCatalogParam) -> int:
        """
        更新AI-Native 应用目录（云端权威）

        :param db: 数据库会话
        :param pk: AI-Native 应用目录（云端权威） ID
        :param obj: 更新 AI-Native 应用目录（云端权威）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除AI-Native 应用目录（云端权威）

        :param db: 数据库会话
        :param pks: AI-Native 应用目录（云端权威） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_app_catalog_dao: CRUDHasnAppCatalog = CRUDHasnAppCatalog(HasnAppCatalog)
