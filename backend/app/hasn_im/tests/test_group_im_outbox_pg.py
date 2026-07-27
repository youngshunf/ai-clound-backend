"""群邀请卡生产方 outbox 的真实 PostgreSQL 测试。"""

from __future__ import annotations

import time
import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_core import HasnAgents, HasnHumans
from backend.app.hasn.service.group_im_outbox import build_group_im_relay
from backend.app.hasn.service.hasn_group_service import hasn_group_service
from backend.app.hasn_im.application.local_gateway import PythonLocalImGateway
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.database.schema_names import SCHEMA_NAMES

pytestmark = pytest.mark.asyncio

_MESSAGES = SCHEMA_NAMES.im_table('hasn_messages')
_INVITES = SCHEMA_NAMES.im_table('hasn_group_agent_invites')
_CONVERSATIONS = SCHEMA_NAMES.im_table('hasn_conversations')
_MEMBERSHIPS = SCHEMA_NAMES.im_table('hasn_conversation_memberships')
_EVENTS = SCHEMA_NAMES.im_event_table('integration_events')
_OUTBOX = 'public.hasn_group_im_command_outbox'


@pytest_asyncio.fixture
async def group_context():
    """写入群主、另一位主人及其分身。"""
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.connect() as connection:
            await connection.execute(sa.text('SELECT 1'))
    except Exception:
        await engine.dispose()
        pytest.fail('群 outbox 测试要求真实 PostgreSQL 可用')

    tag = uuid.uuid4().hex[:10]
    owner_id = f'h_group_owner_{tag}'
    peer_owner_id = f'h_group_peer_{tag}'
    peer_agent_id = f'a_group_peer_{tag}'
    user_seed = 2_900_000 + int(uuid.uuid4().int % 700_000)
    async with session_factory.begin() as db:
        db.add_all(
            [
                HasnHumans(
                    hasn_id=owner_id,
                    star_id=f's_group_owner_{tag}',
                    user_id=user_seed,
                    nickname=f'群主{tag}',
                    status='active',
                ),
                HasnHumans(
                    hasn_id=peer_owner_id,
                    star_id=f's_group_peer_{tag}',
                    user_id=user_seed + 1,
                    nickname=f'分身主人{tag}',
                    status='active',
                ),
                HasnAgents(
                    hasn_id=peer_agent_id,
                    star_id=f'sa_group_peer_{tag}',
                    owner_id=peer_owner_id,
                    display_name=f'受邀分身{tag}',
                    agent_name=f'group-peer-{tag}',
                    status='active',
                ),
            ]
        )
    try:
        yield {
            'session_factory': session_factory,
            'gateway': PythonLocalImGateway(session_factory),
            'owner_id': owner_id,
            'peer_owner_id': peer_owner_id,
            'peer_agent_id': peer_agent_id,
            'tag': tag,
        }
    finally:
        async with session_factory.begin() as db:
            conversation_ids = list(
                (
                    await db.execute(
                        sa.text(
                            f'SELECT id::text FROM {_CONVERSATIONS} '
                            'WHERE group_owner_id = :owner_id '
                            'OR participant_a_id = ANY(:members) '
                            'OR participant_b_id = ANY(:members)'
                        ),
                        {
                            'owner_id': owner_id,
                            'members': [owner_id, peer_owner_id, peer_agent_id],
                        },
                    )
                ).scalars()
            )
            await db.execute(
                sa.text(
                    f'DELETE FROM {_OUTBOX} '
                    "WHERE payload->'principal'->>'canonical_sender' = :owner_id "
                    "OR payload->'message'->'content'->'resource'->'metadata'"
                    "->>'agent_hasn_id' = :agent_id"
                ),
                {'owner_id': owner_id, 'agent_id': peer_agent_id},
            )
            if conversation_ids:
                params = {'conversation_ids': conversation_ids}
                await db.execute(
                    sa.text(
                        f'DELETE FROM {_EVENTS} '
                        'WHERE aggregate_id = ANY(:conversation_ids)'
                    ),
                    params,
                )
                await db.execute(
                    sa.text(
                        f'DELETE FROM {_MESSAGES} '
                        'WHERE conversation_id::text = ANY(:conversation_ids)'
                    ),
                    params,
                )
                await db.execute(
                    sa.text(
                        f'DELETE FROM {_INVITES} '
                        'WHERE conversation_id::text = ANY(:conversation_ids)'
                    ),
                    params,
                )
                await db.execute(
                    sa.text(
                        f'DELETE FROM {_MEMBERSHIPS} '
                        'WHERE conversation_id::text = ANY(:conversation_ids)'
                    ),
                    params,
                )
                await db.execute(
                    sa.text(
                        f'DELETE FROM {_CONVERSATIONS} '
                        'WHERE id::text = ANY(:conversation_ids)'
                    ),
                    params,
                )
            await db.execute(
                sa.text(
                    'DELETE FROM public.hasn_agents WHERE hasn_id = :agent_id'
                ),
                {'agent_id': peer_agent_id},
            )
            await db.execute(
                sa.text(
                    'DELETE FROM public.hasn_humans '
                    'WHERE hasn_id = ANY(:human_ids)'
                ),
                {'human_ids': [owner_id, peer_owner_id]},
            )
        await engine.dispose()


