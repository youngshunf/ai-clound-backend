from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.crud.crud_opportunity import opportunity_dao
from backend.app.hasn_growth.model import Opportunity
from backend.app.hasn_growth.schema.opportunity import CreateOpportunityParam, DeleteOpportunityParam, UpdateOpportunityParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class OpportunityService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> Opportunity:
        """
        获取获客商机（阶段推进 + 金额 + 成交/败因登记）

        :param db: 数据库会话
        :param pk: 获客商机（阶段推进 + 金额 + 成交/败因登记） ID
        :return:
        """
        opportunity = await opportunity_dao.get(db, pk)
        if not opportunity:
            raise errors.NotFoundError(msg='获客商机（阶段推进 + 金额 + 成交/败因登记）不存在')
        return opportunity

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取获客商机（阶段推进 + 金额 + 成交/败因登记）列表

        :param db: 数据库会话
        :return:
        """
        opportunity_select = await opportunity_dao.get_select()
        return await paging_data(db, opportunity_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[Opportunity]:
        """
        获取所有获客商机（阶段推进 + 金额 + 成交/败因登记）

        :param db: 数据库会话
        :return:
        """
        opportunity_list = await opportunity_dao.get_all(db)
        return opportunity_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateOpportunityParam) -> None:
        """
        创建获客商机（阶段推进 + 金额 + 成交/败因登记）

        :param db: 数据库会话
        :param obj: 创建获客商机（阶段推进 + 金额 + 成交/败因登记）参数
        :return:
        """
        await opportunity_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateOpportunityParam) -> int:
        """
        更新获客商机（阶段推进 + 金额 + 成交/败因登记）

        :param db: 数据库会话
        :param pk: 获客商机（阶段推进 + 金额 + 成交/败因登记） ID
        :param obj: 更新获客商机（阶段推进 + 金额 + 成交/败因登记）参数
        :return:
        """
        count = await opportunity_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteOpportunityParam) -> int:
        """
        删除获客商机（阶段推进 + 金额 + 成交/败因登记）

        :param db: 数据库会话
        :param obj: 获客商机（阶段推进 + 金额 + 成交/败因登记） ID 列表
        :return:
        """
        count = await opportunity_dao.delete(db, obj.pks)
        return count


opportunity_service: OpportunityService = OpportunityService()
