"""云端 ↔ NewAPI 积分账户快照的**跨仓**字段契约。

为什么需要这道守卫：`credit_account_service` 用 `item.get('next_reset_at')` 从 NewAPI
的响应里取字段，而云端这边所有测试的 fixture 都是**我们自己写的**——fixture 里写
`next_reset_at`，代码里也读 `next_reset_at`，两边一致所以永远绿；但如果 Go 那侧的
json tag 其实叫别的名字（或被改名），云端拿到的**恒为 None**，而这里一条红都不会有。

这正是「两侧测试各抄自己那侧的字段名 → 双绿而端点从未成功」。唯一能证伪它的办法，
是让断言去读**对面仓的源码**，而不是读我们自己的假数据。

守的是 json tag（wire 名），不是 Go 字段名——前者才会出现在网络上。
"""

from __future__ import annotations

import re

from pathlib import Path

import pytest

#: 云端确实会从账户快照里读取的 wire 字段。改这份清单前先改消费方代码。
CONSUMED_SUBSCRIPTION_FIELDS = frozenset({
    'external_subscription_id',
    'status',
    'cycle_limit_credits',
    'cycle_used_credits',
    'cycle_remaining_credits',
    'cycle_start_at',
    # 「本期额度何时清零」的唯一权威来源。cycle_end_at 是合同终止时刻，不能顶替它。
    'next_reset_at',
    'cycle_end_at',
})
CONSUMED_ACCOUNT_FIELDS = frozenset({'subscriptions', 'total_available_credits', 'measured_at', 'wallet'})

_DTO_PATH = Path(__file__).resolve().parents[2] / '..' / '..' / 'hasn-apps' / 'new-api' / 'dto' / 'internal_credit.go'
_JSON_TAG = re.compile(r'json:"([^",]+)')


def _struct_json_tags(source: str, struct_name: str) -> set[str]:
    """取出某个 Go struct 里所有字段的 json tag 名。"""
    match = re.search(rf'type\s+{struct_name}\s+struct\s*\{{(.*?)\n}}', source, re.DOTALL)
    assert match, f'在 NewAPI 的 DTO 里找不到 struct {struct_name}——它被改名或搬家了，本守卫已失效'
    return set(_JSON_TAG.findall(match.group(1)))


@pytest.fixture(scope='module')
def dto_source() -> str:
    path = _DTO_PATH.resolve()
    if not path.exists():
        # 子仓不在场是「没判过」，不是「通过」——响亮地跳过，不静默放行。
        pytest.skip(f'new-api 仓不在场，跨仓字段契约未校验: {path}')
    return path.read_text(encoding='utf-8')


def test_subscription_view_carries_every_field_the_cloud_reads(dto_source: str) -> None:
    """云端读的每个订阅字段，NewAPI 侧都必须真的以同名 json tag 发出来。"""
    tags = _struct_json_tags(dto_source, 'CreditSubscriptionView')
    missing = CONSUMED_SUBSCRIPTION_FIELDS - tags
    assert not missing, (
        f'云端从订阅快照里读取了 NewAPI 并不发送的字段: {sorted(missing)}。'
        f'NewAPI 实际发送的是 {sorted(tags)}。这类不一致不会让任何一侧的测试变红——'
        f'云端只会拿到 None，然后把「读不到」当成「没有」。'
    )


def test_account_snapshot_carries_every_field_the_cloud_reads(dto_source: str) -> None:
    """账户外层同理：measured_at / total_available_credits 少一个都会静默降级。"""
    tags = _struct_json_tags(dto_source, 'CreditAccount')
    missing = CONSUMED_ACCOUNT_FIELDS - tags
    assert not missing, f'云端从账户快照里读取了 NewAPI 并不发送的字段: {sorted(missing)}'


def test_guard_itself_still_matches_something(dto_source: str) -> None:
    """守卫自检：正则失配或 struct 改名时必须报红，而不是解析出空集然后「通过」。

    没有这一条，上面两个断言在 `tags` 为空集时会**恒真**（空集减任何集合仍是空集
    的反面——`CONSUMED - set()` 非空，其实会红）；但如果 struct 名改了，
    `_struct_json_tags` 里的 assert 才是那道防线，这里再确认一次它确实取到了东西。
    """
    assert len(_struct_json_tags(dto_source, 'CreditSubscriptionView')) >= len(CONSUMED_SUBSCRIPTION_FIELDS)
    assert 'cycle_start_at' in _struct_json_tags(dto_source, 'CreditSubscriptionView')
