from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_unread_counts import hasn_unread_counts_dao
from backend.app.hasn.model import HasnUnreadCounts
from backend.app.hasn.schema.hasn_unread_counts import (
    CreateHasnUnreadCountsParam,
    DeleteHasnUnreadCountsParam,
    UpdateHasnUnreadCountsParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnUnreadCountsService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnUnreadCounts:
        """
        获取HASN 未读计数

        :param db: 数据库会话
        :param pk: HASN 未读计数 ID
        :return:
        """
        hasn_unread_counts = await hasn_unread_counts_dao.get(db, pk)
        if not hasn_unread_counts:
            raise errors.NotFoundError(msg='HASN 未读计数不存在')
        return hasn_unread_counts

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取HASN 未读计数列表

        :param db: 数据库会话
        :return:
        """
        hasn_unread_counts_select = await hasn_unread_counts_dao.get_select()
        return await paging_data(db, hasn_unread_counts_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnUnreadCounts]:
        """
        获取所有HASN 未读计数

        :param db: 数据库会话
        :return:
        """
        hasn_unread_countss = await hasn_unread_counts_dao.get_all(db)
        return hasn_unread_countss

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnUnreadCountsParam) -> None:
        """
        创建HASN 未读计数

        :param db: 数据库会话
        :param obj: 创建HASN 未读计数参数
        :return:
        """
        await hasn_unread_counts_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnUnreadCountsParam) -> int:
        """
        更新HASN 未读计数

        :param db: 数据库会话
        :param pk: HASN 未读计数 ID
        :param obj: 更新HASN 未读计数参数
        :return:
        """
        count = await hasn_unread_counts_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnUnreadCountsParam) -> int:
        """
        删除HASN 未读计数

        :param db: 数据库会话
        :param obj: HASN 未读计数 ID 列表
        :return:
        """
        count = await hasn_unread_counts_dao.delete(db, obj.pks)
        return count


hasn_unread_counts_service: HasnUnreadCountsService = HasnUnreadCountsService()
