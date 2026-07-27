"""云端 IM R3 写入边界静态审计器。"""

from __future__ import annotations

import argparse
import ast
import json
import re

from dataclasses import asdict, dataclass
from pathlib import Path

MOVED_TABLES = frozenset(
    {
        'agent_communication_settings',
        'event_consumer_failures',
        'event_consumer_offsets',
        'hasn_asset_grants',
        'hasn_contact_requests',
        'hasn_contacts',
        'hasn_conversation_memberships',
        'hasn_conversations',
        'hasn_group_agent_invites',
        'hasn_messages',
        'hasn_suppressed_messages',
        'hasn_sync_events',
        'hasn_sync_inbox_events',
        'hasn_unread_projection',
        'integration_events',
    }
)
LEGACY_MESSAGE_EVENTS = frozenset(
    {'message.sent', 'message.received', 'message.agent_reply'}
)
LEGACY_WRITE_ROUTE_MARKERS = (
    '/contacts',
    '/group/members',
    '/unread/counts',
)
_SQL_TABLE_REFERENCE_RE = re.compile(
    r'\b(?:from|join|insert\s+into|update|delete\s+from)\s+'
    r'(?P<table>[A-Za-z_][A-Za-z0-9_]*|'
    r'"[A-Za-z_][A-Za-z0-9_]*")',
    re.IGNORECASE,
)
_MOVED_TABLE_DML_RE = re.compile(
    r'\b(?P<action>insert\s+into|update|delete\s+from)\s+'
    r'(?:(?P<schema>[A-Za-z_][A-Za-z0-9_]*|'
    r'"[A-Za-z_][A-Za-z0-9_]*")\s*\.\s*)?'
    r'(?P<table>[A-Za-z_][A-Za-z0-9_]*|'
    r'"[A-Za-z_][A-Za-z0-9_]*")',
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Finding:
    """一条可追溯的静态审计发现。"""

    path: Path
    line: int
    kind: str
    value: str


def _iter_python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob('*.py') if '__pycache__' not in path.parts)


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding='utf-8'), filename=str(path))


