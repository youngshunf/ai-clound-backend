from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnContentTranslations
from backend.app.hasn.schema.hasn_content_translations import CreateHasnContentTranslationsParam, UpdateHasnContentTranslationsParam


class CRUDHasnContentTranslations(CRUDPlus[HasnContentTranslations]):
    async def get(self, db: AsyncSession, pk: int) -> HasnContentTranslations | None:
        """
        获取用户内容译文缓存（译文是视图，不回写原文表）

        :param db: 数据库会话
        :param pk: 用户内容译文缓存（译文是视图，不回写原文表） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取用户内容译文缓存（译文是视图，不回写原文表）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnContentTranslations]:
        """
        获取所有用户内容译文缓存（译文是视图，不回写原文表）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnContentTranslationsParam) -> None:
        """
        创建用户内容译文缓存（译文是视图，不回写原文表）

        :param db: 数据库会话
        :param obj: 创建用户内容译文缓存（译文是视图，不回写原文表）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnContentTranslationsParam) -> int:
        """
        更新用户内容译文缓存（译文是视图，不回写原文表）

        :param db: 数据库会话
        :param pk: 用户内容译文缓存（译文是视图，不回写原文表） ID
        :param obj: 更新 用户内容译文缓存（译文是视图，不回写原文表）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除用户内容译文缓存（译文是视图，不回写原文表）

        :param db: 数据库会话
        :param pks: 用户内容译文缓存（译文是视图，不回写原文表） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_content_translations_dao: CRUDHasnContentTranslations = CRUDHasnContentTranslations(HasnContentTranslations)
