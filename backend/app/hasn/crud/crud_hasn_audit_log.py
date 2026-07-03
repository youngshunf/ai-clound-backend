from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnAuditLog
from backend.app.hasn.schema.hasn_audit_log import CreateHasnAuditLogParam, UpdateHasnAuditLogParam


class CRUDHasnAuditLog(CRUDPlus[HasnAuditLog]):
    async def get(self, db: AsyncSession, pk: int) -> HasnAuditLog | None:
        """
        获取HASN 审计日志

        :param db: 数据库会话
        :param pk: HASN 审计日志 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取HASN 审计日志列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnAuditLog]:
        """
        获取所有HASN 审计日志

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnAuditLogParam) -> None:
        """
        创建HASN 审计日志

        :param db: 数据库会话
        :param obj: 创建HASN 审计日志参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnAuditLogParam) -> int:
        """
        更新HASN 审计日志

        :param db: 数据库会话
        :param pk: HASN 审计日志 ID
        :param obj: 更新 HASN 审计日志参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除HASN 审计日志

        :param db: 数据库会话
        :param pks: HASN 审计日志 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_audit_log_dao: CRUDHasnAuditLog = CRUDHasnAuditLog(HasnAuditLog)
