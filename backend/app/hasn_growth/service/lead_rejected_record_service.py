from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.crud.crud_lead_rejected_record import lead_rejected_record_dao
from backend.app.hasn_growth.model import LeadRejectedRecord
from backend.app.hasn_growth.schema.lead_rejected_record import CreateLeadRejectedRecordParam, DeleteLeadRejectedRecordParam, UpdateLeadRejectedRecordParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class LeadRejectedRecordService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> LeadRejectedRecord:
        """
        获取Rejected, invalid, duplicate, or failed lead record

        :param db: 数据库会话
        :param pk: Rejected, invalid, duplicate, or failed lead record ID
        :return:
        """
        lead_rejected_record = await lead_rejected_record_dao.get(db, pk)
        if not lead_rejected_record:
            raise errors.NotFoundError(msg='Rejected, invalid, duplicate, or failed lead record不存在')
        return lead_rejected_record

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取Rejected, invalid, duplicate, or failed lead record列表

        :param db: 数据库会话
        :return:
        """
        lead_rejected_record_select = await lead_rejected_record_dao.get_select()
        return await paging_data(db, lead_rejected_record_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[LeadRejectedRecord]:
        """
        获取所有Rejected, invalid, duplicate, or failed lead record

        :param db: 数据库会话
        :return:
        """
        lead_rejected_records = await lead_rejected_record_dao.get_all(db)
        return lead_rejected_records

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateLeadRejectedRecordParam) -> None:
        """
        创建Rejected, invalid, duplicate, or failed lead record

        :param db: 数据库会话
        :param obj: 创建Rejected, invalid, duplicate, or failed lead record参数
        :return:
        """
        await lead_rejected_record_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateLeadRejectedRecordParam) -> int:
        """
        更新Rejected, invalid, duplicate, or failed lead record

        :param db: 数据库会话
        :param pk: Rejected, invalid, duplicate, or failed lead record ID
        :param obj: 更新Rejected, invalid, duplicate, or failed lead record参数
        :return:
        """
        count = await lead_rejected_record_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteLeadRejectedRecordParam) -> int:
        """
        删除Rejected, invalid, duplicate, or failed lead record

        :param db: 数据库会话
        :param obj: Rejected, invalid, duplicate, or failed lead record ID 列表
        :return:
        """
        count = await lead_rejected_record_dao.delete(db, obj.pks)
        return count


lead_rejected_record_service: LeadRejectedRecordService = LeadRejectedRecordService()
