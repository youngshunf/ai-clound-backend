from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.billing.model.pay_merchant import PayMerchant


class CRUDPayMerchant(CRUDPlus[PayMerchant]):
    async def get(self, db: AsyncSession, pk: int) -> PayMerchant | None:
        return await self.select_model(db, pk)

    async def get_select(self, type_: str | None = None, status: int | None = None) -> Select:
        stmt = select(PayMerchant)
        if type_ is not None:
            stmt = stmt.where(PayMerchant.type == type_)
        if status is not None:
            stmt = stmt.where(PayMerchant.status == status)
        return stmt.order_by(PayMerchant.id.desc())

    async def get_all_active(self, db: AsyncSession) -> list[PayMerchant]:
        result = await db.execute(select(PayMerchant).where(PayMerchant.status == 1))
        return list(result.scalars().all())

    async def create(self, db: AsyncSession, obj_dict: dict) -> PayMerchant:
        merchant = PayMerchant(**obj_dict)
        db.add(merchant)
        await db.flush()
        return merchant

    async def update(self, db: AsyncSession, pk: int, obj_dict: dict) -> int:
        return await self.update_model(db, pk, obj_dict)

    async def delete(self, db: AsyncSession, pk: list[int]) -> int:
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pk)


pay_merchant_dao: CRUDPayMerchant = CRUDPayMerchant(PayMerchant)
