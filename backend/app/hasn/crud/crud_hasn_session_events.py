from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnSessionEvents
from backend.app.hasn.schema.hasn_session_events import CreateHasnSessionEventsParam, UpdateHasnSessionEventsParam


class CRUDHasnSessionEvents(CRUDPlus[HasnSessionEvents]):
    async def get(self, db: AsyncSession, pk: int) -> HasnSessionEvents | None:
        """
        获取HASN 会话事件

        :param db: 数据库会话
        :param pk: HASN 会话事件 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取HASN 会话事件列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnSessionEvents]:
        """
        获取所有HASN 会话事件

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnSessionEventsParam) -> None:
        """
        创建HASN 会话事件

        :param db: 数据库会话
        :param obj: 创建HASN 会话事件参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnSessionEventsParam) -> int:
        """
        更新HASN 会话事件

        :param db: 数据库会话
        :param pk: HASN 会话事件 ID
        :param obj: 更新 HASN 会话事件参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除HASN 会话事件

        :param db: 数据库会话
        :param pks: HASN 会话事件 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_session_events_dao: CRUDHasnSessionEvents = CRUDHasnSessionEvents(HasnSessionEvents)
