from uuid import UUID

from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_hosting.model import HasnNodeAuthorizationCodes
from backend.app.hasn_hosting.schema.hasn_node_authorization_codes import CreateHasnNodeAuthorizationCodesParam, UpdateHasnNodeAuthorizationCodesParam


class CRUDHasnNodeAuthorizationCodes(CRUDPlus[HasnNodeAuthorizationCodes]):
    async def get(self, db: AsyncSession, pk: UUID) -> HasnNodeAuthorizationCodes | None:
        """
        获取云端节点设备授权码

        :param db: 数据库会话
        :param pk: 云端节点设备授权码 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取云端节点设备授权码列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnNodeAuthorizationCodes]:
        """
        获取所有云端节点设备授权码

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnNodeAuthorizationCodesParam) -> None:
        """
        创建云端节点设备授权码

        :param db: 数据库会话
        :param obj: 创建云端节点设备授权码参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: UUID, obj: UpdateHasnNodeAuthorizationCodesParam) -> int:
        """
        更新云端节点设备授权码

        :param db: 数据库会话
        :param pk: 云端节点设备授权码 ID
        :param obj: 更新 云端节点设备授权码参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[UUID]) -> int:
        """
        批量删除云端节点设备授权码

        :param db: 数据库会话
        :param pks: 云端节点设备授权码 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_node_authorization_codes_dao: CRUDHasnNodeAuthorizationCodes = CRUDHasnNodeAuthorizationCodes(HasnNodeAuthorizationCodes)
