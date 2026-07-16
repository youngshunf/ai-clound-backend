"""R1-09a 验收：hasn_im.protocol.frame 帧编解码/校验纯模块单测（无 DB，随普通 pytest 恒跑）。

钉死协议层纯模块与 ws_node 现行为逐字节一致：build_frame/build_response/build_error_frame
产出结构、parse_inbound 的 JSON 错误→FrameDecodeError(2004) 与缺字段兜底、KNOWN_METHODS
覆盖 ws_node._recv_loop 现有 12 method 分派全集（新增分派须同步登记，否则本组红）。
"""

from __future__ import annotations

import pytest

from backend.app.hasn_im.protocol import frame


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


def test_known_methods_covers_ws_node_dispatch_set() -> None:
    # 权威锚点：与 ws_node._recv_loop if/elif 分派集逐一对齐（新增分派须同步登记，否则本测试红）
    expected = {
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
    }
    assert set(frame.KNOWN_METHODS) == expected
