"""唤星自研演示文稿（deck，模块 17）AI-Native 内置 manifest + App 声明。

设计事实源：
- docs/hasn-node设计文档/17-演示文稿系统/07-安全权限与平台接入.md §6（注册/manifest/工作台接入）
- docs/hasn-node设计文档/14-AI-Native应用平台/16-应用目录与商业化管理统一设计.md（catalog/manifest 四正交概念）
- docs/hasn-node设计文档/14-AI-Native应用平台/15-AI-Native应用命名空间与目录约定.md（ADR-15：manifest 与应用代码同目录）

deck 是**最轻形态**的 AI-Native 应用：引擎分布在 daemon(Rust) + webui(TS) + 按需 headless 渲染，
无独立重 sidecar、无 Python/ML/LibreOffice、无常驻进程（[06] §7）。deck 是唯一默认演示文稿应用
（早期接入的 Presenton/embedded_desktop sidecar 已彻底删除，deck 接管演示文稿能力，[07] §7）。

⚠️ 方案 A：`tools[]` **置空数组**——`hasn.deck.*` 工具数据面在本地
（hasn-mcp `built_in_deck_tools()`，`source=Local`/`ExecutionLocation::Local`），不经云端 Runtime
Gateway，故不进 `tools[]`（自造 transport 会静默过 validate_manifest 变潜伏炸弹）。工具靠 hasn-mcp
注册、bootstrap 发现；`capabilities[]` 只承载**发现/权限元数据**控制面记录（不参与 `_dispatch_tool` 路由）。

⚠️ scope 与落地工具对齐（hasn-node `crates/hasn-mcp/src/deck.rs`，P3 已落地）：写类 8 工具统一
`deck:manage`（出厂 Ask，owner 三态可覆盖）；读类 4 工具无 required_scopes。**不要**写成早期设计稿
[07] §1 的 `deck:read/write/export`——那是未落地的占位，真相以 `BaseTool.capability_scopes()` 为准。

⚠️ execution_mode：[07] §6.4 设计标签为 `native_huanxing`，落到 catalog 枚举
（`cloud/embedded_desktop/local_tool`，见 `hasn_app_catalog.execution_mode` 列注释）取 **`local_tool`**
（本地工具驱动 + 原生 webui UI，非 cloud 执行、非重 sidecar）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from backend.app.hasn.service.app_catalog_registry import App


# 与既有 AI-Native 审计共表的字段集。
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

# 写类工具统一 scope（落地真相：deck.rs MANAGE_SCOPE）。
_MANAGE_SCOPE = 'deck:manage'


def _read_cap(
    *,
    name: str,
    description: str,
    properties: dict,
    required: list[str],
    page_rank: int,
    tags: list[str],
) -> dict:
    """读类能力（无 required_scopes，低风险，无需确认）——与 deck.rs 读类工具空 scope 对齐。"""
    short = name.split('.', 1)[-1]
    return {
        'capability_id': f'deck.{name}.capability',
        'name': short,
        'description': description,
        'tool_id': f'deck.{name}',
        'mcp_name': f'hasn.deck.{name}',
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
        # D5：审计经 for_agent 上云；本地 AuthorizationAudit 始终先产生（[07] §3）。
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
    *,
    name: str,
    title: str,
    description: str,
    properties: dict,
    required: list[str],
    page_rank: int,
    tags: list[str],
    risk_level: str = 'medium',
) -> dict:
    """写类能力（required_scopes=deck:manage；出厂 Allow，owner 可设 ask/deny override，16-doc D-v3-1）。"""
    return {
        'capability_id': f'deck.{name}.capability',
        'name': title,
        'description': description,
        'tool_id': f'deck.{name}',
        'mcp_name': f'hasn.deck.{name}',
        'required_scopes': [_MANAGE_SCOPE],
        'workspace_roles': ['owner'],
        'input_schema': {
            'type': 'object',
            'properties': properties,
            'required': required,
            'additionalProperties': False,
        },
        'output_schema': {'type': 'object'},
        'risk_level': risk_level,
        # 出厂全 Allow，无需确认（16-doc D-v3-1）；risk_level 仅 UI 提示，owner 可设 ask/deny override。
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


# —— 页对象（page.write_batch / page.write 的子结构）——
_PAGE_ITEM_SCHEMA = {
    'type': 'object',
    'properties': {
        'position': {'type': 'integer', 'minimum': 0, 'description': '页序号（0 基，按序 upsert）'},
        'html': {'type': 'string', 'description': '页面 HTML（16:9 自包含骨架，禁外链）'},
        'title': {'type': ['string', 'null'], 'description': '页标题（可选）'},
        'notes': {'type': ['string', 'null'], 'description': '演讲者备注（可选）'},
    },
    'required': ['position', 'html'],
    'additionalProperties': False,
}

# —— 大纲项（outline.set 的子结构）——
_OUTLINE_ITEM_SCHEMA = {
    'type': 'object',
    'properties': {
        'title': {'type': 'string', 'description': '该页标题/要点'},
        'summary': {'type': ['string', 'null'], 'description': '该页内容摘要（可选）'},
    },
    'required': ['title'],
    'additionalProperties': False,
}


DECK_AI_NATIVE_MANIFEST: dict[str, Any] = {
    'app_id': 'deck',
    # 「可搜索域目录」：namespace 关键词 → 一句话（云端 tool.search 描述自动汇聚，agent 据此选关键词搜该域工具）。
    'domain_summary': {'deck': '演示文稿（创建/编辑/大纲/导出）'},
    'version': '1.0.0',
    'workspace_scope': ['personal'],
    'collaboration_mode': 'none',
    'project_aware': True,
    'project_required': False,
    'project_integration': 'project_aware',
    # catalog 枚举 local_tool（[07] §6.4 设计标签 native_huanxing 映射至此）。
    'execution_mode': 'local_tool',
    # 本地工具数据面（hasn-mcp source=Local），不经云端 Runtime Gateway。
    'transport_mode': 'local',
    # 通知发布能力声明（统一通知设计 §7 / [07] §6.3）：生成完成 / 导出就绪经 Agent JWT 通道发卡给主人。
    'notifications': {
        'emit': {
            'categories': ['app'],
            'card_message': True,
            'display_name': '演示文稿',
        }
    },
    # 资源描述符（doc31 §2，RC-P0）：声明 deck 产出的资源如何寻址/打开/组卡。完成卡、工作会话资源栏、
    # URI 解析、详情跳转全从这份声明派生（deck 是首个试点，验证契约通用性）。演示文稿走独立原生窗口。
    'resources': [
        {
            'resource_kind': 'deck.presentation',
            'uri_domain': 'deck',  # → hasn://deck/{server_id}（doc08 §3 已登记 native_window 域）
            'open': {'mode': 'native_window', 'window': 'deck'},
            'card': {'verb': '演示文稿', 'action_label': '打开演示文稿'},
            'artifact_kind': 'resource',
        }
    ],
    'capabilities': [
        _write_cap(
            name='create',
            title='新建演示文稿',
            description='新建空演示文稿（可带 title/topic/language/style）。返回 deck_id。',
            properties={
                'title': {'type': ['string', 'null'], 'description': '标题（可选）'},
                'topic': {'type': ['string', 'null'], 'description': '主题（可选）'},
                'language': {'type': ['string', 'null'], 'description': '语言（可选）'},
                'style_profile_id': {'type': ['string', 'null'], 'description': '引用样式 id（见 style.list）'},
                'platform_project_id': {
                    'type': ['string', 'null'],
                    'description': '挂靠的平台项目 UUID（可选；省略时自动继承当前工作会话项目）',
                },
            },
            required=[],
            page_rank=10,
            tags=['deck', 'presentation', 'create'],
        ),
        _read_cap(
            name='get',
            description='取 deck 详情（含全部页摘要 / outline / design_contract）。',
            properties={'deck_id': {'type': 'string', 'minLength': 1}},
            required=['deck_id'],
            page_rank=11,
            tags=['deck', 'get', 'read'],
        ),
        _read_cap(
            name='list',
            description='列出当前 owner 的 Deck；默认不收窄，可显式按平台项目筛选。',
            properties={
                'platform_project_id': {
                    'type': ['string', 'null'],
                    'description': '显式筛选的平台项目 UUID；省略时返回全部',
                }
            },
            required=[],
            page_rank=12,
            tags=['deck', 'list', 'read'],
        ),
        _write_cap(
            name='outline.set',
            title='写/改大纲',
            description='写 / 改 deck 大纲（OutlineItem[]）+ 可选 design_contract。先定大纲再逐页写 HTML。',
            properties={
                'deck_id': {'type': 'string', 'minLength': 1},
                'pages': {'type': 'array', 'items': _OUTLINE_ITEM_SCHEMA, 'description': '大纲项数组'},
                'design_contract': {
                    'type': ['object', 'null'],
                    'description': '统一视觉契约 DesignContract（配色/字体/版式约束，可选）',
                },
            },
            required=['deck_id', 'pages'],
            page_rank=13,
            tags=['deck', 'outline', 'write'],
        ),
        _write_cap(
            name='page.write_batch',
            title='批量写页',
            description='批量写多页 HTML（主力，粗粒度）。逐页骨架校验；不合格页进 rejected 让你重写，合格页落盘。',
            properties={
                'deck_id': {'type': 'string', 'minLength': 1},
                'pages': {'type': 'array', 'items': _PAGE_ITEM_SCHEMA, 'description': '按序写入的页数组'},
            },
            required=['deck_id', 'pages'],
            page_rank=14,
            tags=['deck', 'page', 'write', 'batch'],
        ),
        _write_cap(
            name='page.write',
            title='写单页',
            description='写单页 HTML（按 position 序数 upsert，幂等）。骨架不合格则进 rejected。',
            properties={
                'deck_id': {'type': 'string', 'minLength': 1},
                'position': {'type': 'integer', 'minimum': 0},
                'html': {'type': 'string', 'minLength': 1},
                'title': {'type': ['string', 'null']},
                'notes': {'type': ['string', 'null']},
            },
            required=['deck_id', 'position', 'html'],
            page_rank=15,
            tags=['deck', 'page', 'write'],
        ),
        _write_cap(
            name='page.edit',
            title='编辑页',
            description='编辑某页（按 page_id 局部更新；仅传需要改的字段）。',
            properties={
                'deck_id': {'type': 'string', 'minLength': 1},
                'page_id': {'type': 'string', 'minLength': 1},
                'html': {'type': ['string', 'null']},
                'title': {'type': ['string', 'null']},
                'notes': {'type': ['string', 'null']},
            },
            required=['deck_id', 'page_id'],
            page_rank=16,
            tags=['deck', 'page', 'edit'],
        ),
        _write_cap(
            name='page.delete',
            title='删除页',
            description='删除某页（删后自动重排剩余页序）。',
            properties={
                'deck_id': {'type': 'string', 'minLength': 1},
                'page_id': {'type': 'string', 'minLength': 1},
            },
            required=['deck_id', 'page_id'],
            page_rank=17,
            tags=['deck', 'page', 'delete'],
            risk_level='high',
        ),
        _write_cap(
            name='page.reorder',
            title='重排页序',
            description='重排页序：给定按目标顺序排列的全部页 id。',
            properties={
                'deck_id': {'type': 'string', 'minLength': 1},
                'page_ids': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': '按目标顺序排列的全部页 id',
                },
            },
            required=['deck_id', 'page_ids'],
            page_rank=18,
            tags=['deck', 'page', 'reorder'],
        ),
        _write_cap(
            name='finalize',
            title='收尾演示文稿',
            description=(
                '写完最后一页后调一次，把演示文稿标记为「已完成」。首次收尾时系统自动给主人发一张'
                '「演示文稿做好了」卡片（分身不用、也不该自己发卡）。可带 summary 一句话概述；幂等，重复调不重复发卡。'
            ),
            properties={
                'deck_id': {'type': 'string', 'minLength': 1},
                'summary': {'type': ['string', 'null'], 'description': '一句话概述这份演示文稿（作为卡片描述，可选）'},
            },
            required=['deck_id'],
            page_rank=19,
            tags=['deck', 'finalize', 'complete'],
        ),
        _write_cap(
            name='delete',
            title='删除演示文稿',
            description='删除演示文稿（软删，可同步撤销）。',
            properties={'deck_id': {'type': 'string', 'minLength': 1}},
            required=['deck_id'],
            page_rank=20,
            tags=['deck', 'delete', 'manage'],
            risk_level='high',
        ),
        _read_cap(
            name='style.list',
            description='列可复用样式（内置 + 本 owner 自定义 StyleProfile）。',
            properties={},
            required=[],
            page_rank=20,
            tags=['deck', 'style', 'read'],
        ),
        _read_cap(
            name='style.get',
            description='取单个样式（StyleProfile）：含 design_contract + style_prompt。',
            properties={'style_id': {'type': 'string', 'minLength': 1, 'description': '样式 id（slug）'}},
            required=['style_id'],
            page_rank=21,
            tags=['deck', 'style', 'read'],
        ),
    ],
    # 方案 A：本地工具不进 tools[]（走 hasn-mcp source=Local，bootstrap 发现）。
    'tools': [],
    'events': [],
    'reverse_invoke': {'supported': False},
    'ui_interfaces': [{'face': 'ui', 'transport': 'daemon_direct'}],
    'publisher': {'developer_id': 'huanxing-first-party', 'publisher_type': 'first_party', 'name': '唤星'},
    'endpoints': {'tool_endpoint': None, 'event_endpoint': None, 'component_origin': 'loopback'},
    'audit': {'fields': list(_AUDIT_FIELDS)},
}


def build_deck_app() -> App:
    """构造 deck 的 App（[07] §6.2）。延迟导入避免循环依赖。

    deck 是唯一默认演示文稿应用：取 ``install_policy='auto'``（注册即自动挂载；早期接入的 Presenton
    已彻底删除，不再有两个「演示文稿」并存的问题）。UI 为 webui 原生路由（``ui_kind=None`` 内联导航至
    ``entry_route``，同 knowledge/community），非 sidecar、非独立窗口。
    """
    from backend.app.hasn.service.app_catalog_registry import App

    return App(
        id='deck',
        name='演示文稿',
        icon='brand-deck',
        description='一句话生成专业演示文稿——分身替你搭框架、配图表、精修排版，本地预览随时导出 PPT。',
        scope=('personal',),
        entry_route='/apps/deck',
        # 唯一默认演示文稿应用：自动挂载（Presenton 已删除，[07] §7）。
        install_policy='auto',
        collaboration_mode='none',
        project_aware=True,
        project_required=False,
        requires_role=None,
        execution_mode='local_tool',
        # 工作台内路由（内联导航至 entry_route），非新窗口/非 iframe。
        ui_kind=None,
        window_url=None,
        window_origin=None,
    )
