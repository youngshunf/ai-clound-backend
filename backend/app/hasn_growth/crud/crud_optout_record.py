from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_growth.model import OptoutRecord
from backend.app.hasn_growth.schema.optout_record import CreateOptoutRecordParam, UpdateOptoutRecordParam


class CRUDOptoutRecord(CRUDPlus[OptoutRecord]):
    async def get(self, db: AsyncSession, pk: int) -> OptoutRecord | None:
        """
        获取获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout）

        :param db: 数据库会话
        :param pk: 获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[OptoutRecord]:
        """
        获取所有获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateOptoutRecordParam) -> None:
        """
        创建获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout）

        :param db: 数据库会话
        :param obj: 创建获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateOptoutRecordParam) -> int:
        """
        更新获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout）

        :param db: 数据库会话
        :param pk: 获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout） ID
        :param obj: 更新 获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout）

        :param db: 数据库会话
        :param pks: 获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


optout_record_dao: CRUDOptoutRecord = CRUDOptoutRecord(OptoutRecord)
