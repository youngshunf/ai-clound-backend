from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_project.crud.crud_hasn_project_milestone import hasn_project_milestone_dao
from backend.app.hasn_project.model import HasnProjectMilestone
from backend.app.hasn_project.schema.hasn_project_milestone import CreateHasnProjectMilestoneParam, DeleteHasnProjectMilestoneParam, UpdateHasnProjectMilestoneParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnProjectMilestoneService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnProjectMilestone:
        """
        获取平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）

        :param db: 数据库会话
        :param pk: 平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3） ID
        :return:
        """
        hasn_project_milestone = await hasn_project_milestone_dao.get(db, pk)
        if not hasn_project_milestone:
            raise errors.NotFoundError(msg='平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）不存在')
        return hasn_project_milestone

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）列表

        :param db: 数据库会话
        :return:
        """
        hasn_project_milestone_select = await hasn_project_milestone_dao.get_select()
        return await paging_data(db, hasn_project_milestone_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnProjectMilestone]:
        """
        获取所有平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）

        :param db: 数据库会话
        :return:
        """
        hasn_project_milestone_list = await hasn_project_milestone_dao.get_all(db)
        return hasn_project_milestone_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnProjectMilestoneParam) -> None:
        """
        创建平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）

        :param db: 数据库会话
        :param obj: 创建平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）参数
        :return:
        """
        await hasn_project_milestone_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnProjectMilestoneParam) -> int:
        """
        更新平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）

        :param db: 数据库会话
        :param pk: 平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3） ID
        :param obj: 更新平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）参数
        :return:
        """
        count = await hasn_project_milestone_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnProjectMilestoneParam) -> int:
        """
        删除平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3）

        :param db: 数据库会话
        :param obj: 平台项目里程碑（v2·业务状态标记·无依赖无门控，doc38 §12.3） ID 列表
        :return:
        """
        count = await hasn_project_milestone_dao.delete(db, obj.pks)
        return count


hasn_project_milestone_service: HasnProjectMilestoneService = HasnProjectMilestoneService()
