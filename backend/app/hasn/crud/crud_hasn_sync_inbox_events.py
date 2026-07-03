from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnSyncInboxEvents
from backend.app.hasn.schema.hasn_sync_inbox_events import (
    CreateHasnSyncInboxEventsParam,
    UpdateHasnSyncInboxEventsParam,
)


class CRUDHasnSyncInboxEvents(CRUDPlus[HasnSyncInboxEvents]):
    async def get(self, db: AsyncSession, pk: int) -> HasnSyncInboxEvents | None:
        """
        获取HASN 客户端上行 outbox 幂等/冲突

        :param db: 数据库会话
        :param pk: HASN 客户端上行 outbox 幂等/冲突 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取HASN 客户端上行 outbox 幂等/冲突列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnSyncInboxEvents]:
        """
        获取所有HASN 客户端上行 outbox 幂等/冲突

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnSyncInboxEventsParam) -> None:
        """
        创建HASN 客户端上行 outbox 幂等/冲突

        :param db: 数据库会话
        :param obj: 创建HASN 客户端上行 outbox 幂等/冲突参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnSyncInboxEventsParam) -> int:
        """
        更新HASN 客户端上行 outbox 幂等/冲突

        :param db: 数据库会话
        :param pk: HASN 客户端上行 outbox 幂等/冲突 ID
        :param obj: 更新 HASN 客户端上行 outbox 幂等/冲突参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除HASN 客户端上行 outbox 幂等/冲突

        :param db: 数据库会话
        :param pks: HASN 客户端上行 outbox 幂等/冲突 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_sync_inbox_events_dao: CRUDHasnSyncInboxEvents = CRUDHasnSyncInboxEvents(HasnSyncInboxEvents)
