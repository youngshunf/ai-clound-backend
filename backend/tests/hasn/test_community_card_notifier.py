"""分身发完帖子/文章 → 给主人投「可点进详情」卡片消息的回归测试。"""

from __future__ import annotations

import uuid

from datetime import timedelta

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model import HasnAgents, HasnHumans
from backend.app.hasn_im.application.local_gateway import PythonLocalImGateway
from backend.app.hasn.schema.hasn_card_message import validate_card_message_body
from backend.app.hasn_community.service import community_card_notifier as notifier
from backend.app.hasn_community.service.community_im_outbox import (
    build_community_im_relay,
)
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.database.schema_names import SCHEMA_NAMES
from backend.utils.timezone import timezone


def test_build_post_card_headline_status_and_link() -> None:
    card = notifier.build_community_resource_card(
        'post', 'p_abc123', author_name='星创', status='pending_review', preview='一段较长的正文预览……'
    )
    validate_card_message_body(card)  # 不抛即合法
    assert card['title'] == '星创发布了一篇社区帖子'
    assert card['description'] == '一段较长的正文预览……'
    # 状态 + 待审提示进 fields。
    labels = {f['label']: f['value'] for f in card['fields']}
    assert labels['状态'] == '待主人审核'
    assert '需你确认后才会公开发布' in labels['提示']
    # 资源 + 主操作深链。
    assert card['resource']['type'] == 'community.post'
    assert card['resource']['uri'] == 'hasn://community/posts/p_abc123'
    action = card['primary_action']
    assert action['kind'] == 'open_uri'
    assert action['label'] == '查看帖子'
    assert action['uri'] == 'hasn://community/posts/p_abc123'
    assert action['action_id'] == 'open_community_post'
    assert action['event']['payload'] == {'post_id': 'p_abc123'}


def test_build_article_card_headline_and_link() -> None:
    card = notifier.build_community_resource_card(
        'article', 'art_xyz789', author_name='星创', status='published', preview='深度长文标题', resource_title='深度长文标题'
    )
    validate_card_message_body(card)
    assert card['title'] == '星创发布了一篇社区文章'
    assert card['resource']['type'] == 'community.article'
    assert card['resource']['title'] == '深度长文标题'
    assert card['resource']['uri'] == 'hasn://community/articles/art_xyz789'
    labels = {f['label']: f['value'] for f in card['fields']}
    assert labels['状态'] == '已发布'
    assert '提示' not in labels  # 非待审不加审核提示
    assert card['primary_action']['label'] == '查看文章'
    assert card['primary_action']['action_id'] == 'open_community_article'


def test_build_card_author_fallback_when_name_blank() -> None:
    card = notifier.build_community_resource_card(
        'post', 'p_blank', author_name='', status='pending_review', preview=''
    )
    validate_card_message_body(card)
    assert card['title'] == '你的分身发布了一篇社区帖子'


@pytest_asyncio.fixture
async def community_sessions():
    """提供真实 PostgreSQL 会话。"""
    engine = create_async_engine(
        SQLALCHEMY_DATABASE_URL,
        poolclass=NullPool,
    )
    try:
        async with engine.connect() as connection:
            await connection.execute(sa.text('SELECT 1'))
    except Exception as exc:
        await engine.dispose()
        pytest.fail(f'PostgreSQL 不可达：{exc!r}')
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _cleanup_case(sessionmaker, *, owner_id: str, agent_id: str) -> None:
    conversations = SCHEMA_NAMES.im_table('hasn_conversations')
    memberships = SCHEMA_NAMES.im_table('hasn_conversation_memberships')
    messages = SCHEMA_NAMES.im_table('hasn_messages')
    unread = SCHEMA_NAMES.im_table('hasn_unread_projection')
    events = SCHEMA_NAMES.im_event_table('integration_events')
    async with sessionmaker.begin() as db:
        conversation_ids = list(
            (
                await db.execute(
                    sa.text(
                        f'SELECT id FROM {conversations} '
                        'WHERE participant_a_id = :owner OR participant_a_id = :agent '
                        'OR participant_b_id = :owner OR participant_b_id = :agent'
                    ),
                    {'owner': owner_id, 'agent': agent_id},
                )
            )
            .scalars()
            .all()
        )
        if conversation_ids:
            await db.execute(
                sa.text(
                    f'DELETE FROM {events} WHERE aggregate_id IN ('
                    f'SELECT id::text FROM {messages} '
                    'WHERE conversation_id = ANY(:conversation_ids))'
                ),
                {'conversation_ids': conversation_ids},
            )
            for table in (unread, messages, memberships, conversations):
                await db.execute(
                    sa.text(
                        f'DELETE FROM {table} '  # noqa: S608 代码内固定表名
                        'WHERE conversation_id = ANY(:conversation_ids)'
                        if table != conversations
                        else f'DELETE FROM {table} WHERE id = ANY(:conversation_ids)'
                    ),
                    {'conversation_ids': conversation_ids},
                )
        await db.execute(
            sa.text(
                'DELETE FROM hasn_community.im_command_outbox '
                "WHERE payload->'principal'->>'canonical_sender' = :agent"
            ),
            {'agent': agent_id},
        )
        await db.execute(
            sa.delete(HasnAgents).where(HasnAgents.hasn_id == agent_id)
        )
        await db.execute(
            sa.delete(HasnHumans).where(HasnHumans.hasn_id == owner_id)
        )


