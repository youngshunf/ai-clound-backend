from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_speech_package import hasn_speech_package_dao
from backend.app.hasn.model import HasnSpeechPackage
from backend.app.hasn.schema.hasn_speech_package import (
    CreateHasnSpeechPackageParam,
    DeleteHasnSpeechPackageParam,
    UpdateHasnSpeechPackageParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnSpeechPackageService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnSpeechPackage:
        """
        获取语音模型不可变内容寻址包登记

        :param db: 数据库会话
        :param pk: 语音模型不可变内容寻址包登记 ID
        :return:
        """
        hasn_speech_package = await hasn_speech_package_dao.get(db, pk)
        if not hasn_speech_package:
            raise errors.NotFoundError(msg='语音模型不可变内容寻址包登记不存在')
        return hasn_speech_package

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取语音模型不可变内容寻址包登记列表

        :param db: 数据库会话
        :return:
        """
        hasn_speech_package_select = await hasn_speech_package_dao.get_select()
        return await paging_data(db, hasn_speech_package_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnSpeechPackage]:
        """
        获取所有语音模型不可变内容寻址包登记

        :param db: 数据库会话
        :return:
        """
        hasn_speech_package_list = await hasn_speech_package_dao.get_all(db)
        return hasn_speech_package_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnSpeechPackageParam) -> None:
        """
        创建语音模型不可变内容寻址包登记

        :param db: 数据库会话
        :param obj: 创建语音模型不可变内容寻址包登记参数
        :return:
        """
        await hasn_speech_package_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnSpeechPackageParam) -> int:
        """
        更新语音模型不可变内容寻址包登记

        :param db: 数据库会话
        :param pk: 语音模型不可变内容寻址包登记 ID
        :param obj: 更新语音模型不可变内容寻址包登记参数
        :return:
        """
        count = await hasn_speech_package_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnSpeechPackageParam) -> int:
        """
        删除语音模型不可变内容寻址包登记

        :param db: 数据库会话
        :param obj: 语音模型不可变内容寻址包登记 ID 列表
        :return:
        """
        count = await hasn_speech_package_dao.delete(db, obj.pks)
        return count


hasn_speech_package_service: HasnSpeechPackageService = HasnSpeechPackageService()
