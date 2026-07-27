"""旧 daemon 工作流定义的 create-only 导入服务（doc98 R2-c）。

新版场景实例化必须走模板 Owner 接口；此服务只收发布前存量和旧 daemon 产生的完整图快照。
同 UUID 已存在时只接受同一规范化定义，任何差异都显式为 ``DEFINITION_CONFLICT``，绝不按时间戳覆盖。
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from backend.app.hasn_task.crud.crud_workflow import hasn_workflow_dao
from backend.app.hasn_task.schema.workflow import CreateWorkflowParam
from backend.app.hasn_task.service.workflow_service import workflow_service
from backend.common.exception import errors

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _node_snapshot(node: dict[str, Any]) -> dict[str, Any]:
    """提取实际持久化的节点定义字段，消除 JSON key 顺序和可空字段造成的伪冲突。"""
    return {
        'node_key': str(node.get('node_key') or ''),
        'agent_id': str(node.get('agent_id') or ''),
        'name': str(node.get('name') or node.get('node_key') or ''),
        'prompt': str(node.get('prompt') or ''),
        'system_prompt': node.get('system_prompt'),
        'description': node.get('description'),
        'enable_subagents': bool(node.get('enable_subagents', False)),
        'output_spec': node.get('output_spec'),
        'review_policy': node.get('review_policy'),
        'apps': node.get('apps') if isinstance(node.get('apps'), list) else [],
        'skills': node.get('skills') if isinstance(node.get('skills'), list) else [],
        'is_origin': bool(node.get('is_origin', False)),
    }


def canonical_definition(workflow: CreateWorkflowParam | dict[str, Any]) -> dict[str, Any]:
    """把传入或已落库定义压成用于幂等比较的稳定 JSON 对象。"""
    value = workflow.model_dump(mode='json') if isinstance(workflow, CreateWorkflowParam) else workflow
    nodes = value.get('nodes') if isinstance(value.get('nodes'), list) else []
    edges = value.get('edges') if isinstance(value.get('edges'), list) else []
    return {
        'workflow_uuid': str(value.get('workflow_uuid') or ''),
        'name': str(value.get('name') or ''),
        'goal': value.get('goal'),
        'schedule_type': value.get('schedule_type') or 'once',
        'schedule_config': value.get('schedule_config') if isinstance(value.get('schedule_config'), dict) else {},
        'schedule_display': value.get('schedule_display'),
        'timezone': value.get('timezone') or 'Asia/Shanghai',
        'misfire_policy': value.get('misfire_policy') or 'run_once',
        'catchup_limit': value.get('catchup_limit'),
        'continuation_enabled': bool(value.get('continuation_enabled', False)),
        'source': value.get('source') or 'owner',
        'created_by_kind': value.get('created_by_kind') or 'owner',
        'template_key': value.get('template_key'),
        'project_id': str(value.get('project_id')) if value.get('project_id') else None,
        'status': value.get('status') or 'active',
        'nodes': sorted((_node_snapshot(node) for node in nodes if isinstance(node, dict)), key=lambda node: node['node_key']),
        'edges': sorted(
            (
                {'parent': str(edge.get('parent') or ''), 'child': str(edge.get('child') or '')}
                for edge in edges
                if isinstance(edge, dict)
            ),
            key=lambda edge: (edge['parent'], edge['child']),
        ),
    }


def definition_hash(workflow: CreateWorkflowParam | dict[str, Any]) -> str:
    """返回规范化定义 SHA-256，供冲突诊断而非安全鉴权。"""
    encoded = json.dumps(canonical_definition(workflow), ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(encoded.encode()).hexdigest()


class WorkflowDefinitionImportService:
    """只允许 create 或相同哈希重放的旧定义导入。"""

    async def import_one(self, db: AsyncSession, *, owner_id: str, workflow: CreateWorkflowParam) -> str:
        """导入一张完整图，返回 ``created`` 或 ``idempotent``，冲突/项目闸失败显式抛错。"""
        workflow.owner_id = owner_id
        workflow_uuid = (workflow.workflow_uuid or '').strip()
        if not workflow_uuid:
            raise errors.RequestError(msg='旧定义导入必须携带稳定 workflow_uuid')

        # 稳定 UUID 是导入的唯一串行键：并发首导入必须先在同一事务锁内判定是否已有定义，
        # 否则两个请求都看见空值，第二个会以数据库唯一键异常结束而不是得到幂等结果。
        await db.execute(
            sa.text('SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))'),
            {'lock_key': f'workflow-definition-import:{owner_id}:{workflow_uuid}'},
        )

        if workflow.template_key and workflow.project_id is None:
            raise errors.RequestError(msg='PROJECT_REQUIRED：场景定义导入必须携带项目 UUID')
        if workflow.project_id is not None:
            from backend.app.hasn_project.service.project_app_service import project_service

            await project_service.resolve_open_for_new_workflow(
                db, owner=owner_id, project_id=str(workflow.project_id)
            )

        existing = await hasn_workflow_dao.get_by_uuid(db, workflow_uuid)
        if existing is None:
            await workflow_service.create_workflow(db, owner_id=owner_id, obj=workflow)
            return 'created'
        if existing.owner_id != owner_id:
            raise errors.NotFoundError(msg='工作流不存在')

        existing_detail = await workflow_service.get_workflow(db, owner_id=owner_id, workflow_uuid=workflow_uuid)
        existing_payload = {
            'workflow_uuid': existing.workflow_uuid,
            'name': existing.name,
            'goal': existing.goal,
            'schedule_type': existing.schedule_type,
            'schedule_config': existing.schedule_config,
            'schedule_display': existing.schedule_display,
            'timezone': existing.timezone,
            'misfire_policy': existing.misfire_policy,
            'catchup_limit': existing.catchup_limit,
            'continuation_enabled': existing.continuation_enabled,
            'source': existing.source,
            'created_by_kind': existing.created_by_kind,
            'template_key': existing.template_key,
            'project_id': str(existing.project_id) if existing.project_id else None,
            'status': existing.status,
            'nodes': existing_detail['nodes'],
            'edges': existing_detail['edges'],
        }
        expected_hash = definition_hash(workflow)
        actual_hash = definition_hash(existing_payload)
        if expected_hash != actual_hash:
            raise errors.ConflictError(
                msg='DEFINITION_CONFLICT：云端已存在同 UUID 的不同工作流定义',
                data={
                    'code': 'DEFINITION_CONFLICT',
                    'workflow_uuid': workflow_uuid,
                    'expected_definition_hash': expected_hash,
                    'actual_definition_hash': actual_hash,
                },
            )
        return 'idempotent'


workflow_definition_import_service = WorkflowDefinitionImportService()
