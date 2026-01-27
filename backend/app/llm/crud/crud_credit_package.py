from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.llm.model import CreditPackage
from backend.app.llm.schema.credit_package import CreateCreditPackageParam, UpdateCreditPackageParam


class CRUDCreditPackage(CRUDPlus[CreditPackage]):
    async def get(self, db: AsyncSession, pk: int) -> CreditPackage | None:
        """
        获取积分包配置表 - 定义可购买的积分包

        :param db: 数据库会话
        :param pk: 积分包配置表 - 定义可购买的积分包 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取积分包配置表 - 定义可购买的积分包列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[CreditPackage]:
        """
        获取所有积分包配置表 - 定义可购买的积分包

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateCreditPackageParam) -> None:
        """
        创建积分包配置表 - 定义可购买的积分包

        :param db: 数据库会话
        :param obj: 创建积分包配置表 - 定义可购买的积分包参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateCreditPackageParam) -> int:
        """
        更新积分包配置表 - 定义可购买的积分包

        :param db: 数据库会话
        :param pk: 积分包配置表 - 定义可购买的积分包 ID
        :param obj: 更新 积分包配置表 - 定义可购买的积分包参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除积分包配置表 - 定义可购买的积分包

        :param db: 数据库会话
        :param pks: 积分包配置表 - 定义可购买的积分包 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


credit_package_dao: CRUDCreditPackage = CRUDCreditPackage(CreditPackage)
