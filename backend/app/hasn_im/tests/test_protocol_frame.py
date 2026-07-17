"""R1-09a 验收：hasn_im.protocol.frame 帧编解码/校验纯模块单测（无 DB，随普通 pytest 恒跑）。

钉死协议层纯模块与 ws_node 现行为逐字节一致：build_frame/build_response/build_error_frame
产出结构、parse_inbound 的 JSON 错误→FrameDecodeError(2004) 与缺字段兜底、KNOWN_METHODS
覆盖 ws_node._recv_loop 现有 12 method 分派全集（新增分派须同步登记，否则本组红）。
"""

from __future__ import annotations

import ast

from pathlib import Path

import pytest

from backend.app.hasn_im.protocol import frame

# ws_node.py 绝对路径（本文件 = backend/app/hasn_im/tests/test_protocol_frame.py）
_WS_NODE_PY = Path(__file__).resolve().parents[2] / 'hasn' / 'api' / 'ws_node.py'


def _ws_node_dispatch_methods() -> set[str]:
    """AST 解析 ws_node，收集其 table-driven 分派绑定表 `_HANDLERS` 的字面量键集。

    R1-09 分派已由 if/elif 长链改为表驱动（`_HANDLERS: method → _handle_*`）；本守卫真读
    源码里 `_HANDLERS` 的键（非硬编码复制），使 frame.KNOWN_METHODS 成为分派的**强制**权威
    来源：ws_node 若新增/删除一条绑定而未同步 KNOWN_METHODS，本组即红。仅取模块级 `_HANDLERS`
    赋值（含类型注解形态 `_HANDLERS: dict[...] = {...}`），避开同名局部变量。
    """
    tree = ast.parse(_WS_NODE_PY.read_text(encoding='utf-8'), filename=str(_WS_NODE_PY))

    def _is_handlers_target(node: ast.AST) -> bool:
        # 同时认 `_HANDLERS = {...}`（Assign）与 `_HANDLERS: dict = {...}`（AnnAssign）
        if isinstance(node, ast.AnnAssign):
            return isinstance(node.target, ast.Name) and node.target.id == '_HANDLERS'
        if isinstance(node, ast.Assign):
            return any(isinstance(t, ast.Name) and t.id == '_HANDLERS' for t in node.targets)
        return False

    handlers_node = next(node for node in ast.walk(tree) if _is_handlers_target(node))
    assert isinstance(handlers_node.value, ast.Dict), '_HANDLERS 必须是字面量 dict'
    methods: set[str] = set()
    for key in handlers_node.value.keys:
        assert isinstance(key, ast.Constant) and isinstance(key.value, str), '_HANDLERS 键必须是字符串字面量'
        methods.add(key.value)
    return methods


def test_build_frame_shape() -> None:
    assert frame.build_frame('hasn.ping', {'ts': 1}) == {
        'hasn': 'hasn/0.2',
        'method': 'hasn.ping',
        'params': {'ts': 1},
    }


def test_build_frame_defaults_empty_params() -> None:
    assert frame.build_frame('hasn.typing') == {'hasn': 'hasn/0.2', 'method': 'hasn.typing', 'params': {}}
    assert frame.build_frame('hasn.typing', None)['params'] == {}


def test_build_response_result_branch() -> None:
    assert frame.build_response('r1', {'ok': True}) == {'hasn': 'hasn/0.2', 'id': 'r1', 'result': {'ok': True}}


def test_build_response_empty_result_when_none() -> None:
    assert frame.build_response('r2') == {'hasn': 'hasn/0.2', 'id': 'r2', 'result': {}}


def test_build_response_error_branch_takes_precedence() -> None:
    # error 非空 → error 分支（即便 result 也给了），与 ws_node._response 一致
    resp = frame.build_response('r3', {'ignored': 1}, {'code': 9001, 'message': 'x'})
    assert resp == {'hasn': 'hasn/0.2', 'id': 'r3', 'error': {'code': 9001, 'message': 'x'}}
    assert 'result' not in resp


def test_build_error_frame_matches_ws_node_shape() -> None:
    assert frame.build_error_frame(2004, 'JSON 格式错误') == {
        'hasn': 'hasn/0.2',
        'method': 'hasn.error',
        'params': {'code': 2004, 'message': 'JSON 格式错误'},
    }


def test_parse_inbound_ok() -> None:
    method, params, req_id = frame.parse_inbound('{"method": "hasn.message.send", "params": {"to": "a"}, "id": "7"}')
    assert method == 'hasn.message.send'
    assert params == {'to': 'a'}
    assert req_id == '7'


def test_parse_inbound_defaults_when_fields_missing() -> None:
    # 与 ws_node 现行为一致：method 缺→''、params 缺→{}、id 缺→None（整元组一次钉死）
    assert frame.parse_inbound('{}') == ('', {}, None)


def test_parse_inbound_invalid_json_raises_decode_error() -> None:
    with pytest.raises(frame.FrameDecodeError) as ei:
        frame.parse_inbound('{not json')
    assert ei.value.code == 2004
    assert ei.value.message == 'JSON 格式错误'


def test_parse_inbound_toplevel_non_object_raises() -> None:
    # 顶层是数组/标量 → 非法帧（收编前会裸 AttributeError 炸连接，收编后统一显式 2004）
    with pytest.raises(frame.FrameDecodeError):
        frame.parse_inbound('[1, 2, 3]')
    with pytest.raises(frame.FrameDecodeError):
        frame.parse_inbound('"just a string"')


def test_is_known_method() -> None:
    assert frame.is_known_method('hasn.message.send')
    assert not frame.is_known_method('hasn.unknown.method')
    assert not frame.is_known_method('')


def test_known_methods_matches_ws_node_dispatch_set() -> None:
    # 权威锚点（AST 强制）：KNOWN_METHODS 必须与 ws_node 表驱动分派绑定表 `_HANDLERS` 的键集**完全一致**。
    # 真读源码，任一端新增/删除 method 而未同步另一端即红——KNOWN_METHODS 是 typed registry 的权威来源。
    assert set(frame.KNOWN_METHODS) == _ws_node_dispatch_methods()
