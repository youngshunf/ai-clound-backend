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

import re
import uuid

from pathlib import Path
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
import yaml

from backend.app.hasn_task.crud.crud_workflow import hasn_workflow_template_dao
from backend.app.hasn_task.model import HasnWorkflowTemplate
# 实例化复用 agent 建图权威路径（cloud workflow，daemon 经 sync mirror tick/fire）。
from backend.app.hasn_task.service.agent_workflow_service import agent_workflow_service
from backend.app.hasn_task.schema.workflow_template import (
    CreateWorkflowTemplateParam,
    WorkflowTemplateGraphSummary,
    WorkflowTemplatePublic,
)
# 复用工作流建图的无环检测（07 §9.3 · Kahn 拓扑），避免重写；模板校验与执行建图同一语义。
from backend.app.hasn_task.service.workflow_service import detect_cycle
from backend.common.exception import errors
from backend.common.log import log

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.common.dataclasses import AgentTokenPayload

_DOMAIN_DICT_CODE = 'workflow_template_domain'

# ── §6.3 服务端校验护栏常量（doc94 §10-P5 / doc11 §6.3）─────────────────────────────
# 模板节点数上限（模板是「场景蓝图」，节点远少于自由工作流；取保守常量，与 webui 画廊卡负载相称）。
_MAX_NODES = 20

# 产物 kind 权威注册表（doc11 §4.3 node.output_spec.kind）：两内置模板（一人公司 / 金融投研）
# 的 canonical 取值并集 + 通用产物 kind。新增 kind 须先在此登记（与 hub 模板 canonical 对齐），
# 否则 draft/update/publish 校验拒绝——分身读 message 里的具体 kind 名即可自修。
_OUTPUT_KINDS: frozenset[str] = frozenset(
    {
        'workflow_anchor',  # 起点锚点产物（主人输入）
        'knowledge_base',
        'dataset',
        'strategy_card',
        'backtest_report',
        'design_system',
        'design_asset',
        'website',
        'content_collection',
        'lead_list',
        'deck',
        'article',
        # 通用产物 kind（跨场景复用）
        'document',
        'report',
        'image',
        'video',
        'presentation',
        'code',
        'data',
    }
)

# 内置人设 builtin_key（2026-07-12 收敛为 3：全能助理 assistant / 创作专家 content_operator /
# 分析专家 analyst，见 app_catalog_service._CATALOG_AGENT_DEFAULTS）。default_agent_type 命中即
# 「有内置人设模板」；未命中 = 需主人自备（daemon resolve_default_agent_for_app 回退主脑）——**允许**，
# 只是软识别、不拒绝（doc94 §10-P5：允许缺失但要能识别）。
_BUILTIN_PERSONA_KEYS: frozenset[str] = frozenset({'assistant', 'content_operator', 'analyst'})

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


