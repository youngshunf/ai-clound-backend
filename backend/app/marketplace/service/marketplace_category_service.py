from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.marketplace.crud.crud_marketplace_category import marketplace_category_dao
from backend.app.marketplace.model import MarketplaceCategory
from backend.app.marketplace.schema.marketplace_category import CreateMarketplaceCategoryParam, DeleteMarketplaceCategoryParam, UpdateMarketplaceCategoryParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class MarketplaceCategoryService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> MarketplaceCategory:
        """
        获取技能市场分类

        :param db: 数据库会话
        :param pk: 技能市场分类 ID
        :return:
        """
        marketplace_category = await marketplace_category_dao.get(db, pk)
        if not marketplace_category:
            raise errors.NotFoundError(msg='技能市场分类不存在')
        return marketplace_category

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取技能市场分类列表

        :param db: 数据库会话
        :return:
        """
        marketplace_category_select = await marketplace_category_dao.get_select()
        return await paging_data(db, marketplace_category_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[MarketplaceCategory]:
        """
        获取所有技能市场分类

        :param db: 数据库会话
        :return:
        """
        marketplace_categorys = await marketplace_category_dao.get_all(db)
        return marketplace_categorys

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateMarketplaceCategoryParam) -> None:
        """
        创建技能市场分类

        :param db: 数据库会话
        :param obj: 创建技能市场分类参数
        :return:
        """
        await marketplace_category_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateMarketplaceCategoryParam) -> int:
        """
        更新技能市场分类

        :param db: 数据库会话
        :param pk: 技能市场分类 ID
        :param obj: 更新技能市场分类参数
        :return:
        """
        count = await marketplace_category_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteMarketplaceCategoryParam) -> int:
        """
        删除技能市场分类

        :param db: 数据库会话
        :param obj: 技能市场分类 ID 列表
        :return:
        """
        count = await marketplace_category_dao.delete(db, obj.pks)
        return count


marketplace_category_service: MarketplaceCategoryService = MarketplaceCategoryService()
