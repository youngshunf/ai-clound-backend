from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.billing.crud.crud_billing_offering import billing_offering_dao
from backend.app.billing.model import BillingOffering
from backend.app.billing.schema.billing_offering import CreateBillingOfferingParam, DeleteBillingOfferingParam, UpdateBillingOfferingParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class BillingOfferingService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> BillingOffering:
        """
        获取商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）

        :param db: 数据库会话
        :param pk: 商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位） ID
        :return:
        """
        billing_offering = await billing_offering_dao.get(db, pk)
        if not billing_offering:
            raise errors.NotFoundError(msg='商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）不存在')
        return billing_offering

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）列表

        :param db: 数据库会话
        :return:
        """
        billing_offering_select = await billing_offering_dao.get_select()
        return await paging_data(db, billing_offering_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[BillingOffering]:
        """
        获取所有商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）

        :param db: 数据库会话
        :return:
        """
        billing_offering_list = await billing_offering_dao.get_all(db)
        return billing_offering_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateBillingOfferingParam) -> None:
        """
        创建商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）

        :param db: 数据库会话
        :param obj: 创建商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）参数
        :return:
        """
        await billing_offering_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateBillingOfferingParam) -> int:
        """
        更新商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）

        :param db: 数据库会话
        :param pk: 商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位） ID
        :param obj: 更新商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）参数
        :return:
        """
        count = await billing_offering_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteBillingOfferingParam) -> int:
        """
        删除商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）

        :param db: 数据库会话
        :param obj: 商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位） ID 列表
        :return:
        """
        count = await billing_offering_dao.delete(db, obj.pks)
        return count


billing_offering_service: BillingOfferingService = BillingOfferingService()
