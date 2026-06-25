from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_creator.crud.crud_viral_pattern import viral_pattern_dao
from backend.app.hasn_creator.model import ViralPattern
from backend.app.hasn_creator.schema.viral_pattern import (
    CreateViralPatternParam,
    DeleteViralPatternParam,
    UpdateViralPatternParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class ViralPatternService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> ViralPattern:
        """
        获取爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）

        :param db: 数据库会话
        :param pk: 爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL） ID
        :return:
        """
        viral_pattern = await viral_pattern_dao.get(db, pk)
        if not viral_pattern:
            raise errors.NotFoundError(msg='爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）不存在')
        return viral_pattern

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）列表

        :param db: 数据库会话
        :return:
        """
        viral_pattern_select = await viral_pattern_dao.get_select()
        return await paging_data(db, viral_pattern_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[ViralPattern]:
        """
        获取所有爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）

        :param db: 数据库会话
        :return:
        """
        viral_pattern_list = await viral_pattern_dao.get_all(db)
        return viral_pattern_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateViralPatternParam) -> None:
        """
        创建爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）

        :param db: 数据库会话
        :param obj: 创建爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）参数
        :return:
        """
        await viral_pattern_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateViralPatternParam) -> int:
        """
        更新爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）

        :param db: 数据库会话
        :param pk: 爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL） ID
        :param obj: 更新爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）参数
        :return:
        """
        count = await viral_pattern_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteViralPatternParam) -> int:
        """
        删除爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）

        :param db: 数据库会话
        :param obj: 爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL） ID 列表
        :return:
        """
        count = await viral_pattern_dao.delete(db, obj.pks)
        return count


viral_pattern_service: ViralPatternService = ViralPatternService()
