"""sync API 的身份权威与 session maker 静态接线守卫。"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import cast

import sqlalchemy as sa


_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_SYNC_API = _BACKEND_ROOT / 'app/hasn/api/v1/sync.py'
_TASK_SYNC_API = _BACKEND_ROOT / 'app/hasn_task/api/v1/app/sync.py'
_RECEIPT_MIGRATION = (
    _BACKEND_ROOT
    / 'sql/hasn/migrations/2026-07-27-r3-sync-business-receipts.sql'
)


def _annotation(path: Path, function_name: str, argument_name: str) -> str:
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
        and node.name == function_name
    )
    argument = next(
        item for item in function.args.args if item.arg == argument_name
    )
    assert argument.annotation is not None, f'{function_name}.{argument_name} 必须声明类型'
    return ast.unparse(argument.annotation)


def test_general_sync_routes_use_only_sync_sessions() -> None:
    """通用 pull/push 必须分别使用 sync 只读/事务 session。"""
    assert _annotation(_SYNC_API, 'pull_sync_events', 'db') == 'CurrentSyncSession'
    assert (
        _annotation(_SYNC_API, 'push_sync_events', 'db')
        == 'CurrentSyncSessionTransaction'
    )
    assert (
        _annotation(_SYNC_API, 'pull_memory_sync_events', 'db')
        == 'CurrentSyncSession'
    )


def test_conversation_projection_reads_use_im_session() -> None:
    """会话对象已迁入 hasn_im，read-through 不得借 Python 角色读取。"""
    assert _annotation(_SYNC_API, 'get_conversation_object', 'db') == 'CurrentImSession'
    assert (
        _annotation(_SYNC_API, 'batch_get_conversation_objects', 'db')
        == 'CurrentImSession'
    )


def test_message_history_bootstrap_uses_explicit_sync_and_im_sessions() -> None:
    """快照边界读 sync 流头，历史分页只读 IM 事实，禁止借 Python 通用角色。"""
    assert (
        _annotation(_SYNC_API, 'start_message_history_bootstrap', 'sync_db')
        == 'CurrentSyncSession'
    )
    assert (
        _annotation(_SYNC_API, 'start_message_history_bootstrap', 'im_db')
        == 'CurrentImSession'
    )
    assert (
        _annotation(_SYNC_API, 'page_message_history_conversations', 'db')
        == 'CurrentImSession'
    )
    assert (
        _annotation(_SYNC_API, 'page_message_history_messages', 'db')
        == 'CurrentImSession'
    )


def test_task_sync_routes_use_only_sync_sessions() -> None:
    """任务同步入口只能连接 sync 自有域，禁止借 Python 角色跨域读表。"""
    assert (
        _annotation(_TASK_SYNC_API, 'push_task_sync_events', 'db')
        == 'CurrentSyncSessionTransaction'
    )
    assert (
        _annotation(_TASK_SYNC_API, 'pull_task_sync_events', 'db')
        == 'CurrentSyncSession'
    )


def test_general_routes_no_longer_call_legacy_mixed_pull_or_push() -> None:
    """入口不得再把同一 Python session 交给旧混合 sync 服务。"""
    source = _SYNC_API.read_text(encoding='utf-8')
    assert 'hasn_sync_service.pull(db' not in source
    assert 'hasn_sync_service.push(db' not in source


def test_task_pull_does_not_read_business_assignment_table() -> None:
    """节点可见性必须固化在事件载荷，sync pull 不得读取任务业务表。"""
    service = _BACKEND_ROOT / 'app/hasn/service/hasn_sync_service.py'
    content = service.read_text(encoding='utf-8')
    tree = ast.parse(content, filename=str(service))
    gateway_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == 'SqlAlchemySyncGateway'
    )
    gateway = next(
        node
        for node in gateway_class.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == 'pull_task_events'
    )
    source = ast.get_source_segment(content, gateway)
    assert source is not None
    assert 'hasn_task.assignment' not in source


def test_task_write_freezes_node_visibility_in_sync_event() -> None:
    """任务写点必须把节点可见性写入事件，供受限 sync 角色独立拉取。"""
    service = _BACKEND_ROOT / 'app/hasn/service/hasn_sync_service.py'
    content = service.read_text(encoding='utf-8')
    tree = ast.parse(content, filename=str(service))
    gateway_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == 'SqlAlchemySyncGateway'
    )
    writer = next(
        node
        for node in gateway_class.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == '_upsert_task_and_append_event'
    )
    source = ast.get_source_segment(content, writer)
    assert source is not None
    assert "'visible_node_ids': [event_executor_node_id]" in source


def test_internal_receipt_has_no_generated_http_crud_surface() -> None:
    """内部幂等回执不可被 admin/app/agent/open CRUD 修改。"""
    api_root = _BACKEND_ROOT / 'app/hasn/api'
    offenders = [
        path
        for path in api_root.rglob('*.py')
        if 'hasn_sync_business_receipts' in path.read_text(encoding='utf-8')
    ]
    assert offenders == []


def test_receipt_idempotency_constraints_are_self_contained() -> None:
    """metadata 基线与 IF NOT EXISTS 迁移都必须保留两个 receipt 幂等约束。"""
    from backend.app.hasn.model.hasn_sync_business_receipts import (
        HasnSyncBusinessReceipts,
    )

    table = cast(sa.Table, HasnSyncBusinessReceipts.__table__)
    constraint_names = {
        constraint.name
        for constraint in table.constraints
    }
    assert 'uq_hasn_sync_business_receipt_key' in constraint_names
    assert 'uq_hasn_sync_business_receipt_event' in constraint_names

    migration = _RECEIPT_MIGRATION.read_text(encoding='utf-8')
    assert 'ADD CONSTRAINT uq_hasn_sync_business_receipt_key' in migration
    assert 'ADD CONSTRAINT uq_hasn_sync_business_receipt_event' in migration
