from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnPlatformOperatorGrants
from backend.app.hasn.schema.hasn_platform_operator_grants import (
    CreateHasnPlatformOperatorGrantsParam,
    UpdateHasnPlatformOperatorGrantsParam,
)


class CRUDHasnPlatformOperatorGrants(CRUDPlus[HasnPlatformOperatorGrants]):
    async def get(self, db: AsyncSession, pk: int) -> HasnPlatformOperatorGrants | None:
        """
        获取平台运维授予源（Admin-only·G1 特权门）

        :param db: 数据库会话
        :param pk: 平台运维授予源（Admin-only·G1 特权门） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取平台运维授予源（Admin-only·G1 特权门）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnPlatformOperatorGrants]:
        """
        获取所有平台运维授予源（Admin-only·G1 特权门）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnPlatformOperatorGrantsParam) -> None:
        """
        创建平台运维授予源（Admin-only·G1 特权门）

        :param db: 数据库会话
        :param obj: 创建平台运维授予源（Admin-only·G1 特权门）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnPlatformOperatorGrantsParam) -> int:
        """
        更新平台运维授予源（Admin-only·G1 特权门）

        :param db: 数据库会话
        :param pk: 平台运维授予源（Admin-only·G1 特权门） ID
        :param obj: 更新 平台运维授予源（Admin-only·G1 特权门）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除平台运维授予源（Admin-only·G1 特权门）

        :param db: 数据库会话
        :param pks: 平台运维授予源（Admin-only·G1 特权门） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_platform_operator_grants_dao: CRUDHasnPlatformOperatorGrants = CRUDHasnPlatformOperatorGrants(HasnPlatformOperatorGrants)
