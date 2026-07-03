from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hermes.model import HermesAgentChannelBinding
from backend.app.hermes.schema.hermes_agent_channel_binding import (
    CreateHermesAgentChannelBindingParam,
    UpdateHermesAgentChannelBindingParam,
)


class CRUDHermesAgentChannelBinding(CRUDPlus[HermesAgentChannelBinding]):
    async def get(self, db: AsyncSession, pk: int) -> HermesAgentChannelBinding | None:
        """
        获取Hermes Agent 渠道绑定

        :param db: 数据库会话
        :param pk: Hermes Agent 渠道绑定 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取Hermes Agent 渠道绑定列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HermesAgentChannelBinding]:
        """
        获取所有Hermes Agent 渠道绑定

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHermesAgentChannelBindingParam) -> None:
        """
        创建Hermes Agent 渠道绑定

        :param db: 数据库会话
        :param obj: 创建Hermes Agent 渠道绑定参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHermesAgentChannelBindingParam) -> int:
        """
        更新Hermes Agent 渠道绑定

        :param db: 数据库会话
        :param pk: Hermes Agent 渠道绑定 ID
        :param obj: 更新 Hermes Agent 渠道绑定参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除Hermes Agent 渠道绑定

        :param db: 数据库会话
        :param pks: Hermes Agent 渠道绑定 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hermes_agent_channel_binding_dao: CRUDHermesAgentChannelBinding = CRUDHermesAgentChannelBinding(HermesAgentChannelBinding)
