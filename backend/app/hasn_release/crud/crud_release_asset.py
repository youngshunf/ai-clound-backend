from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_release.model import ReleaseAsset
from backend.app.hasn_release.schema.release_asset import CreateReleaseAssetParam, UpdateReleaseAssetParam


class CRUDReleaseAsset(CRUDPlus[ReleaseAsset]):
    async def get(self, db: AsyncSession, pk: int) -> ReleaseAsset | None:
        """
        获取发布资产（平台×包类型：installer=dmg下载页 / updater=app.tar.gz热更新）

        :param db: 数据库会话
        :param pk: 发布资产（平台×包类型：installer=dmg下载页 / updater=app.tar.gz热更新） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取发布资产（平台×包类型：installer=dmg下载页 / updater=app.tar.gz热更新）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[ReleaseAsset]:
        """
        获取所有发布资产（平台×包类型：installer=dmg下载页 / updater=app.tar.gz热更新）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateReleaseAssetParam) -> None:
        """
        创建发布资产（平台×包类型：installer=dmg下载页 / updater=app.tar.gz热更新）

        :param db: 数据库会话
        :param obj: 创建发布资产（平台×包类型：installer=dmg下载页 / updater=app.tar.gz热更新）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateReleaseAssetParam) -> int:
        """
        更新发布资产（平台×包类型：installer=dmg下载页 / updater=app.tar.gz热更新）

        :param db: 数据库会话
        :param pk: 发布资产（平台×包类型：installer=dmg下载页 / updater=app.tar.gz热更新） ID
        :param obj: 更新 发布资产（平台×包类型：installer=dmg下载页 / updater=app.tar.gz热更新）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除发布资产（平台×包类型：installer=dmg下载页 / updater=app.tar.gz热更新）

        :param db: 数据库会话
        :param pks: 发布资产（平台×包类型：installer=dmg下载页 / updater=app.tar.gz热更新） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


release_asset_dao: CRUDReleaseAsset = CRUDReleaseAsset(ReleaseAsset)
