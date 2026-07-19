from collections.abc import Sequence
from typing import cast

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnSpeechPackage
from backend.app.hasn.schema.hasn_speech_package import CreateHasnSpeechPackageParam, UpdateHasnSpeechPackageParam


class CRUDHasnSpeechPackage(CRUDPlus[HasnSpeechPackage]):
    async def get(self, db: AsyncSession, pk: int) -> HasnSpeechPackage | None:
        """
        获取语音模型不可变内容寻址包登记

        :param db: 数据库会话
        :param pk: 语音模型不可变内容寻址包登记 ID
        :return:
        """
        return cast('HasnSpeechPackage | None', await self.select_model(db, pk))

    async def get_select(self) -> Select:
        """获取语音模型不可变内容寻址包登记列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnSpeechPackage]:
        """
        获取所有语音模型不可变内容寻址包登记

        :param db: 数据库会话
        :return:
        """
        return cast('Sequence[HasnSpeechPackage]', await self.select_models(db))

    async def create(self, db: AsyncSession, obj: CreateHasnSpeechPackageParam) -> None:
        """
        创建语音模型不可变内容寻址包登记

        :param db: 数据库会话
        :param obj: 创建语音模型不可变内容寻址包登记参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnSpeechPackageParam) -> int:
        """
        更新语音模型不可变内容寻址包登记

        :param db: 数据库会话
        :param pk: 语音模型不可变内容寻址包登记 ID
        :param obj: 更新 语音模型不可变内容寻址包登记参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除语音模型不可变内容寻址包登记

        :param db: 数据库会话
        :param pks: 语音模型不可变内容寻址包登记 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_speech_package_dao: CRUDHasnSpeechPackage = CRUDHasnSpeechPackage(HasnSpeechPackage)
