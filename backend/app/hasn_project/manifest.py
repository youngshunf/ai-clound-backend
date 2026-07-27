"""平台项目应用（project，对外「项目管理」；模块 14 doc38）工作台入口 manifest。

设计事实源：
- docs/hasn-node设计文档/14-AI-Native应用平台/38-项目管理一级应用(平台项目·联邦挂靠)设计.md
- docs/hasn-node设计文档/14-AI-Native应用平台/实施/38-项目管理一级应用实施清单.md（U1 catalog 播种）

项目是**第三条轴（项目轴）**——只回答「为了哪件事」的业务容器：各应用保留自己的容器
（知识库/获客项目/图坊项目/站点/deck…），通过可空 ``project_id`` 联邦挂靠到平台项目；
产物（``hasn_artifacts``）与工作会话同样打标；本应用作**聚合门面**——项目总览（跨应用产物流
+ 进行中会话 + 挂靠资源 + 参与分身）+ 项目内派发。**不合并各应用容器、不做权限边界、不做
应用挂载**（三条铁律，doc38 §3.2）。

⚠️ execution_mode='cloud'：数据云端权威（``hasn_project`` schema），``hasn.project.*``
是云端平台工具（PJ-P1，随 U3 注册 + 铸 scope）。ui_kind=None：原生 webui 页（模式 B，
``/apps/project`` 列表页 + ``/apps/project/{id}`` 总览页），非 sidecar iframe。

⚠️ install_policy='auto'：项目是「为了哪件事」的一级容器门面，默认挂载工作台侧栏常驻可见
（同 knowledge/community）；全局项目切换器（GlobalProjectSwitcher，U6）是跨应用的主入口。

⚠️ default_agent_type='assistant'（AppCollab doc21 §4.3）：派「建项目 / 在此项目中派发」时
默认承接 owner 名下的「全能助理」内置分身，无则回退主脑——见
``app_catalog_service._CATALOG_AGENT_DEFAULTS`` / ``resolve_default_agent_for_app``。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from backend.app.hasn.service.app_catalog_registry import App

# 与既有 AI-Native 审计共表的字段集（同 plan/deck/designsystem）。
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


# 平台项目 AI-Native manifest（供 register-on-write 解析 resources[] 描述符 + tool.search 域目录）。
# ⚠️ **单资源**应用（doc38 §3 U3 item 7）：只声明一条 `resource_kind='project'` 描述符，**不声明
# `ref_type`**——避免「任一 descriptor 声明 ref_type → 整 app 进多资源模式」的 opt-in 陷阱
# （register-on-write 显式传 descriptor，不依赖 ref_type 解析，无需多资源解析就不声明）。
# 分身建/改项目即经公共接缝 `register_app_resource_artifact(app_id='project', resource_kind='project', ...)`
# 把项目登记进 `hasn_artifacts`（project_id 自挂靠自身），完成即出「项目」卡 + 绑工作会话资源栏。
# uri_domain='project' → hasn://project/{server_id}（U4 进 doc08 §3 registry）；server_id=hasn_project.id（UUID 权威）。
PROJECT_AI_NATIVE_MANIFEST: dict[str, Any] = {
    'app_id': 'project',
    'domain_summary': {'project': '项目（为了哪件事·跨应用产物流聚合·里程碑·联邦挂靠）'},
    'version': '1.0.0',
    'workspace_scope': ['personal', 'enterprise'],  # 双模应用（个人 / 企业，enterprise_id 列，对齐 GE）
    'collaboration_mode': 'none',
    'project_aware': True,
    'project_required': True,
    'project_integration': 'project_required',
    'resources': [
        {
            'resource_kind': 'project',
            'uri_domain': 'project',  # → hasn://project/{server_id}（U4 登记 internal_route 域）
            'open': {'mode': 'internal_route', 'route_template': '/apps/project/:id'},
            'card': {'verb': '项目', 'action_label': '打开项目'},
            'artifact_kind': 'resource',
        },
    ],
    'execution_mode': 'cloud',
    'transport_mode': 'cloud',  # hasn.project.* 是云端平台工具（mcp/tools/project.py），经云端 Runtime Gateway
    # 通知发布能力声明：项目巡检 / 周报 / 完成卡经 emit 发卡给主人（后续切片接 emit）。
    'notifications': {
        'emit': {
            'categories': ['app'],
            'card_message': True,
            'display_name': '项目',
        }
    },
    'capabilities': [],
    'tools': [],
    'events': [],
    'reverse_invoke': {'supported': False},
    'ui_interfaces': [{'face': 'ui', 'transport': 'daemon_direct'}],
    'publisher': {'developer_id': 'huanxing-first-party', 'publisher_type': 'first_party', 'name': '唤星'},
    'endpoints': {'tool_endpoint': None, 'event_endpoint': None, 'component_origin': 'loopback'},
    'audit': {'fields': list(_AUDIT_FIELDS)},
}


def build_project_app() -> App:
    """平台项目 App（cloud / 原生 webui 路由 / 默认挂载）。

    - ``execution_mode='cloud'`` / ``ui_kind=None``：云端权威数据（``hasn_project``），
      原生 webui 工作台页（模式 B）。``hasn.project.*`` 云端平台工具随 U3 注册 + 铸 scope。
    - ``install_policy='auto'``：一级容器门面，默认挂载常驻可见。
    - ``scope=('personal', 'enterprise')``：个人 / 企业双模（``enterprise_id`` 列，对齐 GE）。

    延迟导入 App 避免循环依赖。
    """
    from backend.app.hasn.service.app_catalog_registry import App

    return App(
        id='project',
        name='项目管理',
        icon='brand-project',
        description='一件事，一个「项目」——相关的资料、图片、网页和成果都归到一处，分身的工作进展一页看全。',
        scope=('personal', 'enterprise'),
        collaboration_mode='none',
        entry_route='/apps/project',
        install_policy='auto',
        execution_mode='cloud',
        ui_kind=None,
        project_aware=True,
        project_required=True,
    )
