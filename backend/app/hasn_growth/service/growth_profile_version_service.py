from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.crud.crud_growth_profile_version import growth_profile_version_dao
from backend.app.hasn_growth.model import GrowthProfileVersion
from backend.app.hasn_growth.schema.growth_profile_version import (
    CreateGrowthProfileVersionParam,
    DeleteGrowthProfileVersionParam,
    UpdateGrowthProfileVersionParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class GrowthProfileVersionService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> GrowthProfileVersion:
        """
        获取获客项目已确认画像的不可变版本历史

        :param db: 数据库会话
        :param pk: 获客项目已确认画像的不可变版本历史 ID
        :return:
        """
        growth_profile_version = await growth_profile_version_dao.get(db, pk)
        if not growth_profile_version:
            raise errors.NotFoundError(msg='获客项目已确认画像的不可变版本历史不存在')
        return growth_profile_version

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取获客项目已确认画像的不可变版本历史列表

        :param db: 数据库会话
        :return:
        """
        growth_profile_version_select = await growth_profile_version_dao.get_select()
        return await paging_data(db, growth_profile_version_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[GrowthProfileVersion]:
        """
        获取所有获客项目已确认画像的不可变版本历史

        :param db: 数据库会话
        :return:
        """
        growth_profile_version_list = await growth_profile_version_dao.get_all(db)
        return growth_profile_version_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateGrowthProfileVersionParam) -> None:
        """
        创建获客项目已确认画像的不可变版本历史

        :param db: 数据库会话
        :param obj: 创建获客项目已确认画像的不可变版本历史参数
        :return:
        """
        await growth_profile_version_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateGrowthProfileVersionParam) -> int:
        """
        更新获客项目已确认画像的不可变版本历史

        :param db: 数据库会话
        :param pk: 获客项目已确认画像的不可变版本历史 ID
        :param obj: 更新获客项目已确认画像的不可变版本历史参数
        :return:
        """
        count = await growth_profile_version_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteGrowthProfileVersionParam) -> int:
        """
        删除获客项目已确认画像的不可变版本历史

        :param db: 数据库会话
        :param obj: 获客项目已确认画像的不可变版本历史 ID 列表
        :return:
        """
        count = await growth_profile_version_dao.delete(db, obj.pks)
        return count


growth_profile_version_service: GrowthProfileVersionService = GrowthProfileVersionService()
