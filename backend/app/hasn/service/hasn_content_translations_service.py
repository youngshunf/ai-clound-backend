from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_content_translations import hasn_content_translations_dao
from backend.app.hasn.model import HasnContentTranslations
from backend.app.hasn.schema.hasn_content_translations import CreateHasnContentTranslationsParam, DeleteHasnContentTranslationsParam, UpdateHasnContentTranslationsParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnContentTranslationsService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnContentTranslations:
        """
        获取用户内容译文缓存（译文是视图，不回写原文表）

        :param db: 数据库会话
        :param pk: 用户内容译文缓存（译文是视图，不回写原文表） ID
        :return:
        """
        hasn_content_translations = await hasn_content_translations_dao.get(db, pk)
        if not hasn_content_translations:
            raise errors.NotFoundError(msg='用户内容译文缓存（译文是视图，不回写原文表）不存在')
        return hasn_content_translations

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取用户内容译文缓存（译文是视图，不回写原文表）列表

        :param db: 数据库会话
        :return:
        """
        hasn_content_translations_select = await hasn_content_translations_dao.get_select()
        return await paging_data(db, hasn_content_translations_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnContentTranslations]:
        """
        获取所有用户内容译文缓存（译文是视图，不回写原文表）

        :param db: 数据库会话
        :return:
        """
        hasn_content_translations_list = await hasn_content_translations_dao.get_all(db)
        return hasn_content_translations_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnContentTranslationsParam) -> None:
        """
        创建用户内容译文缓存（译文是视图，不回写原文表）

        :param db: 数据库会话
        :param obj: 创建用户内容译文缓存（译文是视图，不回写原文表）参数
        :return:
        """
        await hasn_content_translations_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnContentTranslationsParam) -> int:
        """
        更新用户内容译文缓存（译文是视图，不回写原文表）

        :param db: 数据库会话
        :param pk: 用户内容译文缓存（译文是视图，不回写原文表） ID
        :param obj: 更新用户内容译文缓存（译文是视图，不回写原文表）参数
        :return:
        """
        count = await hasn_content_translations_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnContentTranslationsParam) -> int:
        """
        删除用户内容译文缓存（译文是视图，不回写原文表）

        :param db: 数据库会话
        :param obj: 用户内容译文缓存（译文是视图，不回写原文表） ID 列表
        :return:
        """
        count = await hasn_content_translations_dao.delete(db, obj.pks)
        return count


hasn_content_translations_service: HasnContentTranslationsService = HasnContentTranslationsService()
