from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnAssetGrants
from backend.app.hasn.schema.hasn_asset_grants import CreateHasnAssetGrantsParam, UpdateHasnAssetGrantsParam


class CRUDHasnAssetGrants(CRUDPlus[HasnAssetGrants]):
    async def get(self, db: AsyncSession, pk: int) -> HasnAssetGrants | None:
        """
        获取HASN 资产授权表（私有资产按会话授予读权，关闭跨 owner 越权）

        :param db: 数据库会话
        :param pk: HASN 资产授权表（私有资产按会话授予读权，关闭跨 owner 越权） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取HASN 资产授权表（私有资产按会话授予读权，关闭跨 owner 越权）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnAssetGrants]:
        """
        获取所有HASN 资产授权表（私有资产按会话授予读权，关闭跨 owner 越权）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnAssetGrantsParam) -> None:
        """
        创建HASN 资产授权表（私有资产按会话授予读权，关闭跨 owner 越权）

        :param db: 数据库会话
        :param obj: 创建HASN 资产授权表（私有资产按会话授予读权，关闭跨 owner 越权）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnAssetGrantsParam) -> int:
        """
        更新HASN 资产授权表（私有资产按会话授予读权，关闭跨 owner 越权）

        :param db: 数据库会话
        :param pk: HASN 资产授权表（私有资产按会话授予读权，关闭跨 owner 越权） ID
        :param obj: 更新 HASN 资产授权表（私有资产按会话授予读权，关闭跨 owner 越权）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除HASN 资产授权表（私有资产按会话授予读权，关闭跨 owner 越权）

        :param db: 数据库会话
        :param pks: HASN 资产授权表（私有资产按会话授予读权，关闭跨 owner 越权） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_asset_grants_dao: CRUDHasnAssetGrants = CRUDHasnAssetGrants(HasnAssetGrants)
