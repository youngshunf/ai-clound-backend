"""工作流历史同步契约测试。"""

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
import sqlalchemy as sa

from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from backend.app.hasn_task.model import HasnWorkflow, HasnWorkflowRun
from backend.app.hasn_task.schema.workflow_sync import WorkflowRunUpstream
from backend.app.hasn_task.service.workflow_sync_service import build_workflow_run_upsert


def test_sync_payload_keeps_cross_node_history_snapshots() -> None:
    """同步协议必须保留不依赖父定义的历史展示快照。"""
    project_id = '5e62555d-6b91-4e03-b630-e5c6d2b9a8c1'

    run = WorkflowRunUpstream.model_validate({
        'workflow_run_uuid': 'wfr_history_snapshot',
        'workflow_uuid': 'wf_history_snapshot',
        'workflow_name_snapshot': '新品发布场景',
        'template_key_snapshot': 'product_launch',
        'project_id': project_id,
    })

    assert run.workflow_name_snapshot == '新品发布场景'
    assert run.template_key_snapshot == 'product_launch'
    assert run.project_id == UUID(project_id)


def test_sync_payload_rejects_invalid_project_id() -> None:
    """非法项目标识必须在协议边界以 422 语义拒绝。"""
    with pytest.raises(ValidationError):
        WorkflowRunUpstream.model_validate({
            'workflow_run_uuid': 'wfr_invalid_project',
            'workflow_uuid': 'wf_invalid_project',
            'project_id': '不是 UUID',
        })


def test_cloud_models_declare_history_and_idempotency_columns() -> None:
    """ORM 模型必须与 R1 的 PostgreSQL 账本列逐一对应。"""
    run_columns = HasnWorkflowRun.__table__.c
    workflow_columns = HasnWorkflow.__table__.c

    assert cast('sa.String', run_columns.workflow_name_snapshot.type).length == 255
    assert cast('sa.String', run_columns.template_key_snapshot.type).length == 128
    assert run_columns.project_id.type.python_type is UUID
    assert cast('sa.String', workflow_columns.instantiation_idempotency_key.type).length == 128


def test_sync_replay_keeps_existing_history_snapshots() -> None:
    """同步重放只能补执行态，不能改写或清空已落库历史。"""
    statement = build_workflow_run_upsert(
        run=WorkflowRunUpstream.model_validate({'workflow_run_uuid': 'wfr_legacy', 'workflow_uuid': 'wf_legacy'}),
        owner_id='owner_legacy',
        now=datetime.now(UTC),
    )
    sql = str(statement.compile(dialect=postgresql.dialect()))

    for field in ('workflow_name_snapshot', 'template_key_snapshot', 'project_id'):
        assert f'{field} = coalesce(hasn_task.workflow_run.{field}, excluded.{field})' in sql
