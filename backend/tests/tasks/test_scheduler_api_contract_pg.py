"""任务调度 API 响应契约的真实 PostgreSQL 回归测试。"""

from __future__ import annotations

from uuid import uuid4

import pytest

from backend.app.task.api.v1.scheduler import get_all_task_schedulers, get_task_scheduler
from backend.app.task.model import TaskScheduler
from backend.app.task.schema.scheduler import GetTaskSchedulerDetail
from backend.common.pagination import PageData
from backend.database.db import async_db_session

pytestmark = pytest.mark.asyncio(loop_scope='module')


def _new_scheduler() -> TaskScheduler:
    """构造可落库且不会实际派发的调度记录。"""
    return TaskScheduler(
        name=f'quality_scheduler_{uuid4().hex}',
        task='backend.app.task.tasks.demo.task_demo',
        args='[]',
        kwargs='{}',
        queue=None,
        exchange=None,
        routing_key=None,
        start_time=None,
        expire_time=None,
        expire_seconds=None,
        type=0,
        interval_every=60,
        interval_period='seconds',
    )


async def test_scheduler_api_returns_declared_detail_dtos() -> None:
    """列表和详情接口必须把真实 ORM 行转换为声明的响应 DTO。"""
    async with async_db_session() as db:
        scheduler = _new_scheduler()
        db.add(scheduler)
        await db.flush()

        all_response = await get_all_task_schedulers(db)
        detail_response = await get_task_scheduler(db, scheduler.id)

        matched = [item for item in all_response.data if item.id == scheduler.id]
        assert len(matched) == 1
        assert isinstance(matched[0], GetTaskSchedulerDetail)
        assert isinstance(detail_response.data, GetTaskSchedulerDetail)
        assert detail_response.data.name == scheduler.name


async def test_scheduler_page_data_converts_real_orm_items_to_detail_dtos() -> None:
    """分页 DTO 必须能将真实 ORM 项转换为公开的调度详情。"""
    async with async_db_session() as db:
        scheduler = _new_scheduler()
        db.add(scheduler)
        await db.flush()

        page_data = PageData[GetTaskSchedulerDetail].model_validate({
            'items': [scheduler],
            'total': 1,
            'page': 1,
            'size': 20,
            'total_pages': 1,
            'links': {
                'first': '?page=1&size=20',
                'last': '?page=1&size=20',
                'self': '?page=1&size=20',
                'next': None,
                'prev': None,
            },
        })

        assert isinstance(page_data.items[0], GetTaskSchedulerDetail)
        assert page_data.items[0].id == scheduler.id
