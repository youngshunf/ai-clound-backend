from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_creator.model import Project
from backend.app.hasn_creator.schema.project import CreateProjectParam, UpdateProjectParam


class CRUDProject(CRUDPlus[Project]):
    async def get(self, db: AsyncSession, pk: int) -> Project | None:
        """
        获取运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度

        :param db: 数据库会话
        :param pk: 运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[Project]:
        """
        获取所有运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateProjectParam) -> None:
        """
        创建运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度

        :param db: 数据库会话
        :param obj: 创建运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateProjectParam) -> int:
        """
        更新运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度

        :param db: 数据库会话
        :param pk: 运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度 ID
        :param obj: 更新 运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度

        :param db: 数据库会话
        :param pks: 运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


project_dao: CRUDProject = CRUDProject(Project)
