from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.deck.crud.crud_page import page_dao
from backend.app.deck.model import Page
from backend.app.deck.schema.page import CreatePageParam, DeletePageParam, UpdatePageParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class PageService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> Page:
        """
        获取演示文稿幻灯片（云端权威）

        :param db: 数据库会话
        :param pk: 演示文稿幻灯片（云端权威） ID
        :return:
        """
        page = await page_dao.get(db, pk)
        if not page:
            raise errors.NotFoundError(msg='演示文稿幻灯片（云端权威）不存在')
        return page

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取演示文稿幻灯片（云端权威）列表

        :param db: 数据库会话
        :return:
        """
        page_select = await page_dao.get_select()
        return await paging_data(db, page_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[Page]:
        """
        获取所有演示文稿幻灯片（云端权威）

        :param db: 数据库会话
        :return:
        """
        page_list = await page_dao.get_all(db)
        return page_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreatePageParam) -> None:
        """
        创建演示文稿幻灯片（云端权威）

        :param db: 数据库会话
        :param obj: 创建演示文稿幻灯片（云端权威）参数
        :return:
        """
        await page_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdatePageParam) -> int:
        """
        更新演示文稿幻灯片（云端权威）

        :param db: 数据库会话
        :param pk: 演示文稿幻灯片（云端权威） ID
        :param obj: 更新演示文稿幻灯片（云端权威）参数
        :return:
        """
        count = await page_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeletePageParam) -> int:
        """
        删除演示文稿幻灯片（云端权威）

        :param db: 数据库会话
        :param obj: 演示文稿幻灯片（云端权威） ID 列表
        :return:
        """
        count = await page_dao.delete(db, obj.pks)
        return count


page_service: PageService = PageService()
