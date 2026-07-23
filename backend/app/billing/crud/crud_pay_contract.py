from collections.abc import Sequence
from datetime import date
from typing import Any, cast

from sqlalchemy import Select, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.billing.model.pay_contract import PayContract
from backend.utils.timezone import timezone


class CRUDPayContract(CRUDPlus[PayContract]):
    @staticmethod
    def _single_contract(result: object) -> PayContract | None:
        """将无关联加载的查询结果收紧为单个支付签约。"""
        if result is not None and not isinstance(result, PayContract):
            raise TypeError('支付签约单模型查询返回了关联结果')
        return cast(PayContract | None, result)

    async def get(self, db: AsyncSession, pk: int) -> PayContract | None:
        return self._single_contract(await self.select_model(db, pk))

    async def get_by_contract_no(self, db: AsyncSession, contract_no: str) -> PayContract | None:
        result = await db.execute(select(PayContract).where(PayContract.contract_no == contract_no))
        return result.scalars().first()

    async def get_by_channel_contract_id(self, db: AsyncSession, channel_contract_id: str) -> PayContract | None:
        result = await db.execute(
            select(PayContract).where(PayContract.channel_contract_id == channel_contract_id)
        )
        return result.scalars().first()

    async def get_active_by_user(self, db: AsyncSession, user_id: int) -> PayContract | None:
        """获取用户当前有效的签约"""
        result = await db.execute(
            select(PayContract)
            .where(PayContract.user_id == user_id, PayContract.status == 1)
            .order_by(PayContract.id.desc())
        )
        return result.scalars().first()

    async def get_due_contracts(self, db: AsyncSession, target_date: date) -> Sequence[PayContract]:
        """获取到期需要扣款的签约"""
        result = await db.execute(
            select(PayContract)
            .where(PayContract.status == 1, PayContract.next_deduct_date <= target_date)
            .order_by(PayContract.id.asc())
        )
        return result.scalars().all()

    async def get_select(
        self,
        user_id: int | None = None,
        status: int | None = None,
    ) -> Select:
        stmt = select(PayContract)
        if user_id is not None:
            stmt = stmt.where(PayContract.user_id == user_id)
        if status is not None:
            stmt = stmt.where(PayContract.status == status)
        return stmt.order_by(PayContract.id.desc())

    async def create(self, db: AsyncSession, obj_dict: dict) -> PayContract:
        contract = PayContract(**obj_dict)
        db.add(contract)
        await db.flush()
        return contract

    async def update_signed(
        self,
        db: AsyncSession,
        contract_no: str,
        channel_contract_id: str,
        next_deduct_date: date,
    ) -> int:
        """签约成功更新"""
        result = cast(
            CursorResult[Any],
            await db.execute(
                update(PayContract)
                .where(PayContract.contract_no == contract_no)
                .values(
                    status=1,
                    channel_contract_id=channel_contract_id,
                    signed_time=timezone.now(),
                    next_deduct_date=next_deduct_date,
                    updated_time=timezone.now(),
                )
            ),
        )
        return result.rowcount or 0

    async def update_terminated(self, db: AsyncSession, contract_no: str, reason: str | None = None) -> int:
        """解约更新"""
        result = cast(
            CursorResult[Any],
            await db.execute(
                update(PayContract)
                .where(PayContract.contract_no == contract_no)
                .values(
                    status=2,
                    terminated_time=timezone.now(),
                    terminate_reason=reason,
                    updated_time=timezone.now(),
                )
            ),
        )
        return result.rowcount or 0

    async def update_deducted(
        self,
        db: AsyncSession,
        contract_no: str,
        next_deduct_date: date,
    ) -> int:
        """扣款成功更新"""
        result = cast(
            CursorResult[Any],
            await db.execute(
                update(PayContract)
                .where(PayContract.contract_no == contract_no)
                .values(
                    last_deduct_time=timezone.now(),
                    deduct_count=PayContract.deduct_count + 1,
                    next_deduct_date=next_deduct_date,
                    updated_time=timezone.now(),
                )
            ),
        )
        return result.rowcount or 0


pay_contract_dao: CRUDPayContract = CRUDPayContract(PayContract)
