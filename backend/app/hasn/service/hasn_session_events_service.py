from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_session_events import hasn_session_events_dao
from backend.app.hasn.model import HasnSessionEvents
from backend.app.hasn.schema.hasn_session_events import (
    CreateHasnSessionEventsParam,
    DeleteHasnSessionEventsParam,
    UpdateHasnSessionEventsParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnSessionEventsService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnSessionEvents:
        """
        获取HASN 会话事件

        :param db: 数据库会话
        :param pk: HASN 会话事件 ID
        :return:
        """
        hasn_session_events = await hasn_session_events_dao.get(db, pk)
        if not hasn_session_events:
            raise errors.NotFoundError(msg='HASN 会话事件不存在')
        return hasn_session_events

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取HASN 会话事件列表

        :param db: 数据库会话
        :return:
        """
        hasn_session_events_select = await hasn_session_events_dao.get_select()
        return await paging_data(db, hasn_session_events_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnSessionEvents]:
        """
        获取所有HASN 会话事件

        :param db: 数据库会话
        :return:
        """
        hasn_session_events_list = await hasn_session_events_dao.get_all(db)
        return hasn_session_events_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnSessionEventsParam) -> None:
        """
        创建HASN 会话事件

        :param db: 数据库会话
        :param obj: 创建HASN 会话事件参数
        :return:
        """
        await hasn_session_events_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnSessionEventsParam) -> int:
        """
        更新HASN 会话事件

        :param db: 数据库会话
        :param pk: HASN 会话事件 ID
        :param obj: 更新HASN 会话事件参数
        :return:
        """
        count = await hasn_session_events_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnSessionEventsParam) -> int:
        """
        删除HASN 会话事件

        :param db: 数据库会话
        :param obj: HASN 会话事件 ID 列表
        :return:
        """
        count = await hasn_session_events_dao.delete(db, obj.pks)
        return count


hasn_session_events_service: HasnSessionEventsService = HasnSessionEventsService()
