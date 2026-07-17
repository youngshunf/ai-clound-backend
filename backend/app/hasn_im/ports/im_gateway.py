"""hasn_im.ports.im_gateway · ImGateway 契约（§5.2）

方法全集见 16 号 §5.2。port 接收命令 + `ServicePrincipal`，**不接收** Session/Request/
Redis client/ORM model。实现（application/adapters）对外不可见。

契约红线（§5.2 禁字段/行为）：
- send 不接受 target 与 conversation_id 联合二选一（conversation_id 必填）；
- 不在发送事务中 get_or_create_conversation（ensure 与 send 分离，§5.3）；
- 不从 body 接受可信 sender/origin node（取自 ServicePrincipal）；
- 不保留绕过判权/事件/审计的 persist_message 公共入口。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from backend.app.hasn_im.ports.dto import (
    ConversationRef,
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


@runtime_checkable
class ImGateway(Protocol):
    """通信域对外唯一写/读入口。"""

    async def ensure_direct_conversation(
        self,
        command: EnsureDirectConversationCommand,
        principal: ServicePrincipal,
    ) -> ConversationRef:
        """幂等取得直聊会话（§5.3）。participant pair 规范化 + 加锁 + 唯一 conversation_id。"""
        ...

    async def send_message(
        self,
        command: SendMessageCommand,
        principal: ServicePrincipal,
    ) -> SendMessageResult:
        """向已有会话发送（§5.2/§7.1）。message + 幂等 + grant + integration event 单事务。"""
        ...

    async def recall_message(
        self,
        command: RecallMessageCommand,
        principal: ServicePrincipal,
    ) -> SendMessageResult:
        """撤回消息（造成 conversation_seq 空洞是允许的）。"""
        ...

    async def list_messages(self, query: ListMessagesQuery) -> MessagePage:
        """消息列表（稳定 cursor：items/has_more/next_cursor）。"""
        ...

    async def list_conversations(self, query: ListConversationsQuery) -> MessagePage:
        """会话列表（cursor/limit + 批量 profile join）。"""
        ...

    async def advance_read_cursor(
        self,
        command: ReadCursorCommand,
        principal: ServicePrincipal,
    ) -> int:
        """推进已读游标，返回推进后的 read_seq（§4.3 单调）。"""
        ...

    async def update_group_members(
        self,
        command: UpdateGroupMembersCommand,
        principal: ServicePrincipal,
    ) -> None:
        """更新群成员周期（§4.2 epoch：退出闭合不删行，重入新周期）。"""
        ...

    async def authz_verdict(
        self,
        *,
        sender_hasn_id: str,
        receiver_hasn_id: str,
        relation_type: str = 'social',
        envelope: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """确定性判权裁决（不落库，供预检/可达性查询）。"""
        ...

    async def release_suppressed(
        self,
        *,
        suppressed_id: int,
        principal: ServicePrincipal,
    ) -> SendMessageResult:
        """放行抑制箱消息（§5.2：重走完整事务分配新 seq）。"""
        ...