async def test_group_invite_rollback_commit_and_relay(group_context) -> None:
    """邀请回滚不留命令；提交后真实 relay 只投递一张可操作卡。"""
    session_factory = group_context['session_factory']
    gateway = group_context['gateway']
    owner_id = group_context['owner_id']
    peer_owner_id = group_context['peer_owner_id']
    peer_agent_id = group_context['peer_agent_id']

    async with session_factory() as db:
        group = await hasn_group_service.create_group(
            db,
            owner_hasn_id=owner_id,
            title=f'可靠邀请群{group_context["tag"]}',
        )
        await db.commit()
    group_id = group['group_id']

    async with session_factory() as db:
        rolled_back = await hasn_group_service.add_members(
            db,
            actor_hasn_id=owner_id,
            group_id=group_id,
            members=[{'hasn_id': peer_agent_id}],
            im_gateway=gateway,
        )
        rolled_invite_id = rolled_back['invited_agents'][0]['invite_id']
        await db.rollback()
    rolled_key = f'group:agent-invite:{rolled_invite_id}:owner-card'
    async with session_factory() as db:
        assert (
            await db.scalar(
                sa.text(f'SELECT count(*) FROM {_OUTBOX} WHERE idempotency_key = :key'),
                {'key': rolled_key},
            )
        ) == 0
        assert (
            await db.scalar(
                sa.text(f'SELECT count(*) FROM {_INVITES} WHERE id = :invite_id'),
                {'invite_id': rolled_invite_id},
            )
        ) == 0

    async with session_factory() as db:
        created = await hasn_group_service.add_members(
            db,
            actor_hasn_id=owner_id,
            group_id=group_id,
            members=[{'hasn_id': peer_agent_id}],
            im_gateway=gateway,
        )
        invite_id = created['invited_agents'][0]['invite_id']
        await db.commit()
    key = f'group:agent-invite:{invite_id}:owner-card'

    async with session_factory() as db:
        duplicate = await hasn_group_service.add_members(
            db,
            actor_hasn_id=owner_id,
            group_id=group_id,
            members=[{'hasn_id': peer_agent_id}],
            im_gateway=gateway,
        )
        await db.commit()
    assert duplicate['invited_agents'] == []
    async with session_factory() as db:
        assert (
            await db.scalar(
                sa.text(f'SELECT count(*) FROM {_OUTBOX} WHERE idempotency_key = :key'),
                {'key': key},
            )
        ) == 1
        assert (
            await db.scalar(
                sa.text(f'SELECT count(*) FROM {_MESSAGES} WHERE local_id = :key'),
                {'key': key},
            )
        ) == 0

    relay = build_group_im_relay(
        session_factory=session_factory,
        gateway=gateway,
        instance_id=f'group-pg-{uuid.uuid4().hex}',
    )
    stats = await relay.drain_once(now=int(time.time()) + 2)
    assert stats.completed >= 1

    async with session_factory() as db:
        outbox_status = await db.scalar(
            sa.text(f'SELECT status FROM {_OUTBOX} WHERE idempotency_key = :key'),
            {'key': key},
        )
        assert outbox_status == 'completed'
        message = (
            await db.execute(
                sa.text(
                    f'SELECT content, from_id, to_id FROM {_MESSAGES} '
                    'WHERE local_id = :key'
                ),
                {'key': key},
            )
        ).mappings().one()
        assert message['from_id'] == peer_agent_id
        assert message['to_id'] == peer_owner_id
        assert message['content']['resource']['id'] == group_id
        assert message['content']['resource']['uri'] == f'hasn://groups/{group_id}'
        assert message['content']['primary_action']['event']['payload'] == {
            'invite_id': invite_id,
            'group_id': group_id,
        }
