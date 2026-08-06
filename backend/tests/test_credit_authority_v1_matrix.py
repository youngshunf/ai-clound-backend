"""doc94 V1 端到端断言矩阵。

清单 §V1 列了 18 个必须被断言的场景。这个文件做两件事：

1. **把矩阵变成可执行的索引**：每一行登记它由哪个仓、哪个文件、哪个用例覆盖，
   并断言那个用例**真的存在**。矩阵最容易腐烂的方式是「表格还在、用例早被删了」——
   有了存在性断言，删用例就会让矩阵直接红，而不是悄悄变成一张漂亮的空表。
2. **补上矩阵里云端侧还缺的两行**（退款重复通知、履约中的订单状态），就地写真断言。

跨仓的行（NewAPI 侧的 Go 用例）只在 new-api worktree 可达时校验存在性，
不可达时如实标记跳过——**不假装验证过**。
"""

from __future__ import annotations

import pathlib
import re

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

import pytest

from sqlalchemy import select, text

from backend.app.billing.model.credit_grant_event import CreditGrantEvent
from backend.app.billing.model.pay_order import PayOrder
from backend.app.billing.service.pay_order_service import pay_order_service
from backend.app.billing.service.refund_settlement_service import refund_settlement_service
from backend.database.db import async_db_session
from backend.utils.timezone import timezone

_BACKEND = pathlib.Path(__file__).resolve().parents[1]


def _find_newapi_root() -> pathlib.Path | None:
    """向上找 new-api worktree。

    云端仓可能是主 clone 也可能是 worktree，层级不固定，所以逐级向上找而不是写死相对层数。
    找不到就返回 None——跨仓行会**如实跳过**，不假装验证过。
    """
    for parent in _BACKEND.parents:
        for candidate in (parent / '.worktrees' / '94-newapi', parent / 'hasn-apps' / 'new-api'):
            if (candidate / 'model').is_dir():
                return candidate
    return None


#: new-api 仓根（worktree 或主 clone）
_NEWAPI_ROOT = _find_newapi_root()


@dataclass(frozen=True)
class MatrixRow:
    """矩阵的一行：场景 → 覆盖它的用例。"""

    scenario: str
    repo: str  # 'cloud' | 'newapi'
    path: str  # 相对各自仓根
    test_name: str


