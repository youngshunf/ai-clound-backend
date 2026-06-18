from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from backend.app.huanxing.service import analytics_service as analytics_module

pytestmark = pytest.mark.asyncio

_SHANGHAI_TZ = ZoneInfo('Asia/Shanghai')


@dataclass
class _ScalarResult:
    value: Any

    def scalar(self) -> Any:
        return self.value


@dataclass
class _RowsResult:
    rows: list[Any]

    def fetchall(self) -> list[Any]:
        return self.rows


class _DbWithoutLocalUsageTable:
    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
        sql = str(stmt)
        if 'llm_usage_log' in sql:
            raise AssertionError('analytics must read LLM usage from new-api, not llm_usage_log')
        if 'credit_transaction' in sql and 'GROUP BY 1' in sql:
            return _RowsResult([
                ('2026-06-15', 1.5),
                ('2026-06-16', 0),
            ])
        if 'user_subscription' in sql and 'GROUP BY tier' in sql:
            return _RowsResult([('free', 2), ('pro', 1)])
        if 'credit_transaction' in sql:
            return _ScalarResult(3)
        if 'user_subscription' in sql:
            return _ScalarResult(2)
        raise AssertionError(f'unexpected query: {sql}')


class _FakeNewApiClient:
    def __init__(self) -> None:
        self.quota_calls: list[tuple[int, int]] = []

    async def get_quota_data(self, *, username: str, start_timestamp: int, end_timestamp: int) -> list[dict]:
        self.quota_calls.append((start_timestamp, end_timestamp))
        assert username == ''
        start_day = datetime.fromtimestamp(start_timestamp, tz=_SHANGHAI_TZ).date()
        day_1 = datetime.combine(start_day + timedelta(days=1), datetime.min.time(), tzinfo=_SHANGHAI_TZ)
        day_2 = datetime.combine(start_day + timedelta(days=2), datetime.min.time(), tzinfo=_SHANGHAI_TZ)
        return [
            {
                'model_name': 'gpt-4o',
                'created_at': int(day_1.timestamp()),
                'quota': 2_000_000,
                'token_used': 120,
                'count': 2,
            },
            {
                'model_name': 'claude-sonnet',
                'created_at': int(day_2.timestamp()),
                'quota': 1_000_000,
                'token_used': 80,
                'count': 1,
            },
            {
                'model_name': 'gpt-4o',
                'created_at': int(day_2.timestamp()),
                'quota': 3_000_000,
                'token_used': 240,
                'count': 3,
            },
        ]


async def test_get_analytics_reads_llm_usage_from_newapi_not_legacy_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """llm_usage_log 已删除；管理看板 LLM 用量必须走 new-api 权威数据源."""
    fake_client = _FakeNewApiClient()
    monkeypatch.setattr(analytics_module, 'newapi_admin_client', fake_client)

    data = await analytics_module.analytics_service.get_analytics(
        db=_DbWithoutLocalUsageTable(), days=7  # type: ignore[arg-type]
    )

    assert fake_client.quota_calls
    assert data['overview']['total_api_calls'] == 6
    assert data['overview']['period_api_calls'] == 6
    assert data['overview']['total_income_credits'] == 3
    assert data['overview']['period_income_credits'] == 3
    assert len(data['trends']['dates']) == 8
    assert len(data['trends']['api_calls']) == 8
    assert sorted(value for value in data['trends']['api_calls'] if value) == [2, 4]
    assert sorted(value for value in data['trends']['token_usage'] if value) == [120, 320]
    assert data['model_distribution'] == [
        {'name': 'gpt-4o', 'value': 5},
        {'name': 'claude-sonnet', 'value': 1},
    ]
    assert data['token_ranking'] == [
        {
            'model': 'gpt-4o',
            'input_tokens': 0,
            'output_tokens': 0,
            'total_tokens': 360,
            'calls': 5,
        },
        {
            'model': 'claude-sonnet',
            'input_tokens': 0,
            'output_tokens': 0,
            'total_tokens': 80,
            'calls': 1,
        },
    ]
