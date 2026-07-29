from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.crud.crud_growth_profile_suggestion import growth_profile_suggestion_dao
from backend.app.hasn_growth.model import GrowthProfileSuggestion
from backend.app.hasn_growth.schema.growth_profile_suggestion import (
    CreateGrowthProfileSuggestionParam,
    DeleteGrowthProfileSuggestionParam,
    UpdateGrowthProfileSuggestionParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class GrowthProfileSuggestionService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> GrowthProfileSuggestion:
        """
        获取分身或系统提出、等待主人确认的画像建议

        :param db: 数据库会话
        :param pk: 分身或系统提出、等待主人确认的画像建议 ID
        :return:
        """
        growth_profile_suggestion = await growth_profile_suggestion_dao.get(db, pk)
        if not growth_profile_suggestion:
            raise errors.NotFoundError(msg='分身或系统提出、等待主人确认的画像建议不存在')
        return growth_profile_suggestion

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取分身或系统提出、等待主人确认的画像建议列表

        :param db: 数据库会话
        :return:
        """
        growth_profile_suggestion_select = await growth_profile_suggestion_dao.get_select()
        return await paging_data(db, growth_profile_suggestion_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[GrowthProfileSuggestion]:
        """
        获取所有分身或系统提出、等待主人确认的画像建议

        :param db: 数据库会话
        :return:
        """
        growth_profile_suggestion_list = await growth_profile_suggestion_dao.get_all(db)
        return growth_profile_suggestion_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateGrowthProfileSuggestionParam) -> None:
        """
        创建分身或系统提出、等待主人确认的画像建议

        :param db: 数据库会话
        :param obj: 创建分身或系统提出、等待主人确认的画像建议参数
        :return:
        """
        await growth_profile_suggestion_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateGrowthProfileSuggestionParam) -> int:
        """
        更新分身或系统提出、等待主人确认的画像建议

        :param db: 数据库会话
        :param pk: 分身或系统提出、等待主人确认的画像建议 ID
        :param obj: 更新分身或系统提出、等待主人确认的画像建议参数
        :return:
        """
        count = await growth_profile_suggestion_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteGrowthProfileSuggestionParam) -> int:
        """
        删除分身或系统提出、等待主人确认的画像建议

        :param db: 数据库会话
        :param obj: 分身或系统提出、等待主人确认的画像建议 ID 列表
        :return:
        """
        count = await growth_profile_suggestion_dao.delete(db, obj.pks)
        return count


growth_profile_suggestion_service: GrowthProfileSuggestionService = GrowthProfileSuggestionService()
