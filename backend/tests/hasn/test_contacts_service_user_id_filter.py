"""Phase 1 US-002: hasn_contacts_service.get_list 必须按 user_id 过滤。

之前 agent_list_hasn_contactss 把 user_id 传给 service，但 service.get_list 忽略
该参数 → 任意 agent 用任意 user_id 都能拿到全表，属于权限泄露。

本单测不依赖真实数据库：mock paging_data 以截获 get_list 内部构造的 SELECT
statement，断言两个不同 user_id 产生不同（参数化到实际 user_id）的 SQL，
且不带 user_id 调用时不注入相关 WHERE 子句。
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sqlalchemy.dialects.postgresql import dialect as pg_dialect

from backend.app.hasn.service.hasn_contacts_service import hasn_contacts_service


def _compiled_sql(stmt: Any) -> tuple[str, dict]:
    compiled = stmt.compile(
        dialect=pg_dialect(),
        compile_kwargs={'literal_binds': False},
    )
    return str(compiled), dict(compiled.params)


@pytest.mark.asyncio
async def test_get_list_filters_by_user_id_via_hasn_humans_subquery() -> None:
    """传入 user_id 时，SELECT 应包含 owner_id IN (SELECT hasn_humans.hasn_id WHERE user_id=:X)。"""
    captured: list = []

    def fake_paging_data(_db: object, stmt: object, **_kwargs: object) -> dict[str, object]:
        captured.append(stmt)
        return {'items': [], 'total': 0}

    db = MagicMock()

    with patch(
        'backend.app.hasn.service.hasn_contacts_service.paging_data',
        new=AsyncMock(side_effect=fake_paging_data),
    ):
        await hasn_contacts_service.get_list(db=db, user_id=100)
        await hasn_contacts_service.get_list(db=db, user_id=200)

    assert len(captured) == 2, f'expected 2 select statements, got {len(captured)}'

    sql_a, params_a = _compiled_sql(captured[0])
    sql_b, params_b = _compiled_sql(captured[1])

    # 两次查询都走 hasn_humans 子查询，WHERE 条件结构相同，仅 user_id 绑定值不同。
    assert 'hasn_humans' in sql_a.lower(), f'expected hasn_humans subquery, got: {sql_a}'
    assert 'hasn_humans' in sql_b.lower()
    assert 'owner_id in' in sql_a.lower().replace('\n', ' ')

    assert 100 in params_a.values(), f'expected user_id=100 bound, got {params_a}'
    assert 200 in params_b.values(), f'expected user_id=200 bound, got {params_b}'
    # 两组绑定参数不应相同（保证 user_id 真的进了 WHERE）。
    assert params_a != params_b, (
        f'expected different bound params for different user_id, got {params_a} == {params_b}'
    )


@pytest.mark.asyncio
async def test_get_list_without_user_id_skips_hasn_humans_subquery() -> None:
    """不传 user_id（admin/app 既有调用）应不注入 hasn_humans 子查询，保持原行为。"""
    captured: list = []

    def fake_paging_data(_db: object, stmt: object, **_kwargs: object) -> dict[str, object]:
        captured.append(stmt)
        return {'items': [], 'total': 0}

    db = MagicMock()

    with patch(
        'backend.app.hasn.service.hasn_contacts_service.paging_data',
        new=AsyncMock(side_effect=fake_paging_data),
    ):
        await hasn_contacts_service.get_list(db=db)

    assert len(captured) == 1
    sql, _ = _compiled_sql(captured[0])
    assert 'hasn_humans' not in sql.lower(), (
        f'unexpected hasn_humans subquery injected without user_id: {sql}'
    )
