from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnTradeSessions
from backend.app.hasn.schema.hasn_trade_sessions import CreateHasnTradeSessionsParam, UpdateHasnTradeSessionsParam


class CRUDHasnTradeSessions(CRUDPlus[HasnTradeSessions]):
    async def get(self, db: AsyncSession, pk: int) -> HasnTradeSessions | None:
        """
        获取HASN 交易会话

        :param db: 数据库会话
        :param pk: HASN 交易会话 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取HASN 交易会话列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnTradeSessions]:
        """
        获取所有HASN 交易会话

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnTradeSessionsParam) -> None:
        """
        创建HASN 交易会话

        :param db: 数据库会话
        :param obj: 创建HASN 交易会话参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnTradeSessionsParam) -> int:
        """
        更新HASN 交易会话

        :param db: 数据库会话
        :param pk: HASN 交易会话 ID
        :param obj: 更新 HASN 交易会话参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除HASN 交易会话

        :param db: 数据库会话
        :param pks: HASN 交易会话 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_trade_sessions_dao: CRUDHasnTradeSessions = CRUDHasnTradeSessions(HasnTradeSessions)
