from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.marketplace.crud.crud_marketplace_download import marketplace_download_dao
from backend.app.marketplace.model import MarketplaceDownload
from backend.app.marketplace.schema.marketplace_download import CreateMarketplaceDownloadParam, DeleteMarketplaceDownloadParam, UpdateMarketplaceDownloadParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class MarketplaceDownloadService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> MarketplaceDownload:
        """
        获取用户下载记录

        :param db: 数据库会话
        :param pk: 用户下载记录 ID
        :return:
        """
        marketplace_download = await marketplace_download_dao.get(db, pk)
        if not marketplace_download:
            raise errors.NotFoundError(msg='用户下载记录不存在')
        return marketplace_download

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取用户下载记录列表

        :param db: 数据库会话
        :return:
        """
        marketplace_download_select = await marketplace_download_dao.get_select()
        return await paging_data(db, marketplace_download_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[MarketplaceDownload]:
        """
        获取所有用户下载记录

        :param db: 数据库会话
        :return:
        """
        marketplace_downloads = await marketplace_download_dao.get_all(db)
        return marketplace_downloads

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateMarketplaceDownloadParam) -> None:
        """
        创建用户下载记录

        :param db: 数据库会话
        :param obj: 创建用户下载记录参数
        :return:
        """
        await marketplace_download_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateMarketplaceDownloadParam) -> int:
        """
        更新用户下载记录

        :param db: 数据库会话
        :param pk: 用户下载记录 ID
        :param obj: 更新用户下载记录参数
        :return:
        """
        count = await marketplace_download_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteMarketplaceDownloadParam) -> int:
        """
        删除用户下载记录

        :param db: 数据库会话
        :param obj: 用户下载记录 ID 列表
        :return:
        """
        count = await marketplace_download_dao.delete(db, obj.pks)
        return count


marketplace_download_service: MarketplaceDownloadService = MarketplaceDownloadService()
