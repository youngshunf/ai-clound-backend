from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_sync_inbox_events import hasn_sync_inbox_events_dao
from backend.app.hasn.model import HasnSyncInboxEvents
from backend.app.hasn.schema.hasn_sync_inbox_events import (
    CreateHasnSyncInboxEventsParam,
    DeleteHasnSyncInboxEventsParam,
    UpdateHasnSyncInboxEventsParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnSyncInboxEventsService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnSyncInboxEvents:
        """
        获取HASN 客户端上行 outbox 幂等/冲突

        :param db: 数据库会话
        :param pk: HASN 客户端上行 outbox 幂等/冲突 ID
        :return:
        """
        hasn_sync_inbox_events = await hasn_sync_inbox_events_dao.get(db, pk)
        if not hasn_sync_inbox_events:
            raise errors.NotFoundError(msg='HASN 客户端上行 outbox 幂等/冲突不存在')
        return hasn_sync_inbox_events

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取HASN 客户端上行 outbox 幂等/冲突列表

        :param db: 数据库会话
        :return:
        """
        hasn_sync_inbox_events_select = await hasn_sync_inbox_events_dao.get_select()
        return await paging_data(db, hasn_sync_inbox_events_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnSyncInboxEvents]:
        """
        获取所有HASN 客户端上行 outbox 幂等/冲突

        :param db: 数据库会话
        :return:
        """
        hasn_sync_inbox_eventss = await hasn_sync_inbox_events_dao.get_all(db)
        return hasn_sync_inbox_eventss

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnSyncInboxEventsParam) -> None:
        """
        创建HASN 客户端上行 outbox 幂等/冲突

        :param db: 数据库会话
        :param obj: 创建HASN 客户端上行 outbox 幂等/冲突参数
        :return:
        """
        await hasn_sync_inbox_events_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnSyncInboxEventsParam) -> int:
        """
        更新HASN 客户端上行 outbox 幂等/冲突

        :param db: 数据库会话
        :param pk: HASN 客户端上行 outbox 幂等/冲突 ID
        :param obj: 更新HASN 客户端上行 outbox 幂等/冲突参数
        :return:
        """
        count = await hasn_sync_inbox_events_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnSyncInboxEventsParam) -> int:
        """
        删除HASN 客户端上行 outbox 幂等/冲突

        :param db: 数据库会话
        :param obj: HASN 客户端上行 outbox 幂等/冲突 ID 列表
        :return:
        """
        count = await hasn_sync_inbox_events_dao.delete(db, obj.pks)
        return count


hasn_sync_inbox_events_service: HasnSyncInboxEventsService = HasnSyncInboxEventsService()
