from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.crud.crud_lead_raw_record import lead_raw_record_dao
from backend.app.hasn_growth.model import LeadRawRecord
from backend.app.hasn_growth.schema.lead_raw_record import CreateLeadRawRecordParam, DeleteLeadRawRecordParam, UpdateLeadRawRecordParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class LeadRawRecordService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> LeadRawRecord:
        """
        获取Raw crawled lead page record

        :param db: 数据库会话
        :param pk: Raw crawled lead page record ID
        :return:
        """
        lead_raw_record = await lead_raw_record_dao.get(db, pk)
        if not lead_raw_record:
            raise errors.NotFoundError(msg='Raw crawled lead page record不存在')
        return lead_raw_record

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取Raw crawled lead page record列表

        :param db: 数据库会话
        :return:
        """
        lead_raw_record_select = await lead_raw_record_dao.get_select()
        return await paging_data(db, lead_raw_record_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[LeadRawRecord]:
        """
        获取所有Raw crawled lead page record

        :param db: 数据库会话
        :return:
        """
        lead_raw_records = await lead_raw_record_dao.get_all(db)
        return lead_raw_records

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateLeadRawRecordParam) -> None:
        """
        创建Raw crawled lead page record

        :param db: 数据库会话
        :param obj: 创建Raw crawled lead page record参数
        :return:
        """
        await lead_raw_record_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateLeadRawRecordParam) -> int:
        """
        更新Raw crawled lead page record

        :param db: 数据库会话
        :param pk: Raw crawled lead page record ID
        :param obj: 更新Raw crawled lead page record参数
        :return:
        """
        count = await lead_raw_record_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteLeadRawRecordParam) -> int:
        """
        删除Raw crawled lead page record

        :param db: 数据库会话
        :param obj: Raw crawled lead page record ID 列表
        :return:
        """
        count = await lead_raw_record_dao.delete(db, obj.pks)
        return count


lead_raw_record_service: LeadRawRecordService = LeadRawRecordService()
