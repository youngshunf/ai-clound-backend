"""项目数据库操作类"""

from sqlalchemy import Select, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.project.model import Project
from backend.app.project.schema.project import ProjectCreate, ProjectUpdate
from backend.utils.timezone import timezone


class CRUDProject(CRUDPlus[Project]):
    """项目数据库操作类"""

    async def get(self, db: AsyncSession, project_id: int) -> Project | None:
        """
        获取项目详情

        :param db: 数据库会话
        :param project_id: 项目 ID
        :return:
        """
        return await self.select_model(db, project_id)

    async def get_by_uuid(self, db: AsyncSession, uuid: str) -> Project | None:
        """
        通过 UUID 获取项目

        :param db: 数据库会话
        :param uuid: 项目 UUID
        :return:
        """
        return await self.select_model_by_column(db, uuid=uuid, is_deleted=False)

    async def get_list_by_user(
        self,
        db: AsyncSession,
        user_id: int,
        name: str | None = None,
        industry: str | None = None,
    ) -> Select:
        """
        获取用户项目列表查询表达式

        :param db: 数据库会话
        :param user_id: 用户 ID
        :param name: 项目名称 (模糊搜索)
        :param industry: 行业筛选
        :return:
        """
        filters = {'user_id': user_id, 'is_deleted': False}

        if name:
            filters['name__like'] = f'%{name}%'
        if industry:
            filters['industry'] = industry

        return await self.select_order('created_time', 'desc', **filters)

    async def get_default_project(self, db: AsyncSession, user_id: int) -> Project | None:
        """
        获取用户默认项目

        :param db: 数据库会话
        :param user_id: 用户 ID
        :return:
        """
        return await self.select_model_by_column(db, user_id=user_id, is_default=True, is_deleted=False)

    async def create(self, db: AsyncSession, obj: ProjectCreate, user_id: int) -> Project:
        """
        创建项目

        :param db: 数据库会话
        :param obj: 创建项目参数
        :param user_id: 用户 ID
        :return:
        """
        dict_obj = obj.model_dump()
        dict_obj['user_id'] = user_id

        # 检查是否是用户的第一个项目，如果是则设为默认
        existing = await self.select_model_by_column(db, user_id=user_id, is_deleted=False)
        if not existing:
            dict_obj['is_default'] = True

        new_project = self.model(**dict_obj)
        db.add(new_project)
        await db.flush()
        await db.refresh(new_project)
        return new_project

    async def update(self, db: AsyncSession, project_id: int, obj: ProjectUpdate) -> int:
        """
        更新项目

        :param db: 数据库会话
        :param project_id: 项目 ID
        :param obj: 更新项目参数
        :return:
        """
        # 过滤掉 None 值
        update_data = obj.model_dump(exclude_unset=True)
        if not update_data:
            return 0

        # 增加版本号
        update_data['server_version'] = Project.server_version + 1

        return await self.update_model(db, project_id, update_data)

    async def set_default(self, db: AsyncSession, project_id: int, user_id: int) -> int:
        """
        设为默认项目

        :param db: 数据库会话
        :param project_id: 项目 ID
        :param user_id: 用户 ID
        :return:
        """
        # 先取消当前默认项目
        stmt = (
            update(self.model)
            .where(self.model.user_id == user_id, self.model.is_default == True)
            .values(is_default=False)
        )
        await db.execute(stmt)

        # 设置新的默认项目
        return await self.update_model(db, project_id, {'is_default': True})

    async def soft_delete(self, db: AsyncSession, project_id: int) -> int:
        """
        软删除项目

        :param db: 数据库会话
        :param project_id: 项目 ID
        :return:
        """
        return await self.update_model(
            db,
            project_id,
            {'is_deleted': True, 'deleted_at': timezone.now()},
        )


project_dao: CRUDProject = CRUDProject(Project)