@pytest.mark.asyncio
async def test_community_card_transaction_and_relay_use_real_im(
    community_sessions,
) -> None:
    """业务回滚无命令；提交后公共 relay 产出一条权威卡片消息。"""
    marker = uuid.uuid4().hex
    owner_id = f'h_ccard_{marker[:16]}'
    agent_id = f'a_ccard_{marker[:16]}'
    gateway = PythonLocalImGateway(session_factory=community_sessions)
    try:
        async with community_sessions.begin() as db:
            db.add(
                HasnHumans(
                    hasn_id=owner_id,
                    star_id=f'h{marker[:24]}',
                    user_id=int(marker[:15], 16),
                    nickname=f'社区卡主人{marker[:8]}',
                    status='active',
                )
            )
            db.add(
                HasnAgents(
                    hasn_id=agent_id,
                    star_id=f'a{marker[:24]}',
                    owner_id=owner_id,
                    display_name='星创',
                    agent_name=f'ccard{marker[:10]}',
                    api_key_hash=marker,
                    status='active',
                    created_via='client',
                )
            )

        rollback_resource = f'p_rb_{marker[:10]}'
        async with community_sessions() as db:
            transaction = await db.begin()
            await notifier.notify_owner_post_card(
                db,
                agent_hasn_id=agent_id,
                owner_hasn_id=owner_id,
                author_name='错误名字',
                post_id=rollback_resource,
                content='应随业务回滚',
                status='pending_review',
                gateway=gateway,
            )
            await transaction.rollback()
        async with community_sessions() as db:
            rolled_back = await db.scalar(
                sa.text(
                    'SELECT count(*) FROM hasn_community.im_command_outbox '
                    'WHERE idempotency_key = :key'
                ),
                {'key': f'community:post:{rollback_resource}:owner-card'},
            )
            assert rolled_back == 0

        resource_id = f'p_{marker[:12]}'
        async with community_sessions.begin() as db:
            command_id = await notifier.notify_owner_post_card(
                db,
                agent_hasn_id=agent_id,
                owner_hasn_id=owner_id,
                author_name='错误名字',
                post_id=resource_id,
                content='正文内容很长很长',
                status='pending_review',
                gateway=gateway,
            )
        assert command_id is not None

        relay = build_community_im_relay(
            session_factory=community_sessions,
            gateway=gateway,
            instance_id=f'test-{marker}',
        )
        stats = await relay.drain_once(
            now=int((timezone.now() + timedelta(seconds=1)).timestamp())
        )
        assert stats.completed == 1

        async with community_sessions() as db:
            row = (
                await db.execute(
                    sa.text(
                        'SELECT status, message_id, payload '
                        'FROM hasn_community.im_command_outbox '
                        'WHERE command_id = :command_id'
                    ),
                    {'command_id': command_id},
                )
            ).one()
            assert row.status == 'completed'
            card = row.payload['message']['content']
            assert card['title'] == '星创发布了一篇社区帖子'
            assert card['resource']['uri'] == (
                f'hasn://community/posts/{resource_id}'
            )
            message_count = await db.scalar(
                sa.text(
                    f'SELECT count(*) FROM {SCHEMA_NAMES.im_table("hasn_messages")} '
                    'WHERE id = :message_id AND content_type = 5'
                ),
                {'message_id': row.message_id},
            )
            assert message_count == 1
    finally:
        await _cleanup_case(
            community_sessions,
            owner_id=owner_id,
            agent_id=agent_id,
        )


@pytest.mark.asyncio
async def test_notify_skips_when_identities_missing(community_sessions) -> None:
    """缺必要标识时不登记命令。"""
    async with community_sessions() as db:
        result = await notifier.notify_owner_post_card(
            db,
            agent_hasn_id='',
            owner_hasn_id='h_missing',
            author_name='星创',
            post_id='p_x',
            content='x',
            status='pending_review',
        )
        assert result is None
