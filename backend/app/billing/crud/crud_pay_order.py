from datetime import datetime
from typing import Sequence

from sqlalchemy import Select, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.billing.model.pay_order import PayOrder
from backend.utils.timezone import timezone


class CRUDPayOrder(CRUDPlus[PayOrder]):
    async def get(self, db: AsyncSession, pk: int) -> PayOrder | None:
        return await self.select_model(db, pk)

    async def get_by_order_no(self, db: AsyncSession, order_no: str) -> PayOrder | None:
        result = await db.execute(select(PayOrder).where(PayOrder.order_no == order_no))
        return result.scalars().first()

    async def get_by_order_no_for_update(self, db: AsyncSession, order_no: str) -> PayOrder | None:
        """带行锁查询（用于回调处理防并发）"""
        result = await db.execute(
            select(PayOrder).where(PayOrder.order_no == order_no).with_for_update()
        )
        return result.scalars().first()

    async def get_select(
        self,
        user_id: int | None = None,
        status: int | None = None,
        order_type: str | None = None,
    ) -> Select:
        stmt = select(PayOrder)
        if user_id is not None:
            stmt = stmt.where(PayOrder.user_id == user_id)
        if status is not None:
            stmt = stmt.where(PayOrder.status == status)
        if order_type is not None:
            stmt = stmt.where(PayOrder.order_type == order_type)
        return stmt.order_by(PayOrder.id.desc())

    async def get_user_orders(self, db: AsyncSession, user_id: int) -> Sequence[PayOrder]:
        result = await db.execute(
            select(PayOrder).where(PayOrder.user_id == user_id).order_by(PayOrder.id.desc())
        )
        return result.scalars().all()

    async def create(self, db: AsyncSession, obj_dict: dict) -> PayOrder:
        """直接用字典创建订单（不走 Schema）"""
        order = PayOrder(**obj_dict)
        db.add(order)
        await db.flush()
        return order

    async def update_status(
        self,
        db: AsyncSession,
        order_no: str,
        status: int,
        channel_order_no: str | None = None,
        channel_user_id: str | None = None,
        success_time: datetime | None = None,
    ) -> int:
        """更新订单状态"""
        values: dict = {'status': status, 'updated_time': timezone.now()}
        if channel_order_no:
            values['channel_order_no'] = channel_order_no
        if channel_user_id:
            values['channel_user_id'] = channel_user_id
        if success_time:
            values['success_time'] = success_time
        result = await db.execute(
            update(PayOrder).where(PayOrder.order_no == order_no).values(**values)
        )
        return result.rowcount

    async def expire_timeout_orders(self, db: AsyncSession) -> int:
        """将超时未支付的订单标记为过期"""
        now = timezone.now()
        result = await db.execute(
            update(PayOrder)
            .where(PayOrder.status == 0, PayOrder.expire_time < now)
            .values(status=4, updated_time=now)
        )
        return result.rowcount

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


pay_order_dao: CRUDPayOrder = CRUDPayOrder(PayOrder)
