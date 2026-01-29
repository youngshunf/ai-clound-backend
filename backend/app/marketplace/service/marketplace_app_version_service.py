from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.marketplace.crud.crud_marketplace_app_version import marketplace_app_version_dao
from backend.app.marketplace.model import MarketplaceAppVersion
from backend.app.marketplace.schema.marketplace_app_version import CreateMarketplaceAppVersionParam, DeleteMarketplaceAppVersionParam, UpdateMarketplaceAppVersionParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class MarketplaceAppVersionService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> MarketplaceAppVersion:
        """
        获取应用版本

        :param db: 数据库会话
        :param pk: 应用版本 ID
        :return:
        """
        marketplace_app_version = await marketplace_app_version_dao.get(db, pk)
        if not marketplace_app_version:
            raise errors.NotFoundError(msg='应用版本不存在')
        return marketplace_app_version

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取应用版本列表

        :param db: 数据库会话
        :return:
        """
        marketplace_app_version_select = await marketplace_app_version_dao.get_select()
        return await paging_data(db, marketplace_app_version_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[MarketplaceAppVersion]:
        """
        获取所有应用版本

        :param db: 数据库会话
        :return:
        """
        marketplace_app_versions = await marketplace_app_version_dao.get_all(db)
        return marketplace_app_versions

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateMarketplaceAppVersionParam) -> None:
        """
        创建应用版本

        :param db: 数据库会话
        :param obj: 创建应用版本参数
        :return:
        """
        await marketplace_app_version_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateMarketplaceAppVersionParam) -> int:
        """
        更新应用版本

        :param db: 数据库会话
        :param pk: 应用版本 ID
        :param obj: 更新应用版本参数
        :return:
        """
        count = await marketplace_app_version_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteMarketplaceAppVersionParam) -> int:
        """
        删除应用版本

        :param db: 数据库会话
        :param obj: 应用版本 ID 列表
        :return:
        """
        count = await marketplace_app_version_dao.delete(db, obj.pks)
        return count


marketplace_app_version_service: MarketplaceAppVersionService = MarketplaceAppVersionService()
