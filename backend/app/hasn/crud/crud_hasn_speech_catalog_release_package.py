from collections.abc import Sequence
from typing import cast

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnSpeechCatalogReleasePackage
from backend.app.hasn.schema.hasn_speech_catalog_release_package import (
    CreateHasnSpeechCatalogReleasePackageParam,
    UpdateHasnSpeechCatalogReleasePackageParam,
)


class CRUDHasnSpeechCatalogReleasePackage(CRUDPlus[HasnSpeechCatalogReleasePackage]):
    async def get(self, db: AsyncSession, pk: int) -> HasnSpeechCatalogReleasePackage | None:
        """
        获取语音 release 平台包与签名元数据快照

        :param db: 数据库会话
        :param pk: 语音 release 平台包与签名元数据快照 ID
        :return:
        """
        return cast('HasnSpeechCatalogReleasePackage | None', await self.select_model(db, pk))

    async def get_select(self) -> Select:
        """获取语音 release 平台包与签名元数据快照列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnSpeechCatalogReleasePackage]:
        """
        获取所有语音 release 平台包与签名元数据快照

        :param db: 数据库会话
        :return:
        """
        return cast('Sequence[HasnSpeechCatalogReleasePackage]', await self.select_models(db))

    async def create(self, db: AsyncSession, obj: CreateHasnSpeechCatalogReleasePackageParam) -> None:
        """
        创建语音 release 平台包与签名元数据快照

        :param db: 数据库会话
        :param obj: 创建语音 release 平台包与签名元数据快照参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnSpeechCatalogReleasePackageParam) -> int:
        """
        更新语音 release 平台包与签名元数据快照

        :param db: 数据库会话
        :param pk: 语音 release 平台包与签名元数据快照 ID
        :param obj: 更新 语音 release 平台包与签名元数据快照参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除语音 release 平台包与签名元数据快照

        :param db: 数据库会话
        :param pks: 语音 release 平台包与签名元数据快照 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_speech_catalog_release_package_dao: CRUDHasnSpeechCatalogReleasePackage = CRUDHasnSpeechCatalogReleasePackage(
    HasnSpeechCatalogReleasePackage
)
