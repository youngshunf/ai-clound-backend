from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_group_members import hasn_group_members_dao
from backend.app.hasn.model import HasnGroupMembers
from backend.app.hasn.schema.hasn_group_members import (
    CreateHasnGroupMembersParam,
    DeleteHasnGroupMembersParam,
    UpdateHasnGroupMembersParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnGroupMembersService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnGroupMembers:
        """
        获取HASN 群成员

        :param db: 数据库会话
        :param pk: HASN 群成员 ID
        :return:
        """
        hasn_group_members = await hasn_group_members_dao.get(db, pk)
        if not hasn_group_members:
            raise errors.NotFoundError(msg='HASN 群成员不存在')
        return hasn_group_members

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取HASN 群成员列表

        :param db: 数据库会话
        :return:
        """
        hasn_group_members_select = await hasn_group_members_dao.get_select()
        return await paging_data(db, hasn_group_members_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnGroupMembers]:
        """
        获取所有HASN 群成员

        :param db: 数据库会话
        :return:
        """
        hasn_group_memberss = await hasn_group_members_dao.get_all(db)
        return hasn_group_memberss

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnGroupMembersParam) -> None:
        """
        创建HASN 群成员

        :param db: 数据库会话
        :param obj: 创建HASN 群成员参数
        :return:
        """
        await hasn_group_members_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnGroupMembersParam) -> int:
        """
        更新HASN 群成员

        :param db: 数据库会话
        :param pk: HASN 群成员 ID
        :param obj: 更新HASN 群成员参数
        :return:
        """
        count = await hasn_group_members_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnGroupMembersParam) -> int:
        """
        删除HASN 群成员

        :param db: 数据库会话
        :param obj: HASN 群成员 ID 列表
        :return:
        """
        count = await hasn_group_members_dao.delete(db, obj.pks)
        return count


hasn_group_members_service: HasnGroupMembersService = HasnGroupMembersService()
