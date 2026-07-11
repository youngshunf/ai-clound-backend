from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnGroupAgentInvites
from backend.app.hasn.schema.hasn_group_agent_invites import CreateHasnGroupAgentInvitesParam, UpdateHasnGroupAgentInvitesParam


class CRUDHasnGroupAgentInvites(CRUDPlus[HasnGroupAgentInvites]):
    async def get(self, db: AsyncSession, pk: int) -> HasnGroupAgentInvites | None:
        """
        获取HASN 群内拉分身邀请确认表（非主人拉分身需主人同意）

        :param db: 数据库会话
        :param pk: HASN 群内拉分身邀请确认表（非主人拉分身需主人同意） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取HASN 群内拉分身邀请确认表（非主人拉分身需主人同意）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnGroupAgentInvites]:
        """
        获取所有HASN 群内拉分身邀请确认表（非主人拉分身需主人同意）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnGroupAgentInvitesParam) -> None:
        """
        创建HASN 群内拉分身邀请确认表（非主人拉分身需主人同意）

        :param db: 数据库会话
        :param obj: 创建HASN 群内拉分身邀请确认表（非主人拉分身需主人同意）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnGroupAgentInvitesParam) -> int:
        """
        更新HASN 群内拉分身邀请确认表（非主人拉分身需主人同意）

        :param db: 数据库会话
        :param pk: HASN 群内拉分身邀请确认表（非主人拉分身需主人同意） ID
        :param obj: 更新 HASN 群内拉分身邀请确认表（非主人拉分身需主人同意）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除HASN 群内拉分身邀请确认表（非主人拉分身需主人同意）

        :param db: 数据库会话
        :param pks: HASN 群内拉分身邀请确认表（非主人拉分身需主人同意） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_group_agent_invites_dao: CRUDHasnGroupAgentInvites = CRUDHasnGroupAgentInvites(HasnGroupAgentInvites)
