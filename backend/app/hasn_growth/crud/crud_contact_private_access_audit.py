from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_growth.model import ContactPrivateAccessAudit
from backend.app.hasn_growth.schema.contact_private_access_audit import (
    CreateContactPrivateAccessAuditParam,
    UpdateContactPrivateAccessAuditParam,
)


class CRUDContactPrivateAccessAudit(CRUDPlus[ContactPrivateAccessAudit]):
    async def get(self, db: AsyncSession, pk: int) -> ContactPrivateAccessAudit | None:
        """
        获取联系人私有资料访问的数据库追加式防篡改审计

        :param db: 数据库会话
        :param pk: 联系人私有资料访问的数据库追加式防篡改审计 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取联系人私有资料访问的数据库追加式防篡改审计列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[ContactPrivateAccessAudit]:
        """
        获取所有联系人私有资料访问的数据库追加式防篡改审计

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateContactPrivateAccessAuditParam) -> None:
        """
        创建联系人私有资料访问的数据库追加式防篡改审计

        :param db: 数据库会话
        :param obj: 创建联系人私有资料访问的数据库追加式防篡改审计参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateContactPrivateAccessAuditParam) -> int:
        """
        更新联系人私有资料访问的数据库追加式防篡改审计

        :param db: 数据库会话
        :param pk: 联系人私有资料访问的数据库追加式防篡改审计 ID
        :param obj: 更新 联系人私有资料访问的数据库追加式防篡改审计参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除联系人私有资料访问的数据库追加式防篡改审计

        :param db: 数据库会话
        :param pks: 联系人私有资料访问的数据库追加式防篡改审计 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


contact_private_access_audit_dao: CRUDContactPrivateAccessAudit = CRUDContactPrivateAccessAudit(
    ContactPrivateAccessAudit
)
