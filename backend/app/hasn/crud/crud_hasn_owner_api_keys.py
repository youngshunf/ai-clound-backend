from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnOwnerApiKeys
from backend.app.hasn.schema.hasn_owner_api_keys import CreateHasnOwnerApiKeysParam, UpdateHasnOwnerApiKeysParam


class CRUDHasnOwnerApiKeys(CRUDPlus[HasnOwnerApiKeys]):
    async def get(self, db: AsyncSession, pk: int) -> HasnOwnerApiKeys | None:
        """
        获取HASN Owner API Key

        :param db: 数据库会话
        :param pk: HASN Owner API Key  ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取HASN Owner API Key 列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnOwnerApiKeys]:
        """
        获取所有HASN Owner API Key

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnOwnerApiKeysParam) -> None:
        """
        创建HASN Owner API Key

        :param db: 数据库会话
        :param obj: 创建HASN Owner API Key 参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnOwnerApiKeysParam) -> int:
        """
        更新HASN Owner API Key

        :param db: 数据库会话
        :param pk: HASN Owner API Key  ID
        :param obj: 更新 HASN Owner API Key 参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除HASN Owner API Key

        :param db: 数据库会话
        :param pks: HASN Owner API Key  ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_owner_api_keys_dao: CRUDHasnOwnerApiKeys = CRUDHasnOwnerApiKeys(HasnOwnerApiKeys)
