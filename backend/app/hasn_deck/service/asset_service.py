from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_deck.crud.crud_asset import asset_dao
from backend.app.hasn_deck.model import Asset
from backend.app.hasn_deck.schema.asset import CreateAssetParam, DeleteAssetParam, UpdateAssetParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class AssetService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> Asset:
        """
        获取演示文稿资产引用（云端权威；二进制存 public.hasn_assets）

        :param db: 数据库会话
        :param pk: 演示文稿资产引用（云端权威；二进制存 public.hasn_assets） ID
        :return:
        """
        asset = await asset_dao.get(db, pk)
        if not asset:
            raise errors.NotFoundError(msg='演示文稿资产引用（云端权威；二进制存 public.hasn_assets）不存在')
        return asset

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取演示文稿资产引用（云端权威；二进制存 public.hasn_assets）列表

        :param db: 数据库会话
        :return:
        """
        asset_select = await asset_dao.get_select()
        return await paging_data(db, asset_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[Asset]:
        """
        获取所有演示文稿资产引用（云端权威；二进制存 public.hasn_assets）

        :param db: 数据库会话
        :return:
        """
        asset_list = await asset_dao.get_all(db)
        return asset_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateAssetParam) -> None:
        """
        创建演示文稿资产引用（云端权威；二进制存 public.hasn_assets）

        :param db: 数据库会话
        :param obj: 创建演示文稿资产引用（云端权威；二进制存 public.hasn_assets）参数
        :return:
        """
        await asset_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateAssetParam) -> int:
        """
        更新演示文稿资产引用（云端权威；二进制存 public.hasn_assets）

        :param db: 数据库会话
        :param pk: 演示文稿资产引用（云端权威；二进制存 public.hasn_assets） ID
        :param obj: 更新演示文稿资产引用（云端权威；二进制存 public.hasn_assets）参数
        :return:
        """
        count = await asset_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteAssetParam) -> int:
        """
        删除演示文稿资产引用（云端权威；二进制存 public.hasn_assets）

        :param db: 数据库会话
        :param obj: 演示文稿资产引用（云端权威；二进制存 public.hasn_assets） ID 列表
        :return:
        """
        count = await asset_dao.delete(db, obj.pks)
        return count


asset_service: AssetService = AssetService()
