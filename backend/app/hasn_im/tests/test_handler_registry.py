"""hasn_im.tests.test_handler_registry · R1-09 typed handler registry 纯模块单测

钉死 registry 与 ws_node 现有 if/elif 分派的一致性契约（无 DB、无 WS）：
- registry 键集 == 协议帧 KNOWN_METHODS（双向，任一漂移即红）；
- 每方法参数形状与 ws_node 里 ``_handle_*`` 签名逐一相符；
- 未知方法 ``resolve_handler_spec`` 返回 None（分派层据此显式回 9001，非静默丢弃）；
- ``build_handler_args`` 按 spec 顺序从上下文取值、纯函数无副作用。
"""

from __future__ import annotations

from backend.app.hasn_im.protocol.frame import KNOWN_METHODS
from backend.app.hasn_im.protocol.handler_registry import (
    HANDLER_SPECS,
    HandlerArg,
    build_handler_args,
    resolve_handler_spec,
)

_WS = HandlerArg.WEBSOCKET
_NODE = HandlerArg.NODE_ID
_CONN = HandlerArg.CONNECTION_ID
_PARAMS = HandlerArg.PARAMS
_ENTITIES = HandlerArg.ACTIVE_ENTITIES
_REQ = HandlerArg.REQ_ID


def test_registry_covers_known_methods_exactly() -> None:
    """registry 键集必须与 KNOWN_METHODS 完全一致（漏接 / 多接都算漂移）。"""
    assert set(HANDLER_SPECS) == set(KNOWN_METHODS)


def test_each_spec_method_matches_its_key() -> None:
    """spec.method 与其 dict 键一致（防复制粘贴写错 method 名）。"""
    for method, spec in HANDLER_SPECS.items():
        assert spec.method == method


def test_arg_shapes_match_ws_node_handler_signatures() -> None:
    """逐方法钉死参数形状 = ws_node 现有 _handle_* 签名（迁移忠实性守卫）。"""
    expected: dict[str, tuple[HandlerArg, ...]] = {
        'hasn.node.add_owner': (_WS, _NODE, _PARAMS, _ENTITIES),
        'hasn.node.remove_owner': (_WS, _NODE, _PARAMS, _ENTITIES),
        'hasn.node.renew_owner': (_WS, _NODE, _PARAMS, _ENTITIES),
        'hasn.node.list_owners': (_WS, _NODE),
        'hasn.node.add_agent': (_WS, _NODE, _PARAMS, _ENTITIES),
        'hasn.node.remove_agent': (_WS, _NODE, _PARAMS, _ENTITIES),
        'hasn.agent.register': (_WS, _NODE, _PARAMS, _ENTITIES, _REQ),
        'hasn.agent.deregister': (_WS, _NODE, _PARAMS, _ENTITIES, _REQ),
        'hasn.message.send': (_WS, _NODE, _PARAMS, _ENTITIES),
        'hasn.message.read': (_PARAMS, _ENTITIES),
        'hasn.typing': (_PARAMS, _ENTITIES),
        'hasn.agent.progress': (_PARAMS, _ENTITIES),
        'hasn.ping': (_WS, _NODE, _CONN, _PARAMS),
    }
    assert set(expected) == set(HANDLER_SPECS)
    for method, args in expected.items():
        assert HANDLER_SPECS[method].args == args, method


def test_only_ping_may_close_loop() -> None:
    """仅 hasn.ping 声明 may_close_loop（superseded 连接关闭）；其余方法一律 False。"""
    for method, spec in HANDLER_SPECS.items():
        assert spec.may_close_loop == (method == 'hasn.ping'), method


def test_unknown_method_resolves_to_none() -> None:
    """未登记方法 → resolve 返回 None（分派层据此显式回 9001）。"""
    assert resolve_handler_spec('hasn.does.not.exist') is None
    assert resolve_handler_spec('') is None


def test_known_method_resolves() -> None:
    spec = resolve_handler_spec('hasn.message.send')
    assert spec is not None
    assert spec.method == 'hasn.message.send'


def test_build_handler_args_orders_by_spec() -> None:
    """build_handler_args 按 spec.args 顺序从上下文取值（纯函数，仅取声明的位置参数）。"""
    ctx = dict(
        websocket='WS',
        node_id='n_1',
        connection_id='c_1',
        params={'k': 'v'},
        active_entities={'h_1'},
        req_id='r_1',
    )
    # 五参签名（register）：全序取齐。
    reg = build_handler_args(HANDLER_SPECS['hasn.agent.register'], **ctx)
    assert reg == ('WS', 'n_1', {'k': 'v'}, {'h_1'}, 'r_1')
    # 两参签名（read）：只取 params + active_entities，不带 websocket/node/req。
    read = build_handler_args(HANDLER_SPECS['hasn.message.read'], **ctx)
    assert read == ({'k': 'v'}, {'h_1'})
    # ping 四参：websocket/node/connection/params（含 connection_id）。
    ping = build_handler_args(HANDLER_SPECS['hasn.ping'], **ctx)
    assert ping == ('WS', 'n_1', 'c_1', {'k': 'v'})


def test_build_handler_args_covers_every_registered_method() -> None:
    """对每个已登记方法都能无异常组装实参，且实参个数 == spec.args 长度。"""
    ctx = dict(
        websocket=object(),
        node_id='n',
        connection_id='c',
        params={},
        active_entities=set(),
        req_id=None,
    )
    for method, spec in HANDLER_SPECS.items():
        args = build_handler_args(spec, **ctx)
        assert len(args) == len(spec.args), method
