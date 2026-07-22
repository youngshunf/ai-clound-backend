"""通知公告查询的真实 PostgreSQL 契约回归测试。"""

from __future__ import annotations

from uuid import uuid4

import pytest

from backend.database.db import async_db_session
from backend.plugin.notice.crud.crud_notice import notice_dao
from backend.plugin.notice.model.notice import Notice

pytestmark = pytest.mark.asyncio(loop_scope='module')


async def test_notice_dao_returns_typed_orm_entities() -> None:
    """单条、全量与过滤查询必须返回真实公告 ORM 实体。"""
    title = f'质量门禁公告-{uuid4().hex[:8]}'

    async with async_db_session() as db:
        notice = Notice(title=title, type=0, status=1, content='真实 PostgreSQL 查询契约验证')
        db.add(notice)
        await db.flush()

        detail = await notice_dao.get(db, notice.id)
        notices = await notice_dao.get_all(db)
        filtered = (await db.execute(await notice_dao.get_select(title, 0, 1))).scalars().all()

        assert isinstance(detail, Notice)
        assert any(item.id == notice.id and isinstance(item, Notice) for item in notices)
        assert any(item.id == notice.id and isinstance(item, Notice) for item in filtered)