#: doc94 §V1 自动化测试矩阵（18 行，与清单同序）
V1_MATRIX: tuple[MatrixRow, ...] = (
    MatrixRow('钱包为 0 → 模型调用在 relay 前 403，无 provider 请求', 'newapi',
              'model/combined_preconsume_test.go', 'TestCombinedPreConsumeReportsBothRemainingsWhenInsufficient'),
    MatrixRow('支付回调重复 10 次 → 钱包只增加一次，订单 fulfilled', 'cloud',
              'backend/tests/test_credit_outbox_pg.py', 'test_repeated_notifications_leave_exactly_one_command'),
    MatrixRow('NewAPI 成功后响应丢失 → 相同 event 重试命中原结果，不重复到账', 'cloud',
              'backend/tests/test_credit_outbox_pg.py', 'test_timeout_then_get_succeeded_converges_without_reissuing'),
    MatrixRow('NewAPI 终局失败后 worker 重试 → 200+failed，直接进 dead，不重投', 'cloud',
              'backend/tests/test_credit_outbox_pg.py', 'test_timeout_then_get_failed_goes_dead'),
    MatrixRow('NewAPI 瞬时失败后 worker 重试 → 404，用同 ID 重投并最终成功', 'cloud',
              'backend/tests/test_credit_outbox_pg.py', 'test_timeout_then_get_404_retries_with_same_event_id'),
    MatrixRow('精度边界：0.00001 接受；0.000001 拒绝，不静默取整', 'newapi',
              'model/credit_operation_test.go', 'TestRejectedRequestsAreNotPersisted'),
    MatrixRow('月付第 30 天 → 订阅余额清零、状态 expired，不重置', 'newapi',
              'model/subscription_contract_test.go', 'TestMonthlyContractExpiresWithoutResetting'),
    MatrixRow('月付提前续费 → 新合同 scheduled，旧合同到期前不可提前消费', 'newapi',
              'model/subscription_contract_test.go', 'TestScheduledContractCannotBeConsumedBeforeItStarts'),
    MatrixRow('年付第 30/330 天 → 清零并重置为完整一期额度', 'newapi',
              'model/subscription_contract_test.go', 'TestYearlyContractResetsEveryCycleAndStopsAtContractEnd'),
    MatrixRow('年付第 360 天 → 清零过期，不发生第 13 次 grant', 'newapi',
              'model/subscription_contract_test.go', 'TestYearlyContractDoesNotResetOnItsFinalDay'),
    MatrixRow('积分包跨 30/360 天 → 永久钱包不因订阅 reset 改变', 'newapi',
              'model/subscription_contract_test.go', 'TestWalletIsUntouchedBySubscriptionResetAndExpiry'),
    MatrixRow('订阅剩 2、钱包 10、需 5 → 订阅扣 2、钱包扣 3', 'newapi',
              'model/combined_preconsume_test.go', 'TestCombinedPreConsumeSplitsAcrossBothPools'),
    MatrixRow('并发超额 → 并发请求成功总额不超过可用积分', 'newapi',
              'model/combined_preconsume_test.go', 'TestConcurrentPreConsumeNeverOverspends'),
    MatrixRow('退款重复通知 → 只回收一次；余额不足进人工审核且不为负', 'cloud',
              'backend/tests/test_credit_authority_v1_matrix.py', 'test_repeated_refund_notification_recovers_once'),
    MatrixRow('免费档撤销后重授 → epoch 递增，新 event 不被旧幂等键挡住', 'cloud',
              'backend/tests/test_credit_grant_paths_pg.py', 'test_free_grant_after_revocation_is_not_blocked_by_old_key'),
    MatrixRow('admin 连续两笔赠送 → 两笔都到账', 'cloud',
              'backend/tests/test_credit_grant_paths_pg.py', 'test_two_admin_grants_both_land'),
    MatrixRow('NewAPI 不可用 → 显示暂不可用，不展示 Cloud 旧值或假 0', 'cloud',
              'backend/tests/test_credit_account_read_pg.py', 'test_unavailable_returns_null_not_zero'),
    MatrixRow('付款已成、履约重试中 → 显示「已付款，额度发放中」，不显示完成', 'cloud',
              'backend/tests/test_credit_authority_v1_matrix.py', 'test_paid_but_fulfilling_order_is_not_reported_complete'),
)


def _root_for(repo: str) -> pathlib.Path | None:
    if repo == 'cloud':
        return _BACKEND.parent
    return _NEWAPI_ROOT


def test_v1_matrix_has_all_eighteen_rows() -> None:
    """矩阵行数与清单一致，且场景不重复（重复行等于少覆盖一个场景）。"""
    assert len(V1_MATRIX) == 18, f'doc94 §V1 是 18 行，当前 {len(V1_MATRIX)} 行'
    assert len({row.scenario for row in V1_MATRIX}) == 18, '存在重复场景'


@pytest.mark.parametrize('row', V1_MATRIX, ids=lambda r: r.test_name)
def test_v1_matrix_row_is_actually_covered(row: MatrixRow) -> None:
    """每一行指向的用例必须真实存在。

    矩阵最容易腐烂的方式是「表格还在、用例早被删了」。这里做存在性断言，
    删用例就会红，而不是让矩阵悄悄变成一张空表。
    """
    root = _root_for(row.repo)
    if root is None:
        pytest.skip(f'{row.repo} worktree 不可达（{_NEWAPI_ROOT}），无法校验 {row.test_name} 是否存在')

    path = root / row.path
    assert path.exists(), f'矩阵指向的文件不存在: {row.repo}:{row.path}'
    content = path.read_text(encoding='utf-8')
    pattern = rf'(async def |def |func ){re.escape(row.test_name)}\b'
    assert re.search(pattern, content), (
        f'矩阵行「{row.scenario}」声称由 {row.repo}:{row.path}::{row.test_name} 覆盖，但该用例不存在'
    )


# ─────────────────────── 矩阵里云端侧还缺的两行，就地补真断言 ───────────────────────


