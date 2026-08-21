"""工作会话结果生产方 outbox 的真实 PostgreSQL 故障窗口测试。"""

from __future__ import annotations

import time
import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_core import HasnAgents, HasnHumans
from backend.app.hasn.service.hasn_sessions_service import hasn_sessions_service
from backend.app.hasn.service.session_im_outbox import build_session_im_relay
from backend.app.hasn_im.application.local_gateway import PythonLocalImGateway
from backend.app.hasn_im.ports.dto import (
    ActorKind,
    EnsureDirectConversationCommand,
    ServicePrincipal,
)
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.database.schema_names import SCHEMA_NAMES

pytestmark = pytest.mark.asyncio

_MESSAGES = SCHEMA_NAMES.im_table('hasn_messages')
_EVENTS = SCHEMA_NAMES.im_event_table('integration_events')
_OUTBOX = 'public.hasn_session_im_command_outbox'


@pytest_asyncio.fixture
async def pg_context():
    """建立无连接复用的真实会话工厂，并写入唯一身份。"""
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.connect() as connection:
            await connection.execute(sa.text('SELECT 1'))
    except Exception:
        await engine.dispose()
        pytest.fail('工作会话 outbox 测试要求真实 PostgreSQL 可用')

    tag = uuid.uuid4().hex[:10]
    owner_id = f'h_session_{tag}'
    other_id = f'h_other_{tag}'
    agent_id = f'a_session_{tag}'
    user_seed = 2_100_000 + int(uuid.uuid4().int % 700_000)
    async with session_factory.begin() as db:
        db.add_all(
            [
                HasnHumans(
                    hasn_id=owner_id,
                    star_id=f's_session_{tag}',
                    user_id=user_seed,
                    nickname=f'会话主人{tag}',
                    status='active',
                ),
                HasnHumans(
                    hasn_id=other_id,
                    star_id=f's_other_{tag}',
                    user_id=user_seed + 1,
                    nickname=f'其他主人{tag}',
                    status='active',
                ),
                HasnAgents(
                    hasn_id=agent_id,
                    star_id=f'sa_session_{tag}',
                    owner_id=owner_id,
                    display_name=f'会话分身{tag}',
                    agent_name=f'session-{tag}',
                    status='active',
                ),
            ]
        )
    try:
        yield {
            'session_factory': session_factory,
            'gateway': PythonLocalImGateway(session_factory),
            'owner_id': owner_id,
            'other_id': other_id,
            'agent_id': agent_id,
            'tag': tag,
        }
    finally:
        await engine.dispose()


def _projection(agent_id: str) -> dict[str, object]:
    """构造工作会话完成投影的真实业务载荷。"""
    return {
        'agent_id': agent_id,
        'origin_type': 'task_run',
        'origin_ref': 'task_run:987',
        'title': '生成客户跟进建议',
        'summary': '已生成客户优先级和跟进建议。',
        'status': 'success',
        'completion_reason': 'auto_on_final',
        'deep_link': 'hasn://tasks/sessions/session-outbox',
        'task_id': 987,
        'task_run_id': 654,
    }


