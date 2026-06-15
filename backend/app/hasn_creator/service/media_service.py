from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_creator.crud.crud_media import media_dao
from backend.app.hasn_creator.model import Media
from backend.app.hasn_creator.schema.media import CreateMediaParam, DeleteMediaParam, UpdateMediaParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class MediaService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> Media:
        """
        获取素材库；配图/封面/视频/模板（私有桶引用）

        :param db: 数据库会话
        :param pk: 素材库；配图/封面/视频/模板（私有桶引用） ID
        :return:
        """
        media = await media_dao.get(db, pk)
        if not media:
            raise errors.NotFoundError(msg='素材库；配图/封面/视频/模板（私有桶引用）不存在')
        return media

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取素材库；配图/封面/视频/模板（私有桶引用）列表

        :param db: 数据库会话
        :return:
        """
        media_select = await media_dao.get_select()
        return await paging_data(db, media_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[Media]:
        """
        获取所有素材库；配图/封面/视频/模板（私有桶引用）

        :param db: 数据库会话
        :return:
        """
        media_list = await media_dao.get_all(db)
        return media_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateMediaParam) -> None:
        """
        创建素材库；配图/封面/视频/模板（私有桶引用）

        :param db: 数据库会话
        :param obj: 创建素材库；配图/封面/视频/模板（私有桶引用）参数
        :return:
        """
        await media_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateMediaParam) -> int:
        """
        更新素材库；配图/封面/视频/模板（私有桶引用）

        :param db: 数据库会话
        :param pk: 素材库；配图/封面/视频/模板（私有桶引用） ID
        :param obj: 更新素材库；配图/封面/视频/模板（私有桶引用）参数
        :return:
        """
        count = await media_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteMediaParam) -> int:
        """
        删除素材库；配图/封面/视频/模板（私有桶引用）

        :param db: 数据库会话
        :param obj: 素材库；配图/封面/视频/模板（私有桶引用） ID 列表
        :return:
        """
        count = await media_dao.delete(db, obj.pks)
        return count


media_service: MediaService = MediaService()
