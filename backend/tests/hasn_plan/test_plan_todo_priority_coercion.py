"""待办 priority 归一化回归测试（修 MCP 工具传字符串 priority → SMALLINT 列 500）。

背景：`hasn.plan.todo.create` 等工具 schema 历史把 priority 声明为 string，分身常传
"high"/"medium"/"low" 语义值；untyped body 直透到 SMALLINT 列时 PostgreSQL 报错 → 500。
service 层 `_coerce_priority` 把语义词/数字串/int 统一归一化为 1–3 的整数。

- 纯函数单元：覆盖语义词/数字串/越界 clamp/非法回落（无需 DB）。
- 真实 PG(15432)：create_todo 传字符串 priority 不再 500，落库为正确 int；事务回滚不污染库。
需要：export DATABASE_PORT=15432。
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from backend.app.hasn_plan.service.plan_app_service import PlanService, _coerce_priority
from backend.database.db import async_db_session


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        ('low', 1),
        ('medium', 2),
        ('normal', 2),
        ('mid', 2),
        ('high', 3),
        ('urgent', 3),
        ('HIGH', 3),  # 不分大小写
        ('  high  ', 3),  # 去空白
        ('1', 1),  # 数字串
        ('2', 2),
        ('3', 3),
        ('5', 3),  # 越界向上 clamp 到 3
        ('0', 1),  # 越界向下 clamp 到 1
        (1, 1),  # 已是 int 原样
        (2, 2),
        (9, 3),  # int 越界 clamp
        (-1, 1),  # int 越界 clamp
        ('随便写的', 2),  # 无法识别 → 默认中等
        ('', 2),  # 空串 → 默认
        (None, 2),  # None → 默认（防御；实际 _pick 已过滤 None）
        (True, 2),  # bool 不当作有效优先级 → 默认
        (False, 2),
    ],
)
def test_coerce_priority(value: object, expected: int) -> None:
    """语义词/数字串/int/越界/非法 一律归一化为 1–3，无法识别回落默认 2。"""
    assert _coerce_priority(value) == expected


@pytest.mark.asyncio(loop_scope='session')
async def test_create_todo_string_priority_persists_as_int() -> None:
    """真实 PG：create_todo 传字符串 priority 不再 500，按语义/数字落库为正确 int。"""
    owner = f'hasnOwner_{uuid4().hex[:18]}'
    svc = PlanService()
    async with async_db_session() as db:
        try:
            # 语义词 "high" → 3（此前直透字符串到 SMALLINT 列 → 500）
            high = await svc.create_todo(db, owner=owner, data={'title': '高优先', 'priority': 'high'})
            assert high['priority'] == 3

            # 语义词 "low" → 1
            low = await svc.create_todo(db, owner=owner, data={'title': '低优先', 'priority': 'low'})
            assert low['priority'] == 1

            # 数字串 "2" → 2
            mid = await svc.create_todo(db, owner=owner, data={'title': '数字串', 'priority': '2'})
            assert mid['priority'] == 2

            # 无法识别 → 默认 2（不报错、不写脏值）
            junk = await svc.create_todo(db, owner=owner, data={'title': '非法值', 'priority': '随便写'})
            assert junk['priority'] == 2

            # 不传 priority → DB 默认 2
            plain = await svc.create_todo(db, owner=owner, data={'title': '不传优先级'})
            assert plain['priority'] == 2
        finally:
            await db.rollback()
