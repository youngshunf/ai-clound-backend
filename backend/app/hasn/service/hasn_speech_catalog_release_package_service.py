from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_speech_catalog_release_package import hasn_speech_catalog_release_package_dao
from backend.app.hasn.model import HasnSpeechCatalogReleasePackage
from backend.app.hasn.schema.hasn_speech_catalog_release_package import (
    CreateHasnSpeechCatalogReleasePackageParam,
    DeleteHasnSpeechCatalogReleasePackageParam,
    UpdateHasnSpeechCatalogReleasePackageParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnSpeechCatalogReleasePackageService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnSpeechCatalogReleasePackage:
        """
        获取语音 release 平台包与签名元数据快照

        :param db: 数据库会话
        :param pk: 语音 release 平台包与签名元数据快照 ID
        :return:
        """
        hasn_speech_catalog_release_package = await hasn_speech_catalog_release_package_dao.get(db, pk)
        if not hasn_speech_catalog_release_package:
            raise errors.NotFoundError(msg='语音 release 平台包与签名元数据快照不存在')
        return hasn_speech_catalog_release_package

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取语音 release 平台包与签名元数据快照列表

        :param db: 数据库会话
        :return:
        """
        hasn_speech_catalog_release_package_select = await hasn_speech_catalog_release_package_dao.get_select()
        return await paging_data(db, hasn_speech_catalog_release_package_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnSpeechCatalogReleasePackage]:
        """
        获取所有语音 release 平台包与签名元数据快照

        :param db: 数据库会话
        :return:
        """
        hasn_speech_catalog_release_package_list = await hasn_speech_catalog_release_package_dao.get_all(db)
        return hasn_speech_catalog_release_package_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnSpeechCatalogReleasePackageParam) -> None:
        """
        创建语音 release 平台包与签名元数据快照

        :param db: 数据库会话
        :param obj: 创建语音 release 平台包与签名元数据快照参数
        :return:
        """
        await hasn_speech_catalog_release_package_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnSpeechCatalogReleasePackageParam) -> int:
        """
        更新语音 release 平台包与签名元数据快照

        :param db: 数据库会话
        :param pk: 语音 release 平台包与签名元数据快照 ID
        :param obj: 更新语音 release 平台包与签名元数据快照参数
        :return:
        """
        count = await hasn_speech_catalog_release_package_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnSpeechCatalogReleasePackageParam) -> int:
        """
        删除语音 release 平台包与签名元数据快照

        :param db: 数据库会话
        :param obj: 语音 release 平台包与签名元数据快照 ID 列表
        :return:
        """
        count = await hasn_speech_catalog_release_package_dao.delete(db, obj.pks)
        return count


hasn_speech_catalog_release_package_service: HasnSpeechCatalogReleasePackageService = (
    HasnSpeechCatalogReleasePackageService()
)
