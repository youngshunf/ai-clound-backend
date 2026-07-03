from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_growth.model import Customer
from backend.app.hasn_growth.schema.customer import CreateCustomerParam, UpdateCustomerParam


class CRUDCustomer(CRUDPlus[Customer]):
    async def get(self, db: AsyncSession, pk: int) -> Customer | None:
        """
        获取获客客户（qualified 线索 / inbound 直建）

        :param db: 数据库会话
        :param pk: 获客客户（qualified 线索 / inbound 直建） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取获客客户（qualified 线索 / inbound 直建）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[Customer]:
        """
        获取所有获客客户（qualified 线索 / inbound 直建）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateCustomerParam) -> None:
        """
        创建获客客户（qualified 线索 / inbound 直建）

        :param db: 数据库会话
        :param obj: 创建获客客户（qualified 线索 / inbound 直建）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateCustomerParam) -> int:
        """
        更新获客客户（qualified 线索 / inbound 直建）

        :param db: 数据库会话
        :param pk: 获客客户（qualified 线索 / inbound 直建） ID
        :param obj: 更新 获客客户（qualified 线索 / inbound 直建）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除获客客户（qualified 线索 / inbound 直建）

        :param db: 数据库会话
        :param pks: 获客客户（qualified 线索 / inbound 直建） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


customer_dao: CRUDCustomer = CRUDCustomer(Customer)
