from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_creator.crud.crud_project import project_dao
from backend.app.hasn_creator.model import Project
from backend.app.hasn_creator.schema.project import CreateProjectParam, DeleteProjectParam, UpdateProjectParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class ProjectService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> Project:
        """
        获取运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度

        :param db: 数据库会话
        :param pk: 运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度 ID
        :return:
        """
        project = await project_dao.get(db, pk)
        if not project:
            raise errors.NotFoundError(msg='运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度不存在')
        return project

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度列表

        :param db: 数据库会话
        :return:
        """
        project_select = await project_dao.get_select()
        return await paging_data(db, project_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[Project]:
        """
        获取所有运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度

        :param db: 数据库会话
        :return:
        """
        project_list = await project_dao.get_all(db)
        return project_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateProjectParam) -> None:
        """
        创建运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度

        :param db: 数据库会话
        :param obj: 创建运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度参数
        :return:
        """
        await project_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateProjectParam) -> int:
        """
        更新运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度

        :param db: 数据库会话
        :param pk: 运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度 ID
        :param obj: 更新运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度参数
        :return:
        """
        count = await project_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteProjectParam) -> int:
        """
        删除运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度

        :param db: 数据库会话
        :param obj: 运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度 ID 列表
        :return:
        """
        count = await project_dao.delete(db, obj.pks)
        return count


project_service: ProjectService = ProjectService()
