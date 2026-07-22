from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_project.model import HasnProject
from backend.app.hasn_project.schema.hasn_project import CreateHasnProjectParam, UpdateHasnProjectParam


class CRUDHasnProject(CRUDPlus[HasnProject]):
    async def get(self, db: AsyncSession, pk: int) -> HasnProject | None:
        """
        获取平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）

        :param db: 数据库会话
        :param pk: 平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnProject]:
        """
        获取所有平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnProjectParam) -> None:
        """
        创建平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）

        :param db: 数据库会话
        :param obj: 创建平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnProjectParam) -> int:
        """
        更新平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）

        :param db: 数据库会话
        :param pk: 平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38） ID
        :param obj: 更新 平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38）

        :param db: 数据库会话
        :param pks: 平台项目（云端权威·业务容器·联邦挂靠聚合门面，doc38） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_project_dao: CRUDHasnProject = CRUDHasnProject(HasnProject)
