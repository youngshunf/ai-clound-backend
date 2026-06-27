from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_reel.model import ReelProject
from backend.app.hasn_reel.schema.reel_project import CreateReelProjectParam, UpdateReelProjectParam


class CRUDReelProject(CRUDPlus[ReelProject]):
    async def get(self, db: AsyncSession, pk: int) -> ReelProject | None:
        """
        获取短视频项目（reel：一组创作的容器 + 默认创作参数）

        :param db: 数据库会话
        :param pk: 短视频项目（reel：一组创作的容器 + 默认创作参数） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取短视频项目（reel：一组创作的容器 + 默认创作参数）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[ReelProject]:
        """
        获取所有短视频项目（reel：一组创作的容器 + 默认创作参数）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateReelProjectParam) -> None:
        """
        创建短视频项目（reel：一组创作的容器 + 默认创作参数）

        :param db: 数据库会话
        :param obj: 创建短视频项目（reel：一组创作的容器 + 默认创作参数）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateReelProjectParam) -> int:
        """
        更新短视频项目（reel：一组创作的容器 + 默认创作参数）

        :param db: 数据库会话
        :param pk: 短视频项目（reel：一组创作的容器 + 默认创作参数） ID
        :param obj: 更新 短视频项目（reel：一组创作的容器 + 默认创作参数）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除短视频项目（reel：一组创作的容器 + 默认创作参数）

        :param db: 数据库会话
        :param pks: 短视频项目（reel：一组创作的容器 + 默认创作参数） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


reel_project_dao: CRUDReelProject = CRUDReelProject(ReelProject)
