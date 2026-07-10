from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.billing.model import BillingPlan
from backend.app.billing.schema.billing_plan import CreateBillingPlanParam, UpdateBillingPlanParam


class CRUDBillingPlan(CRUDPlus[BillingPlan]):
    async def get(self, db: AsyncSession, pk: int) -> BillingPlan | None:
        """
        获取商品档位（价格+配额快照+试用/宽限策略）

        :param db: 数据库会话
        :param pk: 商品档位（价格+配额快照+试用/宽限策略） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取商品档位（价格+配额快照+试用/宽限策略）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[BillingPlan]:
        """
        获取所有商品档位（价格+配额快照+试用/宽限策略）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateBillingPlanParam) -> None:
        """
        创建商品档位（价格+配额快照+试用/宽限策略）

        :param db: 数据库会话
        :param obj: 创建商品档位（价格+配额快照+试用/宽限策略）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateBillingPlanParam) -> int:
        """
        更新商品档位（价格+配额快照+试用/宽限策略）

        :param db: 数据库会话
        :param pk: 商品档位（价格+配额快照+试用/宽限策略） ID
        :param obj: 更新 商品档位（价格+配额快照+试用/宽限策略）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除商品档位（价格+配额快照+试用/宽限策略）

        :param db: 数据库会话
        :param pks: 商品档位（价格+配额快照+试用/宽限策略） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


billing_plan_dao: CRUDBillingPlan = CRUDBillingPlan(BillingPlan)
