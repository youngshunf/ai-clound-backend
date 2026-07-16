"""hasn_im.protocol.frame · HASN 节点协议帧编解码 + 校验（R1-09 协议层纯化）

把 ws_node.py 的帧构造（事件帧 / 响应帧 / 错误帧）与入站帧解析提为**无 DB 纯模块**——
协议编解码与传输（WebSocket / Socket.IO）、业务、ORM 彻底解耦，可独立单测
（doc92 §1.1：ws_node 协议与业务不可分问题）。本模块**不 import** WebSocket / Session /
ORM，纯 str/dict ↔ dict。

R1-09 后续小步在此基础上推进：
- if/elif 分派 → typed handler registry（消费 KNOWN_METHODS + parse_inbound）；
- frame size / 发送超时 / backpressure 配置化；
- Socket.IO 隔离进兼容 adapter。
"""

from __future__ import annotations

import json

from typing import Any

# HASN 节点协议版本。改版本 = 破坏性变更，须与 daemon 端（hasn/0.2）同步。
HASN_PROTOCOL = 'hasn/0.2'

# 节点上行方法全集（= ws_node._recv_loop 现有 if/elif 分派集，typed registry 的权威来源）。
# 新增 method 必须同步登记此集合，否则 is_known_method 判为未知 → 显式 9001（不静默丢弃）。
KNOWN_METHODS: frozenset[str] = frozenset({
    'hasn.node.add_owner',
    'hasn.node.remove_owner',
    'hasn.node.renew_owner',
    'hasn.node.list_owners',
    'hasn.node.add_agent',
    'hasn.node.remove_agent',
    'hasn.agent.register',
    'hasn.agent.deregister',
    'hasn.message.send',
    'hasn.message.read',
    'hasn.typing',
    'hasn.ping',
})

# 入站帧无法解析为合法 JSON 对象时回送的 ws 错误码（与 ws_node 现行为一致）。
FRAME_DECODE_ERROR_CODE = 2004


class FrameDecodeError(Exception):
    """入站原始文本帧无法解析为 JSON 对象（JSON 非法，或顶层不是 dict）。

    携带 ws 错误码（默认 2004），供 ws_node 回送 `hasn.error` 帧后 continue（不断连）。
    收编前顶层非 dict（如数组/标量）会在 ws_node 里裸 AttributeError 冒泡、炸整条连接——
    收编后统一为明确的协议错误响应（合法客户端恒发 dict，不受影响）。
    """

    def __init__(self, message: str = 'JSON 格式错误', code: int = FRAME_DECODE_ERROR_CODE) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def build_frame(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """构造标准 HASN 事件帧 {hasn, method, params}（params 省略/None → 空 dict）。"""
    return {'hasn': HASN_PROTOCOL, 'method': method, 'params': params if params is not None else {}}


def build_response(
    req_id: str,
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造标准 HASN 响应帧 {hasn, id, result|error}。error 非空 → error 分支，否则 result 分支。"""
    resp: dict[str, Any] = {'hasn': HASN_PROTOCOL, 'id': req_id}
    if error:
        resp['error'] = error
    else:
        resp['result'] = result or {}
    return resp


def build_error_frame(code: int, message: str) -> dict[str, Any]:
    """构造标准 HASN 错误事件帧 hasn.error（与 ws_node._send_error 同结构）。"""
    return build_frame('hasn.error', {'code': code, 'message': message})


def is_known_method(method: str) -> bool:
    """method 是否属于当前协议已登记的方法全集（typed registry 分派前的白名单判定）。"""
    return method in KNOWN_METHODS


def parse_inbound(raw: str) -> tuple[str, Any, str | None]:
    """解析入站原始文本帧 → (method, params, req_id)。

    仅做**结构解析**：JSON 非法 / 顶层非 dict → 抛 FrameDecodeError（ws_node 回送 2004 后
    continue，不断连）。**不判 method 是否已知**——保留 ws_node 现行为：未知 method 交由
    分派层的 else 分支返回 9001，而非在此静默丢弃或抛错。字段缺省与 ws_node 逐字节一致：
    method 缺→''、params 缺→{}、id 缺→None（params 原样透传，类型校验交给 handler）。
    """
    try:
        msg = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise FrameDecodeError from exc
    if not isinstance(msg, dict):
        raise FrameDecodeError
    return msg.get('method', ''), msg.get('params', {}), msg.get('id')
