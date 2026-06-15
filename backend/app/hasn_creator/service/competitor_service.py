from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_creator.crud.crud_competitor import competitor_dao
from backend.app.hasn_creator.model import Competitor
from backend.app.hasn_creator.schema.competitor import CreateCompetitorParam, DeleteCompetitorParam, UpdateCompetitorParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class CompetitorService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> Competitor:
        """
        获取竞品账号（定位/选题调研输入）

        :param db: 数据库会话
        :param pk: 竞品账号（定位/选题调研输入） ID
        :return:
        """
        competitor = await competitor_dao.get(db, pk)
        if not competitor:
            raise errors.NotFoundError(msg='竞品账号（定位/选题调研输入）不存在')
        return competitor

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取竞品账号（定位/选题调研输入）列表

        :param db: 数据库会话
        :return:
        """
        competitor_select = await competitor_dao.get_select()
        return await paging_data(db, competitor_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[Competitor]:
        """
        获取所有竞品账号（定位/选题调研输入）

        :param db: 数据库会话
        :return:
        """
        competitor_list = await competitor_dao.get_all(db)
        return competitor_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateCompetitorParam) -> None:
        """
        创建竞品账号（定位/选题调研输入）

        :param db: 数据库会话
        :param obj: 创建竞品账号（定位/选题调研输入）参数
        :return:
        """
        await competitor_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateCompetitorParam) -> int:
        """
        更新竞品账号（定位/选题调研输入）

        :param db: 数据库会话
        :param pk: 竞品账号（定位/选题调研输入） ID
        :param obj: 更新竞品账号（定位/选题调研输入）参数
        :return:
        """
        count = await competitor_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteCompetitorParam) -> int:
        """
        删除竞品账号（定位/选题调研输入）

        :param db: 数据库会话
        :param obj: 竞品账号（定位/选题调研输入） ID 列表
        :return:
        """
        count = await competitor_dao.delete(db, obj.pks)
        return count


competitor_service: CompetitorService = CompetitorService()
