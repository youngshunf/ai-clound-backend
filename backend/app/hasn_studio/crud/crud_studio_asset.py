from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_studio.model import StudioAsset
from backend.app.hasn_studio.schema.studio_asset import CreateStudioAssetParam, UpdateStudioAssetParam


class CRUDStudioAsset(CRUDPlus[StudioAsset]):
    async def get(self, db: AsyncSession, pk: int) -> StudioAsset | None:
        """
        获取视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）

        :param db: 数据库会话
        :param pk: 视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[StudioAsset]:
        """
        获取所有视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateStudioAssetParam) -> None:
        """
        创建视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）

        :param db: 数据库会话
        :param obj: 创建视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateStudioAssetParam) -> int:
        """
        更新视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）

        :param db: 数据库会话
        :param pk: 视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体） ID
        :param obj: 更新 视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体）

        :param db: 数据库会话
        :param pks: 视频素材库（项目的输入素材：脚本/图/音/视频/字幕/配音/配乐/字体） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


studio_asset_dao: CRUDStudioAsset = CRUDStudioAsset(StudioAsset)
