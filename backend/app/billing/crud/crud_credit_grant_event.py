from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.billing.model import CreditGrantEvent
from backend.app.billing.schema.credit_grant_event import CreateCreditGrantEventParam, UpdateCreditGrantEventParam


class CRUDCreditGrantEvent(CRUDPlus[CreditGrantEvent]):
    async def get(self, db: AsyncSession, pk: int) -> CreditGrantEvent | None:
        """
        获取履约事件表（事务 outbox + 云端审计，不保存权威余额）

        :param db: 数据库会话
        :param pk: 履约事件表（事务 outbox + 云端审计，不保存权威余额） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取履约事件表（事务 outbox + 云端审计，不保存权威余额）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[CreditGrantEvent]:
        """
        获取所有履约事件表（事务 outbox + 云端审计，不保存权威余额）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateCreditGrantEventParam) -> None:
        """
        创建履约事件表（事务 outbox + 云端审计，不保存权威余额）

        :param db: 数据库会话
        :param obj: 创建履约事件表（事务 outbox + 云端审计，不保存权威余额）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateCreditGrantEventParam) -> int:
        """
        更新履约事件表（事务 outbox + 云端审计，不保存权威余额）

        :param db: 数据库会话
        :param pk: 履约事件表（事务 outbox + 云端审计，不保存权威余额） ID
        :param obj: 更新 履约事件表（事务 outbox + 云端审计，不保存权威余额）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除履约事件表（事务 outbox + 云端审计，不保存权威余额）

        :param db: 数据库会话
        :param pks: 履约事件表（事务 outbox + 云端审计，不保存权威余额） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


credit_grant_event_dao: CRUDCreditGrantEvent = CRUDCreditGrantEvent(CreditGrantEvent)
