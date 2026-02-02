from typing import Sequence

from sqlalchemy import Select, select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.admin.model import AppVersion
from backend.app.admin.schema.app_version import CreateAppVersionParam, UpdateAppVersionParam


class CRUDAppVersion(CRUDPlus[AppVersion]):
    async def get(self, db: AsyncSession, pk: int) -> AppVersion | None:
        """
        获取应用版本

        :param db: 数据库会话
        :param pk: 应用版本 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_latest_published(
        self, 
        db: AsyncSession, 
        app_code: str,
        platform: str,
        arch: str,
    ) -> AppVersion | None:
        """
        获取指定应用、平台、架构的最新发布版本

        :param db: 数据库会话
        :param app_code: 应用标识
        :param platform: 平台 (darwin/win32/linux)
        :param arch: 架构 (x64/arm64)
        :return:
        """
        stmt = (
            select(AppVersion)
            .where(
                AppVersion.app_code == app_code,
                AppVersion.platform == platform,
                AppVersion.arch == arch,
                AppVersion.status == 'published',
            )
            .order_by(desc(AppVersion.published_at))
            .limit(1)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_manifest(
        self, 
        db: AsyncSession, 
        app_code: str,
        version: str,
    ) -> Sequence[AppVersion]:
        """
        获取指定版本的所有平台二进制文件信息

        :param db: 数据库会话
        :param app_code: 应用标识
        :param version: 版本号
        :return:
        """
        stmt = (
            select(AppVersion)
            .where(
                AppVersion.app_code == app_code,
                AppVersion.version == version,
                AppVersion.status == 'published',
            )
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    async def get_select(self) -> Select:
        """获取应用版本列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[AppVersion]:
        """
        获取所有应用版本

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateAppVersionParam) -> None:
        """
        创建应用版本

        :param db: 数据库会话
        :param obj: 创建应用版本参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateAppVersionParam) -> int:
        """
        更新应用版本

        :param db: 数据库会话
        :param pk: 应用版本 ID
        :param obj: 更新 应用版本参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除应用版本

        :param db: 数据库会话
        :param pks: 应用版本 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


app_version_dao: CRUDAppVersion = CRUDAppVersion(AppVersion)
