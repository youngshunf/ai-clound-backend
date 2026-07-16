from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_project.crud.crud_hasn_project import hasn_project_dao
from backend.app.hasn_project.model import HasnProject
from backend.app.hasn_project.schema.hasn_project import CreateHasnProjectParam, DeleteHasnProjectParam, UpdateHasnProjectParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HasnProjectService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HasnProject:
        """
        获取平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）

        :param db: 数据库会话
        :param pk: 平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38） ID
        :return:
        """
        hasn_project = await hasn_project_dao.get(db, pk)
        if not hasn_project:
            raise errors.NotFoundError(msg='平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）不存在')
        return hasn_project

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）列表

        :param db: 数据库会话
        :return:
        """
        hasn_project_select = await hasn_project_dao.get_select()
        return await paging_data(db, hasn_project_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HasnProject]:
        """
        获取所有平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）

        :param db: 数据库会话
        :return:
        """
        hasn_project_list = await hasn_project_dao.get_all(db)
        return hasn_project_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHasnProjectParam) -> None:
        """
        创建平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）

        :param db: 数据库会话
        :param obj: 创建平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）参数
        :return:
        """
        await hasn_project_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHasnProjectParam) -> int:
        """
        更新平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）

        :param db: 数据库会话
        :param pk: 平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38） ID
        :param obj: 更新平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）参数
        :return:
        """
        count = await hasn_project_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHasnProjectParam) -> int:
        """
        删除平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）

        :param db: 数据库会话
        :param obj: 平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38） ID 列表
        :return:
        """
        count = await hasn_project_dao.delete(db, obj.pks)
        return count


hasn_project_service: HasnProjectService = HasnProjectService()
