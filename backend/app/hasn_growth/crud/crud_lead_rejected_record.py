from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_growth.model import LeadRejectedRecord
from backend.app.hasn_growth.schema.lead_rejected_record import CreateLeadRejectedRecordParam, UpdateLeadRejectedRecordParam


class CRUDLeadRejectedRecord(CRUDPlus[LeadRejectedRecord]):
    async def get(self, db: AsyncSession, pk: int) -> LeadRejectedRecord | None:
        """
        获取Rejected, invalid, duplicate, or failed lead record

        :param db: 数据库会话
        :param pk: Rejected, invalid, duplicate, or failed lead record ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取Rejected, invalid, duplicate, or failed lead record列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[LeadRejectedRecord]:
        """
        获取所有Rejected, invalid, duplicate, or failed lead record

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateLeadRejectedRecordParam) -> None:
        """
        创建Rejected, invalid, duplicate, or failed lead record

        :param db: 数据库会话
        :param obj: 创建Rejected, invalid, duplicate, or failed lead record参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateLeadRejectedRecordParam) -> int:
        """
        更新Rejected, invalid, duplicate, or failed lead record

        :param db: 数据库会话
        :param pk: Rejected, invalid, duplicate, or failed lead record ID
        :param obj: 更新 Rejected, invalid, duplicate, or failed lead record参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除Rejected, invalid, duplicate, or failed lead record

        :param db: 数据库会话
        :param pks: Rejected, invalid, duplicate, or failed lead record ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


lead_rejected_record_dao: CRUDLeadRejectedRecord = CRUDLeadRejectedRecord(LeadRejectedRecord)
