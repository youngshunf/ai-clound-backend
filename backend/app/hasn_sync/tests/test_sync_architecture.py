"""hasn_sync 单向依赖与完整载荷静态守卫。"""

from __future__ import annotations

import ast
from pathlib import Path


_SYNC_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_ROOT = _SYNC_ROOT.parents[1]


def test_sync_kernel_does_not_import_business_modules() -> None:
    """同步内核不得导入 IM、任务、记忆或旧 hasn 业务模块。"""
    offenders: list[str] = []
    for path in sorted(_SYNC_ROOT.rglob('*.py')):
        if 'tests' in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        for node in ast.walk(tree):
            module = ''
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or '']
            else:
                continue
            for module in names:
                if module.startswith('backend.app.') and not module.startswith(
                    'backend.app.hasn_sync'
                ):
                    offenders.append(
                        f'{path.relative_to(_SYNC_ROOT)}:{node.lineno}:{module}'
                    )
    assert not offenders, 'hasn_sync 发现业务反向依赖：\n' + '\n'.join(offenders)


def test_sync_pull_source_contains_no_business_table_names() -> None:
    """pull 只能返回事件信封，不得查询业务表补 payload。"""
    pull_path = _SYNC_ROOT / 'application/pull.py'
    content = pull_path.read_text(encoding='utf-8')
    forbidden = (
        'hasn_messages',
        'hasn_task.',
        'hasn_memory.',
        'hasn_agents',
        'hasn_sessions',
    )
    found = [name for name in forbidden if name in content]
    assert not found, f'pull 出现业务表补载荷引用：{found}'


def test_sync_append_has_one_python_adapter_and_no_direct_table_insert() -> None:
    """生产 Python 只允许适配器调用函数，且不得直接 INSERT sync event 表。"""
    function_callers: list[str] = []
    direct_inserts: list[str] = []
    for path in sorted(_BACKEND_ROOT.rglob('*.py')):
        if 'tests' in path.parts or '__pycache__' in path.parts:
            continue
        content = path.read_text(encoding='utf-8')
        relative = str(path.relative_to(_BACKEND_ROOT))
        if 'FROM hasn_sync.append_event(' in content:
            function_callers.append(relative)
        compact = ' '.join(content.lower().split())
        if (
            'insert into public.hasn_sync_events' in compact
            or 'insert into hasn_sync.hasn_sync_events' in compact
        ):
            direct_inserts.append(relative)
    assert function_callers == ['app/hasn_sync/adapters/sqlalchemy_appender.py']
    assert not direct_inserts, (
        '发现绕过 append_event 的 sync event INSERT：' + ', '.join(direct_inserts)
    )


def test_business_producers_do_not_call_legacy_private_append_methods() -> None:
    """业务 producer 必须依赖 SyncAppender，不得调用旧 gateway 私有方法。"""
    offenders: list[str] = []
    for path in sorted(_BACKEND_ROOT.rglob('*.py')):
        if 'tests' in path.parts or path.name == 'hasn_sync_service.py':
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr
                in {'_append_sync_event', '_append_sync_event_with_id'}
            ):
                offenders.append(
                    f'{path.relative_to(_BACKEND_ROOT)}:{node.lineno}'
                )
    assert not offenders, '业务 producer 仍调用旧私有 append：\n' + '\n'.join(
        offenders
    )


def test_sync_admin_routes_are_read_only() -> None:
    """管理端只能观测 sync 流水，不得提供绕过内核的通用写接口。"""
    admin_root = _BACKEND_ROOT / 'app/hasn/api/v1/admin'
    offenders: list[str] = []
    for name in ('hasn_sync_events.py', 'hasn_sync_inbox_events.py'):
        path = admin_root / name
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr in {'post', 'put', 'patch', 'delete'}
                ):
                    offenders.append(f'{name}:{node.lineno}:{decorator.func.attr}')
    assert not offenders, 'sync 管理路由出现通用写入口：\n' + '\n'.join(offenders)
