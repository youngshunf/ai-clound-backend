"""doc94 P0 止血守卫：云端不得再制造额度。

这三条断言合起来堵住「云端算余额 → 覆盖 NewAPI」的所有入口：
定时任务不再排班、绝对 quota 写接口调用即失败并计数、模拟支付端点返回 410。
"""

import pytest

from fastapi.routing import APIRoute

from backend.app.billing.core.retired_credit_paths import (
    RETIRED_CREDIT_TASK_NAMES,
    RetiredCreditPathError,
    assert_no_retired_credit_tasks,
)
from backend.app.newapi.client import NewApiAdminClient, NewApiError
from backend.app.task.tasks.beat import LOCAL_BEAT_SCHEDULE


def test_current_beat_schedule_has_no_retired_credit_tasks() -> None:
    """现役调度表里不得再出现已退役的积分任务。"""
    assert_no_retired_credit_tasks(LOCAL_BEAT_SCHEDULE)

    scheduled = {str(entry.get('task')) for entry in LOCAL_BEAT_SCHEDULE.values() if isinstance(entry, dict)}
    assert not (scheduled & RETIRED_CREDIT_TASK_NAMES)


def test_startup_guard_rejects_reintroduced_credit_sync_task() -> None:
    """有人把每小时积分对账任务加回调度表时，进程必须启动失败而不是安静地跑起来。"""
    with pytest.raises(RetiredCreditPathError) as exc:
        assert_no_retired_credit_tasks(
            {
                '积分账本每小时对账': {'task': 'newapi_hourly_credit_sync', 'schedule': None},
            }
        )
    assert 'newapi_hourly_credit_sync' in str(exc.value)


@pytest.mark.asyncio
async def test_set_user_quota_is_blocked_and_counted() -> None:
    """绝对 quota 写接口必须调用即失败，并把计数器抬起来（该计数器必须恒为 0）。"""
    from backend.app.billing.observability.metrics import NEWAPI_ABSOLUTE_QUOTA_WRITE_TOTAL

    before = NEWAPI_ABSOLUTE_QUOTA_WRITE_TOTAL.labels(reason='unit-test')._value.get()

    client = NewApiAdminClient(base_url='http://newapi.invalid/api', access_token='x', admin_user_id=1)
    with pytest.raises(NewApiError) as exc:
        await client.set_user_quota(newapi_user_id=1, quota=123, reason='unit-test')
    assert '已封禁' in str(exc.value)

    after = NEWAPI_ABSOLUTE_QUOTA_WRITE_TOTAL.labels(reason='unit-test')._value.get()
    assert after == before + 1


def test_simulated_payment_endpoints_are_gone() -> None:
    """模拟支付端点必须声明 410：不经真实支付即可改订阅或发积分的入口不能留着。"""
    from backend.app.billing.api.v1.app.subscription import router

    retired = {
        route.path: route
        for route in router.routes
        if isinstance(route, APIRoute) and route.path in {'/upgrade', '/purchase'}
    }
    assert set(retired) == {'/upgrade', '/purchase'}
    for path, route in retired.items():
        assert route.status_code == 410, f'{path} 必须返回 410 Gone'
