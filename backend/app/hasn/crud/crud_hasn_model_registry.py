from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnModelRegistry
from backend.app.hasn.schema.hasn_model_registry import CreateHasnModelRegistryParam, UpdateHasnModelRegistryParam


class CRUDHasnModelRegistry(CRUDPlus[HasnModelRegistry]):
    async def get(self, db: AsyncSession, pk: int) -> HasnModelRegistry | None:
        """
        获取模型注册表（new-api 供事实、云端补语义、一处维护全平台下发）

        :param db: 数据库会话
        :param pk: 模型注册表（new-api 供事实、云端补语义、一处维护全平台下发） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取模型注册表（new-api 供事实、云端补语义、一处维护全平台下发）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnModelRegistry]:
        """
        获取所有模型注册表（new-api 供事实、云端补语义、一处维护全平台下发）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnModelRegistryParam) -> None:
        """
        创建模型注册表（new-api 供事实、云端补语义、一处维护全平台下发）

        :param db: 数据库会话
        :param obj: 创建模型注册表（new-api 供事实、云端补语义、一处维护全平台下发）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnModelRegistryParam) -> int:
        """
        更新模型注册表（new-api 供事实、云端补语义、一处维护全平台下发）

        :param db: 数据库会话
        :param pk: 模型注册表（new-api 供事实、云端补语义、一处维护全平台下发） ID
        :param obj: 更新 模型注册表（new-api 供事实、云端补语义、一处维护全平台下发）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除模型注册表（new-api 供事实、云端补语义、一处维护全平台下发）

        :param db: 数据库会话
        :param pks: 模型注册表（new-api 供事实、云端补语义、一处维护全平台下发） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_model_registry_dao: CRUDHasnModelRegistry = CRUDHasnModelRegistry(HasnModelRegistry)
