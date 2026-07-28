from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnAssetBindings
from backend.app.hasn.schema.hasn_asset_bindings import CreateHasnAssetBindingsParam, UpdateHasnAssetBindingsParam


class CRUDHasnAssetBindings(CRUDPlus[HasnAssetBindings]):
    async def get(self, db: AsyncSession, pk: int) -> HasnAssetBindings | None:
        """
        获取逻辑资产与业务资源的权威反向引用

        :param db: 数据库会话
        :param pk: 逻辑资产与业务资源的权威反向引用 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取逻辑资产与业务资源的权威反向引用列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnAssetBindings]:
        """
        获取所有逻辑资产与业务资源的权威反向引用

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnAssetBindingsParam) -> None:
        """
        创建逻辑资产与业务资源的权威反向引用

        :param db: 数据库会话
        :param obj: 创建逻辑资产与业务资源的权威反向引用参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnAssetBindingsParam) -> int:
        """
        更新逻辑资产与业务资源的权威反向引用

        :param db: 数据库会话
        :param pk: 逻辑资产与业务资源的权威反向引用 ID
        :param obj: 更新 逻辑资产与业务资源的权威反向引用参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除逻辑资产与业务资源的权威反向引用

        :param db: 数据库会话
        :param pks: 逻辑资产与业务资源的权威反向引用 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_asset_bindings_dao: CRUDHasnAssetBindings = CRUDHasnAssetBindings(HasnAssetBindings)
