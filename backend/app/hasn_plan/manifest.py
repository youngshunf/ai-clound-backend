"""规划与目标管理应用（plan，模块 19）AI-Native 内置 manifest。

设计事实源：
- docs/hasn-node设计文档/19-规划与目标管理/01-规划与目标管理总体设计.md（§5.5 catalog / §9 工具面 / §11 集成）
- docs/hasn-node设计文档/19-规划与目标管理/实施/01-分阶段实施计划与施工清单.md（P1 应用注册）

plan 是 **Tool-First** AI-Native 个人 PIM 应用（Collaboration Mode `none`）：分身经 `hasn.plan.*`
管理主人的目标/计划/待办/日程/习惯，UI 是这些能力的可视化。形态同 deck/designsystem（local_tool）：
工具数据面在本地 hasn-mcp（P2 落地）+ 云端权威存储（hasn_plan schema），无独立 sidecar、无常驻进程。

⚠️ 方案 A（同 deck/designsystem）：`tools[]` 置空数组——`hasn.plan.*` 工具在本地 hasn-mcp（P2），
读类 source=Local 直调本地纯逻辑 / 写类经 `BackendGateway::for_agent` 落云端 `/api/v1/plan/agent/*`
（Agent JWT 通道），不经云端 Runtime Gateway。本期 P1 capabilities 留空（数据底座 + 云端 Agent CRUD
先行），P2 接 hasn-mcp 工具时补全 capabilities 控制面元数据。

⚠️ execution_mode：catalog 枚举取 `local_tool`（本地工具驱动 + 原生 webui UI），同 deck/designsystem。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.app.hasn.service.app_catalog_registry import App

# 与既有 AI-Native 审计共表的字段集（同 deck/designsystem）。
_AUDIT_FIELDS = [
    'trace_id',
    'workspace',
    'app_id',
    'agent_hasn_id',
    'owner_hasn_id',
    'session_uuid',
    'tool_id',
    'required_scopes',
    'decision',
]


PLAN_AI_NATIVE_MANIFEST = {
    'app_id': 'plan',
    # 「可搜索域目录」：namespace 关键词 → 一句话（云端 tool.search 描述自动汇聚，agent 据此选关键词搜该域工具）。
    'domain_summary': {'plan': '规划（目标/计划/待办/简报/复盘）'},
    'version': '1.0.0',
    'workspace_scope': ['personal', 'enterprise'],  # PLAN-ENT PE-6：双模应用（个人 PIM + 企业日历）
    'collaboration_mode': 'none',
    # 资源描述符（doc31 §2，RC-P6/doc31-A）：plan 是**多资源**应用——派发工作会话的 origin_ref 子类化
    # （resource:plan:goal:{id} / resource:plan:plan:{id}），据 ref_type 段分别映射到目标/计划详情页。
    # 目标 decompose / 计划推进会话完成即出「目标做好了 / 计划做好了」卡 + 登记应用资源产物到会话资源栏。
    # 里程碑（milestone）/待办（todo）/采访（onboarding）等其余 origin_ref 无匹配 ref_type → 回落通用工作
    # 会话卡（它们在 plan 页内下钻/抽屉查看，非独立资源详情页）。plan 的 goal/plan id 即云端权威 id。
    'resources': [
        {
            'resource_kind': 'plan.goal',
            'ref_type': 'goal',  # origin_ref=resource:plan:goal:{id}
            'uri_domain': 'plan/goals',  # → hasn://plan/goals/{id}（doc08 §3 已登记 internal_route 域）
            'open': {'mode': 'internal_route', 'route_template': '/apps/plan/goals/:id'},
            'card': {'verb': '目标', 'action_label': '打开目标'},
            'artifact_kind': 'other',
        },
        {
            'resource_kind': 'plan.plan',
            'ref_type': 'plan',  # origin_ref=resource:plan:plan:{id}
            'uri_domain': 'plan/plans',  # → hasn://plan/plans/{id}
            'open': {'mode': 'internal_route', 'route_template': '/apps/plan/plans/:id'},
            'card': {'verb': '计划', 'action_label': '打开计划'},
            'artifact_kind': 'other',
        },
    ],
    'execution_mode': 'local_tool',
    'transport_mode': 'local',
    # 通知发布能力声明（统一通知设计）：提醒/简报/复盘/确认卡经 Agent JWT 通道发卡给主人（P4/P5 接 emit）。
    'notifications': {
        'emit': {
            'categories': ['app'],
            'card_message': True,
            'display_name': '规划',
        }
    },
    # 方案 A：本地工具不进 tools[]；P1 数据底座先行，capabilities 随 P2 hasn-mcp `hasn.plan.*` 落地补全。
    'capabilities': [],
    'tools': [],
    'events': [],
    'reverse_invoke': {'supported': False},
    'ui_interfaces': [{'face': 'ui', 'transport': 'daemon_direct'}],
    'publisher': {'developer_id': 'huanxing-first-party', 'publisher_type': 'first_party', 'name': '唤星'},
    'endpoints': {'tool_endpoint': None, 'event_endpoint': None, 'component_origin': 'loopback'},
    'audit': {'fields': list(_AUDIT_FIELDS)},
}


def build_plan_app() -> App:
    """plan App（local_tool / 内联路由 / 非自动挂载）。

    - ``install_policy='manual'``：与 growth/creator/designsystem 先例一致——注册 manifest + 铸 scope，
      用户可见启动入口随 webui 工作台 + ``hasn_app_catalog`` 目录行落地。注册到 app_catalog_registry
      是 ``validate_manifest`` 的硬前置（否则 workbench_app_not_found）。
    - ``collaboration_mode='none'`` / ``scope=('personal', 'enterprise')`` 必须与 manifest ``workspace_scope``
      对齐（validate 闸门）。PLAN-ENT PE-6 起 plan 升双模应用（个人 PIM + 企业日历）；``purchasable_by='both'``
      由迁移直改活跃 catalog 行（_catalog_row_from_app 不产此列，仅 INSERT-only seed 用 DB 默认 owner）。
    - ``execution_mode='local_tool'`` / ``ui_kind=None``：原生 webui（非 embedded sidecar）。

    延迟导入 App 避免循环依赖（app_catalog_registry 加载即 default() 反向引用本模块）。
    """
    from backend.app.hasn.service.app_catalog_registry import App

    return App(
        id='plan',
        name='规划',
        icon='brand-plan',
        description='你的目标、计划、待办、日程可视化大脑——分身当参谋长替你拆解目标，当执行秘书替你排期复盘。',
        scope=('personal', 'enterprise'),
        collaboration_mode='none',
        entry_route='/apps/plan',
        install_policy='manual',
        execution_mode='local_tool',
        ui_kind=None,
    )
