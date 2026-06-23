from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_studio.model import StudioProject
from backend.app.hasn_studio.schema.studio_project import CreateStudioProjectParam, UpdateStudioProjectParam


class CRUDStudioProject(CRUDPlus[StudioProject]):
    async def get(self, db: AsyncSession, pk: int) -> StudioProject | None:
        """
        获取视频项目（统一视频引擎 studio：管线/素材/成品的容器）

        :param db: 数据库会话
        :param pk: 视频项目（统一视频引擎 studio：管线/素材/成品的容器） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取视频项目（统一视频引擎 studio：管线/素材/成品的容器）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[StudioProject]:
        """
        获取所有视频项目（统一视频引擎 studio：管线/素材/成品的容器）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateStudioProjectParam) -> None:
        """
        创建视频项目（统一视频引擎 studio：管线/素材/成品的容器）

        :param db: 数据库会话
        :param obj: 创建视频项目（统一视频引擎 studio：管线/素材/成品的容器）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateStudioProjectParam) -> int:
        """
        更新视频项目（统一视频引擎 studio：管线/素材/成品的容器）

        :param db: 数据库会话
        :param pk: 视频项目（统一视频引擎 studio：管线/素材/成品的容器） ID
        :param obj: 更新 视频项目（统一视频引擎 studio：管线/素材/成品的容器）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除视频项目（统一视频引擎 studio：管线/素材/成品的容器）

        :param db: 数据库会话
        :param pks: 视频项目（统一视频引擎 studio：管线/素材/成品的容器） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


studio_project_dao: CRUDStudioProject = CRUDStudioProject(StudioProject)
