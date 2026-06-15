from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_creator.crud.crud_content_stage import content_stage_dao
from backend.app.hasn_creator.model import ContentStage
from backend.app.hasn_creator.schema.content_stage import CreateContentStageParam, DeleteContentStageParam, UpdateContentStageParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class ContentStageService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> ContentStage:
        """
        获取阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播

        :param db: 数据库会话
        :param pk: 阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播 ID
        :return:
        """
        content_stage = await content_stage_dao.get(db, pk)
        if not content_stage:
            raise errors.NotFoundError(msg='阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播不存在')
        return content_stage

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播列表

        :param db: 数据库会话
        :return:
        """
        content_stage_select = await content_stage_dao.get_select()
        return await paging_data(db, content_stage_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[ContentStage]:
        """
        获取所有阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播

        :param db: 数据库会话
        :return:
        """
        content_stage_list = await content_stage_dao.get_all(db)
        return content_stage_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateContentStageParam) -> None:
        """
        创建阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播

        :param db: 数据库会话
        :param obj: 创建阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播参数
        :return:
        """
        await content_stage_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateContentStageParam) -> int:
        """
        更新阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播

        :param db: 数据库会话
        :param pk: 阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播 ID
        :param obj: 更新阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播参数
        :return:
        """
        count = await content_stage_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteContentStageParam) -> int:
        """
        删除阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播

        :param db: 数据库会话
        :param obj: 阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播 ID 列表
        :return:
        """
        count = await content_stage_dao.delete(db, obj.pks)
        return count


content_stage_service: ContentStageService = ContentStageService()
