from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_app_entitlement import hasn_app_entitlement_dao
from backend.app.hasn.model import HasnAppEntitlement
from backend.app.hasn.schema.hasn_app_entitlement import (
    CreateHasnAppEntitlementParam,
    DeleteHasnAppEntitlementParam,
    UpdateHasnAppEntitlementParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnAppEntitlementService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnAppEntitlement:
        """
        获取AI-Native 应用权益（云端权威）

        :param db: 数据库会话
        :param pk: AI-Native 应用权益（云端权威） ID
        :return:
        """
        hasn_app_entitlement = await hasn_app_entitlement_dao.get(db, pk)
        if not hasn_app_entitlement:
            raise errors.NotFoundError(msg='AI-Native 应用权益（云端权威）不存在')
        return hasn_app_entitlement

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取AI-Native 应用权益（云端权威）列表

        :param db: 数据库会话
        :return:
        """
        hasn_app_entitlement_select = await hasn_app_entitlement_dao.get_select()
        return await paging_data(db, hasn_app_entitlement_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnAppEntitlement]:
        """
        获取所有AI-Native 应用权益（云端权威）

        :param db: 数据库会话
        :return:
        """
        hasn_app_entitlement_list = await hasn_app_entitlement_dao.get_all(db)
        return hasn_app_entitlement_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnAppEntitlementParam) -> None:
        """
        创建AI-Native 应用权益（云端权威）

        :param db: 数据库会话
        :param obj: 创建AI-Native 应用权益（云端权威）参数
        :return:
        """
        await hasn_app_entitlement_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnAppEntitlementParam) -> int:
        """
        更新AI-Native 应用权益（云端权威）

        :param db: 数据库会话
        :param pk: AI-Native 应用权益（云端权威） ID
        :param obj: 更新AI-Native 应用权益（云端权威）参数
        :return:
        """
        count = await hasn_app_entitlement_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnAppEntitlementParam) -> int:
        """
        删除AI-Native 应用权益（云端权威）

        :param db: 数据库会话
        :param obj: AI-Native 应用权益（云端权威） ID 列表
        :return:
        """
        count = await hasn_app_entitlement_dao.delete(db, obj.pks)
        return count


hasn_app_entitlement_service: HasnAppEntitlementService = HasnAppEntitlementService()
