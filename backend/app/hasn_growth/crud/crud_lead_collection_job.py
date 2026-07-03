from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_growth.model import LeadCollectionJob
from backend.app.hasn_growth.schema.lead_collection_job import (
    CreateLeadCollectionJobParam,
    UpdateLeadCollectionJobParam,
)


class CRUDLeadCollectionJob(CRUDPlus[LeadCollectionJob]):
    async def get(self, db: AsyncSession, pk: int) -> LeadCollectionJob | None:
        """
        获取AI lead automation collection job

        :param db: 数据库会话
        :param pk: AI lead automation collection job ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取AI lead automation collection job列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[LeadCollectionJob]:
        """
        获取所有AI lead automation collection job

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateLeadCollectionJobParam) -> None:
        """
        创建AI lead automation collection job

        :param db: 数据库会话
        :param obj: 创建AI lead automation collection job参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateLeadCollectionJobParam) -> int:
        """
        更新AI lead automation collection job

        :param db: 数据库会话
        :param pk: AI lead automation collection job ID
        :param obj: 更新 AI lead automation collection job参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除AI lead automation collection job

        :param db: 数据库会话
        :param pks: AI lead automation collection job ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


lead_collection_job_dao: CRUDLeadCollectionJob = CRUDLeadCollectionJob(LeadCollectionJob)
