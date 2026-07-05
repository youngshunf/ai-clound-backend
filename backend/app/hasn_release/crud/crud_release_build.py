from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_release.model import ReleaseBuild
from backend.app.hasn_release.schema.release_build import CreateReleaseBuildParam, UpdateReleaseBuildParam


class CRUDReleaseBuild(CRUDPlus[ReleaseBuild]):
    async def get(self, db: AsyncSession, pk: int) -> ReleaseBuild | None:
        """
        获取CI 构建任务（GitHub Actions 构建进度追踪）

        :param db: 数据库会话
        :param pk: CI 构建任务（GitHub Actions 构建进度追踪） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取CI 构建任务（GitHub Actions 构建进度追踪）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[ReleaseBuild]:
        """
        获取所有CI 构建任务（GitHub Actions 构建进度追踪）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateReleaseBuildParam) -> None:
        """
        创建CI 构建任务（GitHub Actions 构建进度追踪）

        :param db: 数据库会话
        :param obj: 创建CI 构建任务（GitHub Actions 构建进度追踪）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateReleaseBuildParam) -> int:
        """
        更新CI 构建任务（GitHub Actions 构建进度追踪）

        :param db: 数据库会话
        :param pk: CI 构建任务（GitHub Actions 构建进度追踪） ID
        :param obj: 更新 CI 构建任务（GitHub Actions 构建进度追踪）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除CI 构建任务（GitHub Actions 构建进度追踪）

        :param db: 数据库会话
        :param pks: CI 构建任务（GitHub Actions 构建进度追踪） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


release_build_dao: CRUDReleaseBuild = CRUDReleaseBuild(ReleaseBuild)
