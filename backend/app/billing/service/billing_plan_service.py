from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.billing.crud.crud_billing_plan import billing_plan_dao
from backend.app.billing.model import BillingPlan
from backend.app.billing.schema.billing_plan import (
    CreateBillingPlanParam,
    DeleteBillingPlanParam,
    UpdateBillingPlanParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class BillingPlanService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> BillingPlan:
        """
        获取商品档位（价格+配额快照+试用/宽限策略）

        :param db: 数据库会话
        :param pk: 商品档位（价格+配额快照+试用/宽限策略） ID
        :return:
        """
        billing_plan = await billing_plan_dao.get(db, pk)
        if not billing_plan:
            raise errors.NotFoundError(msg='商品档位（价格+配额快照+试用/宽限策略）不存在')
        return billing_plan

    @staticmethod
    async def get_list(
        db: AsyncSession, *, offering_key: str | None = None, status: str | None = None
    ) -> dict[str, Any]:
        """
        获取商品档位（价格+配额快照+试用/宽限策略）列表

        :param db: 数据库会话
        :param offering_key: 按所属 offering 过滤（管理面按商品分组查看档位）
        :param status: 按上/下架状态过滤
        :return:
        """
        billing_plan_select = await billing_plan_dao.get_select(offering_key=offering_key, status=status)
        return await paging_data(db, billing_plan_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[BillingPlan]:
        """
        获取所有商品档位（价格+配额快照+试用/宽限策略）

        :param db: 数据库会话
        :return:
        """
        billing_plan_list = await billing_plan_dao.get_all(db)
        return billing_plan_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateBillingPlanParam) -> None:
        """
        创建商品档位（价格+配额快照+试用/宽限策略）

        :param db: 数据库会话
        :param obj: 创建商品档位（价格+配额快照+试用/宽限策略）参数
        :return:
        """
        await billing_plan_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateBillingPlanParam) -> int:
        """
        更新商品档位（价格+配额快照+试用/宽限策略）

        :param db: 数据库会话
        :param pk: 商品档位（价格+配额快照+试用/宽限策略） ID
        :param obj: 更新商品档位（价格+配额快照+试用/宽限策略）参数
        :return:
        """
        count = await billing_plan_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteBillingPlanParam) -> int:
        """
        删除商品档位（价格+配额快照+试用/宽限策略）

        :param db: 数据库会话
        :param obj: 商品档位（价格+配额快照+试用/宽限策略） ID 列表
        :return:
        """
        count = await billing_plan_dao.delete(db, obj.pks)
        return count


billing_plan_service: BillingPlanService = BillingPlanService()
