from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.crud.crud_lead_audit_log import lead_audit_log_dao
from backend.app.hasn_growth.model import LeadAuditLog
from backend.app.hasn_growth.schema.lead_audit_log import (
    CreateLeadAuditLogParam,
    DeleteLeadAuditLogParam,
    UpdateLeadAuditLogParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class LeadAuditLogService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> LeadAuditLog:
        """
        获取Lead automation PII and compliance audit log

        :param db: 数据库会话
        :param pk: Lead automation PII and compliance audit log ID
        :return:
        """
        lead_audit_log = await lead_audit_log_dao.get(db, pk)
        if not lead_audit_log:
            raise errors.NotFoundError(msg='Lead automation PII and compliance audit log不存在')
        return lead_audit_log

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取Lead automation PII and compliance audit log列表

        :param db: 数据库会话
        :return:
        """
        lead_audit_log_select = await lead_audit_log_dao.get_select()
        return await paging_data(db, lead_audit_log_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[LeadAuditLog]:
        """
        获取所有Lead automation PII and compliance audit log

        :param db: 数据库会话
        :return:
        """
        lead_audit_logs = await lead_audit_log_dao.get_all(db)
        return lead_audit_logs

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateLeadAuditLogParam) -> None:
        """
        创建Lead automation PII and compliance audit log

        :param db: 数据库会话
        :param obj: 创建Lead automation PII and compliance audit log参数
        :return:
        """
        await lead_audit_log_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateLeadAuditLogParam) -> int:
        """
        更新Lead automation PII and compliance audit log

        :param db: 数据库会话
        :param pk: Lead automation PII and compliance audit log ID
        :param obj: 更新Lead automation PII and compliance audit log参数
        :return:
        """
        count = await lead_audit_log_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteLeadAuditLogParam) -> int:
        """
        删除Lead automation PII and compliance audit log

        :param db: 数据库会话
        :param obj: Lead automation PII and compliance audit log ID 列表
        :return:
        """
        count = await lead_audit_log_dao.delete(db, obj.pks)
        return count


lead_audit_log_service: LeadAuditLogService = LeadAuditLogService()
