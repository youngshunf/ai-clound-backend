from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_speech_catalog_release import hasn_speech_catalog_release_dao
from backend.app.hasn.model import HasnSpeechCatalogRelease
from backend.app.hasn.schema.hasn_speech_catalog_release import (
    CreateHasnSpeechCatalogReleaseParam,
    DeleteHasnSpeechCatalogReleaseParam,
    UpdateHasnSpeechCatalogReleaseParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnSpeechCatalogReleaseService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnSpeechCatalogRelease:
        """
        获取语音签名 catalog 不可变发布历史

        :param db: 数据库会话
        :param pk: 语音签名 catalog 不可变发布历史 ID
        :return:
        """
        hasn_speech_catalog_release = await hasn_speech_catalog_release_dao.get(db, pk)
        if not hasn_speech_catalog_release:
            raise errors.NotFoundError(msg='语音签名 catalog 不可变发布历史不存在')
        return hasn_speech_catalog_release

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取语音签名 catalog 不可变发布历史列表

        :param db: 数据库会话
        :return:
        """
        hasn_speech_catalog_release_select = await hasn_speech_catalog_release_dao.get_select()
        return await paging_data(db, hasn_speech_catalog_release_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnSpeechCatalogRelease]:
        """
        获取所有语音签名 catalog 不可变发布历史

        :param db: 数据库会话
        :return:
        """
        hasn_speech_catalog_release_list = await hasn_speech_catalog_release_dao.get_all(db)
        return hasn_speech_catalog_release_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnSpeechCatalogReleaseParam) -> None:
        """
        创建语音签名 catalog 不可变发布历史

        :param db: 数据库会话
        :param obj: 创建语音签名 catalog 不可变发布历史参数
        :return:
        """
        await hasn_speech_catalog_release_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnSpeechCatalogReleaseParam) -> int:
        """
        更新语音签名 catalog 不可变发布历史

        :param db: 数据库会话
        :param pk: 语音签名 catalog 不可变发布历史 ID
        :param obj: 更新语音签名 catalog 不可变发布历史参数
        :return:
        """
        count = await hasn_speech_catalog_release_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnSpeechCatalogReleaseParam) -> int:
        """
        删除语音签名 catalog 不可变发布历史

        :param db: 数据库会话
        :param obj: 语音签名 catalog 不可变发布历史 ID 列表
        :return:
        """
        count = await hasn_speech_catalog_release_dao.delete(db, obj.pks)
        return count


hasn_speech_catalog_release_service: HasnSpeechCatalogReleaseService = HasnSpeechCatalogReleaseService()
