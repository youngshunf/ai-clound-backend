from datetime import datetime
from typing import Any, cast

from sqlalchemy import Select, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.billing.model.pay_refund import PayRefund


class CRUDPayRefund(CRUDPlus[PayRefund]):
    @staticmethod
    def _single_refund(result: object) -> PayRefund | None:
        """将无关联加载的查询结果收紧为单个退款单。"""
        if result is not None and not isinstance(result, PayRefund):
            raise TypeError('退款单单模型查询返回了关联结果')
        return cast(PayRefund | None, result)

    async def get(self, db: AsyncSession, pk: int) -> PayRefund | None:
        return self._single_refund(await self.select_model(db, pk))

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
        result = cast(
            CursorResult[Any],
            await db.execute(update(PayRefund).where(PayRefund.refund_no == refund_no).values(**values)),
        )
        return result.rowcount or 0


pay_refund_dao: CRUDPayRefund = CRUDPayRefund(PayRefund)
