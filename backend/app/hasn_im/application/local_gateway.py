"""hasn_im.application.local_gateway · PythonLocalImGateway（R1-02 第一版包装）

把 `ImGateway` port 薄薄地架在**现有** `route_message` / `get_or_create_conversation`
之上——外部行为与现网**逐字节一致**（R1-02 验收：包装版行为与现网一致），只是把入口
收敛成 port 契约：命令 + `ServicePrincipal` 进、`SendMessageResult` 出，不暴露 Session。

**逐步落地（§5.2）**：严格 conversation_id-first 契约在本层已生效（send 只收 conversation_id，
counterpart 由会话反解），但底层仍复用现网 `route_message`（R2-04 起替换为独立事务/事件写点）。
group 发送、撤回、读列表、read cursor、成员周期、抑制放行随各自 R1-05 切片 / R2 卡逐个补上——
本类现只实现 direct 会话的 ensure + send（contract suite 覆盖面），其余显式抛未实现指向后续卡。

依赖方向（§0.1）：本类属 application 层，对外只经 ports 暴露；业务模块不得直接
import 本类，只认 `ImGateway` 抽象。
"""

from __future__ import annotations

from typing import Any
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.hasn.model import HasnConversations
from backend.app.hasn_im.application import message_service
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
    return await message_service.route_message(
        db=db,
        from_id=from_id,
        to_target=to_target,
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
        reader=reader,
        conversation_id=conversation_id,
        last_msg_id=last_msg_id,
    )


def _participant_type(hasn_id: str) -> str:
    """按 hasn_id 前缀判定参与者类型。"""
    return message_service._entity_type_str(hasn_id)


@dataclass(slots=True)
class PythonLocalImGateway:
    """ImGateway 的 Python 本地实现（R1 包装现网，R2 起替换为独立事务/事件写点）。

    持有 `session_factory`（async_sessionmaker），每个方法自开事务边界——port 不暴露
    Session（§5.2）。现网 `route_message` 内部自行 commit，本层不再二次 commit send 主链。
    """

    session_factory: async_sessionmaker[AsyncSession]

    def _effective_sender(self, principal: ServicePrincipal) -> str:
        """代发（send_as）时的有效发送方；否则即认证发送方。校验在 R2-08 收口。"""
        return principal.send_as or principal.canonical_sender

    async def ensure_direct_conversation(
        self,
        command: EnsureDirectConversationCommand,
        principal: ServicePrincipal,
    ) -> ConversationRef:
        """幂等取得直聊会话（§5.3）——包装 get_or_create_conversation（advisory lock 内查改）。"""
        sender = self._effective_sender(principal)
        peer = command.peer_hasn_id
        async with self.session_factory() as db:
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
            await db.commit()
            return ConversationRef(
                conversation_id=str(conv.id),
                conversation_type=conv.type,
            )

    async def send_message(
        self,
        command: SendMessageCommand,
        principal: ServicePrincipal,
    ) -> SendMessageResult:
        """向已 ensure 的 direct 会话发送（§5.2）——conversation_id-first，counterpart 反解后包装 route_message。"""
        sender = self._effective_sender(principal)
        async with self.session_factory() as db:
            conv = await db.get(HasnConversations, command.conversation_id)
            if conv is None:
                raise ImConversationNotFound(command.conversation_id)
            if conv.type != 'direct':
                # 群发送随 R1-05 切片⑤ groups 接入本 port；第一版包装不承接群路径。
                raise NotImplementedError('群会话发送随 R1-05 groups 切片接入 ImGateway')

            to_target = self._counterpart(conv, sender, command.conversation_id)

            result = await message_service.route_message(
                db,
                from_id=sender,
                to_target=to_target,
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
        if conv.participant_a_id == sender:
            return conv.participant_b_id
        if conv.participant_b_id == sender:
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
            deduped=bool(result.get('deduped')),
        )

    # —— 以下方法随后续 R1 切片 / R2 卡接入，第一版包装不承接（避免与现网双份实现漂移）——

    async def recall_message(
        self, command: RecallMessageCommand, principal: ServicePrincipal
    ) -> SendMessageResult:
        raise NotImplementedError('撤回随 R2-04 send 契约事务化落地')

    async def list_messages(self, query: ListMessagesQuery) -> MessagePage:
        raise NotImplementedError('消息列表随 R1-10 API/查询修复接入 port')

    async def list_conversations(self, query: ListConversationsQuery) -> MessagePage:
        raise NotImplementedError('会话列表随 R1-10 API/查询修复接入 port')

    async def advance_read_cursor(
        self, command: ReadCursorCommand, principal: ServicePrincipal
    ) -> int:
        raise NotImplementedError('已读游标随 R2-03 read cursor 单调落地')

    async def update_group_members(
        self, command: UpdateGroupMembersCommand, principal: ServicePrincipal
    ) -> None:
        raise NotImplementedError('群成员周期随 R2-05 成员 epoch 落地')

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
        raise NotImplementedError('抑制放行随 R2-04 send 契约事务化落地')
