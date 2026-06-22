"""通用网页发布与分享（publish，模块 18）AI-Native 内置 manifest + WorkbenchApp 声明。

设计事实源：
- docs/hasn-node设计文档/18-通用网页发布与分享/00-总览.md / 03-工具与接口.md
- docs/hasn-node设计文档/14-AI-Native应用平台/16-应用目录与商业化管理统一设计.md（catalog/manifest 四正交概念）
- docs/hasn-node设计文档/14-AI-Native应用平台/15-AI-Native应用命名空间与目录约定.md（ADR-15：manifest 与应用代码同目录）

⚠️ 命名约定（ADR-15，hasn_ 前缀）：app_id / catalog / URL（``/api/v1/publish/*``）= ``publish``；
云端目录 / PG schema / 表前缀 / codegen 参数 = ``hasn_publish``（``--app hasn_publish --schema hasn_publish``，
表 ``hasn_publish.site``）。app_id 不带前缀（URL 面向身份），目录与 schema 带 ``hasn_`` 前缀。

⚠️ 方案 A（同 deck/hasn_task manifest）：``tools[]`` **置空数组**——``hasn.publish.*`` 工具数据面
在本地（hasn-node ``crates/hasn-mcp/src/publish.rs``，source=Platform/``ExecutionLocation::Local``，
经 daemon ``domains/publish`` PublishGateway 打包上云），**不经云端 Runtime Gateway**，故不进
``tools[]``（自造 transport 会静默过 validate_manifest 变潜伏炸弹）。工具靠 hasn-mcp 注册、bootstrap
发现；``capabilities[]`` 只承载发现/权限元数据控制面记录（不参与 ``_dispatch_tool`` 路由）。

⚠️ scope 与落地工具对齐（hasn-node ``crates/hasn-mcp/src/publish.rs``，P4 已落地）：
- 写类 5 工具（create/update/set_visibility/revoke/delete）统一 ``publish:write``（MANAGE_SCOPE，
  出厂 Ask，owner 三态可覆盖）；
- 读类 2 工具（get/list）无 required_scopes（``capability_scopes()`` 返空，低风险免确认）。
``publish:read``/``publish:write`` 已在 ``app/mcp/scopes.py`` SCOPE_CATALOG 登记展示元数据。

⚠️ 可见性升级硬闸（A-P6 防绕过）：``create`` 发到敏感档 / ``set_visibility`` 升敏感档时，
即便主人把 ``publish:write`` 写回「总是允许」，仍**无视 capability_modes 强制 Ask**。该硬闸是
hasn-mcp execute 内的执行期闸门（``enforce_visibility_upgrade_gate``），manifest 只记元数据。

⚠️ execution_mode：catalog 枚举取 ``local_tool``（本地工具数据面 + 原生 webui「我发布的」UI，
非 cloud 执行、非重 sidecar），同 deck/hasn_task。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.app.hasn.service.workbench_app_registry import WorkbenchApp

# 与既有 AI-Native 审计共表的字段集（同 deck/hasn_task §11.5）。
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

# 写类工具统一 scope（落地真相：publish.rs MANAGE_SCOPE）。
_MANAGE_SCOPE = 'publish:write'


def _cap(
    *,
    name: str,
    title: str,
    description: str,
    write: bool,
    risk_level: str,
    properties: dict,
    required: list[str],
    page_rank: int,
    tags: list[str],
) -> dict:
    """hasn.publish.* 能力声明。

    读类（write=False）无 required_scopes；写类统一 ``publish:write``。
    出厂全 Allow 免确认（16-doc D-v3-1），owner 可设 ask/deny override；
    敏感可见性另有执行期硬闸（与工具授权正交）。
    """
    return {
        'capability_id': f'publish.{name}.capability',
        'name': title,
        'description': description,
        'tool_id': f'publish.{name}',
        'mcp_name': f'hasn.publish.{name}',
        'required_scopes': [_MANAGE_SCOPE] if write else [],
        'workspace_roles': ['owner'],
        'input_schema': {
            'type': 'object',
            'properties': properties,
            'required': required,
            'additionalProperties': False,
        },
        'output_schema': {'type': 'object'},
        'risk_level': risk_level,
        'human_confirmation': {'required': False},
        'result_writeback': ['audit', 'agent_message'],
        'discovery': {
            'exposure': 'on_demand',
            'summary': description,
            'tags': tags,
            'schema_visibility': 'authorized_agents',
            'default_page_rank': page_rank,
        },
    }


# —— 组件型来源 source_ref 子结构（create/update 共用）——
_SOURCE_REF_SCHEMA = {
    'type': ['object', 'null'],
    'description': '组件型来源溯源 {app, id}（如 deck，由 webui 路径 B 渲染成静态 HTML 随 html 一并传入）',
}

PUBLISH_AI_NATIVE_MANIFEST = {
    'app_id': 'publish',
    'version': '1.0.0',
    'workspace_scope': ['personal'],
    'collaboration_mode': 'none',
    # catalog 枚举 local_tool（本地工具数据面 + 原生 UI）。
    'execution_mode': 'local_tool',
    # 本地工具数据面（hasn-mcp source=Platform/Local → daemon PublishGateway），不经云端 Runtime Gateway。
    'transport_mode': 'local',
    'capabilities': [
        _cap(
            name='create',
            title='发布网页',
            description=(
                '发布一个自包含网页（来源 html / path / source_ref 三选一）。返回 site_id + 分享 URL。'
                '升级到敏感可见性（password/unlisted/public）会触发主人审批（无视「总是允许」的硬闸）。'
            ),
            write=True,
            risk_level='medium',
            properties={
                'html': {'type': ['string', 'null'], 'description': '内联 HTML 源（raw，纯解析不执行脚本）'},
                'path': {
                    'type': ['string', 'null'],
                    'description': '本地 HTML 文件路径（限 Agent 数据目录内，白名单校验）',
                },
                'source_ref': _SOURCE_REF_SCHEMA,
                'title': {'type': ['string', 'null'], 'description': '可选：站点标题'},
                'visibility': {
                    'enum': ['private', 'password', 'unlisted', 'public', None],
                    'description': '可见性（缺省 private）',
                },
                'password': {'type': ['string', 'null'], 'description': 'password 可见性的访问口令'},
                'allow_indexing': {
                    'type': 'boolean',
                    'default': False,
                    'description': '是否允许搜索引擎索引（仅 public 有意义）',
                },
                'expires_at': {'type': ['integer', 'null'], 'description': '过期 Unix 秒（缺省永不过期）'},
            },
            required=[],
            page_rank=10,
            tags=['publish', 'web', 'share', 'create'],
        ),
        _cap(
            name='update',
            title='更新发布内容',
            description='更新站点内容（发新 revision，指向新制品）。可见性不变；改可见性用 set_visibility。',
            write=True,
            risk_level='medium',
            properties={
                'site_id': {'type': 'string', 'minLength': 1, 'description': '目标站点 id'},
                'html': {'type': ['string', 'null'], 'description': '可选：新内联 HTML 源'},
                'path': {'type': ['string', 'null'], 'description': '可选：新本地 HTML 文件路径（白名单校验）'},
                'source_ref': _SOURCE_REF_SCHEMA,
                'title': {'type': ['string', 'null'], 'description': '可选：新标题'},
            },
            required=['site_id'],
            page_rank=11,
            tags=['publish', 'web', 'update'],
        ),
        _cap(
            name='set_visibility',
            title='改可见性',
            description=(
                '改站点可见性（含口令）。升级到敏感可见性（password/unlisted/public）会触发主人审批'
                '（无视「总是允许」的硬闸）。'
            ),
            write=True,
            risk_level='medium',
            properties={
                'site_id': {'type': 'string', 'minLength': 1, 'description': '目标站点 id'},
                'visibility': {
                    'enum': ['private', 'password', 'unlisted', 'public'],
                    'description': '目标可见性',
                },
                'password': {'type': ['string', 'null'], 'description': 'password 可见性的访问口令'},
            },
            required=['site_id', 'visibility'],
            page_rank=12,
            tags=['publish', 'web', 'visibility'],
        ),
        _cap(
            name='revoke',
            title='撤回发布',
            description='撤回发布（站点访问返 410；保留记录可重新发布）。',
            write=True,
            risk_level='medium',
            properties={'site_id': {'type': 'string', 'minLength': 1, 'description': '要撤回的站点 id'}},
            required=['site_id'],
            page_rank=13,
            tags=['publish', 'web', 'revoke'],
        ),
        _cap(
            name='delete',
            title='删除发布',
            description='删除站点（软删，撤销同步）。',
            write=True,
            risk_level='high',
            properties={'site_id': {'type': 'string', 'minLength': 1, 'description': '要删除的站点 id'}},
            required=['site_id'],
            page_rank=14,
            tags=['publish', 'web', 'delete'],
        ),
        _cap(
            name='get',
            title='取发布详情',
            description='取站点详情（可见性 / slug / url / 最新 revision 摘要）。',
            write=False,
            risk_level='low',
            properties={'site_id': {'type': 'string', 'minLength': 1, 'description': '站点 id'}},
            required=['site_id'],
            page_rank=15,
            tags=['publish', 'web', 'get', 'read'],
        ),
        _cap(
            name='list',
            title='列我发布的',
            description='列出当前 owner 的全部发布站点（「我发布的」，不含已删）。',
            write=False,
            risk_level='low',
            properties={},
            required=[],
            page_rank=16,
            tags=['publish', 'web', 'list', 'read'],
        ),
    ],
    # 方案 A：本地工具不进 tools[]（走 hasn-mcp source=Local/Platform，bootstrap 发现）。
    'tools': [],
    'events': [],
    'reverse_invoke': {'supported': False},
    'ui_interfaces': [{'face': 'ui', 'transport': 'daemon_direct'}],
    'publisher': {'developer_id': 'huanxing-first-party', 'publisher_type': 'first_party', 'name': '唤星'},
    'endpoints': {'tool_endpoint': None, 'event_endpoint': None, 'component_origin': 'loopback'},
    'audit': {'fields': list(_AUDIT_FIELDS)},
}


def build_publish_workbench_app() -> WorkbenchApp:
    """构造 publish 的 WorkbenchApp（catalog seed 源 + 工作台入口）。延迟导入避免循环依赖。

    UI 为 webui 原生路由（``ui_kind=None`` 内联导航至 ``/publish``「我发布的」，同 knowledge/community/
    deck/hasn_task），非 sidecar、非独立窗口；execution_mode=local_tool（本地工具驱动 + 原生 UI）。
    install_policy=auto（免费、注册即用）。
    """
    from backend.app.hasn.service.workbench_app_registry import WorkbenchApp

    return WorkbenchApp(
        id='publish',
        name='网页发布',
        icon='brand-publish',
        description='把分身做好的网页、海报、演示一键变成稳定分享链接，谁能看、看多久你说了算。',
        scope=('personal',),
        collaboration_mode='none',
        entry_route='/publish',
        install_policy='auto',
        execution_mode='local_tool',
    )
