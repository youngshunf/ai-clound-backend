"""唤星短视频合成应用（reel，源自 MoneyPrinterTurbo，模块 14 doc19）AI-Native 内置 manifest。

设计事实源：
- docs/hasn-node设计文档/14-AI-Native应用平台/19-MoneyPrinterTurbo短视频合成应用接入设计.md §3/§7（工具/scope）
- docs/hasn-node设计文档/14-AI-Native应用平台/实施/11-MoneyPrinterTurbo短视频合成应用接入实施清单.md P4
- docs/hasn-node设计文档/14-AI-Native应用平台/16-应用目录与商业化管理统一设计.md（catalog/manifest 四正交概念）

reel 是 **Tool-First** 合成式短视频应用：固定线性管线（文案→搜索词→TTS 配音→字幕→库存素材→
moviepy 本地合成出片），由本机 sidecar（fork 自 MoneyPrinterTurbo 的 FastAPI 引擎）执行，daemon
ReelBroker 反代。引擎不内嵌创意决策（交分身），模型/TTS 走主人 new-api / 平台 TTS（无图/视频生成模型）。

定位（doc19 §5.5，福仔 2026-06-22）：reel 是**创作运营（creator）的合成式视频能力提供方**——项目/
素材库/成品/审核发布全用 creator 既有（`project`/`media`/`content`/`content_stage`），reel **不自建**；
content_operator 分身在 creator 内容流水线里调 `hasn.reel.generate` 出片。reel 自身只留瘦引擎页（引擎
管理 + 临时合成 sandbox），不出内容工作台。

⚠️ 方案 A（同 deck/designsystem/film）：`tools[]` **置空数组**——`hasn.reel.*` 工具数据面在本地
（hasn-mcp `built_in_reel_tools()`，source=Local，经进程内 ReelBroker 直达本机 sidecar），
**不经云端 Runtime Gateway `_dispatch_tool`**。故不进 `tools[]`（自造 transport 会静默过
validate_manifest 变潜伏炸弹）；`capabilities[]` 只承载发现/权限元数据控制面记录。

⚠️ scope 与落地工具对齐（hasn-node `crates/hasn-mcp/src/reel.rs` `capability_scopes()` + `scopes.py`）：
读类（task.list/get、material.search）`reel:read`；写类（generate/script.draft/preview/compose）
`reel:write`（出厂 Ask，合成长任务/花配额或本地资源）；上传类（artifact.upload）`reel:export`。
零漂移守卫只比**管理类**（非 `:read`）键：{reel:write, reel:export}（见 test_local_tool_scope_alignment）。

⚠️ execution_mode：catalog 枚举（`cloud/embedded_desktop/local_tool`）取 **`local_tool`**
（本地工具驱动 + sidecar 本机执行 + 原生 webui UI，非 cloud 执行）。按需下载形态（设计 §3
`downloadable_local`）属 P2 分发底座（复用 film 已建底座），其 package/storage/bundled_deps 字段经
catalog config_json.engine 承载；本期与 film 同以 `local_tool` + `install_policy='manual'` 注册。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.app.hasn.service.app_catalog_registry import App

# 与既有 AI-Native 审计共表的字段集（同 deck/designsystem/film）。
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

_READ_SCOPE = 'reel:read'
_WRITE_SCOPE = 'reel:write'
_EXPORT_SCOPE = 'reel:export'


def _read_cap(*, name: str, description: str, properties: dict, required: list[str], page_rank: int) -> dict:
    """读类能力（required_scopes=reel:read；只读/查素材不下载，出厂 Allow）——与 reel.rs 读类工具对齐。"""
    short = name.split('.', 1)[-1]
    return {
        'capability_id': f'reel.{name}.capability',
        'name': short,
        'description': description,
        'tool_id': f'reel.{name}',
        'mcp_name': f'hasn.reel.{name}',
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
            'tags': ['reel', 'video', 'short-video'],
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
) -> dict:
    """写/导出类能力（required_scopes=reel:write 或 reel:export；出厂 Ask——合成花配额/本地资源/上传外发，owner 可覆盖）。"""
    return {
        'capability_id': f'reel.{name}.capability',
        'name': title,
        'description': description,
        'tool_id': f'reel.{name}',
        'mcp_name': f'hasn.reel.{name}',
        'required_scopes': [scope],
        'workspace_roles': ['owner'],
        'input_schema': {
            'type': 'object',
            'properties': properties,
            'required': required,
            'additionalProperties': False,
        },
        'output_schema': {'type': 'object'},
        'risk_level': 'medium',
        # 出厂 Ask（合成长任务/消耗配额或本地合成资源/上传外发）；human_confirmation 仅 UI 提示，owner 三态可覆盖。
        'human_confirmation': {'required': True},
        'result_writeback': ['agent_message', 'audit'],
        'discovery': {
            'exposure': 'on_demand',
            'summary': description,
            'tags': ['reel', 'video', 'short-video'],
            'schema_visibility': 'authorized_agents',
            'default_page_rank': page_rank,
        },
    }


# 出片参数（doc19 §4.4 VideoParams 子集 + 自带素材/归属）。归属由调用方传 creator 内容项/项目上下文。
_GENERATE_PROPERTIES = {
    'subject': {'type': 'string', 'minLength': 1, 'description': '主题/脚本（取自 creator 内容项已有文案或选题）'},
    'content_id': {'type': ['integer', 'null'], 'description': 'creator 内容项 id（归属 + 取脚本/素材上下文，doc19 §5.5）'},
    'video_aspect': {'type': ['string', 'null'], 'description': '画面比例（9:16 竖 / 16:9 横 / 1:1 方）'},
    'video_clip_duration': {'type': ['integer', 'null'], 'minimum': 1, 'description': '单片段时长（秒）'},
    'voice_name': {'type': ['string', 'null'], 'description': '配音音色（edge/平台 TTS）'},
    'voice_rate': {'type': ['number', 'null'], 'description': '语速'},
    'subtitle_enabled': {'type': ['boolean', 'null'], 'description': '是否加字幕'},
    'bgm_type': {'type': ['string', 'null'], 'description': 'BGM 类型/曲目'},
    'video_count': {'type': ['integer', 'null'], 'minimum': 1, 'description': '出片数量'},
    'video_source': {'type': ['string', 'null'], 'description': '素材源：auto（自带优先+库存补缺，§6.6）/pexels/pixabay'},
    'video_materials': {
        'type': ['array', 'null'],
        'items': {'type': 'object'},
        'description': '自带素材引用（取自 creator.media，daemon materialize 后喂引擎，§5.5）',
    },
    'custom_audio_file': {'type': ['string', 'null'], 'description': '自带配音文件（跳 TTS）'},
}

REEL_AI_NATIVE_MANIFEST = {
    'app_id': 'reel',
    # 「可搜索域目录」：namespace 关键词 → 一句话（云端 tool.search 描述自动汇聚，agent 据此选关键词搜该域工具）。
    'domain_summary': {'reel': '短视频合成（脚本→素材→出片）'},
    'version': '1.0.0',
    'workspace_scope': ['personal'],
    'collaboration_mode': 'none',
    'execution_mode': 'local_tool',
    'transport_mode': 'local',
    'notifications': {
        'emit': {
            'categories': ['app'],
            'card_message': True,
            'display_name': '短视频合成',
        }
    },
    # 资源描述符（doc31 §2，RC-P6）：短视频项目 → hasn://reel/projects/{server_id}，应用内详情路由打开。
    # 完成卡 / 工作会话资源栏 / URI 解析 / 详情跳转全从这份声明派生（webui 零改代码）。
    'resources': [
        {
            'resource_kind': 'reel.project',
            'uri_domain': 'reel/projects',  # → hasn://reel/projects/{server_id}（doc08 §3 登记 internal_route 域）
            'open': {'mode': 'internal_route', 'route_template': '/apps/reel/projects/:id'},
            'card': {'verb': '短视频', 'action_label': '打开短视频'},
            'artifact_kind': 'resource',
        }
    ],
    'capabilities': [
        # —— 读类（reel:read，出厂 Allow）——
        _read_cap(
            name='task.list',
            description='列出当前主人的短视频合成任务。',
            properties={'limit': {'type': ['integer', 'null'], 'minimum': 1, 'maximum': 100}},
            required=[],
            page_rank=10,
        ),
        _read_cap(
            name='task.get',
            description='取一个短视频合成任务的状态与产物（文案/配音/字幕/素材/成片）。',
            properties={'task_id': {'type': 'string', 'minLength': 1, 'description': '任务 id'}},
            required=['task_id'],
            page_rank=11,
        ),
        _read_cap(
            name='material.search',
            description='按关键词搜库存素材（Pexels/Pixabay）给分身/主人挑片，不下载、不合成。',
            properties={
                'search_terms': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'minItems': 1,
                    'description': '搜索关键词',
                },
                'video_source': {'type': ['string', 'null'], 'description': 'pexels/pixabay'},
                'minimum_duration': {'type': ['integer', 'null'], 'minimum': 1, 'description': '最短时长（秒）'},
            },
            required=['search_terms'],
            page_rank=12,
        ),
        # —— 写类：合成出片（reel:write，出厂 Ask）——
        _write_cap(
            name='generate',
            title='生成短视频（一把梭）',
            description='主题/脚本（+可选 creator 素材）→ 成片：文案→配音→字幕→素材（自带优先+库存补缺）→本地合成（长任务，进度走 JSONL 流）。归属由调用方传入（creator 内容项/项目上下文）。',
            properties=dict(_GENERATE_PROPERTIES),
            required=['subject'],
            page_rank=20,
        ),
        _write_cap(
            name='script.draft',
            title='文案审稿（仅文案+搜索词）',
            description='只生成视频文案 + 搜索关键词（stop_at=terms），给分身/主人审稿，先不配音不合成。',
            properties={
                'subject': {'type': 'string', 'minLength': 1, 'description': '主题/选题'},
                'content_id': {'type': ['integer', 'null'], 'description': 'creator 内容项 id（取上下文）'},
                'paragraph_number': {'type': ['integer', 'null'], 'minimum': 1, 'description': '段落数'},
                'script_prompt': {'type': ['string', 'null'], 'description': '文案提示词覆盖'},
            },
            required=['subject'],
            page_rank=21,
        ),
        _write_cap(
            name='preview',
            title='半成品预览（配音+字幕+素材）',
            description='用已确认文案出「配音+字幕+素材」预览（stop_at=materials/subtitle），先不合成成片。',
            properties={
                'task_id': {'type': ['string', 'null'], 'description': '已有任务 id（续接已确认文案）'},
                **_GENERATE_PROPERTIES,
            },
            required=['subject'],
            page_rank=22,
        ),
        _write_cap(
            name='compose',
            title='确认后合成成片',
            description='主人确认文案/预览后合成最终成片（moviepy 本地合成，长任务）。成片落本地（重资产本地优先，不自动上云）。',
            properties={
                'task_id': {'type': ['string', 'null'], 'description': '已有任务 id（续接预览）'},
                **_GENERATE_PROPERTIES,
            },
            required=['subject'],
            page_rank=23,
        ),
        # —— 导出类（reel:export，出厂 Ask）——
        _write_cap(
            name='artifact.upload',
            title='上传成片到云端',
            description='把本地短视频成片显式上传到云端私有桶以分享/跨设备查看（本地权威，重资产显式才上云，doc19 §5.5 N20）。',
            properties={
                'task_id': {'type': 'string', 'minLength': 1, 'description': '任务 id'},
                'item': {'type': ['string', 'null'], 'description': '成片条目（如 final-1.mp4），不传则取首个成片'},
            },
            required=['task_id'],
            page_rank=40,
            scope=_EXPORT_SCOPE,
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


def build_reel_app() -> App:
    """reel App（local_tool / 瘦引擎页路由 / 非自动挂载）。

    - ``install_policy='manual'``：reel 是瘦引擎应用（doc19 §4.2/N19），按需装，不自动挂载到工作台
      （``default_mount=FALSE`` 由 install_policy 推导）。内容创作在创作运营完成（§5.5）。
      注册到 app_catalog_registry 是 ``validate_manifest`` 的硬前置（否则 workbench_app_not_found）。
    - ``collaboration_mode='none'`` / ``scope=('personal',)`` 必须与 manifest 对齐（validate 闸门）。
    - ``execution_mode='local_tool'`` / ``ui_kind=None``：原生 webui 瘦引擎页（非 embedded sidecar、非 iframe）。
    - ``entry_route='/apps/reel'``：瘦引擎页（引擎管理 + 临时合成 sandbox），路由统一在 `/apps` 前缀下
      （2026-06-22 路由重构，doc19 §3/§4.2）。

    延迟导入 App 避免循环依赖。
    """
    from backend.app.hasn.service.app_catalog_registry import App

    return App(
        id='reel',
        name='短视频合成',
        icon='brand-reel',
        description='一个主题就能出短视频——分身替你写文案、配音、加字幕、配素材、自动拼成片，口播、带货、资讯批量产出。',
        scope=('personal',),
        collaboration_mode='none',
        entry_route='/apps/reel',
        install_policy='manual',
        execution_mode='local_tool',
        ui_kind=None,
    )
