"""签约 Service — 自动续费核心逻辑"""

from datetime import date
from typing import Any

from dateutil.relativedelta import relativedelta
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.billing.crud.crud_pay_contract import pay_contract_dao
from backend.app.billing.model.pay_contract import PayContract
from backend.app.billing.schema.pay_contract import GetPayContractUserView
from backend.common.exception import errors
from backend.common.pagination import paging_data
from backend.utils.timezone import timezone


def _calc_next_deduct_date(current: date, billing_cycle: str) -> date:
    """计算下次扣款日期"""
    if billing_cycle == 'monthly':
        return current + relativedelta(months=1)
    elif billing_cycle == 'yearly':
        return current + relativedelta(years=1)
    raise ValueError(f'无效的计费周期: {billing_cycle}')


class PayContractService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> PayContract:
        contract = await pay_contract_dao.get(db, pk)
        if not contract:
            raise errors.NotFoundError(msg='签约记录不存在')
        return contract

    @staticmethod
    async def get_list(
        db: AsyncSession,
        user_id: int | None = None,
        status: int | None = None,
    ) -> dict[str, Any]:
        select_stmt = await pay_contract_dao.get_select(user_id=user_id, status=status)
        return await paging_data(db, select_stmt)

    @staticmethod
    async def get_user_contract(*, db: AsyncSession, user_id: int) -> GetPayContractUserView:
        """获取用户当前签约状态（用户端）"""
        contract = await pay_contract_dao.get_active_by_user(db, user_id)
        if not contract:
            return GetPayContractUserView(has_contract=False)
        return GetPayContractUserView(
            has_contract=True,
            tier=contract.tier,
            billing_cycle=contract.billing_cycle,
            deduct_amount=contract.deduct_amount,
            next_deduct_date=contract.next_deduct_date,
            channel_code=contract.channel_code,
        )

    @staticmethod
    async def handle_sign_notify(
        *,
        db: AsyncSession,
        contract_no: str,
        channel_contract_id: str,
    ) -> bool:
        """签约成功回调处理"""
        contract = await pay_contract_dao.get_by_contract_no(db, contract_no)
        if not contract:
            raise errors.NotFoundError(msg=f'签约 {contract_no} 不存在')
        if contract.status == 1:
            return False
        today = timezone.now().date()
        next_deduct = _calc_next_deduct_date(today, contract.billing_cycle)
        await pay_contract_dao.update_signed(
            db, contract_no=contract_no, channel_contract_id=channel_contract_id, next_deduct_date=next_deduct,
        )
        return True

    @staticmethod
    async def cancel_contract(*, db: AsyncSession, user_id: int) -> None:
        """用户主动取消自动续费"""
        contract = await pay_contract_dao.get_active_by_user(db, user_id)
        if not contract:
            raise errors.NotFoundError(msg='没有有效的签约')
        await pay_contract_dao.update_terminated(db, contract_no=contract.contract_no, reason='用户主动取消')

    @staticmethod
    async def handle_unsign_notify(*, db: AsyncSession, contract_no: str) -> bool:
        """解约回调处理"""
        contract = await pay_contract_dao.get_by_contract_no(db, contract_no)
        if not contract:
            raise errors.NotFoundError(msg=f'签约 {contract_no} 不存在')
        if contract.status == 2:
            return False
        await pay_contract_dao.update_terminated(db, contract_no=contract_no, reason='第三方回调解约')
        return True

    @staticmethod
    async def process_due_deductions(*, db: AsyncSession) -> dict:
        """处理到期扣款（定时任务调用）"""
        today = timezone.now().date()
        due_contracts = await pay_contract_dao.get_due_contracts(db, today)
        result = {'total': len(due_contracts), 'success': 0, 'failed': 0}
        for contract in due_contracts:
            try:
                next_deduct = _calc_next_deduct_date(today, contract.billing_cycle)
                await pay_contract_dao.update_deducted(db, contract_no=contract.contract_no, next_deduct_date=next_deduct)
                result['success'] += 1
            except Exception:
                result['failed'] += 1
        return result


pay_contract_service: PayContractService = PayContractService()