async def test_projection_rollback_commit_relay_and_dedupe(pg_context) -> None:
    """业务回滚无命令；提交后 relay 可恢复；响应丢失重试只产生一条消息。"""
    session_factory = pg_context['session_factory']
    gateway = pg_context['gateway']
    owner_id = pg_context['owner_id']
    agent_id = pg_context['agent_id']
    session_id = f'session-{pg_context["tag"]}'
    dedupe_key = f'work_session_result:{session_id}:final'

    async with session_factory() as db:
        rolled_back = await hasn_sessions_service.project_work_session_result(
            db=db,
            owner_id=owner_id,
            session_id=session_id,
            projection_data=_projection(agent_id),
            im_gateway=gateway,
        )
        assert rolled_back['delivery_state'] == 'pending'
        await db.rollback()

    async with session_factory() as db:
        assert (
            await db.scalar(
                sa.text(f'SELECT count(*) FROM {_OUTBOX} WHERE idempotency_key = :key'),
                {'key': dedupe_key},
            )
        ) == 0
        assert (
            await db.scalar(
                sa.text(f'SELECT count(*) FROM {_MESSAGES} WHERE local_id = :key'),
                {'key': dedupe_key},
            )
        ) == 0

    async with session_factory() as db:
        first = await hasn_sessions_service.project_work_session_result(
            db=db,
            owner_id=owner_id,
            session_id=session_id,
            projection_data=_projection(agent_id),
            im_gateway=gateway,
        )
        await db.commit()
    assert first['result_message_id'] is None
    assert first['delivery_state'] == 'pending'
    assert first['created'] is True

    # 模拟业务响应在 commit 后丢失：客户端以相同稳定键重试。
    async with session_factory() as db:
        retry_before_relay = await hasn_sessions_service.project_work_session_result(
            db=db,
            owner_id=owner_id,
            session_id=session_id,
            projection_data=_projection(agent_id),
            im_gateway=gateway,
        )
        await db.commit()
    assert retry_before_relay['delivery_command_id'] == first['delivery_command_id']
    assert retry_before_relay['created'] is False

    relay = build_session_im_relay(
        session_factory=session_factory,
        gateway=gateway,
        instance_id=f'session-pg-{uuid.uuid4().hex}',
    )
    stats = await relay.drain_once(now=int(time.time()) + 2)
    assert stats.completed >= 1
    async with session_factory() as db:
        assert (
            await db.scalar(
                sa.text(
                    f'SELECT status FROM {_OUTBOX} WHERE idempotency_key = :key'
                ),
                {'key': dedupe_key},
            )
        ) == 'completed'

    async with session_factory() as db:
        message = (
            await db.execute(
                sa.text(
                    f'SELECT id, conversation_seq, content, origin_session_id '
                    f'FROM {_MESSAGES} WHERE local_id = :key'
                ),
                {'key': dedupe_key},
            )
        ).mappings().one()
        assert message['conversation_seq'] == 1
        assert message['origin_session_id'] == session_id
        assert message['content']['title'] == '任务「生成客户跟进建议」已完成'
        assert message['content']['primary_action']['uri'] == (
            'hasn://tasks/sessions/session-outbox'
        )
        assert (
            await db.scalar(
                sa.text(
                    f'SELECT count(*) FROM {_EVENTS} '
                    "WHERE event_type = 'im.message.committed.v1' "
                    "AND payload->>'message_id' = :message_id"
                ),
                {'message_id': str(message['id'])},
            )
        ) == 1

    async with session_factory() as db:
        retry_after_relay = await hasn_sessions_service.project_work_session_result(
            db=db,
            owner_id=owner_id,
            session_id=session_id,
            projection_data=_projection(agent_id),
            im_gateway=gateway,
        )
        await db.commit()
    assert retry_after_relay['result_message_id'] == str(message['id'])
    assert retry_after_relay['delivery_state'] == 'completed'
    assert retry_after_relay['delivery_command_id'] == first['delivery_command_id']
    assert (await relay.drain_once(now=int(time.time()) + 2)).claimed == 0


async def test_projection_rejects_missing_agent_and_forged_conversation(
    pg_context,
) -> None:
    """缺身份与非主人直聊会话都 fail closed，且不留下生产命令。"""
    session_factory = pg_context['session_factory']
    gateway = pg_context['gateway']
    owner_id = pg_context['owner_id']
    other_id = pg_context['other_id']
    agent_id = pg_context['agent_id']

    async with session_factory() as db:
        with pytest.raises(errors.RequestError):
            await hasn_sessions_service.project_work_session_result(
                db=db,
                owner_id=owner_id,
                session_id=f'missing-{pg_context["tag"]}',
                projection_data={'summary': '缺身份'},
                im_gateway=gateway,
            )
        await db.rollback()

    foreign = await gateway.ensure_direct_conversation(
        EnsureDirectConversationCommand(peer_hasn_id=other_id),
        ServicePrincipal(
            canonical_sender=agent_id,
            actor_kind=ActorKind.AGENT,
        ),
    )
    forged_key = f'work_session_result:forged-{pg_context["tag"]}:final'
    async with session_factory() as db:
        with pytest.raises(errors.ForbiddenError):
            await hasn_sessions_service.project_work_session_result(
                db=db,
                owner_id=owner_id,
                session_id=f'forged-{pg_context["tag"]}',
                projection_data={
                    **_projection(agent_id),
                    'target_conversation_id': foreign.conversation_id,
                },
                im_gateway=gateway,
            )
        await db.rollback()
    async with session_factory() as db:
        assert (
            await db.scalar(
                sa.text(f'SELECT count(*) FROM {_OUTBOX} WHERE idempotency_key = :key'),
                {'key': forged_key},
            )
        ) == 0
