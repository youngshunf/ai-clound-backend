"""doc94 R1 存量余额 rebase 基线计算验收。

这套断言锁的是**「宁可交给人，也不要猜」**：

1. 能归因的账户按「不可变凭证 − 真实消费」算出目标余额，并给出**增量**
   （rebase 通过幂等履约事件做增量调整，不走任何「设置绝对 quota」入口）；
2. 消费无法归因 → 人工清单，绝不摊派；
3. 目标余额为负 → 人工清单，绝不静默归零、也绝不补成套餐全额；
4. 读不到 NewAPI 账户 → 人工清单，绝不按 0 计算增量。

另外锁住报告形状：``current_credits`` 是改动前余额，报告即可恢复备份。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from backend.scripts.rebase_credit_baseline import (
    REASON_ACCOUNT_UNREADABLE,
    REASON_CONSUMPTION_INDETERMINATE,
    REASON_NEGATIVE_TARGET,
    build_baseline,
    build_report,
)


def _account(remaining: str) -> dict:
    return {'wallet': {'remaining_credits': remaining}}


def _determinate(wallet_consumed: str, subscription_consumed: str = '0') -> dict:
    return {
        'determinate': True,
        'wallet_consumed_credits': wallet_consumed,
        'subscription_consumed_credits': subscription_consumed,
        'unattributed_credits': '0',
    }


def test_delta_is_target_minus_current_not_absolute_set() -> None:
    """应得 100、真实消费 30 → 目标 70；当前 85 → 增量 −15（撤销 15）。

    注意结论是**增量**而非绝对值：rebase 只允许通过幂等履约事件做加减，
    「按云端算出的数字设置绝对余额」正是本轮要消灭的反向数据流。
    """
    baseline = build_baseline(
        user_id=1,
        newapi_user_id=4242,
        entitled_credits=Decimal('100'),
        evidence={'credit_pack_orders': 2},
        consumption=_determinate('30', subscription_consumed='12'),
        account=_account('85'),
    )

    assert baseline.manual_reason is None
    assert baseline.target_wallet_credits == Decimal('70')
    assert baseline.delta_credits == Decimal('-15')
    # 订阅消费如实记进依据，但不参与钱包基线计算（订阅按周期重置）
    assert baseline.evidence['subscription_consumed_credits'] == '12'


def test_indeterminate_consumption_goes_to_manual_review() -> None:
    """NewAPI 说拆不出资金来源 → 人工清单，绝不按比例摊派。"""
    baseline = build_baseline(
        user_id=2,
        newapi_user_id=4243,
        entitled_credits=Decimal('50'),
        evidence={},
        consumption={
            'determinate': False,
            'indeterminate_reason': '17 条历史日志缺少资金池拆分明细',
            'unattributed_credits': '3.5',
            'unattributed_count': 17,
        },
        account=None,
    )

    assert baseline.manual_reason == REASON_CONSUMPTION_INDETERMINATE
    assert baseline.delta_credits == Decimal(0)
    assert baseline.evidence['unattributed_credits'] == '3.5'
    assert baseline.evidence['unattributed_count'] == 17


def test_negative_target_goes_to_manual_review_never_zeroed() -> None:
    """应得 10、已消费 40 → 目标 −30：这是超额放行的痕迹，必须人工判定。"""
    baseline = build_baseline(
        user_id=3,
        newapi_user_id=4244,
        entitled_credits=Decimal('10'),
        evidence={},
        consumption=_determinate('40'),
        account=_account('0'),
    )

    assert baseline.manual_reason == REASON_NEGATIVE_TARGET
    assert baseline.target_wallet_credits == Decimal('-30')
    # 没有算增量：不归零、不补套餐全额，交给人
    assert baseline.delta_credits == Decimal(0)
    assert baseline.manual_detail is not None
    assert '绝不静默归零' in baseline.manual_detail


def test_unreadable_account_goes_to_manual_review() -> None:
    """读不到当前余额就算不出增量；按 0 计算会凭空发一笔额度。"""
    baseline = build_baseline(
        user_id=4,
        newapi_user_id=4245,
        entitled_credits=Decimal('20'),
        evidence={},
        consumption=_determinate('5'),
        account=None,
    )

    assert baseline.manual_reason == REASON_ACCOUNT_UNREADABLE
    assert baseline.target_wallet_credits == Decimal('15')
    assert baseline.delta_credits == Decimal(0)


def test_zero_delta_account_is_applicable_but_writes_nothing() -> None:
    """基线已经正确的账户仍进可写清单，但增量为 0——写入阶段会跳过。"""
    baseline = build_baseline(
        user_id=5,
        newapi_user_id=4246,
        entitled_credits=Decimal('100'),
        evidence={},
        consumption=_determinate('40'),
        account=_account('60'),
    )

    assert baseline.manual_reason is None
    assert baseline.delta_credits == Decimal(0)


def test_report_records_pre_change_balance_as_recoverable_backup() -> None:
    """报告即备份：必须记下改动前余额与逐用户 hash，否则回滚无据可依。"""
    applicable = [
        build_baseline(
            user_id=1,
            newapi_user_id=4242,
            entitled_credits=Decimal('100'),
            evidence={},
            consumption=_determinate('30'),
            account=_account('85'),
        )
    ]
    manual = [
        build_baseline(
            user_id=3,
            newapi_user_id=4244,
            entitled_credits=Decimal('10'),
            evidence={},
            consumption=_determinate('40'),
            account=_account('0'),
        )
    ]

    report = build_report(
        '2026-07-25-A', applicable, manual, applied=False, generated_at='2026-07-25T00:00:00+00:00'
    )

    assert report['applied'] is False
    assert report['applicable_count'] == 1
    assert report['manual_review_count'] == 1
    row = report['applicable'][0]
    assert row['current_credits'] == '85'  # 改动前余额，回滚就靠它
    assert row['target_credits'] == '70'
    assert row['delta_credits'] == '-15'
    assert len(row['fingerprint']) == 16
    assert report['manual_review'][0]['reason'] == REASON_NEGATIVE_TARGET


def test_fingerprint_changes_when_baseline_changes() -> None:
    """逐用户 hash 必须随基线变化，否则起不到复核作用。"""
    common: dict[str, Any] = {
        'user_id': 1,
        'newapi_user_id': 4242,
        'evidence': {},
        'account': _account('85'),
    }
    first = build_baseline(entitled_credits=Decimal('100'), consumption=_determinate('30'), **common)
    second = build_baseline(entitled_credits=Decimal('101'), consumption=_determinate('30'), **common)

    assert first.fingerprint() != second.fingerprint()
