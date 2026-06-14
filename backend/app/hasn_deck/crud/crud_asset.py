from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_deck.model import Asset
from backend.app.hasn_deck.schema.asset import CreateAssetParam, UpdateAssetParam


class CRUDAsset(CRUDPlus[Asset]):
    async def get(self, db: AsyncSession, pk: int) -> Asset | None:
        """
        获取演示文稿资产引用（云端权威；二进制存 public.hasn_assets）

        :param db: 数据库会话
        :param pk: 演示文稿资产引用（云端权威；二进制存 public.hasn_assets） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取演示文稿资产引用（云端权威；二进制存 public.hasn_assets）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[Asset]:
        """
        获取所有演示文稿资产引用（云端权威；二进制存 public.hasn_assets）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateAssetParam) -> None:
        """
        创建演示文稿资产引用（云端权威；二进制存 public.hasn_assets）

        :param db: 数据库会话
        :param obj: 创建演示文稿资产引用（云端权威；二进制存 public.hasn_assets）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateAssetParam) -> int:
        """
        更新演示文稿资产引用（云端权威；二进制存 public.hasn_assets）

        :param db: 数据库会话
        :param pk: 演示文稿资产引用（云端权威；二进制存 public.hasn_assets） ID
        :param obj: 更新 演示文稿资产引用（云端权威；二进制存 public.hasn_assets）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除演示文稿资产引用（云端权威；二进制存 public.hasn_assets）

        :param db: 数据库会话
        :param pks: 演示文稿资产引用（云端权威；二进制存 public.hasn_assets） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


asset_dao: CRUDAsset = CRUDAsset(Asset)
