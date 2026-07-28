from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_growth.model import GrowthProjectMigrationQuarantine
from backend.app.hasn_growth.schema.growth_project_migration_quarantine import (
    CreateGrowthProjectMigrationQuarantineParam,
    UpdateGrowthProjectMigrationQuarantineParam,
)


class CRUDGrowthProjectMigrationQuarantine(CRUDPlus[GrowthProjectMigrationQuarantine]):
    async def get(self, db: AsyncSession, pk: int) -> GrowthProjectMigrationQuarantine | None:
        """
        获取获客项目挂靠、状态迁移和归属边界异常的无敏感隔离清单

        :param db: 数据库会话
        :param pk: 获客项目挂靠、状态迁移和归属边界异常的无敏感隔离清单 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取获客项目挂靠、状态迁移和归属边界异常的无敏感隔离清单列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[GrowthProjectMigrationQuarantine]:
        """
        获取所有获客项目挂靠、状态迁移和归属边界异常的无敏感隔离清单

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateGrowthProjectMigrationQuarantineParam) -> None:
        """
        创建获客项目挂靠、状态迁移和归属边界异常的无敏感隔离清单

        :param db: 数据库会话
        :param obj: 创建获客项目挂靠、状态迁移和归属边界异常的无敏感隔离清单参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateGrowthProjectMigrationQuarantineParam) -> int:
        """
        更新获客项目挂靠、状态迁移和归属边界异常的无敏感隔离清单

        :param db: 数据库会话
        :param pk: 获客项目挂靠、状态迁移和归属边界异常的无敏感隔离清单 ID
        :param obj: 更新 获客项目挂靠、状态迁移和归属边界异常的无敏感隔离清单参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除获客项目挂靠、状态迁移和归属边界异常的无敏感隔离清单

        :param db: 数据库会话
        :param pks: 获客项目挂靠、状态迁移和归属边界异常的无敏感隔离清单 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


growth_project_migration_quarantine_dao: CRUDGrowthProjectMigrationQuarantine = (
    CRUDGrowthProjectMigrationQuarantine(GrowthProjectMigrationQuarantine)
)
