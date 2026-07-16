"""hasn_im.ports.dto · 端口值对象（不可变 DTO）

全部为 frozen dataclass / str Enum——纯值对象，**不含** Session/Request/Redis/ORM model
引用（§5.2 port 禁暴露约束）。业务模块构造这些命令交给 `ImGateway`/`RelationGateway`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ActorKind(str, Enum):
    """认证主体种类（§5.1）。system_service 为系统通知发送方，最小 scope。"""

    HUMAN = 'human'
    AGENT = 'agent'
    NODE = 'node'
    SYSTEM_SERVICE = 'system_service'


class DeliveryState(str, Enum):
    """发送结果投递态（§5.2）。

    - ``accepted``：已提交，将投影 ``message.new``；
    - ``pending_policy``：已入库并占用 ``conversation_seq``，等待上游异步裁决（§9.4），
      判决通过才产生投递事件；
    - ``suppressed``：被确定性门控拦入抑制箱——**不入 message 表、不占 seq**，
      放行时按普通发送重新走完整事务并分配**新 seq**。
    """

    ACCEPTED = 'accepted'
    PENDING_POLICY = 'pending_policy'
    SUPPRESSED = 'suppressed'


@dataclass(frozen=True)
class ServicePrincipal:
    """受信任的调用上下文（§5.1）。

    sender 与 origin_node 从**认证结果**规范化，**不从请求体自报**：
    - ``canonical_sender``：JWT/OwnerKey/node session 认证出的发送方 hasn_id；
    - ``origin_node_id``：从连接/认证上下文获取，请求体不可自报；无节点上下文（系统服务/
      producer relay）规范化为统一哨兵空值（``None``），幂等作用域退化（§5.4）；
    - ``actor_kind``：主体种类；
    - ``send_as``：代表某 Agent 发送时的目标身份，服务端须校验调用者是其 owner 或持受限
      delegation（§5.1）——本 DTO 只承载声明，校验在 application 层。
    """

    canonical_sender: str
    actor_kind: ActorKind
    origin_node_id: str | None = None
    send_as: str | None = None
    origin_session_id: str | None = None


@dataclass(frozen=True)
class EnsureDirectConversationCommand:
    """幂等取得直聊会话（§5.3）。建会话本身不代表允许投递。"""

    peer_hasn_id: str
    relation_type: str = 'social'
    # 差事背景（§6.5 mission_note）：仅新建会话时写入，发送方 owner 私有
    mission_note: str | None = None
    # 差事背景归属 owner（= 发起方 owner）：投影裁剪据此判定（§6.5）。调用方从认证上下文
    # 显式解析发送方主人后传入，**不从「首条消息发送方」反推**（零推断）；无 mission_note 时无意义。
    mission_note_owner_id: str | None = None


@dataclass(frozen=True)
class ConversationRef:
    """会话引用（值对象，不暴露 ORM）。"""

    conversation_id: str
    conversation_type: str = 'direct'  # direct | group
    created: bool = False  # 本次调用是否新建


@dataclass(frozen=True)
class SendMessageCommand:
    """严格发送契约（§5.2）。

    - ``conversation_id`` **必填**——拒绝 target 与 conversation_id 联合二选一（禁字段）；
    - ``idempotency_key`` 必填（WS local_id 映射到同一去重存储）；
    - ``send_as`` 可选、受权限校验。
    """

    conversation_id: str
    content: dict[str, Any]
    content_type: int = 1
    idempotency_key: str | None = None
    msg_type: str = 'message'
    priority: str = 'normal'
    reply_to_id: int | None = None
    context: dict[str, Any] | None = None
    mentions: list[dict[str, Any]] | None = None
    mention_all: bool = False


@dataclass(frozen=True)
class SendMessageResult:
    """发送结果（§5.2）。必须返回显式 ``delivery_state``。

    ``conversation_seq`` 允许空洞（撤回/判决拒绝/pending 均可造成不连续）；客户端不得以
    seq 连续性判定丢失，丢失判定只依据 sync cursor（§5.2 契约）。
    """

    delivery_state: DeliveryState
    conversation_id: str
    message_id: int | None = None
    conversation_seq: int | None = None
    deduped: bool = False
    # 抑制/暂存时的结构化关系反馈（供消息工具诚实回传，§4.1.4 修 B12）
    suppress_reason: str | None = None
    relation: dict[str, Any] | None = None
    pending_request_id: int | None = None


@dataclass(frozen=True)
class RecallMessageCommand:
    """撤回消息命令。"""

    conversation_id: str
    message_id: int


@dataclass(frozen=True)
class ReadCursorCommand:
    """推进已读游标（§4.3）。read_seq 单调推进，不回退。"""

    conversation_id: str
    # 二选一：按目标消息 id 或按目标 seq 推进；application 层归一到 read_seq
    up_to_message_id: int | None = None
    up_to_seq: int | None = None


@dataclass(frozen=True)
class UpdateGroupMembersCommand:
    """更新群成员（§4.2 成员周期）。"""

    conversation_id: str
    add: list[str] = field(default_factory=list)  # 加入成员 hasn_id
    remove: list[str] = field(default_factory=list)  # 退出成员 hasn_id


@dataclass(frozen=True)
class ListMessagesQuery:
    """消息列表查询（稳定 cursor 分页）。"""

    conversation_id: str
    limit: int = 30
    before_cursor: str | None = None  # 游标（不透明），None=最新


@dataclass(frozen=True)
class ListConversationsQuery:
    """会话列表查询（cursor/limit + 批量 profile join）。"""

    viewer_hasn_id: str
    limit: int = 30
    cursor: str | None = None


@dataclass(frozen=True)
class MessagePage:
    """消息分页结果（稳定 cursor：items/has_more/next_cursor）。"""

    items: list[dict[str, Any]]
    has_more: bool = False
    next_cursor: str | None = None