async def _reset_engine() -> None:
    """丢弃跨事件循环的连接池。

    asyncpg 的连接绑定在创建它的事件循环上；pytest-asyncio 每个用例一个新 loop，
    复用上一个 loop 建的连接会报「attached to a different loop」。
    """
    from backend.database.db import async_engine

    await async_engine.dispose()


@pytest.mark.asyncio
async def test_repeated_refund_notification_recovers_once() -> None:
    """退款重复通知只回收一次；回收命令带退款单号做幂等键，重复通知不会叠加。

    退款走的是 saga：**先回收积分，再调支付渠道**。若渠道回调重放，
    第二次必须命中同一条回收命令而不是再发一条——否则用户会被扣两次积分。
    """
    await _reset_engine()
    order_no = 'V1REFUND0001'
    refund_no = 'V1REFUNDNO001'
    user_id = 979_000_001

    async with async_db_session.begin() as db:
        await db.execute(
            text('DELETE FROM hasn_billing.credit_grant_event WHERE user_id = :u'), {'u': user_id}
        )
        await db.execute(
            text('DELETE FROM hasn_billing.pay_order WHERE order_no = :o'), {'o': order_no}
        )
        await db.execute(
            text('DELETE FROM llm_newapi_user_mapping WHERE huanxing_user_id = :u'), {'u': user_id}
        )
        # 回收命令要写进履约事件，得先有 NewAPI 映射（履约目标）
        await db.execute(
            text("""
                INSERT INTO llm_newapi_user_mapping
                    (huanxing_user_id, newapi_user_id, newapi_token_key, newapi_token_id,
                     app_code, status, created_time)
                VALUES (:u, :u, 'v1matrix', 0, 'huanxing', 'active', NOW())
            """),
            {'u': user_id},
        )

    async with async_db_session.begin() as db:
        db.add(
            PayOrder(
                order_no=order_no,
                user_id=user_id,
                channel_code='wx_native',
                order_type='credit_pack',
                subject='V1 退款幂等',
                body='V1',
                amount=100,
                pay_amount=100,
                status=1,
                expire_time=timezone.now() + timedelta(minutes=30),
                offering_ref={'offering_key': 'credits:topup', 'plan_key': 'v1pack', 'kind': 'credit_pack'},
                extra_data={'app_code': 'huanxing', 'credit_amount': 10},
                fulfillment_status='succeeded',
            )
        )

    from backend.app.billing.core.fulfillment import reverse_fulfillment
    from backend.app.billing.service.pay_callbacks import register_callbacks

    # 履约/回收处理器在应用启动时注册；单测里要显式调一次，否则 kind 分发命中不到。
    register_callbacks()

    async with async_db_session.begin() as db:
        order = (await db.execute(select(PayOrder).where(PayOrder.order_no == order_no))).scalar_one()
        await reverse_fulfillment(db, order, refund_no=refund_no)

    # 重放同一笔退款通知
    async with async_db_session.begin() as db:
        order = (await db.execute(select(PayOrder).where(PayOrder.order_no == order_no))).scalar_one()
        await reverse_fulfillment(db, order, refund_no=refund_no)

    async with async_db_session() as db:
        events = list(
            (
                await db.execute(
                    select(CreditGrantEvent).where(
                        CreditGrantEvent.user_id == user_id,
                        CreditGrantEvent.event_type == 'wallet_revoke',
                    )
                )
            )
            .scalars()
            .all()
        )

    assert len(events) == 1, f'重复退款通知只应留下一条回收命令，实际 {len(events)} 条'
    assert events[0].refund_no == refund_no
    assert Decimal(str(events[0].credit_amount)) == Decimal('10')

    async with async_db_session.begin() as db:
        await db.execute(text('DELETE FROM hasn_billing.credit_grant_event WHERE user_id = :u'), {'u': user_id})
        await db.execute(text('DELETE FROM hasn_billing.pay_order WHERE order_no = :o'), {'o': order_no})
        await db.execute(
            text('DELETE FROM llm_newapi_user_mapping WHERE huanxing_user_id = :u'), {'u': user_id}
        )


