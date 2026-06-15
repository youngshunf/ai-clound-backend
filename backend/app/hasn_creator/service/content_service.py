from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_creator.crud.crud_content import content_dao
from backend.app.hasn_creator.model import Content
from backend.app.hasn_creator.schema.content import CreateContentParam, DeleteContentParam, UpdateContentParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class ContentService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> Content:
        """
        获取内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核

        :param db: 数据库会话
        :param pk: 内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核 ID
        :return:
        """
        content = await content_dao.get(db, pk)
        if not content:
            raise errors.NotFoundError(msg='内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核不存在')
        return content

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核列表

        :param db: 数据库会话
        :return:
        """
        content_select = await content_dao.get_select()
        return await paging_data(db, content_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[Content]:
        """
        获取所有内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核

        :param db: 数据库会话
        :return:
        """
        content_list = await content_dao.get_all(db)
        return content_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateContentParam) -> None:
        """
        创建内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核

        :param db: 数据库会话
        :param obj: 创建内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核参数
        :return:
        """
        await content_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateContentParam) -> int:
        """
        更新内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核

        :param db: 数据库会话
        :param pk: 内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核 ID
        :param obj: 更新内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核参数
        :return:
        """
        count = await content_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteContentParam) -> int:
        """
        删除内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核

        :param db: 数据库会话
        :param obj: 内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核 ID 列表
        :return:
        """
        count = await content_dao.delete(db, obj.pks)
        return count


content_service: ContentService = ContentService()
