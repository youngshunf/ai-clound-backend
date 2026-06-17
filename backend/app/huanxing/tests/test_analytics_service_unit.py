from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy.exc import ProgrammingError

from backend.app.huanxing.service.analytics_service import analytics_service

pytestmark = pytest.mark.asyncio


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


class _DbWithoutLegacyUsageTable:
    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
        sql = str(stmt)
        if 'llm_usage_log' in sql:
            raise ProgrammingError(sql, params, Exception('relation "llm_usage_log" does not exist'))
        if 'credit_transaction' in sql and 'GROUP BY 1' in sql:
            return _RowsResult([])
        if 'user_subscription' in sql and 'GROUP BY tier' in sql:
            return _RowsResult([])
        if 'credit_transaction' in sql:
            return _ScalarResult(0)
        if 'user_subscription' in sql:
            return _ScalarResult(0)
        raise AssertionError(f'unexpected query: {sql}')


async def test_get_analytics_tolerates_missing_legacy_llm_usage_log() -> None:
    """生产已迁移到 new-api 后，旧 llm_usage_log 缺表不应拖垮分析看板."""
    data = await analytics_service.get_analytics(db=_DbWithoutLegacyUsageTable(), days=7)  # type: ignore[arg-type]

    assert data['overview']['total_api_calls'] == 0
    assert data['overview']['period_api_calls'] == 0
    assert data['trends']['api_calls'] == [0] * len(data['trends']['dates'])
    assert data['trends']['token_usage'] == [0] * len(data['trends']['dates'])
    assert data['model_distribution'] == []
    assert data['token_ranking'] == []
