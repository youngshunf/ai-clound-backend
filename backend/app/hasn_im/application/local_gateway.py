"""hasn_im.application.local_gateway · Python 本地 IM 应用网关。

网关是业务模块访问 IM 事实的唯一入口：调用方只传不可变命令与认证主体，不接触
``AsyncSession`` 或 ORM。每个方法使用 IM 受限角色连接池自持事务边界；消息列表按成员周期
裁剪，已读事实落在 membership ``read_seq``，旧未读计数表不再参与生产路径。
"""

from __future__ import annotations

import json

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.hasn.model import (
    HasnConversationMemberships,
    HasnConversations,
    HasnHumans,
    HasnMessages,
    HasnSuppressedMessages,
    HasnUnreadProjection,
)
from backend.app.hasn_core import HasnAgents
from backend.app.hasn_im.adapters.sqlalchemy_identity_view import SqlAlchemyIdentityView
from backend.app.hasn_im.application import membership_service, message_service
from backend.app.hasn_im.application.event_appender import append_event
from backend.app.hasn_im.application.errors import (
    ImConversationNotFound,
    ImSenderNotParticipant,
    ImSendRejected,
)
from backend.app.hasn_im.ports.dto import (
    ActorKind,
    ConversationRef,
    DeliveryState,
    EnsureDirectConversationCommand,
    ListConversationsQuery,
    ListMessagesQuery,
    MessagePage,
    ReadCursorCommand,
    RecallMessageCommand,
    SendMessageCommand,
    SendMessageResult,
    ServicePrincipal,
    UpdateGroupMembersCommand,
)
from backend.app.hasn_im.ports.identity_view import IdentityRejected, IdentityView, require_active
from backend.app.hasn_im.consumers.facts import (
    IM_CONVERSATION_UPDATED,
    IM_MESSAGE_RECALLED,
)
from backend.utils.timezone import timezone


async def resolve_target(db: AsyncSession, to_target: str) -> dict[str, Any] | None:
    """通过消息应用服务解析发送目标。"""
    return await message_service.resolve_target(db, to_target)


async def send_to_target(
    db: AsyncSession,
    from_id: str,
    to_target: str,
    *,
    content: dict[str, Any],
    content_type: int = 1,
    msg_type: str = 'message',
    priority: str = 'normal',
    reply_to_id: int | None = None,
    local_id: str | None = None,
    context: dict[str, Any] | None = None,
    origin_node_id: str | None = None,
    origin_session_id: str | None = None,
) -> dict[str, Any]:
    """通过消息应用服务发送消息。"""
    target = await resolve_target(db, to_target)
    conversation_id = None
    if target and target.get('entity_type') == 'group':
        group = await message_service.get_group_conversation(db, str(target['hasn_id']))
        conversation_id = str(group.id) if group else None
    return await message_service.route_message(
        db=db,
        from_id=from_id,
        to_target=to_target,
        conversation_id=conversation_id,
        content=content,
        content_type=content_type,
        msg_type=msg_type,
        priority=priority,
        reply_to_id=reply_to_id,
        local_id=local_id,
        context=context,
        origin_node_id=origin_node_id,
        origin_session_id=origin_session_id,
    )


async def mark_read(
    db: AsyncSession,
    reader: str,
    conversation_id: str,
    last_msg_id: int,
) -> None:
    """通过消息应用服务更新已读游标。"""
    await message_service.mark_read(
        db=db,
        hasn_id=reader,
        conversation_id=conversation_id,
        last_msg_id=last_msg_id,
    )


def _participant_type(hasn_id: str) -> str:
    """按 hasn_id 前缀判定参与者类型。"""
    return message_service._entity_type_str(hasn_id)