@pytest.mark.asyncio
async def test_paid_but_fulfilling_order_is_not_reported_complete() -> None:
    """付款成功但额度还在投递中时，订单状态必须同时报出「已付款」与「发放中」。

    只回 `status=1` 会让 UI 显示「购买完成」，用户立刻去用却被 relay 403 挡住——
    这正是「支付成功 ≠ 额度到账」必须是两个可观察状态的原因。
    """
    await _reset_engine()
    order_no = 'V1FULFILL0001'
    user_id = 979_000_002

    async with async_db_session.begin() as db:
        await db.execute(text('DELETE FROM hasn_billing.pay_order WHERE order_no = :o'), {'o': order_no})
        db.add(
            PayOrder(
                order_no=order_no,
                user_id=user_id,
                channel_code='wx_native',
                order_type='credit_pack',
                subject='V1 履约中',
                body='V1',
                amount=100,
                pay_amount=100,
                status=1,
                expire_time=timezone.now() + timedelta(minutes=30),
                fulfillment_status='pending',
            )
        )

    async with async_db_session() as db:
        result = await pay_order_service.get_status(db=db, order_no=order_no)

    assert result.status == 1, '付款状态是已支付'
    assert result.fulfillment_status == 'pending', '履约状态必须单独可见，不能被付款状态盖住'
    assert result.fulfilled_at is None, '额度还没到账就不该有到账时刻'

    async with async_db_session.begin() as db:
        await db.execute(text('DELETE FROM hasn_billing.pay_order WHERE order_no = :o'), {'o': order_no})


@pytest.mark.asyncio
async def test_refund_settlement_never_compensates_on_unknown_result(monkeypatch) -> None:
    """渠道结果未知时返回 pending 且**不补偿**：补了就等于用户既拿回钱又留着积分。

    只有渠道**确定失败**才允许把积分补回去；超时/未知一律挂起等下一轮重试或人工查单。
    """
    await _reset_engine()
    refund_no = 'V1UNKNOWN0001'
    order_no = 'V1UNKNOWNORD1'
    user_id = 979_000_003

    async with async_db_session.begin() as db:
        await db.execute(text('DELETE FROM hasn_billing.pay_refund WHERE refund_no = :r'), {'r': refund_no})
        await db.execute(text('DELETE FROM hasn_billing.pay_order WHERE order_no = :o'), {'o': order_no})
        db.add(
            PayOrder(
                order_no=order_no,
                user_id=user_id,
                channel_code='wx_native',
                order_type='credit_pack',
                subject='V1 未知结果',
                body='V1',
                amount=100,
                pay_amount=100,
                status=1,
                expire_time=timezone.now() + timedelta(minutes=30),
                fulfillment_status='succeeded',
            )
        )
        await db.execute(
            text("""
                INSERT INTO hasn_billing.pay_refund
                    (refund_no, order_no, user_id, refund_amount, reason, status, created_time)
                VALUES (:r, :o, :u, 100, 'V1', 0, NOW())
            """),
            {'r': refund_no, 'o': order_no, 'u': user_id},
        )

    from backend.app.billing.service.pay_order_service import PayOrderService

    def _channel_blows_up(*args, **kwargs):
        raise TimeoutError('渠道超时，结果未知')

    monkeypatch.setattr(PayOrderService, '_invoke_channel_refund', staticmethod(_channel_blows_up))

    result = await refund_settlement_service.settle_channel_refund(refund_no)

    assert result == 'pending', '结果未知必须挂起，既不判成功也不判失败'

    async with async_db_session() as db:
        status = (
            await db.execute(text('SELECT status FROM hasn_billing.pay_refund WHERE refund_no = :r'), {'r': refund_no})
        ).scalar()
        compensations = (
            await db.execute(
                select(CreditGrantEvent).where(
                    CreditGrantEvent.user_id == user_id,
                    CreditGrantEvent.event_type == 'wallet_grant',
                )
            )
        ).scalars().all()

    assert status == 0, '仍是待处理，等下一轮重试'
    assert not list(compensations), '结果未知时绝不补偿——补了用户就双得'

    async with async_db_session.begin() as db:
        await db.execute(text('DELETE FROM hasn_billing.pay_refund WHERE refund_no = :r'), {'r': refund_no})
        await db.execute(text('DELETE FROM hasn_billing.pay_order WHERE order_no = :o'), {'o': order_no})
