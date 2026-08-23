"""R3 跨域会话角色契约守卫（通用会话不得直接读写 sync/IM 域表）。

**规则**：`backend/database/db.py` 里 `async_db_session = python_backend_db_session`——
也就是说 `CurrentSession` / `CurrentSessionTransaction` 这些**通用依赖跑的是 `astra_python_backend`
受限角色**。按 R3 最小权限设计（见 `backend/app/hasn_im/tests/test_r3_role_sql_contract.py::
test_python_role_has_no_direct_im_or_sync_table_access`），该角色**故意没有** `hasn_sync` /
`hasn_im` 的表权限，跨域写只能经 `hasn_sync.append_event`（SECURITY DEFINER）。

**这条规则失效过三次，全部在 2026-08-23 于生产日志实锤**：

| 报错 | 代码位置 | 说明 |
|---|---|---|
| `permission denied for table hasn_sync_inbox_events` | `agent_task_service._emit_task_event` | `hasn.task.create` 全挂 |
| `permission denied for table hasn_sync_events` | `hasn_sync_service.save_task_run_summary` | 分身上报运行摘要全挂 |
| `permission denied for schema hasn_im` | `community_service.get_recommended_agents` | 见下方「未覆盖」 |

**为什么本地和测试都抓不到**：开发机 `.env` 三个角色 DSN 全空 → `_resolve_role_engine` 回落主
engine → 所有会话都是超级用户，跨域访问永远「能跑」。**生产才是唯一会拒绝的地方**，所以这类缺陷
一路绿灯直达线上。

⚠️ **本守卫是静态的，覆盖面有限，不是「跨域访问已被全面拦住」的证明**：
- 它只钉住**下方明确列出的**两个已修点，防止被改回去；
- 它**不做**调用图可达性分析，因此**发现不了新写的**跨域直读直写；
- 真正的权限矩阵验证只能连**真实生产角色**做（本仓 `scripts/verify_r3_role_privileges.sh`，
  会在事务内跑一遍并 ROLLBACK）。本地无法复现，别把本守卫的绿当成权限没问题。
"""

from __future__ import annotations

import ast

from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent

_AGENT_TASK_SERVICE = _BACKEND / 'app' / 'hasn_task' / 'service' / 'agent_task_service.py'
_SYNC_SERVICE = _BACKEND / 'app' / 'hasn' / 'service' / 'hasn_sync_service.py'


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding='utf-8'))


def _find_calls(tree: ast.AST, func_name: str) -> list[ast.Call]:
    """取出所有形如 `....<func_name>(...)` 的调用节点。"""
    found: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        name = target.attr if isinstance(target, ast.Attribute) else getattr(target, 'id', None)
        if name == func_name:
            found.append(node)
    return found


def _kwarg(call: ast.Call, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _function_sources(path: Path, func_name: str) -> list[str]:
    """取同名函数的**全部**定义源码片段。

    ⚠️ 本文件里 `save_task_run_summary` 有两处定义：Protocol 声明（只有 `...` 占位）与真实实现。
    早期版本只取 `ast.walk` 撞见的第一个 → 拿到的是**空壳 Protocol**，于是「必须包含 producer=」
    这条断言恒不成立（假红），而「不得包含直读 SELECT」恒成立（假绿）。所以这里返回全部定义，
    由调用方按语义分别施加断言。
    """
    text = path.read_text(encoding='utf-8')
    tree = ast.parse(text)
    out = [
        ast.get_source_segment(text, node) or ''
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and node.name == func_name
    ]
    if not out:
        raise AssertionError(f'{path.name} 里找不到函数 {func_name}——守卫判据已失效')
    return out


def _implementation_source(path: Path, func_name: str) -> str:
    """同名定义里取最长的那个 = 真实实现（Protocol 声明只有 `...`，必然最短）。"""
    return max(_function_sources(path, func_name), key=len)


def test_scanner_finds_the_call_sites() -> None:
    """守卫自身有效性：扫描器确实定位到了被判定的调用点，避免零向量假绿。"""
    calls = _find_calls(_parse(_AGENT_TASK_SERVICE), 'save_task_event')
    assert len(calls) > 0, 'agent_task_service 里一个 save_task_event 调用都没扫到，判据已失效'


def test_agent_api_does_not_manage_sync_inbox() -> None:
    """Agent API 是云端直发，不得去管 hasn-node 的同步收件账本（那需要 sync 角色）。"""
    for call in _find_calls(_parse(_AGENT_TASK_SERVICE), 'save_task_event'):
        value = _kwarg(call, 'manage_inbox')
        assert value is not None, (
            'agent_task_service 调用 save_task_event 必须显式传 manage_inbox=False：'
            '默认值 True 会去读写 hasn_sync.hasn_sync_inbox_events，而本路径跑在 '
            'astra_python_backend 角色上，按 R3 最小权限必然 permission denied。'
        )
        assert isinstance(value, ast.Constant) and value.value is False, (
            f'manage_inbox 必须是字面量 False，当前是 {ast.dump(value)}'
        )


def test_run_summary_impl_is_not_the_protocol_stub() -> None:
    """守卫自身有效性：确认取到的是真实实现而非 Protocol 空壳，否则下条断言恒不成立。"""
    impl = _implementation_source(_SYNC_SERVICE, 'save_task_run_summary')
    assert len(_function_sources(_SYNC_SERVICE, 'save_task_run_summary')) >= 2, (
        '预期 save_task_run_summary 有 Protocol 声明 + 真实实现两处定义；结构变了请同步本守卫'
    )
    assert 'INSERT INTO' in impl, '取到的不是真实实现（没有 INSERT），守卫判据已失效'


def test_run_summary_does_not_probe_sync_events_directly() -> None:
    """run summary 的去重不得再直接 SELECT hasn_sync 表，须交给 append_event 的幂等键。"""
    source = _implementation_source(_SYNC_SERVICE, 'save_task_run_summary')
    assert 'FROM {_SYNC_EVENTS}' not in source, (
        'save_task_run_summary 又出现了对 hasn_sync_events 的直接 SELECT：'
        'astra_python_backend 没有该表权限（生产实测 permission denied）。'
        '去重请传 producer + source_event_id，交给 hasn_sync.append_event 的唯一键。'
    )
    assert 'producer=' in source and 'source_event_id=' in source, (
        'save_task_run_summary 必须给 append_event 传 producer + source_event_id 作幂等键，'
        '否则重复上报会追加重复的下行事件。'
    )
