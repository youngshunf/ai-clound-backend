"""hasn_im.ports · 对外契约（唯一允许被其他模块 import 的层）

port 只暴露 Protocol + 纯值对象 DTO；**禁止**暴露 Session/Request/Redis client/ORM model
（§5.2）。业务模块经这些 port 与 IM 交互，实现（application/adapters）对外不可见。
"""

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
    ResolvedTarget,
    SendMessageCommand,
    SendMessageResult,
    ServicePrincipal,
    UpdateGroupMembersCommand,
)
from backend.app.hasn_im.ports.im_gateway import ImGateway
from backend.app.hasn_im.ports.node_binding_view import NodeBindingView, OwnerBindingRef
from backend.app.hasn_im.ports.node_session_gateway import (
    AgentSessionResult,
    NodeRegistration,
    NodeSessionGateway,
    NodeSessionResult,
    OwnerBindingResult,
)
from backend.app.hasn_im.ports.presence_query import OnlinePresence, PresenceQuery
from backend.app.hasn_im.ports.realtime_gateway import RealtimeFrame, RealtimeGateway
from backend.app.hasn_im.ports.realtime_wakeup_bus import RealtimeWakeupBus
from backend.app.hasn_im.ports.relation_gateway import (
    EffectiveRelation,
    RelationGateway,
)

__all__ = [  # noqa: RUF022 — 按 DTO 与 Protocol 分组，避免类型类别混排
    # DTO
    'ActorKind',
    'ConversationRef',
    'DeliveryState',
    'EnsureDirectConversationCommand',
    'ListConversationsQuery',
    'ListMessagesQuery',
    'MessagePage',
    'ReadCursorCommand',
    'RecallMessageCommand',
    'ResolvedTarget',
    'SendMessageCommand',
    'SendMessageResult',
    'ServicePrincipal',
    'UpdateGroupMembersCommand',
    # Protocol
    'ImGateway',
    'NodeBindingView',
    'OwnerBindingRef',
    'NodeSessionGateway',
    'NodeRegistration',
    'NodeSessionResult',
    'OwnerBindingResult',
    'AgentSessionResult',
    'PresenceQuery',
    'OnlinePresence',
    'RealtimeFrame',
    'RealtimeGateway',
    'RealtimeWakeupBus',
    'EffectiveRelation',
    'RelationGateway',
]
