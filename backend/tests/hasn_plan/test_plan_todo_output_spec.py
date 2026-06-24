"""P6-A：待办 output_spec（输出要求）真实 PG 落库/读回验证。

零 mock：用真实本地 PostgreSQL(15432) 跑 PlanService.create_todo/get_todo；事务回滚不污染库。
需要：export DATABASE_PORT=15432。
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from backend.app.hasn_plan.service.plan_app_service import PlanService
from backend.database.db import async_db_session

pytestmark = pytest.mark.asyncio(loop_scope='module')


async def test_create_todo_persists_output_spec() -> None:
    owner = f'hasnOwner_{uuid4().hex[:18]}'
    svc = PlanService()
    spec = {
        'required': True,
        'expects': [
            {'kind': 'document', 'format': 'markdown', 'note': '含 概览/方案/风险 三段，≥800 字'}
        ],
    }
    async with async_db_session() as db:
        try:
            created = await svc.create_todo(
                db,
                owner=owner,
                data={
                    'title': '写竞品调研报告',
                    'notes': '调研 5 家竞品，产出可执行结论',
                    'actor': 'agent',
                    'output_spec': spec,
                    'source': 'decompose',
                },
            )
            assert created['output_spec'] == spec
            assert created['notes']  # 详细任务必填（分解场景，工具/校验层强制）

            # 读回一致
            got = await svc.get_todo(db, owner=owner, pk=created['id'])
            assert got['output_spec']['required'] is True
            assert got['output_spec']['expects'][0]['kind'] == 'document'
            assert got['output_spec']['expects'][0]['format'] == 'markdown'

            # 不传 output_spec → None（owner 亲为待办可空）
            plain = await svc.create_todo(
                db, owner=owner, data={'title': '自己去健身', 'actor': 'owner'}
            )
            assert plain['output_spec'] is None
        finally:
            await db.rollback()
