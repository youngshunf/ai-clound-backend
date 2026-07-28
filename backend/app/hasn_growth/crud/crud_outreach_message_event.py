from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_growth.model import OutreachMessageEvent
from backend.app.hasn_growth.schema.outreach_message_event import (
    CreateOutreachMessageEventParam,
    UpdateOutreachMessageEventParam,
)


class CRUDOutreachMessageEvent(CRUDPlus[OutreachMessageEvent]):
    async def get(self, db: AsyncSession, pk: int) -> OutreachMessageEvent | None:
        """
        获取触达审批、投递、拦截、人工证明和回复的追加式事件

        :param db: 数据库会话
        :param pk: 触达审批、投递、拦截、人工证明和回复的追加式事件 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取触达审批、投递、拦截、人工证明和回复的追加式事件列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[OutreachMessageEvent]:
        """
        获取所有触达审批、投递、拦截、人工证明和回复的追加式事件

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateOutreachMessageEventParam) -> None:
        """
        创建触达审批、投递、拦截、人工证明和回复的追加式事件

        :param db: 数据库会话
        :param obj: 创建触达审批、投递、拦截、人工证明和回复的追加式事件参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateOutreachMessageEventParam) -> int:
        """
        更新触达审批、投递、拦截、人工证明和回复的追加式事件

        :param db: 数据库会话
        :param pk: 触达审批、投递、拦截、人工证明和回复的追加式事件 ID
        :param obj: 更新 触达审批、投递、拦截、人工证明和回复的追加式事件参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除触达审批、投递、拦截、人工证明和回复的追加式事件

        :param db: 数据库会话
        :param pks: 触达审批、投递、拦截、人工证明和回复的追加式事件 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


outreach_message_event_dao: CRUDOutreachMessageEvent = CRUDOutreachMessageEvent(OutreachMessageEvent)