def _docstring_node_ids(tree: ast.AST) -> set[int]:
    """返回模块、类和函数 docstring 对应常量节点的对象标识。"""
    node_ids: set[int] = set()
    for parent in ast.walk(tree):
        if not isinstance(
            parent, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        if not parent.body or not isinstance(parent.body[0], ast.Expr):
            continue
        value = parent.body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            node_ids.add(id(value))
    return node_ids


def find_unqualified_moved_table_sql(root: Path) -> list[Finding]:
    """查找运行时代码里未显式指定 schema 的 moved table SQL。"""
    findings: list[Finding] = []
    for path in _iter_python_files(root):
        tree = _parse(path)
        docstring_ids = _docstring_node_ids(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if id(node) in docstring_ids:
                continue
            for match in _SQL_TABLE_REFERENCE_RE.finditer(node.value):
                table = match.group('table').strip('"')
                if table in MOVED_TABLES:
                    findings.append(
                        Finding(
                            path=path,
                            line=getattr(node, 'lineno', 0),
                            kind='unqualified_moved_table_sql',
                            value=table,
                        )
                    )
    return sorted(findings, key=lambda item: (str(item.path), item.line, item.value))


def find_moved_table_writes(root: Path) -> list[Finding]:
    """查找对 moved table 的 SQL DML，显式 schema 同样纳入审计。"""
    findings: list[Finding] = []
    for path in _iter_python_files(root):
        tree = _parse(path)
        docstring_ids = _docstring_node_ids(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if id(node) in docstring_ids:
                continue
            for match in _MOVED_TABLE_DML_RE.finditer(node.value):
                table = match.group('table').strip('"')
                if table not in MOVED_TABLES:
                    continue
                schema_match = match.group('schema')
                schema = schema_match.strip('"') if schema_match else None
                action = match.group('action').split()[0].upper()
                qualified = f'{schema}.{table}' if schema else table
                findings.append(
                    Finding(
                        path=path,
                        line=getattr(node, 'lineno', 0),
                        kind='moved_table_write',
                        value=f'{action} {qualified}',
                    )
                )
    return sorted(findings, key=lambda item: (str(item.path), item.line, item.value))


def find_legacy_event_producers(root: Path) -> list[Finding]:
    """查找旧消息事件的精确字面量生产点。"""
    findings: list[Finding] = []
    for path in _iter_python_files(root):
        tree = _parse(path)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value in LEGACY_MESSAGE_EVENTS
            ):
                findings.append(
                    Finding(
                        path=path,
                        line=getattr(node, 'lineno', 0),
                        kind='legacy_message_event',
                        value=node.value,
                    )
                )
    return sorted(findings, key=lambda item: (str(item.path), item.line, item.value))


def find_legacy_write_routes(root: Path) -> list[Finding]:
    """查找旧 contacts/group/unread 通用写路由。"""
    findings: list[Finding] = []
    for path in _iter_python_files(root):
        tree = _parse(path)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(
                    decorator.func, ast.Attribute
                ):
                    continue
                method = decorator.func.attr.lower()
                if method not in {'post', 'put', 'patch', 'delete'} or not decorator.args:
                    continue
                route_arg = decorator.args[0]
                if not isinstance(route_arg, ast.Constant) or not isinstance(
                    route_arg.value, str
                ):
                    continue
                route = route_arg.value
                if not any(marker in route for marker in LEGACY_WRITE_ROUTE_MARKERS):
                    continue
                findings.append(
                    Finding(
                        path=path,
                        line=getattr(decorator, 'lineno', 0),
                        kind='legacy_write_route',
                        value=f'{method.upper()} {route}',
                    )
                )
    return sorted(findings, key=lambda item: (str(item.path), item.line, item.value))


def audit_application(root: Path) -> list[Finding]:
    """汇总应用代码的三类 R3 静态发现。"""
    root = root.resolve()

    def is_runtime_source(item: Finding) -> bool:
        relative_parts = item.path.resolve().relative_to(root).parts
        return 'tests' not in relative_parts and 'migration' not in relative_parts

    def is_hasn_route(item: Finding) -> bool:
        relative_parts = item.path.resolve().relative_to(root).parts
        return (
            is_runtime_source(item)
            and len(relative_parts) >= 2
            and relative_parts[0] == 'hasn'
            and relative_parts[1] == 'api'
        )

    def is_authorized_domain_writer(item: Finding) -> bool:
        """只有 IM/sync 自有 application/adapter 可写对应权威表。"""
        relative_parts = item.path.resolve().relative_to(root).parts
        return (
            len(relative_parts) >= 2
            and relative_parts[0] in {'hasn_im', 'hasn_sync'}
            and relative_parts[1] in {'application', 'adapters'}
        )

    sql_findings = [
        item for item in find_unqualified_moved_table_sql(root) if is_runtime_source(item)
    ]
    event_findings = [
        item for item in find_legacy_event_producers(root) if is_runtime_source(item)
    ]
    route_findings = [
        item for item in find_legacy_write_routes(root) if is_hasn_route(item)
    ]
    write_findings = [
        item
        for item in find_moved_table_writes(root)
        if is_runtime_source(item) and not is_authorized_domain_writer(item)
    ]
    return sorted(
        [
            *sql_findings,
            *event_findings,
            *route_findings,
            *write_findings,
        ],
        key=lambda item: (str(item.path), item.line, item.kind, item.value),
    )


def main() -> int:
    """输出 JSON 清单，供 L0 证据和 CI 守卫复用。"""
    parser = argparse.ArgumentParser(description='审计云端 IM R3 运行时代码边界')
    parser.add_argument(
        '--root',
        type=Path,
        default=Path(__file__).resolve().parents[1] / 'app',
        help='要扫描的 Python 应用目录',
    )
    args = parser.parse_args()
    findings = audit_application(args.root.resolve())
    print(
        json.dumps(
            [
                {
                    **asdict(item),
                    'path': str(item.path),
                }
                for item in findings
            ],
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if findings else 0


if __name__ == '__main__':
    raise SystemExit(main())
