from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_creator.crud.crud_content_insight import content_insight_dao
from backend.app.hasn_creator.model import ContentInsight
from backend.app.hasn_creator.schema.content_insight import (
    CreateContentInsightParam,
    DeleteContentInsightParam,
    UpdateContentInsightParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class ContentInsightService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> ContentInsight:
        """
        获取内容洞察（复盘结构化结论，进化沉淀核心）

        :param db: 数据库会话
        :param pk: 内容洞察（复盘结构化结论，进化沉淀核心） ID
        :return:
        """
        content_insight = await content_insight_dao.get(db, pk)
        if not content_insight:
            raise errors.NotFoundError(msg='内容洞察（复盘结构化结论，进化沉淀核心）不存在')
        return content_insight

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取内容洞察（复盘结构化结论，进化沉淀核心）列表

        :param db: 数据库会话
        :return:
        """
        content_insight_select = await content_insight_dao.get_select()
        return await paging_data(db, content_insight_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[ContentInsight]:
        """
        获取所有内容洞察（复盘结构化结论，进化沉淀核心）

        :param db: 数据库会话
        :return:
        """
        content_insight_list = await content_insight_dao.get_all(db)
        return content_insight_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateContentInsightParam) -> None:
        """
        创建内容洞察（复盘结构化结论，进化沉淀核心）

        :param db: 数据库会话
        :param obj: 创建内容洞察（复盘结构化结论，进化沉淀核心）参数
        :return:
        """
        await content_insight_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateContentInsightParam) -> int:
        """
        更新内容洞察（复盘结构化结论，进化沉淀核心）

        :param db: 数据库会话
        :param pk: 内容洞察（复盘结构化结论，进化沉淀核心） ID
        :param obj: 更新内容洞察（复盘结构化结论，进化沉淀核心）参数
        :return:
        """
        count = await content_insight_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteContentInsightParam) -> int:
        """
        删除内容洞察（复盘结构化结论，进化沉淀核心）

        :param db: 数据库会话
        :param obj: 内容洞察（复盘结构化结论，进化沉淀核心） ID 列表
        :return:
        """
        count = await content_insight_dao.delete(db, obj.pks)
        return count


content_insight_service: ContentInsightService = ContentInsightService()
