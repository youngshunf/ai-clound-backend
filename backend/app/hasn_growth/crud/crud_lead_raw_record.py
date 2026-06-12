from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_growth.model import LeadRawRecord
from backend.app.hasn_growth.schema.lead_raw_record import CreateLeadRawRecordParam, UpdateLeadRawRecordParam


class CRUDLeadRawRecord(CRUDPlus[LeadRawRecord]):
    async def get(self, db: AsyncSession, pk: int) -> LeadRawRecord | None:
        """
        获取Raw crawled lead page record

        :param db: 数据库会话
        :param pk: Raw crawled lead page record ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取Raw crawled lead page record列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[LeadRawRecord]:
        """
        获取所有Raw crawled lead page record

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateLeadRawRecordParam) -> None:
        """
        创建Raw crawled lead page record

        :param db: 数据库会话
        :param obj: 创建Raw crawled lead page record参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateLeadRawRecordParam) -> int:
        """
        更新Raw crawled lead page record

        :param db: 数据库会话
        :param pk: Raw crawled lead page record ID
        :param obj: 更新 Raw crawled lead page record参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除Raw crawled lead page record

        :param db: 数据库会话
        :param pks: Raw crawled lead page record ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


lead_raw_record_dao: CRUDLeadRawRecord = CRUDLeadRawRecord(LeadRawRecord)