@dataclass(slots=True)
class PythonLocalImGateway:
    """ImGateway 的 Python 本地实现。

    持有 ``session_factory``，每个方法自开事务边界，端口不暴露 Session。
    """

    session_factory: async_sessionmaker[AsyncSession]
    identity_view: IdentityView | None = None
    # 通知/汇报卡在业务事务内只调用 ensure；绑定该事务后，会话、membership 与业务
    # outbox 原子出现，且能读取同事务中新建的身份。其余网关方法仍保持自管事务。
    bound_ensure_session: AsyncSession | None = None

    def __post_init__(self) -> None:
        """默认把身份只读投影绑定到同一个 IM 受限角色连接池。"""
        if self.identity_view is None:
            self.identity_view = SqlAlchemyIdentityView(
                session_factory=self.session_factory,
                bound_session=self.bound_ensure_session,
            )

    async def _effective_sender(self, principal: ServicePrincipal) -> str:
        """校验认证主体与受限代发关系，返回权威有效发送方。"""
        if principal.actor_kind is ActorKind.SYSTEM_SERVICE:
            if principal.send_as:
                raise ImSendRejected(2002, '系统服务主体不能声明 send_as')
            return principal.canonical_sender

        assert self.identity_view is not None
        try:
            canonical = await require_active(
                self.identity_view,
                principal.canonical_sender,
            )
        except IdentityRejected as exc:
            raise ImSendRejected(2002, str(exc)) from exc
        expected_kind = (
            'human' if principal.actor_kind is ActorKind.HUMAN
            else 'agent' if principal.actor_kind is ActorKind.AGENT
            else None
        )
        if expected_kind is None or canonical.kind != expected_kind:
            raise ImSendRejected(2002, '认证主体类型与 canonical_sender 不匹配')
        if not principal.send_as:
            return canonical.hasn_id
        if canonical.kind != 'human':
            raise ImSendRejected(2002, '只有主人可以代表自有分身发送')
        try:
            delegated = await require_active(
                self.identity_view,
                principal.send_as,
            )
        except IdentityRejected as exc:
            raise ImSendRejected(2002, str(exc)) from exc
        if delegated.kind != 'agent' or delegated.owner_id != canonical.hasn_id:
            raise ImSendRejected(2002, 'send_as 目标不是认证主人名下的活动分身')
        return delegated.hasn_id

    async def _require_active_peer(self, hasn_id: str) -> None:
        """直聊对端必须是存在且活动的人类或分身身份。"""
        assert self.identity_view is not None
        try:
            await require_active(self.identity_view, hasn_id)
        except IdentityRejected as exc:
            raise ImSendRejected(2002, str(exc)) from exc

    async def ensure_direct_conversation(
        self,
        command: EnsureDirectConversationCommand,
        principal: ServicePrincipal,
    ) -> ConversationRef:
        """幂等取得直聊会话（§5.3）——包装 get_or_create_conversation（advisory lock 内查改）。"""
        sender = await self._effective_sender(principal)
        peer = command.peer_hasn_id
        await self._require_active_peer(peer)
        if self.bound_ensure_session is not None:
            db = self.bound_ensure_session
            conv = await message_service.get_or_create_conversation(
                db,
                sender,
                _participant_type(sender),
                peer,
                _participant_type(peer),
                relation_type=command.relation_type,
                mission_note=command.mission_note,
                # mission_note 归属 owner 由**调用方**从认证上下文解析发送方主人后经命令传入
                # （与现网 route 主链 `_resolve_owner_ids(from_id)` 同口径，忠实不漂移）；
                # 无 mission_note 时该值无意义、get_or_create 内亦不落列。
                mission_note_owner_id=command.mission_note_owner_id,
            )
            await db.flush()
            return ConversationRef(
                conversation_id=str(conv.id),
                conversation_type=conv.type,
                relation_type=conv.relation_type or command.relation_type,
            )
        async with self.session_factory() as db:
            conv = await message_service.get_or_create_conversation(
                db,
                sender,
                _participant_type(sender),
                peer,
                _participant_type(peer),
                relation_type=command.relation_type,
                mission_note=command.mission_note,
                mission_note_owner_id=command.mission_note_owner_id,
            )
            await db.commit()
            return ConversationRef(
                conversation_id=str(conv.id),
                conversation_type=conv.type,
                relation_type=conv.relation_type or command.relation_type,
            )

    async def send_message(
        self,
        command: SendMessageCommand,
        principal: ServicePrincipal,
    ) -> SendMessageResult:
        """向已 ensure 的 direct 会话发送（§5.2）——conversation_id-first，counterpart 反解后包装 route_message。"""
        async with self.session_factory() as db:
            conv = await db.get(HasnConversations, command.conversation_id)
            if conv is None:
                raise ImConversationNotFound(command.conversation_id)
            sender = await self._effective_sender(principal)
            if conv.type == 'direct':
                to_target = self._counterpart(conv, sender, command.conversation_id)
                await self._require_active_peer(to_target)
            elif conv.type == 'group' and conv.group_id:
                to_target = conv.group_id
            else:
                raise ImSendRejected(2002, '会话类型不支持发送')

            result = await message_service.route_message(
                db,
                from_id=sender,
                to_target=to_target,
                conversation_id=command.conversation_id,
                content=command.content,
                content_type=command.content_type,
                msg_type=command.msg_type,
                priority=command.priority,
                reply_to_id=command.reply_to_id,
                local_id=command.idempotency_key,
                context=command.context,
                origin_node_id=principal.origin_node_id,
                origin_session_id=principal.origin_session_id,
            )
        return self._to_result(result, command.conversation_id)

    @staticmethod
    def _counterpart(conv: HasnConversations, sender: str, conversation_id: str) -> str:
        """direct 会话里相对发送方的对端 hasn_id。"""
        if conv.participant_a_id == sender and conv.participant_b_id:
            return conv.participant_b_id
        if conv.participant_b_id == sender and conv.participant_a_id:
            return conv.participant_a_id
        raise ImSenderNotParticipant(conversation_id, sender)

    @staticmethod
    def _to_result(result: dict, conversation_id: str) -> SendMessageResult:
        """把现网 route_message 返回 dict 映射为 SendMessageResult（三态）。"""
        if result.get('error'):
            # 协议级硬拒（对方屏蔽/自发/身份未声明）——非投递态，抛结构化异常由协议层映射信封。
            raise ImSendRejected(int(result.get('code') or 2002), str(result.get('message') or '发送被拒'))

        status = result.get('status')
        conv_id = str(result.get('conversation_id') or conversation_id)
        if status == 'suppressed' or result.get('suppressed'):
            return SendMessageResult(
                delivery_state=DeliveryState.SUPPRESSED,
                conversation_id=conv_id,
                message_id=result.get('msg_id'),
                suppressed_id=result.get('suppressed_id'),
                suppress_reason=result.get('suppress_reason'),
                relation=result.get('relation'),
                pending_request_id=result.get('pending_request_id'),
            )
        if status == 'pending_confirmation':
            return SendMessageResult(
                delivery_state=DeliveryState.PENDING_POLICY,
                conversation_id=conv_id,
                suppress_reason=result.get('reason'),
            )
        # 正常投递（含幂等 deduped 命中）
        return SendMessageResult(
            delivery_state=DeliveryState.ACCEPTED,
            conversation_id=conv_id,
            message_id=result.get('msg_id'),
            conversation_seq=result.get('conversation_seq'),
            deduped=bool(result.get('deduped')),
        )

    async def recall_message(
        self, command: RecallMessageCommand, principal: ServicePrincipal
    ) -> SendMessageResult:
        """撤回消息并原子追加集成事件、重建未读投影。"""
        actor = await self._effective_sender(principal)
        conversation_uuid = self._conversation_uuid(command.conversation_id)

        async with self.session_factory() as db:
            conversation = (
                await db.execute(
                    select(HasnConversations)
                    .where(HasnConversations.id == conversation_uuid)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if conversation is None:
                raise ImConversationNotFound(command.conversation_id)
            message = (
                await db.execute(
                    select(HasnMessages)
                    .where(
                        HasnMessages.id == command.message_id,
                        HasnMessages.conversation_id == conversation_uuid,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if message is None:
                raise ImSendRejected(3001, '消息不存在或不属于当前会话')

            can_recall = message.from_id == actor
            if (
                not can_recall
                and principal.actor_kind is ActorKind.HUMAN
                and message.from_id.startswith('a_')
            ):
                owner_id = await db.scalar(
                    select(HasnAgents.owner_id).where(
                        HasnAgents.hasn_id == message.from_id,
                    )
                )
                can_recall = owner_id == principal.canonical_sender
            if not can_recall:
                raise ImSendRejected(2002, '无权撤回此消息')

            if message.status == 4:
                return SendMessageResult(
                    delivery_state=DeliveryState.ACCEPTED,
                    conversation_id=str(message.conversation_id),
                    message_id=message.id,
                    conversation_seq=message.conversation_seq,
                    deduped=True,
                )

            recalled_at = timezone.now()
            message.status = 4
            message.recalled_at = recalled_at
            message.recalled_by = actor
            if conversation.last_message_id == message.id:
                conversation.last_message_preview = '[消息已撤回]'

            active_members = list(
                (
                    await db.execute(
                        select(
                            HasnConversationMemberships.member_hasn_id
                        ).where(
                            HasnConversationMemberships.conversation_id
                            == conversation_uuid,
                            HasnConversationMemberships.left_seq.is_(None),
                            HasnConversationMemberships.state == 'active',
                        )
                    )
                ).scalars()
            )
            for member_hasn_id in active_members:
                await membership_service.rebuild_unread_projection(
                    db,
                    str(conversation_uuid),
                    member_hasn_id,
                    current_seq=conversation.current_seq,
                )

            await append_event(
                db,
                event_type=IM_MESSAGE_RECALLED,
                aggregate_type='conversation',
                aggregate_id=str(conversation_uuid),
                aggregate_seq=message.conversation_seq,
                payload={
                    'conversation_id': str(conversation_uuid),
                    'message_id': str(message.id),
                    'conversation_seq': message.conversation_seq,
                    'recalled_by': actor,
                    'recalled_at': int(recalled_at.timestamp()),
                },
            )
            await db.commit()
            return SendMessageResult(
                delivery_state=DeliveryState.ACCEPTED,
                conversation_id=str(conversation_uuid),
                message_id=message.id,
                conversation_seq=message.conversation_seq,
            )

    @staticmethod
    def _conversation_uuid(conversation_id: str) -> UUID:
        """解析外部会话 ID；非法值与不存在统一按不存在处理。"""
        try:
            return UUID(conversation_id)
        except ValueError as exc:
            raise ImConversationNotFound(conversation_id) from exc

    @staticmethod
    def _entity_kind(hasn_id: str) -> str:
        """把 hasn_id 映射为协议实体类型。"""
        if hasn_id.startswith('h_'):
            return 'human'
        if hasn_id.startswith('a_'):
            return 'agent'
        if hasn_id.startswith('g:'):
            return 'group'
        return 'system'

    @staticmethod
    def _content_kind(content_type: int) -> str:
        """把数据库内容类型映射为协议名称。"""
        return {
            1: 'text',
            2: 'image',
            3: 'file',
            4: 'voice',
            5: 'card',
            6: 'capability_request',
            7: 'capability_response',
        }.get(content_type, 'text')

    @staticmethod
    async def _membership_epochs(
        db: AsyncSession,
        *,
        conversation_id: UUID,
        member_hasn_id: str,
    ) -> list[HasnConversationMemberships]:
        """取得主体在会话内的全部可见周期。"""
        return list(
            (
                await db.execute(
                    select(HasnConversationMemberships).where(
                        HasnConversationMemberships.conversation_id == conversation_id,
                        HasnConversationMemberships.member_hasn_id == member_hasn_id,
                    )
                )
            )
            .scalars()
            .all()
        )

    @staticmethod
    async def _identity_owner_map(
        db: AsyncSession,
        hasn_ids: set[str],
    ) -> dict[str, str]:
        """批量取得身份所属主人，避免消息序列化产生 N+1 查询。"""
        if not hasn_ids:
            return {}
        owners: dict[str, str] = {}
        human_ids = [value for value in hasn_ids if value.startswith('h_')]
        agent_ids = [value for value in hasn_ids if value.startswith('a_')]
        if human_ids:
            human_rows = (
                await db.execute(
                    select(HasnHumans.hasn_id).where(
                        HasnHumans.hasn_id.in_(human_ids),
                    )
                )
            ).scalars()
            owners.update({hasn_id: hasn_id for hasn_id in human_rows})
        if agent_ids:
            agent_rows = (
                await db.execute(
                    select(HasnAgents.hasn_id, HasnAgents.owner_id).where(
                        HasnAgents.hasn_id.in_(agent_ids),
                    )
                )
            ).all()
            owners.update({hasn_id: owner_id for hasn_id, owner_id in agent_rows})
        return owners

    async def list_messages(
        self,
        query: ListMessagesQuery,
        principal: ServicePrincipal,
    ) -> MessagePage:
        """按全部 membership 周期并集分页读取可见消息。"""
        viewer = await self._effective_sender(principal)
        conversation_uuid = self._conversation_uuid(query.conversation_id)
        limit = max(1, min(query.limit, 200))
        try:
            before_id = int(query.before_cursor) if query.before_cursor else None
        except ValueError as exc:
            raise ImSendRejected(2002, '消息分页游标无效') from exc

        async with self.session_factory() as db:
            conversation = await db.get(HasnConversations, conversation_uuid)
            if conversation is None:
                raise ImConversationNotFound(query.conversation_id)
            epochs = await self._membership_epochs(
                db,
                conversation_id=conversation_uuid,
                member_hasn_id=viewer,
            )
            if not epochs:
                raise ImSenderNotParticipant(query.conversation_id, viewer)

            epoch_clauses = []
            for epoch in epochs:
                clause = HasnMessages.conversation_seq >= epoch.joined_seq
                if epoch.left_seq is not None:
                    clause = and_(
                        clause,
                        HasnMessages.conversation_seq <= epoch.left_seq,
                    )
                epoch_clauses.append(clause)

            statement = select(HasnMessages).where(
                HasnMessages.conversation_id == conversation_uuid,
                or_(*epoch_clauses),
            )
            if query.origin_session_id is not None:
                statement = statement.where(
                    HasnMessages.origin_session_id == query.origin_session_id
                )
            if before_id is not None:
                statement = statement.where(HasnMessages.id < before_id)
            rows = list(
                (
                    await db.execute(
                        statement.order_by(HasnMessages.id.desc()).limit(limit + 1)
                    )
                )
                .scalars()
                .all()
            )
            has_more = len(rows) > limit
            page_rows = rows[:limit]
            owner_map = await self._identity_owner_map(
                db,
                {value for msg in page_rows for value in (msg.from_id, msg.to_id)},
            )
            items = [
                {
                    'id': f'msg_{msg.id}',
                    'version': '1.0',
                    'type': msg.msg_type or 'message',
                    'from': {
                        'hasn_id': msg.from_id,
                        'entity_type': self._entity_kind(msg.from_id),
                        'owner_id': owner_map.get(msg.from_id),
                    },
                    'to': {
                        'hasn_id': msg.to_id,
                        'entity_type': self._entity_kind(msg.to_id),
                        'owner_id': owner_map.get(msg.to_id),
                    },
                    'content': {
                        'content_type': self._content_kind(msg.content_type),
                        'body': (
                            msg.content
                            if isinstance(msg.content, dict)
                            else {'text': str(msg.content)}
                        ),
                    },
                    'context': {
                        'conversation_id': str(msg.conversation_id),
                        'conversation_seq': msg.conversation_seq,
                        'relation_type': conversation.relation_type,
                        'reply_to': (
                            str(msg.reply_to_id) if msg.reply_to_id else None
                        ),
                    },
                    'metadata': {
                        'priority': msg.priority or 'normal',
                        'status': msg.status,
                        'created_at': (
                            msg.created_time.isoformat()
                            if msg.created_time
                            else None
                        ),
                        'server_received_at': (
                            msg.server_received_at.isoformat()
                            if msg.server_received_at
                            else None
                        ),
                        'origin_session_id': msg.origin_session_id,
                    },
                    'local_id': str(msg.local_id) if msg.local_id else None,
                }
                for msg in reversed(page_rows)
            ]
            next_cursor = str(page_rows[-1].id) if has_more and page_rows else None
            return MessagePage(
                items=items,
                has_more=has_more,
                next_cursor=next_cursor,
            )

    @staticmethod
    def _decode_conversation_cursor(cursor: str) -> tuple[int, UUID]:
        """解析 ``last_message_id:conversation_uuid`` 复合游标。"""
        try:
            last_message_id, conversation_id = cursor.split(':', 1)
            return int(last_message_id), UUID(conversation_id)
        except (TypeError, ValueError) as exc:
            raise ImSendRejected(2002, '会话分页游标无效') from exc

    @staticmethod
    async def _profile_map(
        db: AsyncSession,
        peer_ids: set[str],
    ) -> dict[str, tuple[str, str | None]]:
        """批量取得会话对端展示名和头像。"""
        profiles: dict[str, tuple[str, str | None]] = {}
        human_ids = [value for value in peer_ids if value.startswith('h_')]
        agent_ids = [value for value in peer_ids if value.startswith('a_')]
        if human_ids:
            rows = (
                await db.execute(
                    select(
                        HasnHumans.hasn_id,
                        HasnHumans.nickname,
                        HasnHumans.avatar,
                    ).where(HasnHumans.hasn_id.in_(human_ids))
                )
            ).all()
            profiles.update(
                {
                    hasn_id: (nickname or hasn_id, avatar)
                    for hasn_id, nickname, avatar in rows
                }
            )
        if agent_ids:
            rows = (
                await db.execute(
                    select(
                        HasnAgents.hasn_id,
                        HasnAgents.display_name,
                        HasnAgents.avatar,
                    ).where(HasnAgents.hasn_id.in_(agent_ids))
                )
            ).all()
            profiles.update(
                {
                    hasn_id: (display_name or hasn_id, avatar)
                    for hasn_id, display_name, avatar in rows
                }
            )
        return profiles

    async def list_conversations(
        self,
        query: ListConversationsQuery,
        principal: ServicePrincipal,
    ) -> MessagePage:
        """从活动 membership 与未读投影分页读取当前会话。"""
        viewer = await self._effective_sender(principal)
        limit = max(1, min(query.limit, 200))
        last_message_sort = func.coalesce(HasnConversations.last_message_id, 0)
        statement = (
            select(
                HasnConversations,
                HasnUnreadProjection.unread_count,
            )
            .join(
                HasnConversationMemberships,
                and_(
                    HasnConversationMemberships.conversation_id
                    == HasnConversations.id,
                    HasnConversationMemberships.member_hasn_id == viewer,
                    HasnConversationMemberships.left_seq.is_(None),
                    HasnConversationMemberships.state == 'active',
                ),
            )
            .outerjoin(
                HasnUnreadProjection,
                and_(
                    HasnUnreadProjection.conversation_id == HasnConversations.id,
                    HasnUnreadProjection.member_hasn_id == viewer,
                ),
            )
            .where(HasnConversations.status != 'disbanded')
        )
        if query.cursor:
            cursor_message_id, cursor_conversation_id = (
                self._decode_conversation_cursor(query.cursor)
            )
            statement = statement.where(
                or_(
                    last_message_sort < cursor_message_id,
                    and_(
                        last_message_sort == cursor_message_id,
                        HasnConversations.id < cursor_conversation_id,
                    ),
                )
            )
        statement = statement.order_by(
            last_message_sort.desc(),
            HasnConversations.id.desc(),
        ).limit(limit + 1)

        async with self.session_factory() as db:
            rows = list((await db.execute(statement)).all())
            has_more = len(rows) > limit
            page_rows = rows[:limit]
            peer_ids = {
                peer_id
                for conversation, _ in page_rows
                if (
                    peer_id := (
                        conversation.participant_b_id
                        if conversation.participant_a_id == viewer
                        else conversation.participant_a_id
                    )
                )
                and conversation.type == 'direct'
            }
            profiles = await self._profile_map(db, peer_ids)
            items: list[dict[str, Any]] = []
            for conversation, unread_count in page_rows:
                if conversation.type == 'group':
                    peer_id = conversation.group_id or str(conversation.id)
                    peer_name = conversation.group_name or peer_id
                    peer_type = 'group'
                    peer_avatar = conversation.group_avatar_url
                else:
                    peer_id = (
                        conversation.participant_b_id
                        if conversation.participant_a_id == viewer
                        else conversation.participant_a_id
                    ) or ''
                    peer_name, peer_avatar = profiles.get(
                        peer_id,
                        (peer_id, None),
                    )
                    peer_type = self._entity_kind(peer_id)
                items.append(
                    {
                        'id': str(conversation.id),
                        'type': conversation.type or 'direct',
                        'peer_id': peer_id,
                        'peer_name': peer_name,
                        'peer_type': peer_type,
                        'peer_avatar': peer_avatar,
                        'relation_type': conversation.relation_type,
                        'last_message_preview': conversation.last_message_preview,
                        'last_message_at': (
                            str(conversation.last_message_at)
                            if conversation.last_message_at
                            else None
                        ),
                        'last_message_from': conversation.last_message_from,
                        'unread_count': int(unread_count or 0),
                        'message_count': conversation.message_count or 0,
                    }
                )
            next_cursor = None
            if has_more and page_rows:
                last_conversation = page_rows[-1][0]
                next_cursor = (
                    f'{last_conversation.last_message_id or 0}:'
                    f'{last_conversation.id}'
                )
            return MessagePage(
                items=items,
                has_more=has_more,
                next_cursor=next_cursor,
            )

    async def advance_read_cursor(
        self, command: ReadCursorCommand, principal: ServicePrincipal
    ) -> int:
        """按消息 ID 或会话序号单调推进活动 membership 的 read_seq。"""
        reader = await self._effective_sender(principal)
        conversation_uuid = self._conversation_uuid(command.conversation_id)
        if command.up_to_message_id is not None and command.up_to_seq is not None:
            raise ImSendRejected(2002, '已读命令只能指定一种目标游标')

        async with self.session_factory() as db:
            conversation = await db.get(
                HasnConversations,
                conversation_uuid,
                with_for_update=True,
            )
            if conversation is None:
                raise ImConversationNotFound(command.conversation_id)
            if command.up_to_message_id is not None:
                incoming_seq = (
                    await db.execute(
                        select(HasnMessages.conversation_seq).where(
                            HasnMessages.id == command.up_to_message_id,
                            HasnMessages.conversation_id == conversation_uuid,
                        )
                    )
                ).scalar_one_or_none()
                if incoming_seq is None:
                    raise ImSendRejected(3001, '目标消息不属于当前会话')
            elif command.up_to_seq is not None:
                incoming_seq = command.up_to_seq
            else:
                incoming_seq = conversation.current_seq

            read_seq = await membership_service.advance_read_seq(
                db,
                str(conversation_uuid),
                reader,
                incoming_seq=max(0, int(incoming_seq)),
                current_seq=conversation.current_seq,
            )
            if read_seq is None:
                raise ImSenderNotParticipant(command.conversation_id, reader)
            await membership_service.rebuild_unread_projection(
                db,
                str(conversation_uuid),
                reader,
                current_seq=conversation.current_seq,
            )
            await db.commit()
            return read_seq

    async def update_group_members(
        self, command: UpdateGroupMembersCommand, principal: ServicePrincipal
    ) -> None:
        """在一个 IM 事务内闭合、重建群 membership epoch。"""
        actor = await self._effective_sender(principal)
        conversation_uuid = self._conversation_uuid(command.conversation_id)
        additions = list(dict.fromkeys(value.strip() for value in command.add if value.strip()))
        removals = list(
            dict.fromkeys(value.strip() for value in command.remove if value.strip())
        )
        overlap = set(additions) & set(removals)
        if overlap:
            raise ImSendRejected(
                2002,
                f'同一成员不能同时加入和退出：{sorted(overlap)}',
            )
        for member_hasn_id in additions:
            await self._require_active_peer(member_hasn_id)

        async with self.session_factory() as db:
            conversation = (
                await db.execute(
                    select(HasnConversations)
                    .where(HasnConversations.id == conversation_uuid)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if (
                conversation is None
                or conversation.type != 'group'
                or conversation.status != 'active'
            ):
                raise ImConversationNotFound(command.conversation_id)

            active_rows = list(
                (
                    await db.execute(
                        select(HasnConversationMemberships)
                        .where(
                            HasnConversationMemberships.conversation_id
                            == conversation_uuid,
                            HasnConversationMemberships.left_seq.is_(None),
                            HasnConversationMemberships.state == 'active',
                        )
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            active_by_id = {
                row.member_hasn_id: row
                for row in active_rows
            }
            actor_membership = active_by_id.get(actor)
            if actor_membership is None:
                raise ImSendRejected(2002, '只有群成员可以更新群成员')
            actor_is_admin = actor_membership.role in {'owner', 'admin'}

            actual_additions = [
                value for value in additions if value not in active_by_id
            ]
            actual_removals = [
                value for value in removals if value in active_by_id
            ]
            if conversation.group_owner_id in actual_removals:
                raise ImSendRejected(2002, '群主不可被移除')

            if actual_additions and not actor_is_admin:
                if any(value.startswith('a_') for value in actual_additions):
                    if not conversation.allow_member_invite_agent:
                        raise ImSendRejected(2002, '群主已限制普通成员添加分身')

            for member_hasn_id in actual_additions:
                if member_hasn_id.startswith('a_'):
                    agent_owner_id = await db.scalar(
                        select(HasnAgents.owner_id).where(
                            HasnAgents.hasn_id == member_hasn_id,
                        )
                    )
                    if agent_owner_id and agent_owner_id != actor:
                        raise ImSendRejected(
                            2002,
                            '添加他人分身须先走主人确认邀请流程',
                        )

            for member_hasn_id in actual_removals:
                target = active_by_id[member_hasn_id]
                allowed = member_hasn_id == actor or actor_is_admin
                if not allowed and target.member_type == 'agent':
                    target_owner_id = await db.scalar(
                        select(HasnAgents.owner_id).where(
                            HasnAgents.hasn_id == member_hasn_id,
                        )
                    )
                    allowed = target_owner_id == actor
                if not allowed:
                    raise ImSendRejected(2002, '仅本人或群管理员可移除群成员')

            if len(active_rows) - len(actual_removals) + len(actual_additions) > (
                conversation.max_members or 500
            ):
                raise ImSendRejected(2002, '群成员数已达到上限')

            for member_hasn_id in actual_removals:
                await membership_service.leave_epoch(
                    db,
                    str(conversation_uuid),
                    member_hasn_id,
                    current_seq=conversation.current_seq,
                    state=(
                        'left'
                        if member_hasn_id == actor
                        else 'removed'
                    ),
                )

            for member_hasn_id in actual_additions:
                member_type, member_name, member_star_id = (
                    await self._member_identity_snapshot(
                        db,
                        member_hasn_id,
                    )
                )
                epoch = await membership_service.join_epoch(
                    db,
                    str(conversation_uuid),
                    member_hasn_id,
                    current_seq=conversation.current_seq,
                    member_type=member_type,
                    role='member',
                )
                epoch.member_name = member_name
                epoch.member_star_id = member_star_id
                epoch.invited_by = actor
                epoch.joined_at = timezone.now()

            if not actual_additions and not actual_removals:
                await db.rollback()
                return

            conversation.member_count = (
                len(active_rows)
                - len(actual_removals)
                + len(actual_additions)
            )
            conversation.revision = int(conversation.revision or 0) + 1
            affected_ids = sorted(
                {
                    *(row.member_hasn_id for row in active_rows),
                    *actual_additions,
                }
            )
            await append_event(
                db,
                event_type=IM_CONVERSATION_UPDATED,
                aggregate_type='conversation',
                aggregate_id=str(conversation_uuid),
                aggregate_seq=conversation.revision,
                payload={
                    'conversation_id': str(conversation_uuid),
                    'revision': conversation.revision,
                    'change': 'members',
                    'added_member_hasn_ids': actual_additions,
                    'removed_member_hasn_ids': actual_removals,
                    'audience_hasn_ids': affected_ids,
                    'updated_by': actor,
                },
            )
            await db.commit()

    @staticmethod
    async def _member_identity_snapshot(
        db: AsyncSession,
        member_hasn_id: str,
    ) -> tuple[str, str, str]:
        """读取群名册所需的公开身份快照。"""
        if member_hasn_id.startswith('h_'):
            row = (
                await db.execute(
                    select(
                        HasnHumans.nickname,
                        HasnHumans.star_id,
                    ).where(HasnHumans.hasn_id == member_hasn_id)
                )
            ).one_or_none()
            if row is None:
                raise ImSendRejected(2002, '群成员身份不存在')
            return 'human', row.nickname or member_hasn_id, row.star_id or ''
        if member_hasn_id.startswith('a_'):
            row = (
                await db.execute(
                    select(
                        HasnAgents.display_name,
                        HasnAgents.star_id,
                    ).where(HasnAgents.hasn_id == member_hasn_id)
                )
            ).one_or_none()
            if row is None:
                raise ImSendRejected(2002, '群成员身份不存在')
            return 'agent', row.display_name or member_hasn_id, row.star_id or ''
        raise ImSendRejected(2002, '群成员只支持人类或分身身份')

    async def authz_verdict(
        self,
        *,
        sender_hasn_id: str,
        receiver_hasn_id: str,
        relation_type: str = 'social',
        envelope: dict | None = None,
    ) -> dict:
        async with self.session_factory() as db:
            return await message_service.check_relation_permission(
                db,
                sender_id=sender_hasn_id,
                receiver_id=receiver_hasn_id,
                msg_type=relation_type,
            )

    async def release_suppressed(
        self, *, suppressed_id: int, principal: ServicePrincipal
    ) -> SendMessageResult:
        """主人放行抑制命令：关系变更、消息、新 seq 与集成事件同一 IM 事务。"""
        owner_id = await self._effective_sender(principal)
        if principal.actor_kind is not ActorKind.HUMAN or owner_id != principal.canonical_sender:
            raise ImSendRejected(2002, '只有主人身份可以放行抑制消息')

        from backend.app.hasn_im.application.provider import (
            get_transactional_relation_gateway,
        )
        from backend.app.hasn_im.application.suppression_service import (
            commit_suppressed_rows,
        )

        async with self.session_factory() as db:
            row = (
                await db.execute(
                    select(HasnSuppressedMessages)
                    .where(HasnSuppressedMessages.id == suppressed_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is None or row.owner_id != owner_id:
                raise ImSendRejected(3001, '抑制消息不存在或不属于当前主人')
            if row.message_id is not None and row.resolved_at is not None:
                existing = await db.get(HasnMessages, row.message_id)
                if existing is None:
                    raise ImSendRejected(3001, '已放行抑制记录对应的消息不存在')
                return SendMessageResult(
                    delivery_state=DeliveryState.ACCEPTED,
                    conversation_id=str(existing.conversation_id),
                    message_id=existing.id,
                    conversation_seq=existing.conversation_seq,
                    deduped=True,
                )
            if row.suppress_reason == 'agent_frozen':
                raise ImSendRejected(2002, '分身已停用，请先恢复分身后再放行')
            command = dict(row.command_payload or {})
            if command.get('owner_id') != owner_id:
                raise ImSendRejected(2002, '抑制命令主人归属与认证主体不一致')
            if command.get('to_id') != row.hasn_id:
                raise ImSendRejected(2002, '抑制命令接收方与抑制记录不一致')
            sender_id = str(command.get('from_id') or '')
            await self._require_active_peer(sender_id)
            await self._require_active_peer(row.hasn_id)

            if row.suppress_reason in {'permission_denied', 'abuse_restricted'}:
                request_id_value = (row.policy_snapshot or {}).get(
                    'pending_request_id'
                )
                gateway = get_transactional_relation_gateway(db)
                await gateway.upsert_release_contact(
                    owner_hasn_id=owner_id,
                    peer_hasn_id=sender_id,
                    minimum_trust_level=2,
                    request_id=(
                        int(request_id_value)
                        if request_id_value is not None
                        else None
                    ),
                )
            elif row.suppress_reason == 'social_disabled':
                gateway = get_transactional_relation_gateway(db)
                await gateway.update_agent_communication_settings(
                    owner_hasn_id=owner_id,
                    agent_hasn_id=row.hasn_id,
                    social_enabled=True,
                )

            try:
                committed = await commit_suppressed_rows(
                    db,
                    [row],
                )
            except ValueError as exc:
                raise ImSendRejected(3015, str(exc)) from exc
            _, message, deduped = committed[0]
            await db.commit()
            return SendMessageResult(
                delivery_state=DeliveryState.ACCEPTED,
                conversation_id=str(message.conversation_id),
                message_id=message.id,
                conversation_seq=message.conversation_seq,
                deduped=deduped,
            )

    async def list_suppressed(
        self,
        *,
        principal: ServicePrincipal,
    ) -> list[dict[str, Any]]:
        """列出主人可见的待放行命令，旧已入消息记录仅作迁移期兼容读取。"""
        owner_id = await self._effective_sender(principal)
        if principal.actor_kind is not ActorKind.HUMAN or owner_id != principal.canonical_sender:
            raise ImSendRejected(2002, '只有主人身份可以查看抑制箱')

        async with self.session_factory() as db:
            rows = list(
                (
                    await db.execute(
                        select(HasnSuppressedMessages)
                        .where(
                            HasnSuppressedMessages.owner_id == owner_id,
                            HasnSuppressedMessages.visible_to_owner.is_(True),
                            HasnSuppressedMessages.resolved_at.is_(None),
                        )
                        .order_by(HasnSuppressedMessages.id.asc())
                    )
                )
                .scalars()
                .all()
            )
            legacy_message_ids = [
                row.message_id
                for row in rows
                if row.message_id is not None and not row.command_payload
            ]
            legacy_messages: dict[int, HasnMessages] = {}
            if legacy_message_ids:
                legacy_messages = {
                    message.id: message
                    for message in (
                        await db.execute(
                            select(HasnMessages).where(
                                HasnMessages.id.in_(legacy_message_ids)
                            )
                        )
                    )
                    .scalars()
                    .all()
                }
            sender_ids = {
                str(
                    (row.command_payload or {}).get('from_id')
                    or row.sender_hasn_id
                    or getattr(
                        legacy_messages.get(row.message_id)
                        if row.message_id is not None
                        else None,
                        'from_id',
                        '',
                    )
                )
                for row in rows
            }
            sender_ids.discard('')
            owner_map = await self._identity_owner_map(db, sender_ids)
            sender_owner_ids = set(owner_map.values())
            owner_names = {
                hasn_id: nickname
                for hasn_id, nickname in (
                    await db.execute(
                        select(HasnHumans.hasn_id, HasnHumans.nickname).where(
                            HasnHumans.hasn_id.in_(sender_owner_ids)
                        )
                    )
                ).all()
            }

        items: list[dict[str, Any]] = []
        for row in rows:
            legacy_message = (
                legacy_messages.get(row.message_id)
                if row.message_id is not None
                else None
            )
            command = dict(row.command_payload or {})
            content = command.get('content')
            if content is None and legacy_message is not None:
                content = legacy_message.content
            sender_id = str(
                command.get('from_id')
                or row.sender_hasn_id
                or getattr(legacy_message, 'from_id', '')
            )
            text = ''
            if isinstance(content, dict):
                raw_text = content.get('text') or content.get('content') or ''
                text = raw_text if isinstance(raw_text, str) else ''
            elif isinstance(content, str):
                text = content
            sender_owner_id = owner_map.get(sender_id)
            pending_request_id = (row.policy_snapshot or {}).get(
                'pending_request_id'
            )
            items.append(
                {
                    'suppressed_id': str(row.id),
                    'message_id': (
                        str(row.message_id) if row.message_id is not None else None
                    ),
                    'conversation_id': str(row.conversation_id),
                    'agent_hasn_id': row.hasn_id,
                    'reason': row.suppress_reason or 'permission_denied',
                    'created_at': (
                        int(row.created_time.timestamp())
                        if row.created_time
                        else 0
                    ),
                    'message_preview': text.strip().replace('\n', ' ')[:80],
                    'original_body': json.dumps(
                        {'text': text},
                        ensure_ascii=False,
                    ),
                    'sender_hasn_id': sender_id or None,
                    'sender_owner_hasn_id': sender_owner_id,
                    'sender_owner_name': owner_names.get(sender_owner_id),
                    'pending_request_id': (
                        str(pending_request_id)
                        if pending_request_id is not None
                        else None
                    ),
                }
            )
        return items
