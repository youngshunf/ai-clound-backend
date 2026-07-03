from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.marketplace.crud.crud_marketplace_template_version import marketplace_template_version_dao
from backend.app.marketplace.model import MarketplaceTemplateVersion
from backend.app.marketplace.schema.marketplace_template_version import (
    CreateMarketplaceTemplateVersionParam,
    DeleteMarketplaceTemplateVersionParam,
    UpdateMarketplaceTemplateVersionParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class MarketplaceTemplateVersionService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> MarketplaceTemplateVersion:
        """
        获取模板版本

        :param db: 数据库会话
        :param pk: 模板版本 ID
        :return:
        """
        marketplace_template_version = await marketplace_template_version_dao.get(db, pk)
        if not marketplace_template_version:
            raise errors.NotFoundError(msg='模板版本不存在')
        return marketplace_template_version

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取模板版本列表

        :param db: 数据库会话
        :return:
        """
        marketplace_template_version_select = await marketplace_template_version_dao.get_select()
        return await paging_data(db, marketplace_template_version_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[MarketplaceTemplateVersion]:
        """
        获取所有模板版本

        :param db: 数据库会话
        :return:
        """
        marketplace_template_version_list = await marketplace_template_version_dao.get_all(db)
        return marketplace_template_version_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateMarketplaceTemplateVersionParam) -> None:
        """
        创建模板版本

        :param db: 数据库会话
        :param obj: 创建模板版本参数
        :return:
        """
        await marketplace_template_version_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateMarketplaceTemplateVersionParam) -> int:
        """
        更新模板版本

        :param db: 数据库会话
        :param pk: 模板版本 ID
        :param obj: 更新模板版本参数
        :return:
        """
        count = await marketplace_template_version_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteMarketplaceTemplateVersionParam) -> int:
        """
        删除模板版本

        :param db: 数据库会话
        :param obj: 模板版本 ID 列表
        :return:
        """
        count = await marketplace_template_version_dao.delete(db, obj.pks)
        return count


marketplace_template_version_service: MarketplaceTemplateVersionService = MarketplaceTemplateVersionService()
