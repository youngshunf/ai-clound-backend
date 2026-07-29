"""主人透明的会话与消息历史物化快照。

增量同步流只负责追平变化，不承担历史备份。新设备先在可重复读事务中把当前可见
会话与消息投影物化为短期快照，再按稳定序号分页恢复本地镜像。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from jose import JWTError, jwt
from sqlalchemy import delete, exists, func, insert, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from backend.app.hasn.model import (
    HasnConversationMemberships,
    HasnConversations,
    HasnImHistorySnapshotConversations,
    HasnImHistorySnapshotMessages,
    HasnImHistorySnapshots,
    HasnMessages,
)
from backend.app.hasn.service.conversation_projection import (
    build_conversation_projection,
    content_type_to_mime,
)
from backend.app.hasn_core import HasnAgents
from backend.core.conf import settings
from backend.utils.timezone import timezone

_TOKEN_KIND = 'im_history_snapshot_v2'
_SNAPSHOT_TTL = timedelta(minutes=15)
_CONVERSATION_BATCH_SIZE = 200
_MESSAGE_BATCH_SIZE = 500
_RECALLED_STATUS = 4


class HistorySnapshotTokenError(ValueError):
    """快照令牌无效、已过期或不再满足当前身份边界。"""


@dataclass(frozen=True, slots=True)
class HistorySnapshot:
    """已经物化完成的消息历史快照边界。"""

    snapshot_token: str
    head_revision: int
    message_upper_bound: int
    conversation_count: int
    message_count: int
    history_complete: bool


@dataclass(frozen=True, slots=True)
class HistorySnapshotPage:
    """历史快照稳定分页。"""

    items: list[dict[str, Any]]
    has_more: bool
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class _SnapshotClaims:
    snapshot_id: UUID
    owner_id: str
    expires_time: datetime


async def _owner_identity_ids(
    db: AsyncSession,
    owner_id: str,
) -> tuple[str, ...]:
    agent_ids = (
        (
            await db.execute(
                select(HasnAgents.hasn_id).where(
                    HasnAgents.owner_id == owner_id,
                    HasnAgents.status == 'active',
                    HasnAgents.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    return tuple(dict.fromkeys((owner_id, *(str(value) for value in agent_ids))))


def _encode_snapshot(
    *,
    snapshot_id: UUID,
    owner_id: str,
    expires_time: datetime,
) -> str:
    return jwt.encode(
        {
            'kind': _TOKEN_KIND,
            'snapshot_id': str(snapshot_id),
            'owner_id': owner_id,
            'exp': int(expires_time.timestamp()),
        },
        settings.TOKEN_SECRET_KEY,
        algorithm=settings.TOKEN_ALGORITHM,
    )


def _decode_snapshot(
    snapshot_token: str,
    *,
    owner_id: str,
) -> _SnapshotClaims:
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
        return _SnapshotClaims(
            snapshot_id=UUID(str(payload['snapshot_id'])),
            owner_id=token_owner,
            expires_time=datetime.fromtimestamp(
                int(payload['exp']),
                tz=timezone.now().tzinfo,
            ),
        )
    except HistorySnapshotTokenError:
        raise
    except (JWTError, KeyError, TypeError, ValueError) as exc:
        raise HistorySnapshotTokenError('快照令牌无效或已过期') from exc


def _parse_index_cursor(after: str | None) -> int:
    if after is None:
        return 0
    try:
        cursor = int(after)
    except ValueError as exc:
        raise HistorySnapshotTokenError('历史快照游标无效') from exc
    if cursor < 0:
        raise HistorySnapshotTokenError('历史快照游标无效')
    return cursor


def _group_projection_members(
    memberships: list[HasnConversationMemberships],
) -> list[HasnConversationMemberships]:
    active = [membership for membership in memberships if membership.left_seq is None and membership.state == 'active']
    if active:
        return active

    # 已解散群没有活动 epoch，仍保留每个成员最后一次展示快照供历史恢复。
    latest_by_member: dict[str, HasnConversationMemberships] = {}
    for membership in sorted(
        memberships,
        key=lambda value: (
            int(value.joined_seq),
            int(value.id),
        ),
    ):
        latest_by_member[membership.member_hasn_id] = membership
    return [latest_by_member[member_id] for member_id in sorted(latest_by_member)]


def _conversation_projection(
    conversation: HasnConversations,
    *,
    memberships: list[HasnConversationMemberships],
    owner_id: str,
    identity_set: set[str],
    unread_count: int,
) -> dict[str, Any]:
    projection = build_conversation_projection(
        conversation,
        members=(_group_projection_members(memberships) if conversation.type == 'group' else None),
        viewer_owner_hasn_id=owner_id,
    )
    visible_epochs = [membership for membership in memberships if membership.member_hasn_id in identity_set]
    complete_starts = [
        int(membership.history_complete_from_seq)
        for membership in visible_epochs
        if membership.history_complete_from_seq is not None
    ]
    projection['status'] = conversation.status or 'active'
    projection['history_complete'] = bool(visible_epochs) and len(complete_starts) == len(visible_epochs)
    projection['history_complete_from_seq'] = min(complete_starts) if complete_starts else None
    projection['read_state'] = [
        {
            'member_hasn_id': membership.member_hasn_id,
            'joined_seq': int(membership.joined_seq),
            'left_seq': (int(membership.left_seq) if membership.left_seq is not None else None),
            'read_seq': int(membership.read_seq),
            'state': membership.state,
        }
        for membership in sorted(
            visible_epochs,
            key=lambda value: (value.member_hasn_id, int(value.joined_seq)),
        )
    ]
    projection['unread_count'] = max(0, int(unread_count))
    return projection


def _message_projection(
    message: HasnMessages,
    *,
    identity_set: set[str],
) -> dict[str, Any]:
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
    return payload


async def _materialize_conversations(
    db: AsyncSession,
    *,
    snapshot_id: UUID,
    owner_id: str,
    identities: tuple[str, ...],
) -> tuple[int, bool]:
    visible_membership = aliased(HasnConversationMemberships)
    unread_epoch = aliased(HasnConversationMemberships)
    identity_set = set(identities)
    item_index = 0
    history_complete = True
    last_conversation_id: UUID | None = None

    while True:
        statement = (
            select(HasnConversations)
            .where(
                exists(
                    select(1).where(
                        visible_membership.conversation_id == HasnConversations.id,
                        visible_membership.member_hasn_id.in_(identities),
                    )
                )
            )
            .order_by(HasnConversations.id.asc())
            .limit(_CONVERSATION_BATCH_SIZE)
        )
        if last_conversation_id is not None:
            statement = statement.where(
                HasnConversations.id > last_conversation_id,
            )
        conversations = list((await db.execute(statement)).scalars().all())
        if not conversations:
            break

        conversation_ids = [UUID(str(conversation.id)) for conversation in conversations]
        membership_rows = list(
            (
                await db.execute(
                    select(HasnConversationMemberships).where(
                        HasnConversationMemberships.conversation_id.in_(conversation_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        memberships_by_conversation: dict[
            UUID,
            list[HasnConversationMemberships],
        ] = {}
        for membership in membership_rows:
            memberships_by_conversation.setdefault(
                UUID(str(membership.conversation_id)),
                [],
            ).append(membership)

        unread_rows = (
            await db.execute(
                select(
                    HasnMessages.conversation_id,
                    func.count(func.distinct(HasnMessages.id)),
                )
                .where(
                    HasnMessages.conversation_id.in_(conversation_ids),
                    HasnMessages.from_id.not_in(identities),
                    HasnMessages.status != _RECALLED_STATUS,
                    exists(
                        select(1).where(
                            unread_epoch.conversation_id == HasnMessages.conversation_id,
                            unread_epoch.member_hasn_id.in_(identities),
                            unread_epoch.left_seq.is_(None),
                            unread_epoch.state == 'active',
                            HasnMessages.conversation_seq >= unread_epoch.joined_seq,
                            HasnMessages.conversation_seq > unread_epoch.read_seq,
                        )
                    ),
                )
                .group_by(HasnMessages.conversation_id)
            )
        ).all()
        unread_by_conversation = {UUID(str(conversation_id)): int(count) for conversation_id, count in unread_rows}

        insert_rows: list[dict[str, Any]] = []
        for conversation in conversations:
            projection = _conversation_projection(
                conversation,
                memberships=memberships_by_conversation.get(
                    UUID(str(conversation.id)),
                    [],
                ),
                owner_id=owner_id,
                identity_set=identity_set,
                unread_count=unread_by_conversation.get(
                    UUID(str(conversation.id)),
                    0,
                ),
            )
            item_index += 1
            history_complete = history_complete and projection.get('history_complete') is True
            insert_rows.append({
                'snapshot_id': snapshot_id,
                'item_index': item_index,
                'conversation_id': conversation.id,
                'payload': projection,
            })
        await db.execute(
            insert(HasnImHistorySnapshotConversations),
            insert_rows,
        )
        last_conversation_id = conversation_ids[-1]

    return item_index, history_complete


async def _materialize_messages(
    db: AsyncSession,
    *,
    snapshot_id: UUID,
    identities: tuple[str, ...],
) -> tuple[int, int]:
    visible_epoch = aliased(HasnConversationMemberships)
    identity_set = set(identities)
    item_index = 0
    upper_bound = 0
    last_message_id = 0

    while True:
        messages = list(
            (
                await db.execute(
                    select(HasnMessages)
                    .where(
                        HasnMessages.id > last_message_id,
                        exists(
                            select(1).where(
                                visible_epoch.conversation_id == HasnMessages.conversation_id,
                                visible_epoch.member_hasn_id.in_(identities),
                                HasnMessages.conversation_seq >= visible_epoch.joined_seq,
                                or_(
                                    visible_epoch.left_seq.is_(None),
                                    HasnMessages.conversation_seq <= visible_epoch.left_seq,
                                ),
                            )
                        ),
                    )
                    .order_by(HasnMessages.id.asc())
                    .limit(_MESSAGE_BATCH_SIZE)
                )
            )
            .scalars()
            .all()
        )
        if not messages:
            break
        insert_rows: list[dict[str, Any]] = []
        for message in messages:
            item_index += 1
            insert_rows.append({
                'snapshot_id': snapshot_id,
                'item_index': item_index,
                'message_id': int(message.id),
                'payload': _message_projection(
                    message,
                    identity_set=identity_set,
                ),
            })
        await db.execute(
            insert(HasnImHistorySnapshotMessages),
            insert_rows,
        )
        last_message_id = int(messages[-1].id)
        upper_bound = last_message_id

    return item_index, upper_bound


async def start_history_snapshot(
    db: AsyncSession,
    *,
    owner_id: str,
    head_revision: int,
) -> HistorySnapshot:
    """在一个可重复读事务中物化稳定且短期有效的恢复快照。"""
    await db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'))
    now = timezone.now()
    await db.execute(
        delete(HasnImHistorySnapshots).where(
            HasnImHistorySnapshots.expires_time <= now,
        )
    )
    identities = await _owner_identity_ids(db, owner_id)
    snapshot = HasnImHistorySnapshots(
        owner_id=owner_id,
        identity_ids=list(identities),
        head_revision=max(0, int(head_revision)),
        message_upper_bound=0,
        conversation_count=0,
        message_count=0,
        history_complete=False,
        expires_time=now + _SNAPSHOT_TTL,
    )
    db.add(snapshot)
    await db.flush()

    (
        snapshot.conversation_count,
        snapshot.history_complete,
    ) = await _materialize_conversations(
        db,
        snapshot_id=snapshot.id,
        owner_id=owner_id,
        identities=identities,
    )
    (
        snapshot.message_count,
        snapshot.message_upper_bound,
    ) = await _materialize_messages(
        db,
        snapshot_id=snapshot.id,
        identities=identities,
    )
    expires_time = timezone.now() + _SNAPSHOT_TTL
    snapshot.expires_time = expires_time
    await db.flush()
    return HistorySnapshot(
        snapshot_token=_encode_snapshot(
            snapshot_id=snapshot.id,
            owner_id=owner_id,
            expires_time=expires_time,
        ),
        head_revision=snapshot.head_revision,
        message_upper_bound=snapshot.message_upper_bound,
        conversation_count=snapshot.conversation_count,
        message_count=snapshot.message_count,
        history_complete=snapshot.history_complete,
    )


async def _load_authorized_snapshot(
    db: AsyncSession,
    *,
    owner_id: str,
    snapshot_token: str,
) -> HasnImHistorySnapshots:
    claims = _decode_snapshot(snapshot_token, owner_id=owner_id)
    snapshot = (
        (
            await db.execute(
                select(HasnImHistorySnapshots).where(
                    HasnImHistorySnapshots.id == claims.snapshot_id,
                    HasnImHistorySnapshots.owner_id == owner_id,
                )
            )
        )
        .scalars()
        .one_or_none()
    )
    if snapshot is None:
        raise HistorySnapshotTokenError('历史快照不存在或已清理')
    if snapshot.expires_time <= timezone.now() or claims.expires_time <= timezone.now():
        raise HistorySnapshotTokenError('历史快照已过期，请重新开始')

    current_identities = set(await _owner_identity_ids(db, owner_id))
    captured_identities = {str(identity) for identity in snapshot.identity_ids if isinstance(identity, str)}
    if owner_id not in captured_identities or not captured_identities.issubset(current_identities):
        raise HistorySnapshotTokenError(
            '快照身份归属已变化，请重新开始',
        )
    return snapshot


async def list_history_snapshot_conversations(
    db: AsyncSession,
    *,
    owner_id: str,
    snapshot_token: str,
    after: str | None,
    limit: int,
) -> HistorySnapshotPage:
    """按物化序号返回不可变的会话历史页。"""
    snapshot = await _load_authorized_snapshot(
        db,
        owner_id=owner_id,
        snapshot_token=snapshot_token,
    )
    cursor = _parse_index_cursor(after)
    page_limit = max(1, min(int(limit), 200))
    rows = list(
        (
            await db.execute(
                select(HasnImHistorySnapshotConversations)
                .where(
                    HasnImHistorySnapshotConversations.snapshot_id == snapshot.id,
                    HasnImHistorySnapshotConversations.item_index > cursor,
                )
                .order_by(HasnImHistorySnapshotConversations.item_index.asc())
                .limit(page_limit + 1)
            )
        )
        .scalars()
        .all()
    )
    has_more = len(rows) > page_limit
    page_rows = rows[:page_limit]
    return HistorySnapshotPage(
        items=[dict(row.payload) for row in page_rows],
        has_more=has_more,
        next_cursor=(str(page_rows[-1].item_index) if has_more and page_rows else None),
    )


async def list_history_snapshot_messages(
    db: AsyncSession,
    *,
    owner_id: str,
    snapshot_token: str,
    after: str | None,
    limit: int,
) -> HistorySnapshotPage:
    """按物化序号返回不可变的消息历史页。"""
    snapshot = await _load_authorized_snapshot(
        db,
        owner_id=owner_id,
        snapshot_token=snapshot_token,
    )
    cursor = _parse_index_cursor(after)
    page_limit = max(1, min(int(limit), 500))
    rows = list(
        (
            await db.execute(
                select(HasnImHistorySnapshotMessages)
                .where(
                    HasnImHistorySnapshotMessages.snapshot_id == snapshot.id,
                    HasnImHistorySnapshotMessages.item_index > cursor,
                )
                .order_by(HasnImHistorySnapshotMessages.item_index.asc())
                .limit(page_limit + 1)
            )
        )
        .scalars()
        .all()
    )
    has_more = len(rows) > page_limit
    page_rows = rows[:page_limit]
    return HistorySnapshotPage(
        items=[dict(row.payload) for row in page_rows],
        has_more=has_more,
        next_cursor=(str(page_rows[-1].item_index) if has_more and page_rows else None),
    )
