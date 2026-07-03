from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_owner_api_keys import hasn_owner_api_keys_dao
from backend.app.hasn.model import HasnOwnerApiKeys
from backend.app.hasn.schema.hasn_owner_api_keys import (
    CreateHasnOwnerApiKeysParam,
    DeleteHasnOwnerApiKeysParam,
    UpdateHasnOwnerApiKeysParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnOwnerApiKeysService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnOwnerApiKeys:
        """
        获取HASN Owner API Key

        :param db: 数据库会话
        :param pk: HASN Owner API Key  ID
        :return:
        """
        hasn_owner_api_keys = await hasn_owner_api_keys_dao.get(db, pk)
        if not hasn_owner_api_keys:
            raise errors.NotFoundError(msg='HASN Owner API Key 不存在')
        return hasn_owner_api_keys

    @staticmethod
    async def get_list(db: AsyncSession, user_id: int | None = None) -> dict[str, Any]:
        """
        获取HASN Owner API Key 列表

        :param db: 数据库会话
        :return:
        """
        hasn_owner_api_keys_select = await hasn_owner_api_keys_dao.get_select()
        if user_id is not None:
            hasn_owner_api_keys_select = hasn_owner_api_keys_select.where(HasnOwnerApiKeys.user_id == user_id)
        return await paging_data(db, hasn_owner_api_keys_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnOwnerApiKeys]:
        """
        获取所有HASN Owner API Key

        :param db: 数据库会话
        :return:
        """
        hasn_owner_api_keyss = await hasn_owner_api_keys_dao.get_all(db)
        return hasn_owner_api_keyss

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnOwnerApiKeysParam, user_id: int | None = None) -> HasnOwnerApiKeys:
        """
        创建HASN Owner API Key

        :param db: 数据库会话
        :param obj: 创建HASN Owner API Key 参数
        :return:
        """
        payload = obj.model_dump()
        if user_id is not None:
            payload['user_id'] = user_id
        await hasn_owner_api_keys_dao.create(db, CreateHasnOwnerApiKeysParam(**payload))
        result = await db.execute(
            select(HasnOwnerApiKeys).where(HasnOwnerApiKeys.key_id == payload['key_id'])
        )
        return result.scalar_one()

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnOwnerApiKeysParam, user_id: int | None = None) -> int:
        """
        更新HASN Owner API Key

        :param db: 数据库会话
        :param pk: HASN Owner API Key  ID
        :param obj: 更新HASN Owner API Key 参数
        :return:
        """
        count = await hasn_owner_api_keys_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnOwnerApiKeysParam) -> int:
        """
        删除HASN Owner API Key

        :param db: 数据库会话
        :param obj: HASN Owner API Key  ID 列表
        :return:
        """
        count = await hasn_owner_api_keys_dao.delete(db, obj.pks)
        return count


hasn_owner_api_keys_service: HasnOwnerApiKeysService = HasnOwnerApiKeysService()
