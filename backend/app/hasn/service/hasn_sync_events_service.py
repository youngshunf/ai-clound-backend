from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_sync_events import hasn_sync_events_dao
from backend.app.hasn.model import HasnSyncEvents
from backend.app.hasn.schema.hasn_sync_events import (
    CreateHasnSyncEventsParam,
    DeleteHasnSyncEventsParam,
    UpdateHasnSyncEventsParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnSyncEventsService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnSyncEvents:
        """
        获取HASN 服务端下行同步事件

        :param db: 数据库会话
        :param pk: HASN 服务端下行同步事件 ID
        :return:
        """
        hasn_sync_events = await hasn_sync_events_dao.get(db, pk)
        if not hasn_sync_events:
            raise errors.NotFoundError(msg='HASN 服务端下行同步事件不存在')
        return hasn_sync_events

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取HASN 服务端下行同步事件列表

        :param db: 数据库会话
        :return:
        """
        hasn_sync_events_select = await hasn_sync_events_dao.get_select()
        return await paging_data(db, hasn_sync_events_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnSyncEvents]:
        """
        获取所有HASN 服务端下行同步事件

        :param db: 数据库会话
        :return:
        """
        hasn_sync_eventss = await hasn_sync_events_dao.get_all(db)
        return hasn_sync_eventss

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnSyncEventsParam) -> None:
        """
        创建HASN 服务端下行同步事件

        :param db: 数据库会话
        :param obj: 创建HASN 服务端下行同步事件参数
        :return:
        """
        await hasn_sync_events_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnSyncEventsParam) -> int:
        """
        更新HASN 服务端下行同步事件

        :param db: 数据库会话
        :param pk: HASN 服务端下行同步事件 ID
        :param obj: 更新HASN 服务端下行同步事件参数
        :return:
        """
        count = await hasn_sync_events_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnSyncEventsParam) -> int:
        """
        删除HASN 服务端下行同步事件

        :param db: 数据库会话
        :param obj: HASN 服务端下行同步事件 ID 列表
        :return:
        """
        count = await hasn_sync_events_dao.delete(db, obj.pks)
        return count


hasn_sync_events_service: HasnSyncEventsService = HasnSyncEventsService()
