from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_creator.model import Account
from backend.app.hasn_creator.schema.account import CreateAccountParam, UpdateAccountParam


class CRUDAccount(CRUDPlus[Account]):
    async def get(self, db: AsyncSession, pk: int) -> Account | None:
        """
        获取平台账号（1:N project）；同一项目多平台真实账号

        :param db: 数据库会话
        :param pk: 平台账号（1:N project）；同一项目多平台真实账号 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取平台账号（1:N project）；同一项目多平台真实账号列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[Account]:
        """
        获取所有平台账号（1:N project）；同一项目多平台真实账号

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateAccountParam) -> None:
        """
        创建平台账号（1:N project）；同一项目多平台真实账号

        :param db: 数据库会话
        :param obj: 创建平台账号（1:N project）；同一项目多平台真实账号参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateAccountParam) -> int:
        """
        更新平台账号（1:N project）；同一项目多平台真实账号

        :param db: 数据库会话
        :param pk: 平台账号（1:N project）；同一项目多平台真实账号 ID
        :param obj: 更新 平台账号（1:N project）；同一项目多平台真实账号参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除平台账号（1:N project）；同一项目多平台真实账号

        :param db: 数据库会话
        :param pks: 平台账号（1:N project）；同一项目多平台真实账号 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


account_dao: CRUDAccount = CRUDAccount(Account)
