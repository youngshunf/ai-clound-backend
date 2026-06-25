from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_creator.model import Media
from backend.app.hasn_creator.schema.media import CreateMediaParam, UpdateMediaParam


class CRUDMedia(CRUDPlus[Media]):
    async def get(self, db: AsyncSession, pk: int) -> Media | None:
        """
        获取素材库；配图/封面/视频/模板（私有桶引用）

        :param db: 数据库会话
        :param pk: 素材库；配图/封面/视频/模板（私有桶引用） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取素材库；配图/封面/视频/模板（私有桶引用）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[Media]:
        """
        获取所有素材库；配图/封面/视频/模板（私有桶引用）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateMediaParam) -> None:
        """
        创建素材库；配图/封面/视频/模板（私有桶引用）

        :param db: 数据库会话
        :param obj: 创建素材库；配图/封面/视频/模板（私有桶引用）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateMediaParam) -> int:
        """
        更新素材库；配图/封面/视频/模板（私有桶引用）

        :param db: 数据库会话
        :param pk: 素材库；配图/封面/视频/模板（私有桶引用） ID
        :param obj: 更新 素材库；配图/封面/视频/模板（私有桶引用）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除素材库；配图/封面/视频/模板（私有桶引用）

        :param db: 数据库会话
        :param pks: 素材库；配图/封面/视频/模板（私有桶引用） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


media_dao: CRUDMedia = CRUDMedia(Media)
