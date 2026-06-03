from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_community.model import HasnCircleMembers
from backend.app.hasn_community.schema.hasn_circle_members import CreateHasnCircleMembersParam, UpdateHasnCircleMembersParam


class CRUDHasnCircleMembers(CRUDPlus[HasnCircleMembers]):
    async def get(self, db: AsyncSession, pk: int) -> HasnCircleMembers | None:
        """
        获取圈子成员与角色

        :param db: 数据库会话
        :param pk: 圈子成员与角色 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取圈子成员与角色列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnCircleMembers]:
        """
        获取所有圈子成员与角色

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnCircleMembersParam) -> None:
        """
        创建圈子成员与角色

        :param db: 数据库会话
        :param obj: 创建圈子成员与角色参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnCircleMembersParam) -> int:
        """
        更新圈子成员与角色

        :param db: 数据库会话
        :param pk: 圈子成员与角色 ID
        :param obj: 更新 圈子成员与角色参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除圈子成员与角色

        :param db: 数据库会话
        :param pks: 圈子成员与角色 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_circle_members_dao: CRUDHasnCircleMembers = CRUDHasnCircleMembers(HasnCircleMembers)
