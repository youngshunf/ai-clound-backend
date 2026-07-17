from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_finance.model import Watchlist
from backend.app.hasn_finance.schema.watchlist import CreateWatchlistParam, UpdateWatchlistParam


class CRUDWatchlist(CRUDPlus[Watchlist]):
    async def get(self, db: AsyncSession, pk: int) -> Watchlist | None:
        """
        获取自选股（人工维护·非产物·不登记 hasn_artifacts，05 §3.1.1）

        :param db: 数据库会话
        :param pk: 自选股（人工维护·非产物·不登记 hasn_artifacts，05 §3.1.1） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取自选股（人工维护·非产物·不登记 hasn_artifacts，05 §3.1.1）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[Watchlist]:
        """
        获取所有自选股（人工维护·非产物·不登记 hasn_artifacts，05 §3.1.1）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateWatchlistParam) -> None:
        """
        创建自选股（人工维护·非产物·不登记 hasn_artifacts，05 §3.1.1）

        :param db: 数据库会话
        :param obj: 创建自选股（人工维护·非产物·不登记 hasn_artifacts，05 §3.1.1）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateWatchlistParam) -> int:
        """
        更新自选股（人工维护·非产物·不登记 hasn_artifacts，05 §3.1.1）

        :param db: 数据库会话
        :param pk: 自选股（人工维护·非产物·不登记 hasn_artifacts，05 §3.1.1） ID
        :param obj: 更新 自选股（人工维护·非产物·不登记 hasn_artifacts，05 §3.1.1）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除自选股（人工维护·非产物·不登记 hasn_artifacts，05 §3.1.1）

        :param db: 数据库会话
        :param pks: 自选股（人工维护·非产物·不登记 hasn_artifacts，05 §3.1.1） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


watchlist_dao: CRUDWatchlist = CRUDWatchlist(Watchlist)
