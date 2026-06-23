from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_studio.crud.crud_studio_asset import studio_asset_dao
from backend.app.hasn_studio.model import StudioAsset
from backend.app.hasn_studio.schema.studio_asset import (
    CreateStudioAssetParam,
    DeleteStudioAssetParam,
    UpdateStudioAssetParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class StudioAssetService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> StudioAsset:
        """
        获取视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）

        :param db: 数据库会话
        :param pk: 视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体） ID
        :return:
        """
        studio_asset = await studio_asset_dao.get(db, pk)
        if not studio_asset:
            raise errors.NotFoundError(msg='视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）不存在')
        return studio_asset

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）列表

        :param db: 数据库会话
        :return:
        """
        studio_asset_select = await studio_asset_dao.get_select()
        return await paging_data(db, studio_asset_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[StudioAsset]:
        """
        获取所有视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）

        :param db: 数据库会话
        :return:
        """
        studio_asset_list = await studio_asset_dao.get_all(db)
        return studio_asset_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateStudioAssetParam) -> None:
        """
        创建视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）

        :param db: 数据库会话
        :param obj: 创建视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）参数
        :return:
        """
        await studio_asset_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateStudioAssetParam) -> int:
        """
        更新视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）

        :param db: 数据库会话
        :param pk: 视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体） ID
        :param obj: 更新视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）参数
        :return:
        """
        count = await studio_asset_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteStudioAssetParam) -> int:
        """
        删除视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）

        :param db: 数据库会话
        :param obj: 视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体） ID 列表
        :return:
        """
        count = await studio_asset_dao.delete(db, obj.pks)
        return count


studio_asset_service: StudioAssetService = StudioAssetService()
