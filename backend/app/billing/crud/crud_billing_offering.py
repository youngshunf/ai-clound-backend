from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.billing.model import BillingOffering
from backend.app.billing.schema.billing_offering import CreateBillingOfferingParam, UpdateBillingOfferingParam


class CRUDBillingOffering(CRUDPlus[BillingOffering]):
    async def get(self, db: AsyncSession, pk: int) -> BillingOffering | None:
        """
        获取商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）

        :param db: 数据库会话
        :param pk: 商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[BillingOffering]:
        """
        获取所有商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateBillingOfferingParam) -> None:
        """
        创建商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）

        :param db: 数据库会话
        :param obj: 创建商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateBillingOfferingParam) -> int:
        """
        更新商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）

        :param db: 数据库会话
        :param pk: 商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位） ID
        :param obj: 更新 商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）

        :param db: 数据库会话
        :param pks: 商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


billing_offering_dao: CRUDBillingOffering = CRUDBillingOffering(BillingOffering)
