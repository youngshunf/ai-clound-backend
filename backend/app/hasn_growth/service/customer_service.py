from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.crud.crud_customer import customer_dao
from backend.app.hasn_growth.model import Customer
from backend.app.hasn_growth.schema.customer import CreateCustomerParam, DeleteCustomerParam, UpdateCustomerParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class CustomerService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> Customer:
        """
        获取获客客户（qualified 线索 / inbound 直建）

        :param db: 数据库会话
        :param pk: 获客客户（qualified 线索 / inbound 直建） ID
        :return:
        """
        customer = await customer_dao.get(db, pk)
        if not customer:
            raise errors.NotFoundError(msg='获客客户（qualified 线索 / inbound 直建）不存在')
        return customer

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取获客客户（qualified 线索 / inbound 直建）列表

        :param db: 数据库会话
        :return:
        """
        customer_select = await customer_dao.get_select()
        return await paging_data(db, customer_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[Customer]:
        """
        获取所有获客客户（qualified 线索 / inbound 直建）

        :param db: 数据库会话
        :return:
        """
        customer_list = await customer_dao.get_all(db)
        return customer_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateCustomerParam) -> None:
        """
        创建获客客户（qualified 线索 / inbound 直建）

        :param db: 数据库会话
        :param obj: 创建获客客户（qualified 线索 / inbound 直建）参数
        :return:
        """
        await customer_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateCustomerParam) -> int:
        """
        更新获客客户（qualified 线索 / inbound 直建）

        :param db: 数据库会话
        :param pk: 获客客户（qualified 线索 / inbound 直建） ID
        :param obj: 更新获客客户（qualified 线索 / inbound 直建）参数
        :return:
        """
        count = await customer_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteCustomerParam) -> int:
        """
        删除获客客户（qualified 线索 / inbound 直建）

        :param db: 数据库会话
        :param obj: 获客客户（qualified 线索 / inbound 直建） ID 列表
        :return:
        """
        count = await customer_dao.delete(db, obj.pks)
        return count


customer_service: CustomerService = CustomerService()
