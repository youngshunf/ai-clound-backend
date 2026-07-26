from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_project.model import HasnProjectInspection
from backend.app.hasn_project.schema.hasn_project_inspection import CreateHasnProjectInspectionParam, UpdateHasnProjectInspectionParam


class CRUDHasnProjectInspection(CRUDPlus[HasnProjectInspection]):
    async def get(self, db: AsyncSession, pk: int) -> HasnProjectInspection | None:
        """
        获取平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11）

        :param db: 数据库会话
        :param pk: 平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnProjectInspection]:
        """
        获取所有平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnProjectInspectionParam) -> None:
        """
        创建平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11）

        :param db: 数据库会话
        :param obj: 创建平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnProjectInspectionParam) -> int:
        """
        更新平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11）

        :param db: 数据库会话
        :param pk: 平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11） ID
        :param obj: 更新 平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11）

        :param db: 数据库会话
        :param pks: 平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_project_inspection_dao: CRUDHasnProjectInspection = CRUDHasnProjectInspection(HasnProjectInspection)
