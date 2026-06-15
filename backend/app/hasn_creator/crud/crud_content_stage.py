from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_creator.model import ContentStage
from backend.app.hasn_creator.schema.content_stage import CreateContentStageParam, UpdateContentStageParam


class CRUDContentStage(CRUDPlus[ContentStage]):
    async def get(self, db: AsyncSession, pk: int) -> ContentStage | None:
        """
        获取阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播

        :param db: 数据库会话
        :param pk: 阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[ContentStage]:
        """
        获取所有阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateContentStageParam) -> None:
        """
        创建阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播

        :param db: 数据库会话
        :param obj: 创建阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateContentStageParam) -> int:
        """
        更新阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播

        :param db: 数据库会话
        :param pk: 阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播 ID
        :param obj: 更新 阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播

        :param db: 数据库会话
        :param pks: 阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


content_stage_dao: CRUDContentStage = CRUDContentStage(ContentStage)
