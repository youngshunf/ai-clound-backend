from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_growth.model import ContactPrivateProfile
from backend.app.hasn_growth.schema.contact_private_profile import (
    CreateContactPrivateProfileParam,
    UpdateContactPrivateProfileParam,
)


class CRUDContactPrivateProfile(CRUDPlus[ContactPrivateProfile]):
    async def get(self, db: AsyncSession, pk: int) -> ContactPrivateProfile | None:
        """
        获取Owner 或企业对全局联系人的私有资料密文

        :param db: 数据库会话
        :param pk: Owner 或企业对全局联系人的私有资料密文 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取Owner 或企业对全局联系人的私有资料密文列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[ContactPrivateProfile]:
        """
        获取所有Owner 或企业对全局联系人的私有资料密文

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateContactPrivateProfileParam) -> None:
        """
        创建Owner 或企业对全局联系人的私有资料密文

        :param db: 数据库会话
        :param obj: 创建Owner 或企业对全局联系人的私有资料密文参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateContactPrivateProfileParam) -> int:
        """
        更新Owner 或企业对全局联系人的私有资料密文

        :param db: 数据库会话
        :param pk: Owner 或企业对全局联系人的私有资料密文 ID
        :param obj: 更新 Owner 或企业对全局联系人的私有资料密文参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除Owner 或企业对全局联系人的私有资料密文

        :param db: 数据库会话
        :param pks: Owner 或企业对全局联系人的私有资料密文 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


contact_private_profile_dao: CRUDContactPrivateProfile = CRUDContactPrivateProfile(ContactPrivateProfile)
