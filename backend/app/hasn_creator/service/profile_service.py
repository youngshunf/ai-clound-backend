from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_creator.crud.crud_profile import profile_dao
from backend.app.hasn_creator.model import Profile
from backend.app.hasn_creator.schema.profile import CreateProfileParam, DeleteProfileParam, UpdateProfileParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class ProfileService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> Profile:
        """
        获取项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）

        :param db: 数据库会话
        :param pk: 项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心） ID
        :return:
        """
        profile = await profile_dao.get(db, pk)
        if not profile:
            raise errors.NotFoundError(msg='项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）不存在')
        return profile

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）列表

        :param db: 数据库会话
        :return:
        """
        profile_select = await profile_dao.get_select()
        return await paging_data(db, profile_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[Profile]:
        """
        获取所有项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）

        :param db: 数据库会话
        :return:
        """
        profile_list = await profile_dao.get_all(db)
        return profile_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateProfileParam) -> None:
        """
        创建项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）

        :param db: 数据库会话
        :param obj: 创建项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）参数
        :return:
        """
        await profile_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateProfileParam) -> int:
        """
        更新项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）

        :param db: 数据库会话
        :param pk: 项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心） ID
        :param obj: 更新项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）参数
        :return:
        """
        count = await profile_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteProfileParam) -> int:
        """
        删除项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）

        :param db: 数据库会话
        :param obj: 项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心） ID 列表
        :return:
        """
        count = await profile_dao.delete(db, obj.pks)
        return count


profile_service: ProfileService = ProfileService()
