from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_deck.crud.crud_style_profile import style_profile_dao
from backend.app.hasn_deck.model import StyleProfile
from backend.app.hasn_deck.schema.style_profile import (
    CreateStyleProfileParam,
    DeleteStyleProfileParam,
    UpdateStyleProfileParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class StyleProfileService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> StyleProfile:
        """
        获取演示文稿可复用样式 StyleProfile（云端权威，仅 custom）

        :param db: 数据库会话
        :param pk: 演示文稿可复用样式 StyleProfile（云端权威，仅 custom） ID
        :return:
        """
        style_profile = await style_profile_dao.get(db, pk)
        if not style_profile:
            raise errors.NotFoundError(msg='演示文稿可复用样式 StyleProfile（云端权威，仅 custom）不存在')
        return style_profile

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取演示文稿可复用样式 StyleProfile（云端权威，仅 custom）列表

        :param db: 数据库会话
        :return:
        """
        style_profile_select = await style_profile_dao.get_select()
        return await paging_data(db, style_profile_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[StyleProfile]:
        """
        获取所有演示文稿可复用样式 StyleProfile（云端权威，仅 custom）

        :param db: 数据库会话
        :return:
        """
        style_profile_list = await style_profile_dao.get_all(db)
        return style_profile_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateStyleProfileParam) -> None:
        """
        创建演示文稿可复用样式 StyleProfile（云端权威，仅 custom）

        :param db: 数据库会话
        :param obj: 创建演示文稿可复用样式 StyleProfile（云端权威，仅 custom）参数
        :return:
        """
        await style_profile_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateStyleProfileParam) -> int:
        """
        更新演示文稿可复用样式 StyleProfile（云端权威，仅 custom）

        :param db: 数据库会话
        :param pk: 演示文稿可复用样式 StyleProfile（云端权威，仅 custom） ID
        :param obj: 更新演示文稿可复用样式 StyleProfile（云端权威，仅 custom）参数
        :return:
        """
        count = await style_profile_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteStyleProfileParam) -> int:
        """
        删除演示文稿可复用样式 StyleProfile（云端权威，仅 custom）

        :param db: 数据库会话
        :param obj: 演示文稿可复用样式 StyleProfile（云端权威，仅 custom） ID 列表
        :return:
        """
        count = await style_profile_dao.delete(db, obj.pks)
        return count


style_profile_service: StyleProfileService = StyleProfileService()
