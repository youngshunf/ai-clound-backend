"""唤星自研设计系统生成应用（designsystem，模块 14）AI-Native 内置 manifest。

设计事实源：
- docs/hasn-node设计文档/14-AI-Native应用平台/20-设计系统生成应用(自研)架构设计.md §7（安全/权限）
- docs/hasn-node设计文档/14-AI-Native应用平台/实施/12-设计系统生成应用实施清单.md P7（catalog/manifest 注册 + 铸 scope）
- docs/hasn-node设计文档/14-AI-Native应用平台/16-应用目录与商业化管理统一设计.md（catalog/manifest 四正交概念）

designsystem 是 **Tool-First** AI-Native 应用：给分身**确定性刀**（四层 token 契约 compile/derive/
validate/extract 纯函数 + 导入/存取），**不内部调 LLM**（编排交分身）。形态同 deck（[20] Tool-First）：
引擎分布在 daemon(Rust crate `hasn-designsystem-core`) + hasn-mcp(本地工具) + 云端权威存储，
无独立 sidecar、无常驻进程。

⚠️ 方案 A（同 deck）：`tools[]` **置空数组**——`hasn.designsystem.*` 工具数据面在本地
（hasn-mcp `built_in_designsystem_tools()`）：纯函数 4 个 `source=Local` 直调 P1 crate；云端 4 个
（import/save/list/get）经 `ToolExecutionContext::backend().for_agent(agent).designsystem.*` 落云端
`/api/v1/designsystem/agent/*`（Agent JWT 通道），**不经云端 Runtime Gateway `_dispatch_tool`**。
故都不进 `tools[]`（自造 transport 会静默过 validate_manifest 变潜伏炸弹）；`capabilities[]` 只承载
发现/权限元数据控制面记录。

⚠️ scope 与落地工具对齐（hasn-node `crates/hasn-mcp/src/designsystem.rs` + `scopes.py`，DS-P4 已落地）：
写类 import/save 统一 `designsystem:write`（出厂 Allow，owner 三态可覆盖）；读类 + 确定性纯函数
（compile_tokens/derive/validate/extract_components/list/get）无 required_scopes（不设假闸门）。
`designsystem:publish`（分享/发布，P9/P10 云端端点）本期工具不注册（绝不注册只报错的工具），
但 scope 已登记进 platform_scopes 展示词表（DS-P7）为后续解锁；授权走三态 capability_modes，JWT scopes 已退役（实施102 S0）。

⚠️ execution_mode：catalog 枚举（`cloud/embedded_desktop/local_tool`）取 **`local_tool`**
（本地工具驱动 + 原生 webui UI，非 cloud 执行、非重 sidecar），同 deck。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.app.hasn.service.app_catalog_registry import App

# 与既有 AI-Native 审计共表的字段集（同 deck）。
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

# 写类工具统一 scope（落地真相：designsystem.rs import/save）。
_WRITE_SCOPE = 'designsystem:write'


def _read_cap(
    *, name: str, description: str, properties: dict, required: list[str], page_rank: int, tags: list[str]
) -> dict:
    """读类 / 确定性纯函数能力（无 required_scopes）——与 designsystem.rs 读类/纯函数工具空 scope 对齐。"""
    short = name.split('.', 1)[-1]
    return {
        'capability_id': f'designsystem.{name}.capability',
        'name': short,
        'description': description,
        'tool_id': f'designsystem.{name}',
        'mcp_name': f'hasn.designsystem.{name}',
        'required_scopes': [],
        'workspace_roles': ['owner'],
        'input_schema': {
            'type': 'object',
            'properties': properties,
            'required': required,
            'additionalProperties': False,
        },
        'output_schema': {'type': 'object'},
        'risk_level': 'low',
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


def _write_cap(
    *, name: str, title: str, description: str, properties: dict, required: list[str], page_rank: int, tags: list[str]
) -> dict:
    """写类能力（required_scopes=designsystem:write；出厂 Allow，owner 可设 ask/deny override）。"""
    return {
        'capability_id': f'designsystem.{name}.capability',
        'name': title,
        'description': description,
        'tool_id': f'designsystem.{name}',
        'mcp_name': f'hasn.designsystem.{name}',
        'required_scopes': [_WRITE_SCOPE],
        'workspace_roles': ['owner'],
        'input_schema': {
            'type': 'object',
            'properties': properties,
            'required': required,
            'additionalProperties': False,
        },
        'output_schema': {'type': 'object'},
        'risk_level': 'medium',
        # 出厂全 Allow，无需确认；risk_level 仅 UI 提示，owner 可设 ask/deny override。
        'human_confirmation': {'required': False},
        'result_writeback': ['agent_message', 'audit'],
        'discovery': {
            'exposure': 'on_demand',
            'summary': description,
            'tags': tags,
            'schema_visibility': 'authorized_agents',
            'default_page_rank': page_rank,
        },
    }


DESIGNSYSTEM_AI_NATIVE_MANIFEST = {
    'app_id': 'designsystem',
    # 「可搜索域目录」：namespace 关键词 → 一句话（云端 tool.search 描述自动汇聚，agent 据此选关键词搜该域工具）。
    'domain_summary': {'designsystem': '设计系统（令牌编译/校验/组件抽取）'},
    'version': '1.0.0',
    'workspace_scope': ['personal'],
    'collaboration_mode': 'none',
    'execution_mode': 'local_tool',
    'transport_mode': 'local',
    # 通知发布能力声明（统一通知设计）：生成完成 / 分享经 Agent JWT 通道发卡给主人（P10 接 emit）。
    'notifications': {
        'emit': {
            'categories': ['app'],
            'card_message': True,
            'display_name': '设计系统',
        }
    },
    # 资源描述符（doc31 §2，RC-P6）：设计系统规范 → hasn://designsystem/{server_id}，应用内详情路由打开。
    # internal_route·与 hasnUri 硬编码特例同结果（行为不变）。
    'resources': [
        {
            'resource_kind': 'designsystem.spec',
            'uri_domain': 'designsystem',  # → hasn://designsystem/{server_id}（doc08 §3 已登记 internal_route 域）
            'open': {'mode': 'internal_route', 'route_template': '/apps/designsystem/:id'},
            'card': {'verb': '设计系统', 'action_label': '打开设计系统'},
            'artifact_kind': 'resource',
        }
    ],
    'capabilities': [
        # —— 确定性纯函数（本地直调 P1 crate，无 scope）——
        _read_cap(
            name='compile_tokens',
            description='原始变量/DESIGN.md → 标准命名 + 四层归类(A1/A2/B-slot/C) + 缺槽别名回填，产 tokens.css。',
            properties={
                'source_tokens': {'type': 'string', 'description': '原始变量 / 描述（自由 CSS 自定义属性或自然语言）'},
            },
            required=['source_tokens'],
            page_rank=10,
            tags=['designsystem', 'tokens', 'compile'],
        ),
        _read_cap(
            name='derive',
            description='tokens.css → design-tokens.json（分层/血缘/评分）+ tailwind-v4.css（@theme 映射）。',
            properties={'tokens_css': {'type': 'string', 'minLength': 1, 'description': '四层契约 tokens.css'}},
            required=['tokens_css'],
            page_rank=11,
            tags=['designsystem', 'derive', 'tailwind'],
        ),
        _read_cap(
            name='validate',
            description='四层契约校验 + A2/B-slot 完整性 + 命名漂移 + 反模式检测 → 评分 0-100 + issues。',
            properties={
                'tokens_css': {'type': 'string', 'minLength': 1},
                'design_tokens_json': {'type': ['object', 'null'], 'description': '可选派生产物（一并校验血缘）'},
            },
            required=['tokens_css'],
            page_rank=12,
            tags=['designsystem', 'validate', 'score'],
        ),
        _read_cap(
            name='extract_components',
            description='components.html + tokens.css → components.manifest.json（组件清单，绑定 token 引用）。',
            properties={
                'components_html': {'type': 'string', 'minLength': 1},
                'tokens_css': {'type': 'string', 'minLength': 1},
            },
            required=['components_html', 'tokens_css'],
            page_rank=13,
            tags=['designsystem', 'components', 'extract'],
        ),
        # —— 云端工具（for_agent → /api/v1/designsystem/agent/*，Agent JWT 通道）——
        _write_cap(
            name='import',
            title='导入设计系统草稿',
            description='从 shadcn registry / GitHub 仓库 / 截图 → tokens.css 草稿（草稿≠最终，交分身 compile 收口）。',
            properties={
                'source': {
                    'type': 'string',
                    'enum': ['shadcn', 'github', 'screenshot', 'url'],
                    'description': '导入来源',
                },
                'ref': {
                    'type': 'string',
                    'minLength': 1,
                    'description': 'registry item URL / owner/repo[#branch] / 页面 URL',
                },
            },
            required=['source', 'ref'],
            page_rank=14,
            tags=['designsystem', 'import', 'write'],
        ),
        _write_cap(
            name='save',
            title='存设计系统（落一版）',
            description='组装完整 bundle → 写云端表 + 落一版 revision + 注册供下游 picker（出一版可回滚）。',
            properties={
                'design_system_id': {'type': ['integer', 'null'], 'description': '存量 id（null=新建）'},
                'slug': {'type': 'string', 'minLength': 1, 'description': 'owner 内唯一短名'},
                'name': {'type': 'string', 'minLength': 1, 'description': '展示名'},
                'content': {
                    'type': 'object',
                    'description': '四层 token 契约产物（tokens_css + 派生 + design_md + 组件 + 评分报告）',
                },
                'category': {'type': ['string', 'null'], 'description': '分类（saas/editorial/...）'},
                'score': {'type': ['integer', 'null'], 'minimum': 0, 'maximum': 100},
                'grade': {'type': ['string', 'null']},
            },
            required=['slug', 'name', 'content'],
            page_rank=15,
            tags=['designsystem', 'save', 'write'],
        ),
        # —— 读类（for_agent，无 scope）——
        _read_cap(
            name='list',
            description='列出分身可见的设计系统（builtin ∪ owner ∪ 企业 ∪ 共享）。',
            properties={
                'category': {'type': ['string', 'null'], 'description': '按分类过滤（可选）'},
            },
            required=[],
            page_rank=16,
            tags=['designsystem', 'list', 'read'],
        ),
        _read_cap(
            name='get',
            description='取一套设计系统详情（含当前版本完整内容：tokens/派生/评分报告/组件/DESIGN.md）。',
            properties={'design_system_id': {'type': 'integer', 'minimum': 1}},
            required=['design_system_id'],
            page_rank=17,
            tags=['designsystem', 'get', 'read'],
        ),
        _read_cap(
            name='check_scenes',
            description='自查组件画廊场景覆盖：required_scenes × 当前 components.html 实检标记 → 逐场景'
            '「已配齐 X/Y · 缺哪几件 + 怎么补」。required_scenes 只是要求、不等于已配齐，完成前调它核实。',
            properties={
                'design_system_id': {
                    'type': ['integer', 'null'],
                    'description': '自查已存设计系统（缺省=对内联 HTML dry-run）',
                },
                'components_html': {'type': ['string', 'null'], 'description': '可选：直接检测这段草稿 HTML'},
                'required_scenes': {
                    'type': ['array', 'null'],
                    'items': {'type': 'string'},
                    'description': '可选：覆盖要求',
                },
            },
            required=[],
            page_rank=18,
            tags=['designsystem', 'scenes', 'check', 'read'],
        ),
    ],
    # 方案 A：本地工具不进 tools[]（走 hasn-mcp source=Local / for_agent，bootstrap 发现）。
    'tools': [],
    'events': [],
    'reverse_invoke': {'supported': False},
    'ui_interfaces': [{'face': 'ui', 'transport': 'daemon_direct'}],
    'publisher': {'developer_id': 'huanxing-first-party', 'publisher_type': 'first_party', 'name': '唤星'},
    'endpoints': {'tool_endpoint': None, 'event_endpoint': None, 'component_origin': 'loopback'},
    'audit': {'fields': list(_AUDIT_FIELDS)},
}


def build_designsystem_app() -> App:
    """designsystem App（local_tool / 内联路由 / 非自动挂载）。

    - ``install_policy='manual'``：本期（P7）只铸 scope + 注册 manifest，**不自动挂载**到工作台；
      用户可见的启动入口随 P8 的 webui 工作台 + ``hasn_app_catalog`` 目录行落地（同 growth/creator
      先例）。注册到 app_catalog_registry 是 ``validate_manifest`` 的硬前置（否则 workbench_app_not_found）。
    - ``collaboration_mode='none'`` / ``scope=('personal',)`` 必须与 manifest 对齐（validate 闸门：
      ``workspace_scope ⊆ scope`` 且 ``collaboration_mode`` 相等）。
    - ``execution_mode='local_tool'`` / ``ui_kind=None``：原生 webui（非 embedded sidecar）。

    延迟导入 App 避免循环依赖（app_catalog_registry 模块加载即 default() 反向引用本模块）。
    """
    from backend.app.hasn.service.app_catalog_registry import App

    return App(
        id='designsystem',
        name='设计系统',
        icon='brand-designsystem',
        description='给分身一把确定性的设计契约刀——编译、派生、校验设计 token 与组件，导入导出一气呵成。',
        scope=('personal',),
        collaboration_mode='none',
        entry_route='/apps/designsystem',
        install_policy='manual',
        execution_mode='local_tool',
        ui_kind=None,
    )
