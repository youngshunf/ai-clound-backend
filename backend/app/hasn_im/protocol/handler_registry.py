"""hasn_im.protocol.handler_registry · 节点上行方法 typed handler registry（R1-09 协议层纯化）

把 ws_node._recv_loop 的 if/elif 长链分派提为**声明式表**：方法 → 该方法 handler 的位置
参数形状（``HandlerArg`` 序列）。表本身是**无 DB 纯声明**——不 import handler 实现、不碰
WebSocket / Session / Redis，可独立单测「覆盖 ``KNOWN_METHODS`` 全集 + 参数形状正确 +
未知方法显式无 spec」（doc92 §1.1：ws_node 协议与业务不可分问题）。

ws_node 侧据本表 table-driven 分派：按 spec 从连接上下文取对应位置参数调本地 ``_handle_*``，
消灭 if/elif；未在表内的方法 → 显式 9001（不静默丢弃）。handler 实现（引用 WS/Session）仍
留在 ws_node，本模块只声明「方法↔参数形状」这层纯契约。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from backend.app.hasn_im.protocol.frame import KNOWN_METHODS


class HandlerArg(str, Enum):
    """节点上行 handler 可注入的连接上下文位置参数（table-driven 分派的取值来源）。

    与 ws_node 侧 ``_handle_*`` 的形参一一对应；``build_handler_args`` 据 spec 从连接上下文
    按此顺序取值组装实参。
    """

    WEBSOCKET = 'websocket'
    NODE_ID = 'node_id'
    CONNECTION_ID = 'connection_id'
    PARAMS = 'params'
    ACTIVE_ENTITIES = 'active_entities'
    REQ_ID = 'req_id'


@dataclass(frozen=True)
class HandlerSpec:
    """一个节点上行方法的分派声明：位置参数形状 + 是否可请求关闭收发循环。

    ``args`` 是 handler 的**位置参数顺序**（与 ws_node 里 ``_handle_*`` 签名一一对应，改
    签名必须同步改此表，否则 ``build_handler_args`` 传错参）。``may_close_loop`` 标记该
    handler 可能要求关闭连接收发循环（当前仅 ``hasn.ping`` 的 superseded 关闭）——分派层据此
    判定 handler 返回真值时是否 break 收发循环，非该标记的 handler 返回值一律忽略。
    """

    method: str
    args: tuple[HandlerArg, ...]
    may_close_loop: bool = False


# 便捷别名（表定义处收窄书写噪声）。
_WS = HandlerArg.WEBSOCKET
_NODE = HandlerArg.NODE_ID
_CONN = HandlerArg.CONNECTION_ID
_PARAMS = HandlerArg.PARAMS
_ENTITIES = HandlerArg.ACTIVE_ENTITIES
_REQ = HandlerArg.REQ_ID


# 方法 → 分派声明。**权威来源 = ws_node 现有 if/elif 分派 + 各 _handle_* 签名**（R1-09 逐字
# 对照迁移，行为不变）。新增/改签名的 method 必须同步改此表 + KNOWN_METHODS，否则不变量守卫
# （见文件末尾断言）与单测会红。
HANDLER_SPECS: dict[str, HandlerSpec] = {
    'hasn.node.add_owner': HandlerSpec('hasn.node.add_owner', (_WS, _NODE, _PARAMS, _ENTITIES)),
    'hasn.node.remove_owner': HandlerSpec('hasn.node.remove_owner', (_WS, _NODE, _PARAMS, _ENTITIES)),
    'hasn.node.renew_owner': HandlerSpec('hasn.node.renew_owner', (_WS, _NODE, _PARAMS, _ENTITIES)),
    'hasn.node.list_owners': HandlerSpec('hasn.node.list_owners', (_WS, _NODE)),
    'hasn.node.add_agent': HandlerSpec('hasn.node.add_agent', (_WS, _NODE, _PARAMS, _ENTITIES)),
    'hasn.node.remove_agent': HandlerSpec('hasn.node.remove_agent', (_WS, _NODE, _PARAMS, _ENTITIES)),
    'hasn.agent.register': HandlerSpec('hasn.agent.register', (_WS, _NODE, _PARAMS, _ENTITIES, _REQ)),
    'hasn.agent.deregister': HandlerSpec('hasn.agent.deregister', (_WS, _NODE, _PARAMS, _ENTITIES, _REQ)),
    'hasn.message.send': HandlerSpec('hasn.message.send', (_WS, _NODE, _PARAMS, _ENTITIES)),
    'hasn.message.read': HandlerSpec('hasn.message.read', (_PARAMS, _ENTITIES)),
    'hasn.typing': HandlerSpec('hasn.typing', (_PARAMS, _ENTITIES)),
    'hasn.ping': HandlerSpec('hasn.ping', (_WS, _NODE, _CONN, _PARAMS), may_close_loop=True),
}


def resolve_handler_spec(method: str) -> HandlerSpec | None:
    """取 method 的分派声明；未登记方法返回 None（分派层据此显式回 9001，非静默丢弃）。"""
    return HANDLER_SPECS.get(method)


def build_handler_args(
    spec: HandlerSpec,
    *,
    websocket: object,
    node_id: object,
    connection_id: object,
    params: object,
    active_entities: object,
    req_id: object,
) -> tuple[object, ...]:
    """按 spec 的参数形状，从连接上下文取值组装 handler 位置实参（纯函数，无副作用）。

    返回元组顺序 = ``spec.args`` 顺序，直接 ``handler(*args)`` 调用。上下文值一律显式传入，
    本函数不接触任何 WS / DB / 全局态，故可脱离连接单测。
    """
    _lookup: dict[HandlerArg, object] = {
        HandlerArg.WEBSOCKET: websocket,
        HandlerArg.NODE_ID: node_id,
        HandlerArg.CONNECTION_ID: connection_id,
        HandlerArg.PARAMS: params,
        HandlerArg.ACTIVE_ENTITIES: active_entities,
        HandlerArg.REQ_ID: req_id,
    }
    return tuple(_lookup[arg] for arg in spec.args)


# 不变量（静态半边）：registry 键集必须与协议帧 KNOWN_METHODS 完全一致——两处任一新增/漏改
# 即在 import 期炸，杜绝「登记了方法但没进 registry / 反之」的漂移。ws_node 侧另有绑定表
# （method → _handle_* 实现）与本表键集一致的运行期断言，形成双向守卫。
assert set(HANDLER_SPECS) == set(KNOWN_METHODS), (
    'HANDLER_SPECS 与 KNOWN_METHODS 不一致：'
    f'仅在 registry={set(HANDLER_SPECS) - set(KNOWN_METHODS)}，'
    f'仅在 KNOWN_METHODS={set(KNOWN_METHODS) - set(HANDLER_SPECS)}'
)
