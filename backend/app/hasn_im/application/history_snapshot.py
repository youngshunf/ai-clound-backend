"""主人透明的会话与消息历史快照。

增量同步流只负责追平变化，不承担历史备份。这里以签名快照令牌固定消息上界，
再按稳定主键游标分页返回当前主人可见的完整云端镜像。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from jose import JWTError, jwt
from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from backend.app.hasn.model import (
    HasnConversationMemberships,
    HasnConversations,
    HasnMessages,
)
from backend.app.hasn.service.conversation_projection import (
    build_conversation_projection,
    content_type_to_mime,
)
from backend.app.hasn_core import HasnAgents
from backend.core.conf import settings
from backend.utils.timezone import timezone

_TOKEN_KIND = 'im_history_snapshot_v1'


class HistorySnapshotTokenError(ValueError):
    """快照令牌无效、被篡改或不属于当前主人。"""


@dataclass(frozen=True, slots=True)
class HistorySnapshot:
    """固定的消息历史快照边界。"""

    snapshot_token: str
    head_revision: int
    message_upper_bound: int


@dataclass(frozen=True, slots=True)
class HistorySnapshotPage:
    """历史快照稳定分页。"""

    items: list[dict[str, Any]]
    has_more: bool
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class _SnapshotBoundary:
    owner_id: str
    identity_ids: tuple[str, ...]
    head_revision: int
    message_upper_bound: int
    captured_at: datetime


async def _owner_identity_ids(db: AsyncSession, owner_id: str) -> tuple[str, ...]:
    agent_ids = (await db.execute(select(HasnAgents.hasn_id).where(HasnAgents.owner_id == owner_id))).scalars().all()
    return tuple(dict.fromkeys((owner_id, *(str(value) for value in agent_ids))))


def _encode_boundary(boundary: _SnapshotBoundary) -> str:
    payload = {
        'kind': _TOKEN_KIND,
        'owner_id': boundary.owner_id,
        'identity_ids': list(boundary.identity_ids),
        'head_revision': boundary.head_revision,
        'message_upper_bound': boundary.message_upper_bound,
        'captured_at': boundary.captured_at.isoformat(),
    }
    return jwt.encode(
        payload,
        settings.TOKEN_SECRET_KEY,
        algorithm=settings.TOKEN_ALGORITHM,
    )


def _decode_boundary(snapshot_token: str, *, owner_id: str) -> _SnapshotBoundary:
    try:
        payload = jwt.decode(
            snapshot_token,
            settings.TOKEN_SECRET_KEY,
            algorithms=[settings.TOKEN_ALGORITHM],
        )
        if payload.get('kind') != _TOKEN_KIND:
            raise HistorySnapshotTokenError('快照令牌类型无效')
        token_owner = str(payload['owner_id'])
        if token_owner != owner_id:
            raise HistorySnapshotTokenError('快照令牌不属于当前主人')
        identity_ids = tuple(str(value) for value in payload['identity_ids'])
        if not identity_ids or identity_ids[0] != token_owner:
            raise HistorySnapshotTokenError('快照身份边界无效')
        return _SnapshotBoundary(
            owner_id=token_owner,
            identity_ids=identity_ids,
            head_revision=int(payload['head_revision']),
            message_upper_bound=int(payload['message_upper_bound']),
            captured_at=datetime.fromisoformat(str(payload['captured_at'])),
        )
    except HistorySnapshotTokenError:
        raise
    except (JWTError, KeyError, TypeError, ValueError) as exc:
        raise HistorySnapshotTokenError('快照令牌无效') from exc


def _parse_uuid_cursor(after: str | None) -> UUID | None:
    if after is None:
        return None
    try:
        return UUID(after)
    except ValueError as exc:
        raise HistorySnapshotTokenError('会话快照游标无效') from exc


def _parse_message_cursor(after: str | None) -> int | None:
    if after is None:
        return None
    try:
        cursor = int(after)
    except ValueError as exc:
        raise HistorySnapshotTokenError('消息快照游标无效') from exc
    if cursor < 0:
        raise HistorySnapshotTokenError('消息快照游标无效')
    return cursor


async def start_history_snapshot(
    db: AsyncSession,
    *,
    owner_id: str,
    head_revision: int,
) -> HistorySnapshot:
    """捕获同步流头与消息主键上界，后续分页均受该边界约束。"""
    identity_ids = await _owner_identity_ids(db, owner_id)
    message_upper_bound = int((await db.execute(select(func.coalesce(func.max(HasnMessages.id), 0)))).scalar_one())
    boundary = _SnapshotBoundary(
        owner_id=owner_id,
        identity_ids=identity_ids,
        head_revision=max(0, int(head_revision)),
        message_upper_bound=message_upper_bound,
        captured_at=timezone.now(),
    )
    return HistorySnapshot(
        snapshot_token=_encode_boundary(boundary),
        head_revision=boundary.head_revision,
        message_upper_bound=boundary.message_upper_bound,
    )


def _active_conversation_exists(
    *,
    identities: tuple[str, ...],
    captured_at: datetime,
) -> ColumnElement[bool]:
    active_membership = aliased(HasnConversationMemberships)
    return (
        exists(
            select(1).where(
                active_membership.conversation_id == HasnConversations.id,
                active_membership.member_hasn_id.in_(identities),
                active_membership.left_seq.is_(None),
                active_membership.state == 'active',
            )
        )
        & (HasnConversations.status != 'disbanded')
        & (HasnConversations.created_time <= captured_at)
    )


async def list_history_snapshot_conversations(
    db: AsyncSession,
    *,
    owner_id: str,
    snapshot_token: str,
    after: str | None,
    limit: int,
) -> HistorySnapshotPage:
    """按会话 UUID 升序返回主人本人和名下分身当前可见的会话。"""
    boundary = _decode_boundary(snapshot_token, owner_id=owner_id)
    identities = boundary.identity_ids
    cursor = _parse_uuid_cursor(after)
    page_limit = max(1, min(int(limit), 200))
    statement = select(HasnConversations).where(
        _active_conversation_exists(
            identities=identities,
            captured_at=boundary.captured_at,
        )
    )
    if cursor is not None:
        statement = statement.where(HasnConversations.id > cursor)
    conversations = list(
        (await db.execute(statement.order_by(HasnConversations.id.asc()).limit(page_limit + 1))).scalars().all()
    )
    has_more = len(conversations) > page_limit
    page_rows = conversations[:page_limit]
    conversation_ids = [row.id for row in page_rows]
    membership_rows = (
        list(
            (
                await db.execute(
                    select(HasnConversationMemberships).where(
                        HasnConversationMemberships.conversation_id.in_(conversation_ids),
                        HasnConversationMemberships.left_seq.is_(None),
                        HasnConversationMemberships.state == 'active',
                    )
                )
            )
            .scalars()
            .all()
        )
        if conversation_ids
        else []
    )
    members_by_conversation: dict[str, list[HasnConversationMemberships]] = {}
    for membership in membership_rows:
        members_by_conversation.setdefault(
            str(membership.conversation_id),
            [],
        ).append(membership)

    items: list[dict[str, Any]] = []
    identity_set = set(identities)
    for conversation in page_rows:
        members = members_by_conversation.get(str(conversation.id), [])
        projection = build_conversation_projection(
            conversation,
            members=members if conversation.type == 'group' else None,
            viewer_owner_hasn_id=owner_id,
        )
        visible_memberships = [membership for membership in members if membership.member_hasn_id in identity_set]
        projection['history_complete'] = bool(visible_memberships) and all(
            membership.history_complete_from_seq is not None for membership in visible_memberships
        )
        complete_starts = [
            int(membership.history_complete_from_seq)
            for membership in visible_memberships
            if membership.history_complete_from_seq is not None
        ]
        projection['history_complete_from_seq'] = min(complete_starts) if complete_starts else None
        items.append(projection)

    return HistorySnapshotPage(
        items=items,
        has_more=has_more,
        next_cursor=(str(page_rows[-1].id) if has_more and page_rows else None),
    )


async def list_history_snapshot_messages(
    db: AsyncSession,
    *,
    owner_id: str,
    snapshot_token: str,
    after: str | None,
    limit: int,
) -> HistorySnapshotPage:
    """按消息 ID 升序返回快照上界内、成员周期允许读取的消息。"""
    boundary = _decode_boundary(snapshot_token, owner_id=owner_id)
    identities = boundary.identity_ids
    identity_set = set(identities)
    cursor = _parse_message_cursor(after)
    page_limit = max(1, min(int(limit), 500))
    visible_epoch = aliased(HasnConversationMemberships)
    active_membership = aliased(HasnConversationMemberships)
    active_conversation = exists(
        select(1)
        .select_from(HasnConversations)
        .join(
            active_membership,
            active_membership.conversation_id == HasnConversations.id,
        )
        .where(
            HasnConversations.id == HasnMessages.conversation_id,
            HasnConversations.status != 'disbanded',
            HasnConversations.created_time <= boundary.captured_at,
            active_membership.member_hasn_id.in_(identities),
            active_membership.left_seq.is_(None),
            active_membership.state == 'active',
        )
    )
    epoch_visibility = exists(
        select(1).where(
            visible_epoch.conversation_id == HasnMessages.conversation_id,
            visible_epoch.member_hasn_id.in_(identities),
            HasnMessages.conversation_seq >= visible_epoch.joined_seq,
            or_(
                visible_epoch.left_seq.is_(None),
                HasnMessages.conversation_seq <= visible_epoch.left_seq,
            ),
        )
    )
    statement = select(HasnMessages).where(
        HasnMessages.id <= boundary.message_upper_bound,
        active_conversation,
        epoch_visibility,
    )
    if cursor is not None:
        statement = statement.where(HasnMessages.id > cursor)
    messages = list((await db.execute(statement.order_by(HasnMessages.id.asc()).limit(page_limit + 1))).scalars().all())
    has_more = len(messages) > page_limit
    page_rows = messages[:page_limit]
    items: list[dict[str, Any]] = []
    for message in page_rows:
        created_at = message.created_time or message.server_received_at
        payload: dict[str, Any] = {
            'conversation_id': str(message.conversation_id),
            'message_id': str(message.id),
            'conversation_seq': int(message.conversation_seq),
            'sender_hasn_id': message.from_id,
            'recipient_hasn_id': message.to_id,
            'origin_node_id': message.origin_node_id,
            'content_type': content_type_to_mime(message.content_type),
            'content_body': (message.content if isinstance(message.content, dict) else {'text': str(message.content)}),
            'process_blocks': list(message.process_blocks or []),
            'local_id': message.local_id,
            'msg_type': message.msg_type or 'message',
            'status': int(message.status),
            'priority': message.priority or 'normal',
            'reply_to_id': (str(message.reply_to_id) if message.reply_to_id else None),
            'created_at': int(created_at.timestamp()),
        }
        if message.from_id in identity_set and message.origin_session_id:
            payload['origin_session_id'] = message.origin_session_id
        items.append(payload)
    return HistorySnapshotPage(
        items=items,
        has_more=has_more,
        next_cursor=(str(page_rows[-1].id) if has_more and page_rows else None),
    )
