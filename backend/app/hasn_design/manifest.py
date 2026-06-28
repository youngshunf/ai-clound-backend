"""唤星矢量设计应用（design，源自 OpenPencil，模块 14 doc27）AI-Native 内置 manifest。

设计事实源：
- docs/hasn-node设计文档/14-AI-Native应用平台/27-OpenPencil矢量设计工具接入设计(本地sidecar·画布即应用).md
  §5.3/§5.4/§5.9（工具/scope/数据模型）
- docs/hasn-node设计文档/14-AI-Native应用平台/实施/27-OpenPencil矢量设计工具接入实施清单.md P3-B（OP-P3-5）
- docs/hasn-node设计文档/14-AI-Native应用平台/16-应用目录与商业化管理统一设计.md（catalog/manifest 四正交概念）

design 是 **本地 sidecar · 画布即应用** 的矢量设计工具（Figma/Pencil 替代）：OpenPencil 的真实 web 画布
编辑器由 daemon 管理、独立窗口加载（经 daemon 反代）；分身经 `hasn.design.*` 本地工具（DesignBroker→pen-mcp）
在主人正看的画布上实时出设计（海报/UI 稿/插画/图形/Logo 排版），产物经 export 落 hasn://asset + hasn_artifacts。
引擎与 UI 一字不重写，唤星只做桥接 + 加固 + 换肤（福仔 2026-06-27 拍板：本地 sidecar + 桥成 hasn.design.*）。

⚠️ 方案 A（同 deck/designsystem/film/reel）：`tools[]` **置空数组**——`hasn.design.*` 工具数据面在本地
（hasn-mcp `crates/hasn-mcp/src/design.rs`，source=Local，经进程内 DesignBroker 直达本机 OpenPencil sidecar），
**不经云端 Runtime Gateway `_dispatch_tool`**。故不进 `tools[]`（自造 transport 会静默过 validate_manifest
变潜伏炸弹）；`capabilities[]` 只承载发现/权限元数据控制面记录。

⚠️ scope 与落地工具对齐（hasn-node `crates/hasn-mcp/src/design.rs` `capability_scopes()` + `scopes.py`，OP-P3-A 待落）：
读类（get/get_selection/read_nodes/find_empty_space/get_design_prompt/export）`design:read`（出厂 Allow，
§5.3 表 export=design:read）；写类（batch_design/skeleton/content/refine/节点增改/set_variables/set_themes）
`design:write`（创作类出厂 Allow——画布迭代不花算力；破坏性 delete_node/replace_node 仍 `design:write` 但
human_confirmation=True 出厂 Ask，§5.3 note）；代码生成（codegen）`design:codegen`（出厂 Allow）。
跨仓零漂移只比**管理类**（非 `:read`）键：{design:write, design:codegen}
（见 test_design_management_scopes_match_cross_repo_contract）。

⚠️ execution_mode：catalog 枚举（`cloud/embedded_desktop/local_tool`）取 **`local_tool`**（本地工具驱动 +
sidecar 本机执行 + OpenPencil 真实画布 UI）。UI 载体：`ui_interfaces` 标 `window='standalone'`——编辑器**独立窗口**
打开（CanvasKit 重渲染 + 画布覆盖层不嵌主壳，福仔 2026-06-27 拍板，§5.5），经 daemon 反代加载，非主窗口 iframe。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.app.hasn.service.app_catalog_registry import App

# 与既有 AI-Native 审计共表的字段集（同 deck/designsystem/film/reel）。
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

_READ_SCOPE = 'design:read'
_WRITE_SCOPE = 'design:write'
_CODEGEN_SCOPE = 'design:codegen'

# 派发设计分身的业务提示词（catalog.work_session_system_prompt 出厂源 + 工作会话注入，§5.9-3）。
# 教分身：项目已备好空白画布 → 读现状 → 按 brief 用 batch_design/分层 skeleton→content→refine 真出设计
# → 破坏性操作先确认 → 成品实时存进项目（主人在编辑器内查看/导出）→ 文案/品牌/方案定稿摊给主人确认。
# app_catalog_service 导入此常量作 _CATALOG_AGENT_DEFAULTS['design'] 的提示词（单一事实源，序列化时
# 直接取此值故为读时权威）。注意：hasn.design.* 工具面没有 open_document（新项目 daemon 已预写空 .op），
# 与 daemon domains/design/dispatch.rs 的内联业务提示词保持同义。
DESIGN_BUSINESS_PROMPT = (
    '你是矢量设计应用的执行分身：在主人的设计画布上把需求做成专业的矢量设计'
    '（海报/UI 稿/插画/图形/Logo 排版）。本次项目已备好空白画布，直接动手——用 get/get_selection 读现状；'
    '动手前先用 get_design_prompt 取设计知识自我增益。按需求选路径——简单图用 batch_design 一次成型，'
    '复杂稿走分层 skeleton→content→refine 逐步推进；用 insert/update/move/copy 与 set_variables/set_themes 精修。'
    '破坏性操作（delete/replace）先与主人确认。你画的内容会实时存进本次项目，主人在设计应用里打开本项目即可'
    '查看、继续编辑并导出（export 需主人已打开实时画布；无实时画布时不要假装导出成品，如实说明成品已存进项目）。'
    '只调用 hasn.design.* 工具，文案/品牌/方案定稿摊给主人确认，零 fake，失败如实报错。'
)


def _read_cap(*, name: str, description: str, properties: dict, required: list[str], page_rank: int) -> dict:
    """读类能力（design:read；读画布/取设计知识/导出渲染结果，出厂 Allow）——对齐 design.rs 读类工具。"""
    short = name.split('.', 1)[-1]
    return {
        'capability_id': f'design.{name}.capability',
        'name': short,
        'description': description,
        'tool_id': f'design.{name}',
        'mcp_name': f'hasn.design.{name}',
        'required_scopes': [_READ_SCOPE],
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
            'tags': ['design', 'vector', 'canvas'],
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
    scope: str = _WRITE_SCOPE,
    ask: bool = False,
) -> dict:
    """写/出码类能力（required_scopes=design:write 或 design:codegen）。

    出厂三态：创作类 ``ask=False`` → human_confirmation.required=False（**Allow**——画布迭代不花算力、不出片，
    分身可随便画，对齐 studio:write 哲学 + §5.3 note）；破坏性（delete_node/replace_node）``ask=True`` →
    human_confirmation.required=True（**Ask**——破坏性默认询问）。human_confirmation 仅 UI 提示，
    owner 可经 capability_modes 三态覆盖。
    """
    return {
        'capability_id': f'design.{name}.capability',
        'name': title,
        'description': description,
        'tool_id': f'design.{name}',
        'mcp_name': f'hasn.design.{name}',
        'required_scopes': [scope],
        'workspace_roles': ['owner'],
        'input_schema': {
            'type': 'object',
            'properties': properties,
            'required': required,
            'additionalProperties': False,
        },
        'output_schema': {'type': 'object'},
        'risk_level': 'medium' if ask else 'low',
        'human_confirmation': {'required': ask},
        'result_writeback': ['agent_message', 'audit'],
        'discovery': {
            'exposure': 'on_demand',
            'summary': description,
            'tags': ['design', 'vector', 'canvas'],
            'schema_visibility': 'authorized_agents',
            'default_page_rank': page_rank,
        },
    }


# 项目上下文：design 工具都在某个设计项目（= 一个 OpenPencil 文档 = 一个 .op）上操作。
# daemon file 模式据 project_id 解析 op_path 喂 pen-mcp（§5.9-0 1:1:1）。
_PROJECT_ID = {
    'project_id': {'type': 'string', 'minLength': 1, 'description': '设计项目 id（= 一个 OpenPencil 文档/.op）'}
}

DESIGN_AI_NATIVE_MANIFEST = {
    'app_id': 'design',
    # 「可搜索域目录」：namespace 关键词 → 一句话（云端 tool.search 描述自动汇聚，agent 据此选关键词搜该域工具）。
    'domain_summary': {'design': '矢量设计（画布出图：海报/UI 稿/插画/图形/Logo）'},
    'version': '1.0.0',
    'workspace_scope': ['personal'],
    'collaboration_mode': 'none',
    'execution_mode': 'local_tool',
    'transport_mode': 'local',
    'notifications': {
        'emit': {
            'categories': ['app'],
            'card_message': True,
            'display_name': '矢量设计',
        }
    },
    'capabilities': [
        # —— 读类（design:read，出厂 Allow）——
        _read_cap(
            name='get',
            description='读取设计画布的节点树/文档（batch_get），了解当前画布结构。',
            properties=dict(_PROJECT_ID),
            required=['project_id'],
            page_rank=10,
        ),
        _read_cap(
            name='get_selection',
            description='读取画布当前选中的节点（分身先看主人选了什么再改）。',
            properties=dict(_PROJECT_ID),
            required=['project_id'],
            page_rank=11,
        ),
        _read_cap(
            name='read_nodes',
            description='按节点 id 读取指定节点的详细属性。',
            properties={
                **_PROJECT_ID,
                'node_ids': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'minItems': 1,
                    'description': '节点 id 列表',
                },
            },
            required=['project_id', 'node_ids'],
            page_rank=12,
        ),
        _read_cap(
            name='find_empty_space',
            description='在画布上找一块空白区域放新内容（避免与已有节点重叠）。',
            properties={
                **_PROJECT_ID,
                'width': {'type': ['number', 'null'], 'minimum': 1, 'description': '需要的宽度'},
                'height': {'type': ['number', 'null'], 'minimum': 1, 'description': '需要的高度'},
            },
            required=['project_id'],
            page_rank=13,
        ),
        _read_cap(
            name='get_design_prompt',
            description='取分段设计知识 prompt（schema/layout/roles/icons…）供分身自我增益，先学再画。',
            properties={
                'section': {
                    'type': ['string', 'null'],
                    'description': '知识分段（schema/layout/roles/icons…），不传取总览',
                },
            },
            required=[],
            page_rank=14,
        ),
        _read_cap(
            name='export',
            description='导出截图/SVG → 私有桶 → hasn://asset + hasn_artifacts（产物回流，§5.3 design:read）。',
            properties={
                **_PROJECT_ID,
                'format': {
                    'type': ['string', 'null'],
                    'description': '导出格式：png（截图，需画布在跑）/ svg（矢量，file 模式可出）',
                },
            },
            required=['project_id'],
            page_rank=15,
        ),
        # —— 写类·创作（design:write，出厂 Allow——画布迭代不花算力）——
        _write_cap(
            name='batch_design',
            title='一次成型出设计（DSL）',
            description='单发 DSL 一次性生成完整设计（小图/简单稿首选）：传设计意图 DSL，引擎自动布局成型。',
            properties={
                **_PROJECT_ID,
                'design': {
                    'type': 'string',
                    'minLength': 1,
                    'description': '设计 DSL（描述要画什么，引擎解析 + 自动布局）',
                },
                'section': {'type': ['string', 'null'], 'description': '作用到哪个区域/页面（不传作用整文档）'},
            },
            required=['project_id', 'design'],
            page_rank=20,
        ),
        _write_cap(
            name='skeleton',
            title='分层①搭结构',
            description='复杂稿分层设计第一步：先搭骨架结构（区块/栅格/层级），不填内容。',
            properties={
                **_PROJECT_ID,
                'brief': {'type': 'string', 'minLength': 1, 'description': '设计需求/意图'},
            },
            required=['project_id', 'brief'],
            page_rank=21,
        ),
        _write_cap(
            name='content',
            title='分层②填内容',
            description='复杂稿分层设计第二步：往骨架里填具体内容（文本/图形/组件），可分区多次调用聚焦上下文。',
            properties={
                **_PROJECT_ID,
                'section': {'type': ['string', 'null'], 'description': '本次填充的区块/区域（聚焦上下文）'},
                'brief': {'type': ['string', 'null'], 'description': '本区块内容意图'},
            },
            required=['project_id'],
            page_rank=22,
        ),
        _write_cap(
            name='refine',
            title='分层③精修',
            description='复杂稿分层设计第三步：精修对齐/间距/配色/层级，把设计打磨到对客可用。',
            properties={
                **_PROJECT_ID,
                'instruction': {'type': ['string', 'null'], 'description': '精修指令（不传按设计规范自动精修）'},
            },
            required=['project_id'],
            page_rank=23,
        ),
        _write_cap(
            name='insert_node',
            title='插入节点',
            description='在画布插入一个新节点（形状/文本/图片/组等）。',
            properties={
                **_PROJECT_ID,
                'node': {'type': 'object', 'description': '节点定义（类型/属性/坐标）'},
            },
            required=['project_id', 'node'],
            page_rank=24,
        ),
        _write_cap(
            name='update_node',
            title='更新节点',
            description='更新已有节点的属性（位置/尺寸/样式/文本…）。',
            properties={
                **_PROJECT_ID,
                'node_id': {'type': 'string', 'minLength': 1, 'description': '目标节点 id'},
                'patch': {'type': 'object', 'description': '要更新的属性'},
            },
            required=['project_id', 'node_id', 'patch'],
            page_rank=25,
        ),
        _write_cap(
            name='move_node',
            title='移动节点',
            description='移动节点到新坐标/新父节点。',
            properties={
                **_PROJECT_ID,
                'node_id': {'type': 'string', 'minLength': 1, 'description': '目标节点 id'},
                'x': {'type': ['number', 'null'], 'description': '新 x 坐标'},
                'y': {'type': ['number', 'null'], 'description': '新 y 坐标'},
            },
            required=['project_id', 'node_id'],
            page_rank=26,
        ),
        _write_cap(
            name='copy_node',
            title='复制节点',
            description='复制一个节点（含子树）到画布。',
            properties={
                **_PROJECT_ID,
                'node_id': {'type': 'string', 'minLength': 1, 'description': '要复制的节点 id'},
            },
            required=['project_id', 'node_id'],
            page_rank=27,
        ),
        _write_cap(
            name='set_variables',
            title='设置设计变量',
            description='设置/更新设计变量（颜色/尺寸/字体等可复用 token）。',
            properties={
                **_PROJECT_ID,
                'variables': {'type': 'object', 'description': '变量键值'},
            },
            required=['project_id', 'variables'],
            page_rank=28,
        ),
        _write_cap(
            name='set_themes',
            title='设置主题',
            description='设置/切换设计主题（主题预设）。',
            properties={
                **_PROJECT_ID,
                'themes': {'type': 'object', 'description': '主题定义/预设名'},
            },
            required=['project_id', 'themes'],
            page_rank=29,
        ),
        # —— 写类·破坏性（design:write，出厂 Ask——默认询问）——
        _write_cap(
            name='delete_node',
            title='删除节点（破坏性）',
            description='从画布删除一个节点（含子树）。破坏性操作，出厂默认询问主人。',
            properties={
                **_PROJECT_ID,
                'node_id': {'type': 'string', 'minLength': 1, 'description': '要删除的节点 id'},
            },
            required=['project_id', 'node_id'],
            page_rank=30,
            ask=True,
        ),
        _write_cap(
            name='replace_node',
            title='替换节点（破坏性）',
            description='用新节点替换已有节点。破坏性操作，出厂默认询问主人。',
            properties={
                **_PROJECT_ID,
                'node_id': {'type': 'string', 'minLength': 1, 'description': '被替换的节点 id'},
                'node': {'type': 'object', 'description': '替换成的新节点定义'},
            },
            required=['project_id', 'node_id', 'node'],
            page_rank=31,
            ask=True,
        ),
        # —— 出码类（design:codegen，出厂 Allow——确定性出码）——
        _write_cap(
            name='codegen',
            title='设计稿出代码',
            description='设计稿生成多平台代码（plan→submit→assemble，确定性出码）。可后置（落地 design.rs）。',
            properties={
                **_PROJECT_ID,
                'platform': {'type': ['string', 'null'], 'description': '目标平台（react/vue/html/swiftui…）'},
            },
            required=['project_id'],
            page_rank=40,
            scope=_CODEGEN_SCOPE,
        ),
    ],
    # 方案 A：本地工具不进 tools[]（走 hasn-mcp source=Local，bootstrap 发现）。
    'tools': [],
    'events': [],
    'reverse_invoke': {'supported': False},
    # UI 载体：独立窗口（standalone）经 daemon 反代加载 OpenPencil 真实画布编辑器（§5.5，福仔拍板，非主窗口 iframe）。
    'ui_interfaces': [{'face': 'ui', 'transport': 'daemon_direct', 'window': 'standalone'}],
    'publisher': {'developer_id': 'huanxing-first-party', 'publisher_type': 'first_party', 'name': '唤星'},
    'endpoints': {'tool_endpoint': None, 'event_endpoint': None, 'component_origin': 'loopback'},
    'audit': {'fields': list(_AUDIT_FIELDS)},
}


def build_design_app() -> App:
    """design App（local_tool / 项目管理 + 派发台路由 / 非自动挂载）。

    - ``install_policy='manual'``：design 是本地 sidecar 应用（OpenPencil 引擎随桌面端下发），按需装，
      不自动挂载到工作台（``default_mount=FALSE`` 由 install_policy 推导）。注册到 app_catalog_registry 是
      ``validate_manifest`` 的硬前置（否则 workbench_app_not_found）。
    - ``collaboration_mode='none'`` / ``scope=('personal',)`` 必须与 manifest 对齐（validate 闸门）。
    - ``execution_mode='local_tool'`` / ``ui_kind=None``：主窗口 `/apps/design` 是项目管理 + 派发台（原生 webui）；
      编辑器经 daemon 反代在**独立窗口**加载 OpenPencil 真画布（webui openDesignWindow，P4，非云端 App 字段承载）。
    - ``entry_route='/apps/design'``：主窗口路由（项目网格 + 派发抽屉 + 产物回流，§5.5）。

    延迟导入 App 避免循环依赖。
    """
    from backend.app.hasn.service.app_catalog_registry import App

    return App(
        id='design',
        name='矢量设计',
        icon='brand-design',
        description=(
            'AI-native 矢量设计画布（Figma/Pencil 替代，源自 OpenPencil）——分身经 hasn.design.* '
            '在主人画布上实时出设计（海报/UI 稿/插画/图形/Logo），本地 sidecar 出图、产物回流，源文件本地优先。'
        ),
        scope=('personal',),
        collaboration_mode='none',
        entry_route='/apps/design',
        install_policy='manual',
        execution_mode='local_tool',
        ui_kind=None,
    )
