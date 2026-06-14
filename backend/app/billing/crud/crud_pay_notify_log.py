from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.billing.model.pay_notify_log import PayNotifyLog


class CRUDPayNotifyLog(CRUDPlus[PayNotifyLog]):
    async def get(self, db: AsyncSession, pk: int) -> PayNotifyLog | None:
        return await self.select_model(db, pk)

    async def get_select(self, order_no: str | None = None, channel_code: str | None = None) -> Select:
        stmt = select(PayNotifyLog)
        if order_no is not None:
            stmt = stmt.where(PayNotifyLog.order_no == order_no)
        if channel_code is not None:
            stmt = stmt.where(PayNotifyLog.channel_code == channel_code)
        return stmt.order_by(PayNotifyLog.id.desc())

    async def create(self, db: AsyncSession, obj_dict: dict) -> PayNotifyLog:
        log = PayNotifyLog(**obj_dict)
        db.add(log)
        await db.flush()
        return log


pay_notify_log_dao: CRUDPayNotifyLog = CRUDPayNotifyLog(PayNotifyLog)
