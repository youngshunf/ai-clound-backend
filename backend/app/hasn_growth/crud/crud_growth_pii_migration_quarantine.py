from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_growth.model import GrowthPiiMigrationQuarantine
from backend.app.hasn_growth.schema.growth_pii_migration_quarantine import (
    CreateGrowthPiiMigrationQuarantineParam,
    UpdateGrowthPiiMigrationQuarantineParam,
)


class CRUDGrowthPiiMigrationQuarantine(CRUDPlus[GrowthPiiMigrationQuarantine]):
    async def get(self, db: AsyncSession, pk: int) -> GrowthPiiMigrationQuarantine | None:
        """
        获取无法证明授权主体或合法依据的 PII 迁移隔离清单，不保存明文

        :param db: 数据库会话
        :param pk: 无法证明授权主体或合法依据的 PII 迁移隔离清单，不保存明文 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取无法证明授权主体或合法依据的 PII 迁移隔离清单，不保存明文列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[GrowthPiiMigrationQuarantine]:
        """
        获取所有无法证明授权主体或合法依据的 PII 迁移隔离清单，不保存明文

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateGrowthPiiMigrationQuarantineParam) -> None:
        """
        创建无法证明授权主体或合法依据的 PII 迁移隔离清单，不保存明文

        :param db: 数据库会话
        :param obj: 创建无法证明授权主体或合法依据的 PII 迁移隔离清单，不保存明文参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateGrowthPiiMigrationQuarantineParam) -> int:
        """
        更新无法证明授权主体或合法依据的 PII 迁移隔离清单，不保存明文

        :param db: 数据库会话
        :param pk: 无法证明授权主体或合法依据的 PII 迁移隔离清单，不保存明文 ID
        :param obj: 更新 无法证明授权主体或合法依据的 PII 迁移隔离清单，不保存明文参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除无法证明授权主体或合法依据的 PII 迁移隔离清单，不保存明文

        :param db: 数据库会话
        :param pks: 无法证明授权主体或合法依据的 PII 迁移隔离清单，不保存明文 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


growth_pii_migration_quarantine_dao: CRUDGrowthPiiMigrationQuarantine = CRUDGrowthPiiMigrationQuarantine(
    GrowthPiiMigrationQuarantine
)
