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

from pathlib import Path
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
import yaml

from backend.app.hasn_task.crud.crud_workflow import hasn_workflow_template_dao
from backend.app.hasn_task.model import HasnWorkflowTemplate
from backend.app.hasn_task.schema.workflow_template import (
    CreateWorkflowTemplateParam,
    WorkflowTemplateGraphSummary,
    WorkflowTemplatePublic,
)
from backend.common.exception import errors
from backend.common.log import log

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_DOMAIN_DICT_CODE = 'workflow_template_domain'

# 内置模板可被 hub 重新下发覆盖的派生字段（对齐 hub 官方内置不变量：INSERT-only + builtin_key 可更新）。
# 这些字段随 hub 重扫覆盖；template_uuid（端云同步主键）与 owner_id（归属）恒不动。
_BUILTIN_UPDATABLE_FIELDS = (
    'domain',
    'name',
    'tagline',
    'description',
    'sort_order',
    'icon',
    'accent',
    'graph_spec',
    'status',
    'version',
    'source',
    'builtin_key',
)


def build_builtin_template_data(raw: dict[str, Any]) -> dict[str, Any]:
    """把 hub `workflow-template.yaml` 解析结果映射为 workflow_template 行入参（顶层标量 + graph_spec 整块）。

    校验（在系统边界失败即抛，上层记 warning 跳过该模板）：template_key / name 必填、graph_spec 必须是 mapping。
    graph_spec 内部结构云端不解析，原样落 JSONB；只有顶层标量映射到表列。domain 须已在字典内（不新建）。
    """
    if not isinstance(raw, dict):
        raise TypeError('workflow-template.yaml 顶层必须是 mapping')
    template_key = str(raw.get('template_key') or '').strip()
    if not template_key:
        raise ValueError('缺少 template_key')
    name = str(raw.get('name') or '').strip()
    if not name:
        raise ValueError('缺少 name')
    graph_spec = raw.get('graph_spec')
    if not isinstance(graph_spec, dict):
        raise ValueError('graph_spec 缺失或不是 mapping')
    # template_uuid：hub 显式声明的端云稳定 id 原样沿用（daemon 镜像/实例化据此对齐）；缺省才本地生成 wft_ 前缀
    template_uuid = str(raw.get('template_uuid') or '').strip() or f'wft_{uuid.uuid4().hex}'
    return {
        'template_key': template_key,
        'template_uuid': template_uuid,
        'domain': raw.get('domain') or None,
        'name': name,
        'tagline': raw.get('tagline') or None,
        'description': raw.get('description') or None,
        'sort_order': int(raw.get('sort_order') or 0),
        'icon': raw.get('icon') or None,
        'accent': raw.get('accent') or None,
        'graph_spec': graph_spec,
        'is_builtin': bool(raw.get('is_builtin', True)),
        'builtin_key': (str(raw.get('builtin_key')).strip() if raw.get('builtin_key') else template_key),
        'status': raw.get('status') or 'draft',
        'owner_id': raw.get('owner_id') or None,  # 内置恒空
        'source': raw.get('source') or 'builtin',
        'market_ref': raw.get('market_ref') or None,
        'sku_ref': raw.get('sku_ref') or None,
        'version': int(raw.get('version') or 1),
    }


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

    # ---------- 内置工作流模板下发（hub workflow-templates/ → 本表·幂等 seed loader） ----------

    @staticmethod
    async def upsert_builtin_template(db: AsyncSession, *, data: dict[str, Any]) -> str:
        """幂等下发一个内置工作流模板（hub 官方内置不变量：INSERT-only + builtin_key 可更新，不覆盖用户/市场行）。

        识别按全局唯一 template_key（内置 template_key ≡ builtin_key）：
        - 不存在 → INSERT 新内置行。
        - 已存在且为内置行（is_builtin 且 owner_id 空）→ 只更新派生字段（domain/name/tagline/description/
          sort_order/icon/accent/graph_spec/status/version/source/builtin_key）；**绝不**改 template_uuid
          （端云同步主键）与 owner_id（归属）。
        - 已存在但 owner_id 非空或非内置（用户自建 / 市场物化 / 分身生成）→ 拒绝覆盖，记 warning 跳过（守 owner 归属边界）。

        返回 'inserted' / 'updated' / 'skipped'（供扫描编排汇总统计）。
        """
        template_key = data['template_key']
        existing = await hasn_workflow_template_dao.get_by_key(db, template_key)
        if existing is None:
            tpl = HasnWorkflowTemplate(
                template_uuid=data['template_uuid'],
                template_key=template_key,
                domain=data['domain'],
                name=data['name'],
                tagline=data['tagline'],
                description=data['description'],
                sort_order=data['sort_order'],
                icon=data['icon'],
                accent=data['accent'],
                graph_spec=data['graph_spec'],
                is_builtin=data['is_builtin'],
                builtin_key=data['builtin_key'],
                status=data['status'],
                owner_id=data['owner_id'],
                source=data['source'],
                market_ref=data['market_ref'],
                sku_ref=data['sku_ref'],
                version=data['version'],
            )
            db.add(tpl)
            await db.flush()
            return 'inserted'

        # 只认「内置行」可被 hub 重新下发覆盖；用户/市场/分身行一律不动
        if existing.owner_id is not None or not existing.is_builtin:
            log.warning(
                f'拒绝以内置 seed 覆盖非内置工作流模板行 template_key={template_key} '
                f'owner_id={existing.owner_id} is_builtin={existing.is_builtin}'
            )
            return 'skipped'

        # 内置行：原地更新派生字段（SQLAlchemy Unit of Work；template_uuid/owner_id 恒不动）
        for field in _BUILTIN_UPDATABLE_FIELDS:
            setattr(existing, field, data[field])
        existing.is_builtin = True  # 再确认内置标记
        await db.flush()
        return 'updated'

    @classmethod
    async def sync_builtin_workflow_templates(
        cls, db: AsyncSession, *, repo_root: str | Path
    ) -> dict[str, int]:
        """扫描 hub `workflow-templates/*/workflow-template.yaml` 并幂等 upsert 进 workflow_template 表。

        由 marketplace 的 github sync 传入已 checkout 的 hub 仓根（repo_root）+ 已开事务触发；解析/字段映射/
        upsert 归本模块（workflow_template 域）自持——marketplace 只负责给出 repo_root 与触发，不耦合工作流模板 schema。
        glob 为 1 级目录（`workflow-templates/*/workflow-template.yaml`）。单个模板解析失败 / 字段缺失 → 记 warning
        跳过该模板不中断整体（可恢复→warning）。返回各结果计数（total/inserted/updated/skipped/failed）。
        """
        root = Path(repo_root) / 'workflow-templates'
        results = {'total': 0, 'inserted': 0, 'updated': 0, 'skipped': 0, 'failed': 0}
        if not root.exists():
            log.warning(f'hub workflow-templates 目录不存在，跳过内置工作流模板下发: {root}')
            return results

        for yaml_path in sorted(root.glob('*/workflow-template.yaml')):
            results['total'] += 1
            try:
                raw = yaml.safe_load(yaml_path.read_text(encoding='utf-8')) or {}
                data = build_builtin_template_data(raw)
                outcome = await cls.upsert_builtin_template(db, data=data)
                results[outcome] += 1
            except Exception as exc:  # noqa: PERF203
                # 单模板可恢复失败：记 warning 跳过，绝不拖垮整体 sync
                results['failed'] += 1
                log.warning(f'内置工作流模板 seed 跳过 {yaml_path}：{exc}')
        log.info(f'内置工作流模板下发汇总: {results}')
        return results


workflow_template_service = WorkflowTemplateService()
