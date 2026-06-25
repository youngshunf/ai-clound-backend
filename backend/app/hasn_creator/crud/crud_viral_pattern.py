from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_creator.model import ViralPattern
from backend.app.hasn_creator.schema.viral_pattern import CreateViralPatternParam, UpdateViralPatternParam


class CRUDViralPattern(CRUDPlus[ViralPattern]):
    async def get(self, db: AsyncSession, pk: int) -> ViralPattern | None:
        """
        获取爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）

        :param db: 数据库会话
        :param pk: 爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[ViralPattern]:
        """
        获取所有爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateViralPatternParam) -> None:
        """
        创建爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）

        :param db: 数据库会话
        :param obj: 创建爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateViralPatternParam) -> int:
        """
        更新爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）

        :param db: 数据库会话
        :param pk: 爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL） ID
        :param obj: 更新 爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）

        :param db: 数据库会话
        :param pks: 爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


viral_pattern_dao: CRUDViralPattern = CRUDViralPattern(ViralPattern)
