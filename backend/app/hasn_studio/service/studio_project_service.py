from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_studio.crud.crud_studio_project import studio_project_dao
from backend.app.hasn_studio.model import StudioProject
from backend.app.hasn_studio.schema.studio_project import (
    CreateStudioProjectParam,
    DeleteStudioProjectParam,
    UpdateStudioProjectParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class StudioProjectService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> StudioProject:
        """
        获取视频项目（统一视频引擎 studio：管线/素材/成品的容器）

        :param db: 数据库会话
        :param pk: 视频项目（统一视频引擎 studio：管线/素材/成品的容器） ID
        :return:
        """
        studio_project = await studio_project_dao.get(db, pk)
        if not studio_project:
            raise errors.NotFoundError(msg='视频项目（统一视频引擎 studio：管线/素材/成品的容器）不存在')
        return studio_project

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取视频项目（统一视频引擎 studio：管线/素材/成品的容器）列表

        :param db: 数据库会话
        :return:
        """
        studio_project_select = await studio_project_dao.get_select()
        return await paging_data(db, studio_project_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[StudioProject]:
        """
        获取所有视频项目（统一视频引擎 studio：管线/素材/成品的容器）

        :param db: 数据库会话
        :return:
        """
        studio_project_list = await studio_project_dao.get_all(db)
        return studio_project_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateStudioProjectParam) -> None:
        """
        创建视频项目（统一视频引擎 studio：管线/素材/成品的容器）

        :param db: 数据库会话
        :param obj: 创建视频项目（统一视频引擎 studio：管线/素材/成品的容器）参数
        :return:
        """
        await studio_project_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateStudioProjectParam) -> int:
        """
        更新视频项目（统一视频引擎 studio：管线/素材/成品的容器）

        :param db: 数据库会话
        :param pk: 视频项目（统一视频引擎 studio：管线/素材/成品的容器） ID
        :param obj: 更新视频项目（统一视频引擎 studio：管线/素材/成品的容器）参数
        :return:
        """
        count = await studio_project_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteStudioProjectParam) -> int:
        """
        删除视频项目（统一视频引擎 studio：管线/素材/成品的容器）

        :param db: 数据库会话
        :param obj: 视频项目（统一视频引擎 studio：管线/素材/成品的容器） ID 列表
        :return:
        """
        count = await studio_project_dao.delete(db, obj.pks)
        return count


studio_project_service: StudioProjectService = StudioProjectService()
