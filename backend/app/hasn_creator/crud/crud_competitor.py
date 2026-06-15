from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_creator.model import Competitor
from backend.app.hasn_creator.schema.competitor import CreateCompetitorParam, UpdateCompetitorParam


class CRUDCompetitor(CRUDPlus[Competitor]):
    async def get(self, db: AsyncSession, pk: int) -> Competitor | None:
        """
        获取竞品账号（定位/选题调研输入）

        :param db: 数据库会话
        :param pk: 竞品账号（定位/选题调研输入） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取竞品账号（定位/选题调研输入）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[Competitor]:
        """
        获取所有竞品账号（定位/选题调研输入）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateCompetitorParam) -> None:
        """
        创建竞品账号（定位/选题调研输入）

        :param db: 数据库会话
        :param obj: 创建竞品账号（定位/选题调研输入）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateCompetitorParam) -> int:
        """
        更新竞品账号（定位/选题调研输入）

        :param db: 数据库会话
        :param pk: 竞品账号（定位/选题调研输入） ID
        :param obj: 更新 竞品账号（定位/选题调研输入）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除竞品账号（定位/选题调研输入）

        :param db: 数据库会话
        :param pks: 竞品账号（定位/选题调研输入） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


competitor_dao: CRUDCompetitor = CRUDCompetitor(Competitor)
