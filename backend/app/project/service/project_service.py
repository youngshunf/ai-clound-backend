"""项目业务服务类"""

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.project.crud.crud_project import project_dao
from backend.app.project.model.project import Project
from backend.app.project.schema.project import ProjectCreate, ProjectUpdate
from backend.common.exception import errors
from backend.common.pagination import paging_data


class ProjectService:
    """项目服务类"""

    @staticmethod
    async def get(*, db: AsyncSession, project_id: str) -> Project:
        """
        获取项目详情

        :param db: 数据库会话
        :param project_id: 项目 ID (UUID)
        :return:
        """
        project = await project_dao.get(db, project_id)
        if not project or project.is_deleted:
            raise errors.NotFoundError(msg='项目不存在')
        return project

    @staticmethod
    async def get_list(
        *,
        db: AsyncSession,
        user_id: str,
        name: str | None = None,
        industry: str | None = None,
    ):
        """
        获取项目列表

        :param db: 数据库会话
        :param user_id: 用户 ID
        :param name: 项目名称
        :param industry: 行业
        :return:
        """
        select_stmt = await project_dao.get_list_by_user(db, user_id, name, industry)
        page_data = await paging_data(db, select_stmt)
        return page_data

    @staticmethod
    async def get_default(*, db: AsyncSession, user_id: str) -> Project | None:
        """
        获取用户默认项目

        :param db: 数据库会话
        :param user_id: 用户 ID
        :return:
        """
        return await project_dao.get_default_project(db, user_id)

    @staticmethod
    async def create(*, db: AsyncSession, obj: ProjectCreate, user_id: str) -> Project:
        """
        创建项目

        :param db: 数据库会话
        :param obj: 创建项目参数
        :param user_id: 用户 ID
        :return:
        """
        return await project_dao.create(db, obj, user_id)

    @staticmethod
    async def update(*, db: AsyncSession, project_id: str, obj: ProjectUpdate, user_id: str) -> int:
        """
        更新项目

        :param db: 数据库会话
        :param project_id: 项目 ID
        :param obj: 更新项目参数
        :param user_id: 用户 ID
        :return:
        """
        # 验证项目归属
        project = await project_dao.get(db, project_id)
        if not project or project.is_deleted:
            raise errors.NotFoundError(msg='项目不存在')
        if project.user_id != user_id:
            raise errors.ForbiddenError(msg='无权操作此项目')

        return await project_dao.update(db, project_id, obj)

    @staticmethod
    async def set_default(*, db: AsyncSession, project_id: str, user_id: str) -> int:
        """
        设为默认项目

        :param db: 数据库会话
        :param project_id: 项目 ID
        :param user_id: 用户 ID
        :return:
        """
        # 验证项目归属
        project = await project_dao.get(db, project_id)
        if not project or project.is_deleted:
            raise errors.NotFoundError(msg='项目不存在')
        if project.user_id != user_id:
            raise errors.ForbiddenError(msg='无权操作此项目')

        return await project_dao.set_default(db, project_id, user_id)

    @staticmethod
    async def delete(*, db: AsyncSession, project_id: str, user_id: str) -> int:
        """
        删除项目

        :param db: 数据库会话
        :param project_id: 项目 ID
        :param user_id: 用户 ID
        :return:
        """
        # 验证项目归属
        project = await project_dao.get(db, project_id)
        if not project or project.is_deleted:
            raise errors.NotFoundError(msg='项目不存在')
        if project.user_id != user_id:
            raise errors.ForbiddenError(msg='无权操作此项目')

        # 不能删除默认项目
        if project.is_default:
            raise errors.ForbiddenError(msg='不能删除默认项目，请先设置其他项目为默认')

        return await project_dao.soft_delete(db, project_id)


project_service: ProjectService = ProjectService()
