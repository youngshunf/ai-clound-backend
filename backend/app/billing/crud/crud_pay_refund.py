from datetime import datetime

from sqlalchemy import Select, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.billing.model.pay_refund import PayRefund


class CRUDPayRefund(CRUDPlus[PayRefund]):
    async def get(self, db: AsyncSession, pk: int) -> PayRefund | None:
        return await self.select_model(db, pk)

    async def get_by_refund_no(self, db: AsyncSession, refund_no: str) -> PayRefund | None:
        result = await db.execute(select(PayRefund).where(PayRefund.refund_no == refund_no))
        return result.scalars().first()

    async def get_select(self, order_no: str | None = None) -> Select:
        stmt = select(PayRefund)
        if order_no is not None:
            stmt = stmt.where(PayRefund.order_no == order_no)
        return stmt.order_by(PayRefund.id.desc())

    async def create(self, db: AsyncSession, obj_dict: dict) -> PayRefund:
        refund = PayRefund(**obj_dict)
        db.add(refund)
        await db.flush()
        return refund

    async def update_status(
        self,
        db: AsyncSession,
        refund_no: str,
        *,
        status: int,
        channel_refund_no: str | None = None,
        success_time: datetime | None = None,
    ) -> int:
        """按退款单号更新退款记录状态（退款回调确认用）。返回受影响行数。"""
        values: dict = {'status': status}
        if channel_refund_no is not None:
            values['channel_refund_no'] = channel_refund_no
        if success_time is not None:
            values['success_time'] = success_time
        result = await db.execute(update(PayRefund).where(PayRefund.refund_no == refund_no).values(**values))
        return result.rowcount


pay_refund_dao: CRUDPayRefund = CRUDPayRefund(PayRefund)
