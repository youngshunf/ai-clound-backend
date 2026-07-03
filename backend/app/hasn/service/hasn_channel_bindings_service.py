from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_channel_bindings import hasn_channel_bindings_dao
from backend.app.hasn.model import HasnChannelBindings
from backend.app.hasn.schema.hasn_channel_bindings import (
    CreateHasnChannelBindingsParam,
    DeleteHasnChannelBindingsParam,
    UpdateHasnChannelBindingsParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnChannelBindingsService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnChannelBindings:
        """
        获取HASN Channel Binding

        :param db: 数据库会话
        :param pk: HASN Channel Binding  ID
        :return:
        """
        hasn_channel_bindings = await hasn_channel_bindings_dao.get(db, pk)
        if not hasn_channel_bindings:
            raise errors.NotFoundError(msg='HASN Channel Binding 不存在')
        return hasn_channel_bindings

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取HASN Channel Binding 列表

        :param db: 数据库会话
        :return:
        """
        hasn_channel_bindings_select = await hasn_channel_bindings_dao.get_select()
        return await paging_data(db, hasn_channel_bindings_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnChannelBindings]:
        """
        获取所有HASN Channel Binding

        :param db: 数据库会话
        :return:
        """
        hasn_channel_bindingss = await hasn_channel_bindings_dao.get_all(db)
        return hasn_channel_bindingss

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnChannelBindingsParam) -> None:
        """
        创建HASN Channel Binding

        :param db: 数据库会话
        :param obj: 创建HASN Channel Binding 参数
        :return:
        """
        await hasn_channel_bindings_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnChannelBindingsParam) -> int:
        """
        更新HASN Channel Binding

        :param db: 数据库会话
        :param pk: HASN Channel Binding  ID
        :param obj: 更新HASN Channel Binding 参数
        :return:
        """
        count = await hasn_channel_bindings_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnChannelBindingsParam) -> int:
        """
        删除HASN Channel Binding

        :param db: 数据库会话
        :param obj: HASN Channel Binding  ID 列表
        :return:
        """
        count = await hasn_channel_bindings_dao.delete(db, obj.pks)
        return count


hasn_channel_bindings_service: HasnChannelBindingsService = HasnChannelBindingsService()
