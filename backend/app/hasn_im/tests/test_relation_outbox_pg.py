"""身份事实到 IM 控制边的事务 outbox 真实 PostgreSQL 故障窗口测试。"""

from __future__ import annotations

import uuid

from datetime import timedelta

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model import (
    HasnAgents,
    HasnContacts,
    HasnHumans,
    HasnRelationCommandOutbox,
)
from backend.app.hasn.service.hasn_relation_command_outbox_service import (
    HasnRelationCommandOutboxService,
    RelationCommandOutboxRelay,
)
from backend.app.hasn_im.adapters.sqlalchemy_relation_gateway import (
    SqlAlchemyRelationGateway,
)
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.utils.timezone import timezone

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def relation_outbox_sessions():
    """为故障窗口测试提供不跨事件循环复用连接的真实数据库会话。"""
    engine = create_async_engine(
        SQLALCHEMY_DATABASE_URL,
        poolclass=NullPool,
        future=True,
    )
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text('SELECT 1'))
    except OperationalError as exc:
        await engine.dispose()
        pytest.fail(f'PostgreSQL 不可达：{exc}')
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


class _FaultAfterImCommit:
    """故障注入：真实完成 IM 写入后模拟 relay 丢失响应。"""

    def __init__(self, real_gateway: SqlAlchemyRelationGateway) -> None:
        self._real_gateway = real_gateway

    async def ensure_owner_agent_control_edge(
        self,
        *,
        owner_hasn_id: str,
        agent_hasn_id: str,
    ) -> dict[str, object]:
        await self._real_gateway.ensure_owner_agent_control_edge(
            owner_hasn_id=owner_hasn_id,
            agent_hasn_id=agent_hasn_id,
        )
        raise ConnectionError('故障注入：IM 已提交但 relay 未收到响应')


async def test_relation_outbox_rollback_and_response_loss_recover(
    relation_outbox_sessions,
) -> None:
    """业务回滚无命令；响应丢失后同键重试只生成一条控制边。"""
    marker = uuid.uuid4().hex
    owner_id = f'h_relout_{marker[:16]}'
    agent_id = f'a_relout_{marker[:16]}'
    rolled_back_agent_id = f'a_relout_rb_{marker[:12]}'
    service = HasnRelationCommandOutboxService()
    real_gateway = SqlAlchemyRelationGateway(
        session_factory=relation_outbox_sessions,
    )

    async with relation_outbox_sessions.begin() as db:
        db.add(
            HasnHumans(
                hasn_id=owner_id,
                star_id=f'h{marker[:24]}',
                user_id=int(marker[:15], 16),
                nickname='控制边测试主人',
                status='active',
            )
        )
        db.add(
            HasnAgents(
                hasn_id=agent_id,
                star_id=f'a{marker[:24]}',
                owner_id=owner_id,
                display_name='控制边测试分身',
                agent_name=f'relout{marker[:10]}',
                api_key_hash=marker,
                status='active',
                created_via='client',
            )
        )

    async with relation_outbox_sessions() as db:
        transaction = await db.begin()
        await service.enqueue_owner_agent_control_edge(
            db,
            owner_hasn_id=owner_id,
            agent_hasn_id=rolled_back_agent_id,
        )
        await transaction.rollback()

    async with relation_outbox_sessions() as db:
        rolled_back_count = await db.scalar(
            sa.select(sa.func.count())
            .select_from(HasnRelationCommandOutbox)
            .where(
                HasnRelationCommandOutbox.peer_hasn_id == rolled_back_agent_id,
            )
        )
        assert rolled_back_count == 0

    async with relation_outbox_sessions.begin() as db:
        command_id = await service.enqueue_owner_agent_control_edge(
            db,
            owner_hasn_id=owner_id,
            agent_hasn_id=agent_id,
        )

    first = RelationCommandOutboxRelay(
        session_factory=relation_outbox_sessions,
        relation_gateway=_FaultAfterImCommit(real_gateway),
        backoff_seconds=(1,),
    )
    first_stats = await first.drain_once(now=timezone.now(), batch_limit=10)
    assert first_stats.claimed == 1
    assert first_stats.retried == 1

    async with relation_outbox_sessions() as db:
        contacts_after_loss = (
            await db.execute(
                sa.select(HasnContacts).where(
                    HasnContacts.owner_id == owner_id,
                    HasnContacts.peer_id == agent_id,
                    HasnContacts.relation_type == 'social',
                )
            )
        ).scalars().all()
        assert len(contacts_after_loss) == 1
        assert contacts_after_loss[0].trust_level == 5
        assert contacts_after_loss[0].status == 'connected'

    recovered = RelationCommandOutboxRelay(
        session_factory=relation_outbox_sessions,
        relation_gateway=real_gateway,
        backoff_seconds=(1,),
    )
    recovered_stats = await recovered.drain_once(
        now=timezone.now() + timedelta(seconds=2),
        batch_limit=10,
    )
    assert recovered_stats.claimed == 1
    assert recovered_stats.completed == 1

    async with relation_outbox_sessions() as db:
        outbox = await db.scalar(
            sa.select(HasnRelationCommandOutbox).where(
                HasnRelationCommandOutbox.command_id == command_id,
            )
        )
        assert outbox is not None
        assert outbox.status == 'completed'
        contact_count = await db.scalar(
            sa.select(sa.func.count())
            .select_from(HasnContacts)
            .where(
                HasnContacts.owner_id == owner_id,
                HasnContacts.peer_id == agent_id,
                HasnContacts.relation_type == 'social',
            )
        )
        assert contact_count == 1

    async with relation_outbox_sessions.begin() as db:
        await db.execute(
            sa.delete(HasnRelationCommandOutbox).where(
                HasnRelationCommandOutbox.owner_hasn_id == owner_id,
            )
        )
        await db.execute(
            sa.delete(HasnContacts).where(
                sa.or_(
                    HasnContacts.owner_id == owner_id,
                    HasnContacts.peer_id == agent_id,
                )
            )
        )
        await db.execute(
            sa.delete(HasnAgents).where(HasnAgents.hasn_id == agent_id)
        )
        await db.execute(
            sa.delete(HasnHumans).where(HasnHumans.hasn_id == owner_id)
        )
