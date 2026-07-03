from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.billing.crud.crud_pay_merchant import pay_merchant_dao
from backend.app.billing.model.pay_merchant import PayMerchant
from backend.app.billing.schema.pay_merchant import CreatePayMerchantParam, UpdatePayMerchantParam
from backend.common.exception import errors


class PayMerchantService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> PayMerchant | None:
        return await pay_merchant_dao.get(db, pk)

    @staticmethod
    async def get_list(db: AsyncSession, type_: str | None = None, status: int | None = None) -> dict[str, Any]:
        select_stmt = await pay_merchant_dao.get_select(type_=type_, status=status)
        return {'select': select_stmt}

    @staticmethod
    async def get_all_active(*, db: AsyncSession) -> list[PayMerchant]:
        return await pay_merchant_dao.get_all_active(db)

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreatePayMerchantParam) -> PayMerchant:
        return await pay_merchant_dao.create(db, obj.model_dump())

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdatePayMerchantParam) -> int:
        existing = await pay_merchant_dao.get(db, pk)
        if not existing:
            raise errors.NotFoundError(msg='商户不存在')
        return await pay_merchant_dao.update(db, pk, obj.model_dump(exclude_unset=True))

    @staticmethod
    async def delete(*, db: AsyncSession, pk: list[int]) -> int:
        return await pay_merchant_dao.delete(db, pk)


pay_merchant_service: PayMerchantService = PayMerchantService()
