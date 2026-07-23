from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnArtifactContributions
from backend.app.hasn.schema.hasn_artifact_contributions import CreateHasnArtifactContributionsParam, UpdateHasnArtifactContributionsParam


class CRUDHasnArtifactContributions(CRUDPlus[HasnArtifactContributions]):
    async def get(self, db: AsyncSession, pk: int) -> HasnArtifactContributions | None:
        """
        获取Agent 对产物的不可变参与记录

        :param db: 数据库会话
        :param pk: Agent 对产物的不可变参与记录 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取Agent 对产物的不可变参与记录列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnArtifactContributions]:
        """
        获取所有Agent 对产物的不可变参与记录

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnArtifactContributionsParam) -> None:
        """
        创建Agent 对产物的不可变参与记录

        :param db: 数据库会话
        :param obj: 创建Agent 对产物的不可变参与记录参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnArtifactContributionsParam) -> int:
        """
        更新Agent 对产物的不可变参与记录

        :param db: 数据库会话
        :param pk: Agent 对产物的不可变参与记录 ID
        :param obj: 更新 Agent 对产物的不可变参与记录参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除Agent 对产物的不可变参与记录

        :param db: 数据库会话
        :param pks: Agent 对产物的不可变参与记录 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_artifact_contributions_dao: CRUDHasnArtifactContributions = CRUDHasnArtifactContributions(HasnArtifactContributions)
