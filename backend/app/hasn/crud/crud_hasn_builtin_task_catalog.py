from typing import Sequence

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnBuiltinTaskCatalog
from backend.app.hasn.schema.hasn_builtin_task_catalog import CreateHasnBuiltinTaskCatalogParam, UpdateHasnBuiltinTaskCatalogParam


class CRUDHasnBuiltinTaskCatalog(CRUDPlus[HasnBuiltinTaskCatalog]):
    @staticmethod
    async def list_enabled(db: AsyncSession) -> Sequence[HasnBuiltinTaskCatalog]:
        """启用中的内置任务目录（按 builtin_key 稳定排序，供 daemon 拉取播种）。"""
        result = await db.execute(
            select(HasnBuiltinTaskCatalog)
            .where(HasnBuiltinTaskCatalog.enabled.is_(True))
            .order_by(HasnBuiltinTaskCatalog.builtin_key.asc())
        )
        return result.scalars().all()

    async def get(self, db: AsyncSession, pk: int) -> HasnBuiltinTaskCatalog | None:
        """
        获取HASN 官方内置任务目录（云端权威）

        :param db: 数据库会话
        :param pk: HASN 官方内置任务目录（云端权威） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取HASN 官方内置任务目录（云端权威）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnBuiltinTaskCatalog]:
        """
        获取所有HASN 官方内置任务目录（云端权威）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnBuiltinTaskCatalogParam) -> None:
        """
        创建HASN 官方内置任务目录（云端权威）

        :param db: 数据库会话
        :param obj: 创建HASN 官方内置任务目录（云端权威）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnBuiltinTaskCatalogParam) -> int:
        """
        更新HASN 官方内置任务目录（云端权威）

        :param db: 数据库会话
        :param pk: HASN 官方内置任务目录（云端权威） ID
        :param obj: 更新 HASN 官方内置任务目录（云端权威）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除HASN 官方内置任务目录（云端权威）

        :param db: 数据库会话
        :param pks: HASN 官方内置任务目录（云端权威） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_builtin_task_catalog_dao: CRUDHasnBuiltinTaskCatalog = CRUDHasnBuiltinTaskCatalog(HasnBuiltinTaskCatalog)
