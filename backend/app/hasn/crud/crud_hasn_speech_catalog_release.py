from collections.abc import Sequence
from typing import cast

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnSpeechCatalogRelease
from backend.app.hasn.schema.hasn_speech_catalog_release import (
    CreateHasnSpeechCatalogReleaseParam,
    UpdateHasnSpeechCatalogReleaseParam,
)


class CRUDHasnSpeechCatalogRelease(CRUDPlus[HasnSpeechCatalogRelease]):
    async def get(self, db: AsyncSession, pk: int) -> HasnSpeechCatalogRelease | None:
        """
        获取语音签名 catalog 不可变发布历史

        :param db: 数据库会话
        :param pk: 语音签名 catalog 不可变发布历史 ID
        :return:
        """
        return cast('HasnSpeechCatalogRelease | None', await self.select_model(db, pk))

    async def get_select(self) -> Select:
        """获取语音签名 catalog 不可变发布历史列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnSpeechCatalogRelease]:
        """
        获取所有语音签名 catalog 不可变发布历史

        :param db: 数据库会话
        :return:
        """
        return cast('Sequence[HasnSpeechCatalogRelease]', await self.select_models(db))

    async def create(self, db: AsyncSession, obj: CreateHasnSpeechCatalogReleaseParam) -> None:
        """
        创建语音签名 catalog 不可变发布历史

        :param db: 数据库会话
        :param obj: 创建语音签名 catalog 不可变发布历史参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnSpeechCatalogReleaseParam) -> int:
        """
        更新语音签名 catalog 不可变发布历史

        :param db: 数据库会话
        :param pk: 语音签名 catalog 不可变发布历史 ID
        :param obj: 更新 语音签名 catalog 不可变发布历史参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除语音签名 catalog 不可变发布历史

        :param db: 数据库会话
        :param pks: 语音签名 catalog 不可变发布历史 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_speech_catalog_release_dao: CRUDHasnSpeechCatalogRelease = CRUDHasnSpeechCatalogRelease(HasnSpeechCatalogRelease)