def validate_graph_spec(graph_spec: Any) -> None:
    """§6.3 服务端图蓝图校验护栏（不触库·纯函数·draft/update/publish 共用）。

    不合法即抛 `errors.RequestError`，message 指名到具体 node_key / edge / app / kind，
    便于分身读 message 自修（scenario-designer 技能据此教分身修蓝图）。校验项：
    1. **图合法**：结构对、node_key 全局唯一、边引用存在、无自环、DAG 无环（复用 detect_cycle）、
       至少一个 is_origin=true 起点、节点数 ≤ _MAX_NODES；
    2. **引用合法**：apps[] 每个 app 在应用目录存在、output_spec.kind 在产物 kind 注册表内；
       default_agent_type 非内置人设 → 软识别为「需主人自备」（不拒绝）。
    """
    if not isinstance(graph_spec, dict):
        raise errors.RequestError(msg='graph_spec 必须是对象 {nodes:[...], edges:[...]}')
    nodes = graph_spec.get('nodes')
    edges = graph_spec.get('edges') or []
    if not isinstance(nodes, list) or not nodes:
        raise errors.RequestError(msg='graph_spec.nodes 至少需要一个节点')
    if not isinstance(edges, list):
        raise errors.RequestError(msg='graph_spec.edges 必须是数组')
    if len(nodes) > _MAX_NODES:
        raise errors.RequestError(msg=f'节点数超限（{len(nodes)} > {_MAX_NODES}）')

    # 应用目录权威来源：in-process app_catalog_registry（catalog DB 的 seed 源，同步权威）。
    # 延迟导入避免模块初始化期触发 registry.default()（其会 import 全部应用 manifest）。
    from backend.app.hasn.service.app_catalog_registry import app_catalog_registry

    valid_app_ids = {app.id for app in app_catalog_registry.list()}

    node_keys: list[str] = []
    key_set: set[str] = set()
    origin_count = 0
    for idx, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise errors.RequestError(msg=f'第 {idx + 1} 个节点不是对象')
        node_key = str(node.get('node_key') or '').strip()
        if not node_key:
            raise errors.RequestError(msg=f'第 {idx + 1} 个节点缺少 node_key')
        if node_key in key_set:
            raise errors.RequestError(msg=f'node_key 重复: {node_key}')
        key_set.add(node_key)
        node_keys.append(node_key)
        if node.get('is_origin') is True:
            origin_count += 1

        # 引用合法：apps[] 每个 app 必须在应用目录存在
        apps = node.get('apps') or []
        if not isinstance(apps, list):
            raise errors.RequestError(msg=f'节点 {node_key} 的 apps 必须是数组')
        for app_id in apps:
            if app_id not in valid_app_ids:
                raise errors.RequestError(msg=f'节点 {node_key} 引用了不存在的应用: {app_id}')

        # 引用合法：output_spec.kind 必须在产物 kind 注册表内（缺省/空放行——起点等可无产物）
        output_spec = node.get('output_spec')
        if isinstance(output_spec, dict):
            kind = output_spec.get('kind')
            if kind and kind not in _OUTPUT_KINDS:
                raise errors.RequestError(msg=f'节点 {node_key} 的 output_spec.kind 未注册: {kind}')

        # default_agent_type：软识别（内置人设 vs 需主人自备），**不拒绝**（doc94 §10-P5）。
        # 非内置也允许——daemon resolve_default_agent_for_app 命中不到则回退主脑。

    if origin_count == 0:
        raise errors.RequestError(msg='graph_spec 至少需要一个 is_origin=true 的起点节点')

    # 边引用存在 + 非自环 + DAG 无环
    edge_tuples: list[tuple[str, str]] = []
    for i, edge in enumerate(edges):
        if not isinstance(edge, dict):
            raise errors.RequestError(msg=f'第 {i + 1} 条边不是对象')
        parent = str(edge.get('parent') or '').strip()
        child = str(edge.get('child') or '').strip()
        if parent not in key_set:
            raise errors.RequestError(msg=f'边引用的父节点不存在: {parent or "(空)"}')
        if child not in key_set:
            raise errors.RequestError(msg=f'边引用的子节点不存在: {child or "(空)"}')
        if parent == child:
            raise errors.RequestError(msg=f'节点不能依赖自己: {parent}')
        edge_tuples.append((parent, child))

    cycle = detect_cycle(node_keys, edge_tuples)
    if cycle is not None:
        raise errors.RequestError(msg=f'依赖图存在环，涉及节点: {", ".join(cycle)}')


