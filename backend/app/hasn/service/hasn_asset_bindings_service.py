from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_asset_bindings import hasn_asset_bindings_dao
from backend.app.hasn.model import HasnAssetBindings
from backend.app.hasn.schema.hasn_asset_bindings import CreateHasnAssetBindingsParam, DeleteHasnAssetBindingsParam, UpdateHasnAssetBindingsParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnAssetBindingsService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnAssetBindings:
        """
        获取逻辑资产与业务资源的权威反向引用

        :param db: 数据库会话
        :param pk: 逻辑资产与业务资源的权威反向引用 ID
        :return:
        """
        hasn_asset_bindings = await hasn_asset_bindings_dao.get(db, pk)
        if not hasn_asset_bindings:
            raise errors.NotFoundError(msg='逻辑资产与业务资源的权威反向引用不存在')
        return hasn_asset_bindings

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取逻辑资产与业务资源的权威反向引用列表

        :param db: 数据库会话
        :return:
        """
        hasn_asset_bindings_select = await hasn_asset_bindings_dao.get_select()
        return await paging_data(db, hasn_asset_bindings_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnAssetBindings]:
        """
        获取所有逻辑资产与业务资源的权威反向引用

        :param db: 数据库会话
        :return:
        """
        hasn_asset_bindings_list = await hasn_asset_bindings_dao.get_all(db)
        return hasn_asset_bindings_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnAssetBindingsParam) -> None:
        """
        创建逻辑资产与业务资源的权威反向引用

        :param db: 数据库会话
        :param obj: 创建逻辑资产与业务资源的权威反向引用参数
        :return:
        """
        await hasn_asset_bindings_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnAssetBindingsParam) -> int:
        """
        更新逻辑资产与业务资源的权威反向引用

        :param db: 数据库会话
        :param pk: 逻辑资产与业务资源的权威反向引用 ID
        :param obj: 更新逻辑资产与业务资源的权威反向引用参数
        :return:
        """
        count = await hasn_asset_bindings_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnAssetBindingsParam) -> int:
        """
        删除逻辑资产与业务资源的权威反向引用

        :param db: 数据库会话
        :param obj: 逻辑资产与业务资源的权威反向引用 ID 列表
        :return:
        """
        count = await hasn_asset_bindings_dao.delete(db, obj.pks)
        return count


hasn_asset_bindings_service: HasnAssetBindingsService = HasnAssetBindingsService()
