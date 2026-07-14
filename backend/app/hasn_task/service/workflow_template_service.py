"""工作流模板 service（云端权威读 API，P3 模板层 doc11 §4）。

职责边界（P3-cloud）：只提供**模板本体的读 API**（列表 / 详情）+ 建模板（供内置 seed /
分身 draft / 测试）。实例化的真实物化（读模板 graph_spec → 本地建 workflow+node+run → sync
上云）归 **P3-daemon**（daemon 侧 doc94 §9-D）——云端**不建 run**：workflow_run 由 daemon
本地 fire 后经 sync 落库，云端只读回显（见 agent_workflow_service.run 只置 next_run_at，
driver 本地节点 fire，中心不 tick）。

可见性：内置模板（is_builtin 或 owner_id 空）对所有人可见 + 自己名下模板；跨户模板不可见。
graph_summary 从 graph_spec 派生（节点数/应用数/阶段面包屑/人设列表），前端无须再解析蓝图。
"""

from __future__ import annotations

import uuid

from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from backend.app.hasn_task.crud.crud_workflow import hasn_workflow_template_dao
from backend.app.hasn_task.model import HasnWorkflowTemplate
from backend.app.hasn_task.schema.workflow_template import (
    CreateWorkflowTemplateParam,
    WorkflowTemplateGraphSummary,
    WorkflowTemplatePublic,
)
from backend.common.exception import errors

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_DOMAIN_DICT_CODE = 'workflow_template_domain'


# ============================ 纯函数：graph_spec 派生（可单测） ============================


def derive_graph_summary(graph_spec: dict | None) -> WorkflowTemplateGraphSummary:
    """从图蓝图派生卡片摘要：节点数 / 去重应用数 / 阶段面包屑（按 order）/ 去重人设类型。"""
    spec = graph_spec if isinstance(graph_spec, dict) else {}
    nodes = spec.get('nodes') if isinstance(spec.get('nodes'), list) else []

    app_union: set[str] = set()
    agent_types: list[str] = []
    seen_agent: set[str] = set()
    steps: list[dict[str, Any]] = []

    for idx, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        apps = node.get('apps')
        if isinstance(apps, list):
            app_union.update(a for a in apps if a)

        agent_type = node.get('default_agent_type')
        if agent_type and agent_type not in seen_agent:
            seen_agent.add(agent_type)
            agent_types.append(agent_type)

        display = node.get('display') if isinstance(node.get('display'), dict) else {}
        order = display.get('order')
        label = display.get('step_label') or node.get('name') or node.get('node_key') or ''
        steps.append({'label': label, 'order': order if isinstance(order, int) else idx})

    steps.sort(key=lambda s: s['order'])
    return WorkflowTemplateGraphSummary(
        node_count=len(nodes),
        app_count=len(app_union),
        steps=steps,
        agent_types=agent_types,
    )


def _to_public(tpl: HasnWorkflowTemplate, *, include_graph_spec: bool) -> dict[str, Any]:
    """模板行 → 读 API 投影（列表不带 graph_spec，详情带全量蓝图）。"""
    public = WorkflowTemplatePublic(
        template_key=tpl.template_key,
        template_uuid=tpl.template_uuid,
        domain=tpl.domain,
        name=tpl.name,
        tagline=tpl.tagline,
        description=tpl.description,
        sort_order=tpl.sort_order,
        icon=tpl.icon,
        accent=tpl.accent,
        status=tpl.status,
        source=tpl.source,
        is_builtin=tpl.is_builtin,
        builtin_key=tpl.builtin_key,
        owner_id=tpl.owner_id,
        version=tpl.version,
        market_ref=tpl.market_ref,
        sku_ref=tpl.sku_ref,
        graph_summary=derive_graph_summary(tpl.graph_spec),
        graph_spec=(tpl.graph_spec if include_graph_spec else None),
    )
    return public.model_dump(mode='json')


class WorkflowTemplateService:
    """工作流模板云端权威读 + 建（owner/agent 通道共用；跨户不可见）。"""

    # ---------- 读 ----------

    @staticmethod
    async def _domain_meta(db: AsyncSession) -> list[dict[str, Any]]:
        """领域分组显示元数据（组名/图标/色），来自系统字典 workflow_template_domain。"""
        rows = await db.execute(
            sa.text(
                'SELECT value, label, color, sort, remark FROM sys_dict_data '
                'WHERE type_code = :code AND status = 1 ORDER BY sort'
            ),
            {'code': _DOMAIN_DICT_CODE},
        )
        return [
            {
                'domain': r['value'],
                'label': r['label'],
                'color': r['color'],
                'sort': r['sort'],
                'icon': r['remark'] or None,  # icon 存 remark（sys_dict_data 无 icon 列）
            }
            for r in rows.mappings().all()
        ]

    @classmethod
    async def list_templates(
        cls,
        db: AsyncSession,
        *,
        owner_id: str,
        domain_only: bool = False,
        domain: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """列可见模板（内置 + 自己名下）+ 领域分组元数据。列表不含 graph_spec（详情才带）。"""
        rows = await hasn_workflow_template_dao.list_visible(
            db, owner_id=owner_id, domain_only=domain_only, domain=domain, status=status
        )
        templates = [_to_public(t, include_graph_spec=False) for t in rows]
        domains = await cls._domain_meta(db)
        return {'templates': templates, 'domains': domains}

    @staticmethod
    async def get_template(db: AsyncSession, *, owner_id: str, template_key: str) -> dict[str, Any]:
        """取单模板详情（含 graph_spec）。内置可见 / 自己名下可见；跨户 NotFound（不泄露）。"""
        tpl = await hasn_workflow_template_dao.get_by_key(db, template_key)
        if tpl is None:
            raise errors.NotFoundError(msg='模板不存在')
        visible = tpl.is_builtin or tpl.owner_id is None or tpl.owner_id == owner_id
        if not visible:
            raise errors.NotFoundError(msg='模板不存在')
        return _to_public(tpl, include_graph_spec=True)

    # ---------- 建（内置 seed / 分身 draft / 测试；真实 draft/publish 工具在 P5） ----------

    @staticmethod
    async def create_template(
        db: AsyncSession, *, owner_id: str | None, obj: CreateWorkflowTemplateParam
    ) -> HasnWorkflowTemplate:
        """建模板行（template_key 全局唯一，冲突即拒）。内置传 owner_id=None。"""
        if await hasn_workflow_template_dao.get_by_key(db, obj.template_key):
            raise errors.RequestError(msg=f'模板键已存在: {obj.template_key}')
        tpl = HasnWorkflowTemplate(
            template_uuid=f'wft_{uuid.uuid4().hex}',
            template_key=obj.template_key,
            domain=obj.domain,
            name=obj.name,
            tagline=obj.tagline,
            description=obj.description,
            sort_order=obj.sort_order,
            icon=obj.icon,
            accent=obj.accent,
            graph_spec=obj.graph_spec or {},
            is_builtin=obj.is_builtin,
            builtin_key=obj.builtin_key,
            status=obj.status,
            owner_id=owner_id,
            source=obj.source,
            market_ref=obj.market_ref,
            sku_ref=obj.sku_ref,
            version=obj.version,
        )
        db.add(tpl)
        await db.flush()
        return tpl


workflow_template_service = WorkflowTemplateService()
