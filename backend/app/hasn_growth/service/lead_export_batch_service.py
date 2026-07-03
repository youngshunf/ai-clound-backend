from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.crud.crud_lead_export_batch import lead_export_batch_dao
from backend.app.hasn_growth.model import LeadExportBatch
from backend.app.hasn_growth.schema.lead_export_batch import (
    CreateLeadExportBatchParam,
    DeleteLeadExportBatchParam,
    UpdateLeadExportBatchParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class LeadExportBatchService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> LeadExportBatch:
        """
        获取Lead CSV export batch

        :param db: 数据库会话
        :param pk: Lead CSV export batch ID
        :return:
        """
        lead_export_batch = await lead_export_batch_dao.get(db, pk)
        if not lead_export_batch:
            raise errors.NotFoundError(msg='Lead CSV export batch不存在')
        return lead_export_batch

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取Lead CSV export batch列表

        :param db: 数据库会话
        :return:
        """
        lead_export_batch_select = await lead_export_batch_dao.get_select()
        return await paging_data(db, lead_export_batch_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[LeadExportBatch]:
        """
        获取所有Lead CSV export batch

        :param db: 数据库会话
        :return:
        """
        lead_export_batchs = await lead_export_batch_dao.get_all(db)
        return lead_export_batchs

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateLeadExportBatchParam) -> None:
        """
        创建Lead CSV export batch

        :param db: 数据库会话
        :param obj: 创建Lead CSV export batch参数
        :return:
        """
        await lead_export_batch_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateLeadExportBatchParam) -> int:
        """
        更新Lead CSV export batch

        :param db: 数据库会话
        :param pk: Lead CSV export batch ID
        :param obj: 更新Lead CSV export batch参数
        :return:
        """
        count = await lead_export_batch_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteLeadExportBatchParam) -> int:
        """
        删除Lead CSV export batch

        :param db: 数据库会话
        :param obj: Lead CSV export batch ID 列表
        :return:
        """
        count = await lead_export_batch_dao.delete(db, obj.pks)
        return count


lead_export_batch_service: LeadExportBatchService = LeadExportBatchService()
