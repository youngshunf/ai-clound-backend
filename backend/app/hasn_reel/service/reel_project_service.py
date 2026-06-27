from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_reel.crud.crud_reel_project import reel_project_dao
from backend.app.hasn_reel.model import ReelProject
from backend.app.hasn_reel.schema.reel_project import CreateReelProjectParam, DeleteReelProjectParam, UpdateReelProjectParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class ReelProjectService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> ReelProject:
        """
        获取短视频项目（reel：一组创作的容器 + 默认创作参数）

        :param db: 数据库会话
        :param pk: 短视频项目（reel：一组创作的容器 + 默认创作参数） ID
        :return:
        """
        reel_project = await reel_project_dao.get(db, pk)
        if not reel_project:
            raise errors.NotFoundError(msg='短视频项目（reel：一组创作的容器 + 默认创作参数）不存在')
        return reel_project

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取短视频项目（reel：一组创作的容器 + 默认创作参数）列表

        :param db: 数据库会话
        :return:
        """
        reel_project_select = await reel_project_dao.get_select()
        return await paging_data(db, reel_project_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[ReelProject]:
        """
        获取所有短视频项目（reel：一组创作的容器 + 默认创作参数）

        :param db: 数据库会话
        :return:
        """
        reel_project_list = await reel_project_dao.get_all(db)
        return reel_project_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateReelProjectParam) -> None:
        """
        创建短视频项目（reel：一组创作的容器 + 默认创作参数）

        :param db: 数据库会话
        :param obj: 创建短视频项目（reel：一组创作的容器 + 默认创作参数）参数
        :return:
        """
        await reel_project_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateReelProjectParam) -> int:
        """
        更新短视频项目（reel：一组创作的容器 + 默认创作参数）

        :param db: 数据库会话
        :param pk: 短视频项目（reel：一组创作的容器 + 默认创作参数） ID
        :param obj: 更新短视频项目（reel：一组创作的容器 + 默认创作参数）参数
        :return:
        """
        count = await reel_project_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteReelProjectParam) -> int:
        """
        删除短视频项目（reel：一组创作的容器 + 默认创作参数）

        :param db: 数据库会话
        :param obj: 短视频项目（reel：一组创作的容器 + 默认创作参数） ID 列表
        :return:
        """
        count = await reel_project_dao.delete(db, obj.pks)
        return count


reel_project_service: ReelProjectService = ReelProjectService()