def _slugify(name: str) -> str:
    """把展示名收敛成 ascii slug（供 template_key 可读前缀）；中文/空 → 回落 'wf'。"""
    slug = re.sub(r'[^a-z0-9]+', '_', (name or '').lower()).strip('_')
    return slug[:24] or 'wf'


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

    # ---------- P5：分身模板工具（draft / update / instantiate / publish；graph_spec 过 §6.3 校验） ----------

    @classmethod
    async def _gen_unique_template_key(cls, db: AsyncSession, name: str) -> str:
        """据展示名生成 owner 内唯一（且全局唯一）的 template_key：`tpl_{slug}_{hex}`。"""
        base = _slugify(name)
        for _ in range(5):
            candidate = f'tpl_{base}_{uuid.uuid4().hex[:8]}'
            if await hasn_workflow_template_dao.get_by_key(db, candidate) is None:
                return candidate
        # 极端碰撞（几乎不可能）：用全长 hex 兜底，保证唯一
        return f'tpl_{base}_{uuid.uuid4().hex}'

    @classmethod
    async def draft_template(
        cls, db: AsyncSession, *, owner_id: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """分身提交模板草案（graph_spec 全量）→ 过 §6.3 校验 → 存 draft/source=agent/owner=本人/version=1。"""
        graph_spec = params.get('graph_spec') or {}
        validate_graph_spec(graph_spec)  # 不合法即抛，message 具体便于分身自修

        template_key = await cls._gen_unique_template_key(db, str(params.get('name') or 'workflow'))
        obj = CreateWorkflowTemplateParam(
            template_key=template_key,
            name=str(params.get('name') or '未命名工作流'),
            domain=params.get('domain') or None,
            tagline=params.get('tagline') or None,
            description=params.get('description') or None,
            sort_order=int(params.get('sort_order') or 0),
            icon=params.get('icon') or None,
            accent=params.get('accent') or None,
            graph_spec=graph_spec,
            is_builtin=False,
            status='draft',
            source='agent',
            version=1,
        )
        tpl = await cls.create_template(db, owner_id=owner_id, obj=obj)
        return _to_public(tpl, include_graph_spec=True)

    @classmethod
    async def update_template(
        cls, db: AsyncSession, *, owner_id: str, template_key: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """更新自己名下 draft/active 模板（version+1）；改 builtin/别人的 → 拒绝。"""
        tpl = await hasn_workflow_template_dao.get_by_key(db, template_key)
        if tpl is None:
            raise errors.NotFoundError(msg='模板不存在')
        if tpl.is_builtin or tpl.owner_id is None:
            raise errors.RequestError(msg='不能修改内置模板')
        if tpl.owner_id != owner_id:
            # 跨户私有模板不可见 → NotFound 不泄露归属
            raise errors.NotFoundError(msg='模板不存在')
        if tpl.status not in ('draft', 'active'):
            raise errors.RequestError(msg=f'当前状态 {tpl.status} 不可修改')

        # graph_spec 若传新版 → 过同套 §6.3 校验后替换
        new_graph = params.get('graph_spec')
        if new_graph is not None:
            validate_graph_spec(new_graph)
            tpl.graph_spec = new_graph

        # 局部标量字段（只改显式传入的非 None 值）
        for field in ('name', 'domain', 'tagline', 'description', 'icon', 'accent'):
            if params.get(field) is not None:
                setattr(tpl, field, params[field])
        if params.get('sort_order') is not None:
            tpl.sort_order = int(params['sort_order'])

        tpl.version = (tpl.version or 1) + 1
        await db.flush()
        return _to_public(tpl, include_graph_spec=True)

    @classmethod
    async def instantiate_template(
        cls, db: AsyncSession, *, agent: AgentTokenPayload, template_key: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """据模板实例化 cloud 权威 workflow（daemon 经 sync mirror tick/fire）。

        读 template.graph_spec → 映射为 workflow create 参数（title/goal/起点输入/节点定制覆盖）→ 复用
        `agent_workflow_service.create_workflow`（带 template_key 溯源）→ 返 workflow 引用（workflow_id）。
        付费模板权益判定本期恒 pass 短路（见下 P7 挂点）。
        """
        owner_id = agent.owner_hasn_id
        tpl = await hasn_workflow_template_dao.get_by_key(db, template_key)
        if tpl is None:
            raise errors.NotFoundError(msg='模板不存在')
        visible = tpl.is_builtin or tpl.owner_id is None or tpl.owner_id == owner_id
        if not visible:
            raise errors.NotFoundError(msg='模板不存在')

        # 付费模板权益判定（doc11 §8.3）：本期恒 pass 短路。
        # P7 挂点：sku_ref 非空才真判 → resolve_access(feature_key=f'workflow_template:{template_key}')，
        # 不通即抛 need_purchase；免费模板（sku_ref 空）直接放行。
        _ = tpl.sku_ref  # noqa: F841  （P7 接权益判定后消费）

        wf_params = cls._template_to_workflow_params(tpl, params)
        result = await agent_workflow_service.create_workflow(db, agent=agent, params=wf_params)

        # 记模板溯源到 workflow.template_key 列（HasnWorkflow ORM 未映射该列，走定向 UPDATE，同一事务内）
        await db.execute(
            sa.text('UPDATE hasn_task.workflow SET template_key = :tk WHERE workflow_uuid = :wu'),
            {'tk': template_key, 'wu': result['workflow_id']},
        )
        result['template_key'] = template_key
        return result

    @staticmethod
    def _template_to_workflow_params(tpl: HasnWorkflowTemplate, params: dict[str, Any]) -> dict[str, Any]:
        """把 template.graph_spec 映射为 agent_workflow_service.create_workflow 的 params。

        - 节点：template node → workflow 节点；agent_id 缺省=发起分身（create_workflow 默认），
          起点节点 prompt 取「起点输入」origin_input，其余取模板 prompt；node_overrides 可逐节点覆盖
          prompt/agent_id/system_prompt。
        - 边：{parent, child} 原样透传（DAG 已在 draft 校验，create_workflow 建图会再复验无环）。
        """
        graph = tpl.graph_spec or {}
        t_nodes = graph.get('nodes') or []
        t_edges = graph.get('edges') or []
        overrides = params.get('node_overrides') or {}
        origin_input = params.get('origin_input')

        nodes: list[dict[str, Any]] = []
        for tn in t_nodes:
            if not isinstance(tn, dict):
                continue
            node_key = tn.get('node_key')
            ov = overrides.get(node_key) or {}
            is_origin = tn.get('is_origin') is True
            # prompt 非空兜底链：override → (起点用 origin_input / 其余用模板 prompt) → 描述 → 名称 → node_key
            if is_origin:
                prompt = ov.get('prompt') or origin_input or tn.get('description') or tn.get('name') or node_key
            else:
                prompt = ov.get('prompt') or tn.get('prompt') or tn.get('description') or tn.get('name') or node_key
            nodes.append(
                {
                    'node_key': node_key,
                    'name': tn.get('name') or node_key,
                    'agent_id': ov.get('agent_id'),  # None → 发起分身
                    'prompt': prompt,
                    'system_prompt': ov.get('system_prompt') or tn.get('system_prompt') or None,
                    'description': tn.get('description'),
                }
            )
        edges = [
            {'parent': e.get('parent'), 'child': e.get('child')}
            for e in t_edges
            if isinstance(e, dict)
        ]
        return {
            'name': params.get('title') or tpl.name,
            'goal': params.get('goal') or tpl.description,
            'schedule_type': 'once',
            'nodes': nodes,
            'edges': edges,
        }

    @classmethod
    async def publish_template(
        cls, db: AsyncSession, *, owner_id: str, template_key: str
    ) -> dict[str, Any]:
        """上架自己名下模板：过 §6.3 校验 → status 转 active + version 快照 + market_ref 占位。

        本期 publish 只做「校验 + status 转换 + market_ref 占位 + version 快照」；完整 marketplace
        listing/定价集成归 P7（不硬塞新枚举）。publish = ask 闸（外发+动钱语义，三态由 server.call_tool
        据 manifest human_confirmation 统一判定，工具体不二次校验）。
        """
        tpl = await hasn_workflow_template_dao.get_by_key(db, template_key)
        if tpl is None:
            raise errors.NotFoundError(msg='模板不存在')
        if tpl.is_builtin or tpl.owner_id is None:
            raise errors.RequestError(msg='不能发布内置模板')
        if tpl.owner_id != owner_id:
            raise errors.NotFoundError(msg='模板不存在')

        validate_graph_spec(tpl.graph_spec or {})

        tpl.status = 'active'  # 上架生效（P7 接 pending 审核态后再细分）
        tpl.version = (tpl.version or 1) + 1  # version 快照：升版即冻结本次上架蓝图
        tpl.market_ref = f'{template_key}@{tpl.version}'  # 市场溯源占位（P7 换真 listing id）
        await db.flush()
        return _to_public(tpl, include_graph_spec=True)

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
