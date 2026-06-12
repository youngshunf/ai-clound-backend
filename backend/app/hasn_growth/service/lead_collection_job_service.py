from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.crud.crud_lead_collection_job import lead_collection_job_dao
from backend.app.hasn_growth.model import LeadCollectionJob
from backend.app.hasn_growth.schema.lead_collection_job import CreateLeadCollectionJobParam, DeleteLeadCollectionJobParam, UpdateLeadCollectionJobParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class LeadCollectionJobService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> LeadCollectionJob:
        """
        获取AI lead automation collection job

        :param db: 数据库会话
        :param pk: AI lead automation collection job ID
        :return:
        """
        lead_collection_job = await lead_collection_job_dao.get(db, pk)
        if not lead_collection_job:
            raise errors.NotFoundError(msg='AI lead automation collection job不存在')
        return lead_collection_job

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取AI lead automation collection job列表

        :param db: 数据库会话
        :return:
        """
        lead_collection_job_select = await lead_collection_job_dao.get_select()
        return await paging_data(db, lead_collection_job_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[LeadCollectionJob]:
        """
        获取所有AI lead automation collection job

        :param db: 数据库会话
        :return:
        """
        lead_collection_jobs = await lead_collection_job_dao.get_all(db)
        return lead_collection_jobs

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateLeadCollectionJobParam) -> None:
        """
        创建AI lead automation collection job

        :param db: 数据库会话
        :param obj: 创建AI lead automation collection job参数
        :return:
        """
        await lead_collection_job_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateLeadCollectionJobParam) -> int:
        """
        更新AI lead automation collection job

        :param db: 数据库会话
        :param pk: AI lead automation collection job ID
        :param obj: 更新AI lead automation collection job参数
        :return:
        """
        count = await lead_collection_job_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteLeadCollectionJobParam) -> int:
        """
        删除AI lead automation collection job

        :param db: 数据库会话
        :param obj: AI lead automation collection job ID 列表
        :return:
        """
        count = await lead_collection_job_dao.delete(db, obj.pks)
        return count


lead_collection_job_service: LeadCollectionJobService = LeadCollectionJobService()
