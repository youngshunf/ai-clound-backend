from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnArtifactRegistrationOutbox
from backend.app.hasn.schema.hasn_artifact_registration_outbox import CreateHasnArtifactRegistrationOutboxParam, UpdateHasnArtifactRegistrationOutboxParam


class CRUDHasnArtifactRegistrationOutbox(CRUDPlus[HasnArtifactRegistrationOutbox]):
    async def get(self, db: AsyncSession, pk: int) -> HasnArtifactRegistrationOutbox | None:
        """
        获取Agent 产物登记可靠投递与修复队列

        :param db: 数据库会话
        :param pk: Agent 产物登记可靠投递与修复队列 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取Agent 产物登记可靠投递与修复队列列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnArtifactRegistrationOutbox]:
        """
        获取所有Agent 产物登记可靠投递与修复队列

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnArtifactRegistrationOutboxParam) -> None:
        """
        创建Agent 产物登记可靠投递与修复队列

        :param db: 数据库会话
        :param obj: 创建Agent 产物登记可靠投递与修复队列参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnArtifactRegistrationOutboxParam) -> int:
        """
        更新Agent 产物登记可靠投递与修复队列

        :param db: 数据库会话
        :param pk: Agent 产物登记可靠投递与修复队列 ID
        :param obj: 更新 Agent 产物登记可靠投递与修复队列参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除Agent 产物登记可靠投递与修复队列

        :param db: 数据库会话
        :param pks: Agent 产物登记可靠投递与修复队列 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_artifact_registration_outbox_dao: CRUDHasnArtifactRegistrationOutbox = CRUDHasnArtifactRegistrationOutbox(HasnArtifactRegistrationOutbox)
