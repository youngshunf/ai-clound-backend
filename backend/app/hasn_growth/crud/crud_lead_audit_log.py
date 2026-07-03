from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_growth.model import LeadAuditLog
from backend.app.hasn_growth.schema.lead_audit_log import CreateLeadAuditLogParam, UpdateLeadAuditLogParam


class CRUDLeadAuditLog(CRUDPlus[LeadAuditLog]):
    async def get(self, db: AsyncSession, pk: int) -> LeadAuditLog | None:
        """
        获取Lead automation PII and compliance audit log

        :param db: 数据库会话
        :param pk: Lead automation PII and compliance audit log ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取Lead automation PII and compliance audit log列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[LeadAuditLog]:
        """
        获取所有Lead automation PII and compliance audit log

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateLeadAuditLogParam) -> None:
        """
        创建Lead automation PII and compliance audit log

        :param db: 数据库会话
        :param obj: 创建Lead automation PII and compliance audit log参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateLeadAuditLogParam) -> int:
        """
        更新Lead automation PII and compliance audit log

        :param db: 数据库会话
        :param pk: Lead automation PII and compliance audit log ID
        :param obj: 更新 Lead automation PII and compliance audit log参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除Lead automation PII and compliance audit log

        :param db: 数据库会话
        :param pks: Lead automation PII and compliance audit log ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


lead_audit_log_dao: CRUDLeadAuditLog = CRUDLeadAuditLog(LeadAuditLog)
