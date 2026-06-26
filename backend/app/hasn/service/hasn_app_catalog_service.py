from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.crud.crud_hasn_app_catalog import hasn_app_catalog_dao
from backend.app.hasn.model import HasnAppCatalog
from backend.app.hasn.schema.hasn_app_catalog import (
    CreateHasnAppCatalogParam,
    DeleteHasnAppCatalogParam,
    UpdateHasnAppCatalogParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnAppCatalogService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnAppCatalog:
        """
        获取AI-Native 应用目录（云端权威）

        :param db: 数据库会话
        :param pk: AI-Native 应用目录（云端权威） ID
        :return:
        """
        hasn_app_catalog = await hasn_app_catalog_dao.get(db, pk)
        if not hasn_app_catalog:
            raise errors.NotFoundError(msg='AI-Native 应用目录（云端权威）不存在')
        return hasn_app_catalog

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取AI-Native 应用目录（云端权威）列表

        :param db: 数据库会话
        :return:
        """
        hasn_app_catalog_select = await hasn_app_catalog_dao.get_select()
        return await paging_data(db, hasn_app_catalog_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnAppCatalog]:
        """
        获取所有AI-Native 应用目录（云端权威）

        :param db: 数据库会话
        :return:
        """
        hasn_app_catalog_list = await hasn_app_catalog_dao.get_all(db)
        return hasn_app_catalog_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnAppCatalogParam) -> None:
        """
        创建AI-Native 应用目录（云端权威）

        :param db: 数据库会话
        :param obj: 创建AI-Native 应用目录（云端权威）参数
        :return:
        """
        await hasn_app_catalog_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnAppCatalogParam) -> int:
        """
        更新AI-Native 应用目录（云端权威）

        :param db: 数据库会话
        :param pk: AI-Native 应用目录（云端权威） ID
        :param obj: 更新AI-Native 应用目录（云端权威）参数
        :return:
        """
        count = await hasn_app_catalog_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def update_config(*, db: AsyncSession, pk: int, config_json: dict) -> int:
        """
        仅更新应用专属平台级配置 JSON（管理端「编辑配置」），不回填整行

        :param db: 数据库会话
        :param pk: AI-Native 应用目录（云端权威） ID
        :param config_json: 应用专属平台级配置 JSON
        :return:
        """
        # 存在性校验：不存在直接 404，避免静默 0 影响（与全字段 update 行为对齐）
        await HasnAppCatalogService.get(db=db, pk=pk)
        count = await hasn_app_catalog_dao.update_config(db, pk, config_json)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnAppCatalogParam) -> int:
        """
        删除AI-Native 应用目录（云端权威）

        :param db: 数据库会话
        :param obj: AI-Native 应用目录（云端权威） ID 列表
        :return:
        """
        count = await hasn_app_catalog_dao.delete(db, obj.pks)
        return count


hasn_app_catalog_service: HasnAppCatalogService = HasnAppCatalogService()
