"""已退役的云端积分写入路径与启动守卫（doc94 P0）。

云端彻底退出积分余额、用量、扣减与换算：NewAPI 是唯一权威。P0 的作用是**先停止继续制造额度**——
在删干净之前，先让任何残留的反向数据流在启动期或调用期显性失败，而不是安静地继续跑。

两类守卫：

1. **启动守卫**：生产配置里若仍排着已退役的积分定时任务（典型是每小时把「云端剩余额度」
   写回 NewAPI 的对账任务），进程直接启动失败。这类任务只要还在跑，NewAPI 刚扣掉的额度
   就会在下一轮被云端写回，余额不足门禁被反向充值抵消。
2. **调用守卫**：任何「设置绝对 quota」或「直接写云端余额表」的调用点都要计数 + `error` 告警，
   两个计数器必须恒为 0。
"""

from __future__ import annotations

from backend.app.billing.observability.metrics import (
    LEGACY_CREDIT_WRITE_ATTEMPT_TOTAL,
    NEWAPI_ABSOLUTE_QUOTA_WRITE_TOTAL,
)
from backend.common.log import log

# 已退役的 celery 任务名。它们不得再出现在任何 beat 调度表里。
RETIRED_CREDIT_TASK_NAMES: frozenset[str] = frozenset(
    {
        # 每小时把「NewAPI 已用量 + 云端剩余额度」算成目标 quota 覆盖写回 NewAPI。
        'newapi_hourly_credit_sync',
        # 年付订阅在云端直接写余额桶与流水表发积分。
        'grant_yearly_subscription_credits',
    }
)


class RetiredCreditPathError(RuntimeError):
    """命中已退役的云端积分写入路径。"""


def assert_no_retired_credit_tasks(schedule: dict) -> None:
    """启动守卫：beat 调度表里出现已退役积分任务即启动失败。

    :param schedule: celery beat 调度表（`{显示名: {'task': 任务名, ...}}`）
    :raises RetiredCreditPathError: 命中任一已退役任务名
    """
    offenders = sorted(
        {
            str(entry.get('task'))
            for entry in schedule.values()
            if isinstance(entry, dict) and str(entry.get('task')) in RETIRED_CREDIT_TASK_NAMES
        }
    )
    if offenders:
        raise RetiredCreditPathError(
            f'检测到已退役的云端积分定时任务仍在调度表中: {", ".join(offenders)}。'
            'NewAPI 是积分唯一权威，这些任务会把云端算出的余额反向覆盖回 NewAPI，必须先从调度表移除。'
        )


def record_absolute_quota_write_attempt(reason: str) -> None:
    """记录一次「对 NewAPI 设置绝对 quota」的尝试。

    这是本轮重构要消灭的反向数据流，计数器必须恒为 0；出现即 `error` 告警。
    """
    NEWAPI_ABSOLUTE_QUOTA_WRITE_TOTAL.labels(reason=reason).inc()
    log.error(f'[CreditAuthority] 拦截「设置 NewAPI 绝对 quota」调用: reason={reason}。NewAPI 是积分唯一权威，云端不得覆盖余额。')


def record_legacy_credit_write_attempt(reason: str) -> None:
    """记录一次「直接写云端旧余额表」的尝试，计数器同样必须恒为 0。"""
    LEGACY_CREDIT_WRITE_ATTEMPT_TOTAL.labels(reason=reason).inc()
    log.error(f'[CreditAuthority] 拦截「直接写云端余额表」调用: reason={reason}。云端已无余额权威，请改走履约事件。')
