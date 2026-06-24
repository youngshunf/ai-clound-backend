"""唤星统一视频引擎接入应用（studio，源自 OpenMontage，模块 14 doc22）AI-Native catalog + 内置 manifest。

设计事实源：docs/hasn-node设计文档/14-AI-Native应用平台/22-OpenMontage统一视频引擎(云服务·工具即服务)选型设计.md
§3（cloud-brokered 架构 / §3.1 数据模型）/§3.6（分享协作全复用）；实施/22（P0–P9 施工清单）。

studio 是 **cloud-brokered** AI-Native 应用（对齐 creator/finance/quant，非 reel/film 的 local_tool）：
- 分身经**云端 MCP** 调 `hasn.studio.*`（Agent MCP Key → `/api/v1/mcp/streamable` → `app_tool_loader` 投影成
  AppTool → `ai_native_runtime_gateway`（transport=gateway_internal）→ 进程内直调云端 handler →
  studio_service（落 hasn_studio PG）→ montage_engine_provider → 内网 REST 调引擎服务
  （huanxing-apps/montage-engine-service）跑真渲染/出片）。
- 产品数据权威全在唤星 PG（不变量 #4）；引擎服务无产品表，只持渲染运行态（crash-only 可重跑）。

⚠️ P3 工具面（read/write 出厂 allow；render/run_pipeline/run_tool/export 出厂 ask）：12 工具走
  gateway_internal → `app/mcp/apps/studio/studio_tool_handlers.py` → `studio_service`。
  **双重身份（§3.5）**：工具注册在云端 MCP gateway_internal，**任意应用的分身**（creator/task/workflow…）
  都能编排调 `hasn.studio.run_pipeline`，产物经 `hasn://asset/` 回填组合（零数据合并）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.app.hasn.service.app_catalog_registry import App

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

_SCOPE_READ = 'studio:read'
_SCOPE_WRITE = 'studio:write'
_SCOPE_RENDER = 'studio:render'
_SCOPE_EXPORT = 'studio:export'
_SCOPE_SHARE = 'studio:share'


def _cap(
    *,
    name: str,
    title: str,
    description: str,
    scope: str,
    properties: dict,
    required: list[str],
    page_rank: int,
    tags: list[str],
    ask: bool,
    risk_level: str = 'low',
) -> dict:
    """hasn.studio.* 能力声明。

    ``name`` 既是 capability/tool_id 扁平标识，也是 ``hasn.studio.<name>`` 后缀（本期工具名无层级）。
    出厂三态贴 scopes.py：read/write 出厂 allow（``ask=False``，human_confirmation.required=False）；
    render/run_pipeline/run_tool/export 出厂 ask（``ask=True``，花算力/外发，主人裁决）。
    """
    return {
        'capability_id': f'hasn_studio.{name}.capability',
        'name': title,
        'description': description,
        'tool_id': f'hasn_studio.{name}',
        'mcp_name': f'hasn.studio.{name}',
        'required_scopes': [scope],
        'workspace_roles': ['owner'],
        'input_schema': {
            'type': 'object',
            'properties': properties,
            'required': required,
            'additionalProperties': False,
        },
        'output_schema': {'type': 'object'},
        'risk_level': risk_level,
        'human_confirmation': {'required': ask},
        'result_writeback': ['audit', 'agent_message'],
        'discovery': {
            'exposure': 'on_demand',
            'summary': description,
            'tags': tags,
            'schema_visibility': 'authorized_agents',
            'default_page_rank': page_rank,
        },
    }


def _tool_from_cap(cap: dict) -> dict:
    """从 capability 派生 `tools[]`（transport=gateway_internal，云端进程内直调 handler）。

    ``handler`` = ``studio.<flat_name>``（tool_id 去 ``hasn_studio.`` 前缀换 ``studio.``），对应
    `ai_native_runtime_gateway._internal_handlers()` 注册表键 → `studio_tool_handlers.handle_<name>`。
    幂等性按 scope 推导：纯读（studio:read）可安全重试 → idempotent=True；写/渲染/导出 → False。
    """
    scopes = list(cap.get('required_scopes') or [])
    return {
        'tool_id': cap['tool_id'],
        'mcp_name': cap['mcp_name'],
        'transport': 'gateway_internal',
        'handler': str(cap['tool_id']).replace('hasn_studio.', 'studio.', 1),
        'required_scopes': scopes,
        'risk_level': cap['risk_level'],
        'idempotent': scopes == [_SCOPE_READ],
    }


# 14 个工具（设计 §3 / §3.5 / §3.6）。顺序即 tools[] 顺序：读(6) → 写(2) → 渲染(3) → 导出(1) → 分享/发布(2)。
_CAPABILITIES = [
    # ---------------- 读（studio:read，出厂 allow，idempotent） ----------------
    _cap(
        name='list_pipelines',
        title='列出可用视频流水线',
        description='经引擎取可用视频流水线目录（只 production；如 cinematic/montage 等，含 key/标题/说明）。只读。',
        scope=_SCOPE_READ,
        properties={},
        required=[],
        page_rank=10,
        tags=['studio', 'pipeline', 'read'],
        ask=False,
    ),
    _cap(
        name='list_projects',
        title='列出我的视频项目',
        description='列出当前主人名下的全部视频项目（owner 隔离，最近优先，含状态/默认流水线）。只读。',
        scope=_SCOPE_READ,
        properties={},
        required=[],
        page_rank=11,
        tags=['studio', 'project', 'read'],
        ask=False,
    ),
    _cap(
        name='get_project',
        title='查看视频项目详情',
        description='按 project_id 读取一个视频项目详情（含设置/默认流水线 + 其素材列表，owner 隔离）。只读。',
        scope=_SCOPE_READ,
        properties={'project_id': {'type': 'integer', 'minimum': 1, 'description': '项目 id'}},
        required=['project_id'],
        page_rank=12,
        tags=['studio', 'project', 'read'],
        ask=False,
    ),
    _cap(
        name='list_assets',
        title='列出项目素材',
        description='按 project_id 列出项目素材库（脚本/图/音/视频/字幕/配音/配乐/字体，owner 隔离）。只读。',
        scope=_SCOPE_READ,
        properties={'project_id': {'type': 'integer', 'minimum': 1, 'description': '项目 id'}},
        required=['project_id'],
        page_rank=13,
        tags=['studio', 'asset', 'read'],
        ask=False,
    ),
    _cap(
        name='list_artifacts',
        title='列出视频成品',
        description='列出视频成品（owner 隔离，可选 project_id 过滤）。序列化边界把 hasn://asset/ 换签名 URL。只读。',
        scope=_SCOPE_READ,
        properties={
            'project_id': {'type': 'integer', 'minimum': 1, 'description': '按项目过滤（可选，不传列全部）'},
        },
        required=[],
        page_rank=14,
        tags=['studio', 'artifact', 'read'],
        ask=False,
    ),
    _cap(
        name='get_render_job',
        title='读取渲染任务',
        description='按 render_job_id 读取渲染任务状态/进度/阶段/成本（owner 隔离）。非终态惰性轮询引擎落库；'
        '成功首次轮询到则物化成品（成片落私有桶 + 回流产物索引）。失败如实透传引擎真实错误（零 fake）。只读。',
        scope=_SCOPE_READ,
        properties={
            'render_job_id': {'type': 'integer', 'minimum': 1, 'description': '渲染任务 id（run_pipeline 返回）'},
        },
        required=['render_job_id'],
        page_rank=15,
        tags=['studio', 'render', 'read'],
        ask=False,
    ),
    # ---------------- 写（studio:write，出厂 allow） ----------------
    _cap(
        name='save_project',
        title='保存/更新视频项目',
        description='新建或更新视频项目（不出片、不花算力，分身可随便迭代）。新建必带 title；传 project_id 则按 '
        'owner 隔离更新。可设默认流水线/设置/封面/绑定分身/状态。',
        scope=_SCOPE_WRITE,
        properties={
            'project_id': {'type': 'integer', 'minimum': 1, 'description': '更新已存项目时传其 id；新建则不传'},
            'title': {'type': 'string', 'minLength': 1, 'description': '项目标题（新建必填）'},
            'description': {'type': 'string', 'description': '项目说明（可选）'},
            'default_pipeline_key': {'type': 'string', 'description': '项目默认流水线 key（来自 list_pipelines）'},
            'settings': {'type': 'object', 'description': '项目设置（调性/分辨率/默认参数/品牌，喂渲染缺省）'},
            'cover_asset_uri': {'type': 'string', 'description': '封面 hasn://asset/（可选）'},
            'bound_agent_id': {'type': 'string', 'description': '绑定协作分身 hasn_id（可选）'},
            'status': {'type': 'string', 'enum': ['draft', 'active', 'archived'], 'description': '项目状态（可选）'},
        },
        required=[],
        page_rank=20,
        tags=['studio', 'project', 'write'],
        ask=False,
    ),
    _cap(
        name='save_storyboard',
        title='保存分镜脚本',
        description='为项目保存分镜脚本（文本或 JSON）。存进 project.settings.storyboard（不出片、不花算力），'
        '喂 run_pipeline 缺省。',
        scope=_SCOPE_WRITE,
        properties={
            'project_id': {'type': 'integer', 'minimum': 1, 'description': '项目 id'},
            'storyboard': {
                'description': '分镜脚本（自由文本或结构化 JSON 对象/数组）',
            },
        },
        required=['project_id'],
        page_rank=21,
        tags=['studio', 'storyboard', 'write'],
        ask=False,
    ),
    # ---------------- 渲染（studio:render，出厂 ask；run_tool 同 ask=可能花钱） ----------------
    _cap(
        name='run_pipeline',
        title='跑流水线出片',
        description='复合主入口：按 (project_id, pipeline_key, input) 跑视频流水线出片（脚本→分镜→配音→合成，'
        '花算力，须主人审批）。job 式：立即返回任务 ref，用 get_render_job 轮询。可被任意应用的分身跨应用编排调用。',
        scope=_SCOPE_RENDER,
        properties={
            'project_id': {'type': 'integer', 'minimum': 1, 'description': '项目 id'},
            'pipeline_key': {'type': 'string', 'description': '流水线 key（缺省回落项目 default_pipeline_key）'},
            'input': {
                'type': 'object',
                'description': '渲染入参（props/demo/composition_id 透传引擎；脚本/素材引用/参数）',
            },
            'work_session_id': {'type': 'string', 'description': '触发的工作会话 id（续接锚点，可选）'},
        },
        required=['project_id'],
        page_rank=30,
        tags=['studio', 'render', 'pipeline'],
        ask=True,
        risk_level='medium',
    ),
    _cap(
        name='render',
        title='直接渲染',
        description='较低层直渲染：(project_id, props?|demo?, composition_id?, pipeline_key?) → 提交渲染（花算力，'
        '须主人审批）。job 式：立即返回任务 ref，用 get_render_job 轮询。',
        scope=_SCOPE_RENDER,
        properties={
            'project_id': {'type': 'integer', 'minimum': 1, 'description': '项目 id'},
            'props': {'type': 'object', 'description': '合成入参（与 demo 二选一）'},
            'demo': {'type': 'string', 'description': '内置 demo-props 名（与 props 二选一，自检用）'},
            'composition_id': {'type': 'string', 'description': '合成 id（可选）'},
            'pipeline_key': {'type': 'string', 'description': '流水线 key（缺省回落项目 default_pipeline_key）'},
            'work_session_id': {'type': 'string', 'description': '触发的工作会话 id（可选）'},
        },
        required=['project_id'],
        page_rank=31,
        tags=['studio', 'render'],
        ask=True,
        risk_level='medium',
    ),
    _cap(
        name='run_tool',
        title='调用引擎原子工具',
        description='透传调用引擎的原子工具（创作段：配音/图像/字幕等，外部 provider 可能花钱，须主人审批）。',
        scope=_SCOPE_RENDER,
        properties={
            'tool_name': {'type': 'string', 'minLength': 1, 'description': '引擎原子工具名（来自引擎工具目录）'},
            'inputs': {'type': 'object', 'description': '工具入参'},
        },
        required=['tool_name'],
        page_rank=32,
        tags=['studio', 'tool'],
        ask=True,
        risk_level='medium',
    ),
    # ---------------- 导出（studio:export，出厂 ask） ----------------
    _cap(
        name='export',
        title='导出成片',
        description='导出最终成片（owner 隔离，序列化边界换 CDN 签名下载 URL；花算力/外发，须主人审批）。',
        scope=_SCOPE_EXPORT,
        properties={
            'artifact_id': {'type': 'integer', 'minimum': 1, 'description': '成品 id（list_artifacts 返回）'},
            'format': {'type': 'string', 'description': '导出格式（可选，当前成片即 mp4）'},
        },
        required=['artifact_id'],
        page_rank=40,
        tags=['studio', 'export'],
        ask=True,
        risk_level='medium',
    ),
    # ---------------- 分享 / 发布（studio:share，出厂 ask=外发；§3.6 全复用 resource_share + M18） ----------------
    _cap(
        name='share',
        title='分享视频项目/成品',
        description='把视频项目或成片分享给人或分身（viewer/editor/manager，全复用平台 resource_share，须主人审批）。'
        '分享给分身（grantee_type=agent）即能力授权：该分身的 hasn.studio.* 可在权限内操作该产物。',
        scope=_SCOPE_SHARE,
        properties={
            'resource_type': {'type': 'string', 'enum': ['project', 'artifact'], 'description': '分享对象类型'},
            'resource_id': {'type': 'integer', 'minimum': 1, 'description': '项目 id 或成品 id'},
            'grantee_type': {'type': 'string', 'enum': ['human', 'agent', 'enterprise'], 'description': '协作者类型'},
            'grantee_id': {'type': 'string', 'minLength': 1, 'description': '人/分身 hasn_id 或企业 id'},
            'permission': {'type': 'string', 'enum': ['viewer', 'editor', 'manager'], 'description': '权限档'},
        },
        required=['resource_type', 'resource_id', 'grantee_type', 'grantee_id', 'permission'],
        page_rank=41,
        tags=['studio', 'share', 'collaboration'],
        ask=True,
    ),
    _cap(
        name='publish',
        title='发布成片为可分享网页',
        description='把成片对外公开发布为可分享网页（/s/{slug}，全复用 M18 web 发布；外发，须主人审批）。'
        '签名 URL 只在访问时现解析（不烤进页面）。已发布过则更新（slug/URL 不变）。',
        scope=_SCOPE_SHARE,
        properties={
            'artifact_id': {'type': 'integer', 'minimum': 1, 'description': '成品 id（list_artifacts 返回）'},
            'title': {'type': 'string', 'description': '对外标题（可选，缺省用成品标题）'},
            'visibility': {
                'type': 'string',
                'enum': ['private', 'password', 'unlisted', 'public'],
                'description': '可见性（缺省 unlisted）',
            },
            'password': {'type': 'string', 'description': 'visibility=password 时口令（可选）'},
            'allow_download': {'type': 'boolean', 'description': '是否允许下载成片（可选，默认否）'},
        },
        required=['artifact_id'],
        page_rank=42,
        tags=['studio', 'publish', 'share'],
        ask=True,
    ),
]


STUDIO_AI_NATIVE_MANIFEST = {
    'app_id': 'studio',
    'version': '1.0.0',
    'workspace_scope': ['personal'],
    'collaboration_mode': 'none',
    'execution_mode': 'cloud',
    # cloud-brokered 工具模型（对齐 creator/finance/quant）：工具数据面经 gateway_internal 进程内直调云端 handler →
    # studio_service（落 hasn_studio PG）→ montage_engine_provider → montage-engine-service，不经本地
    # hasn-mcp / daemon Agent 代理（视频渲染/出片是集中托管的云端业务，算力按量计费要集中审计/计费）。
    'transport_mode': 'cloud',
    'notifications': {
        'emit': {
            'categories': ['app', 'reminder'],
            'card_message': True,
            'display_name': '视频引擎',
        }
    },
    'capabilities': _CAPABILITIES,
    'tools': [_tool_from_cap(cap) for cap in _CAPABILITIES],
    'events': [],
    'reverse_invoke': {'supported': False},
    'ui_interfaces': [{'face': 'ui', 'transport': 'daemon_direct'}],
    'publisher': {'developer_id': 'huanxing-first-party', 'publisher_type': 'first_party', 'name': '唤星'},
    'endpoints': {'tool_endpoint': None, 'event_endpoint': None, 'component_origin': 'loopback'},
    'audit': {'fields': list(_AUDIT_FIELDS)},
}


def build_studio_app() -> App:
    """studio App（cloud-brokered / 自建视频工作台 /apps/studio / 按需安装）。

    - ``execution_mode='cloud'``：分身工具经云端 MCP → 云端后端 Broker → 引擎服务（非本地工具、非 sidecar iframe）。
    - ``install_policy='manual'``：统一视频引擎是专业能力，按需装、不自动挂载到工作台（对齐 creator/finance/quant）。
    - ``collaboration_mode='none'`` / ``scope=('personal',)``：个人模式（分享协作走 resource_share，§3.6 全复用）。
    - ``entry_route='/apps/studio'``：自建视频工作台（项目/流水线/素材/成品库，随 webui 落地）。
    - ``default_agent_type`` 由 catalog DB 行承载（content_operator「内容运营官」），不在 App dataclass。

    延迟导入 App 避免循环依赖。
    """
    from backend.app.hasn.service.app_catalog_registry import App

    return App(
        id='studio',
        name='视频引擎',
        icon='brand-studio',
        description='统一视频引擎工作台——主人挑流水线、派分身出片（脚本→分镜→配音→合成），成品库一键管理'
        '（cloud-brokered，算力按量计费）。',
        scope=('personal',),
        collaboration_mode='none',
        entry_route='/apps/studio',
        install_policy='manual',
        execution_mode='cloud',
        ui_kind=None,
    )
