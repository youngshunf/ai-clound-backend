from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.admin.crud.crud_app_version import app_version_dao
from backend.app.admin.model import AppVersion
from backend.app.admin.schema.app_version import CreateAppVersionParam, DeleteAppVersionParam, UpdateAppVersionParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class AppVersionService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> AppVersion:
        """
        获取应用版本

        :param db: 数据库会话
        :param pk: 应用版本 ID
        :return:
        """
        app_version = await app_version_dao.get(db, pk)
        if not app_version:
            raise errors.NotFoundError(msg='应用版本不存在')
        return app_version

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取应用版本列表

        :param db: 数据库会话
        :return:
        """
        app_version_select = await app_version_dao.get_select()
        return await paging_data(db, app_version_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[AppVersion]:
        """
        获取所有应用版本

        :param db: 数据库会话
        :return:
        """
        app_versions = await app_version_dao.get_all(db)
        return app_versions

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateAppVersionParam) -> None:
        """
        创建应用版本

        :param db: 数据库会话
        :param obj: 创建应用版本参数
        :return:
        """
        await app_version_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateAppVersionParam) -> int:
        """
        更新应用版本

        :param db: 数据库会话
        :param pk: 应用版本 ID
        :param obj: 更新应用版本参数
        :return:
        """
        count = await app_version_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteAppVersionParam) -> int:
        """
        删除应用版本

        :param db: 数据库会话
        :param obj: 应用版本 ID 列表
        :return:
        """
        count = await app_version_dao.delete(db, obj.pks)
        return count


app_version_service: AppVersionService = AppVersionService()
