from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.marketplace.crud.crud_marketplace_app import marketplace_app_dao
from backend.app.marketplace.model import MarketplaceApp
from backend.app.marketplace.schema.marketplace_app import CreateMarketplaceAppParam, DeleteMarketplaceAppParam, UpdateMarketplaceAppParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class MarketplaceAppService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> MarketplaceApp:
        """
        获取技能市场应用

        :param db: 数据库会话
        :param pk: 技能市场应用 ID
        :return:
        """
        marketplace_app = await marketplace_app_dao.get(db, pk)
        if not marketplace_app:
            raise errors.NotFoundError(msg='技能市场应用不存在')
        return marketplace_app

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取技能市场应用列表

        :param db: 数据库会话
        :return:
        """
        marketplace_app_select = await marketplace_app_dao.get_select()
        return await paging_data(db, marketplace_app_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[MarketplaceApp]:
        """
        获取所有技能市场应用

        :param db: 数据库会话
        :return:
        """
        marketplace_apps = await marketplace_app_dao.get_all(db)
        return marketplace_apps

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateMarketplaceAppParam) -> None:
        """
        创建技能市场应用

        :param db: 数据库会话
        :param obj: 创建技能市场应用参数
        :return:
        """
        await marketplace_app_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateMarketplaceAppParam) -> int:
        """
        更新技能市场应用

        :param db: 数据库会话
        :param pk: 技能市场应用 ID
        :param obj: 更新技能市场应用参数
        :return:
        """
        count = await marketplace_app_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteMarketplaceAppParam) -> int:
        """
        删除技能市场应用

        :param db: 数据库会话
        :param obj: 技能市场应用 ID 列表
        :return:
        """
        count = await marketplace_app_dao.delete(db, obj.pks)
        return count


marketplace_app_service: MarketplaceAppService = MarketplaceAppService()
