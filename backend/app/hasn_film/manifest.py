"""唤星视频生成应用（film，源自 VideoClaw，模块 14）AI-Native 内置 manifest。

设计事实源：
- docs/hasn-node设计文档/14-AI-Native应用平台/18-VideoClaw视频生成应用接入与按需下载形态设计.md
- docs/hasn-node设计文档/14-AI-Native应用平台/实施/10-VideoClaw视频生成应用接入实施清单.md P4（工具/scope）
- docs/hasn-node设计文档/14-AI-Native应用平台/16-应用目录与商业化管理统一设计.md（catalog/manifest 四正交概念）

film 是 **Tool-First** AI-Native 应用：6 阶段视频编排（剧本→角色/场景→分镜→参考图→片段→后期）
+ 临时工作台（文生图/图生图/短视频）+ 一次性 Pipeline，由本机 sidecar（fork 自 VideoClaw 的
FastAPI 编排器）执行，daemon FilmBroker 反代。引擎不内嵌创意决策（交分身），模型走主人 new-api 配额。

⚠️ 方案 A（同 deck/designsystem）：`tools[]` **置空数组**——`hasn.film.*` 工具数据面在本地
（hasn-mcp `built_in_film_tools()`，source=Local，经进程内 FilmBroker 直达本机 sidecar），
**不经云端 Runtime Gateway `_dispatch_tool`**。故不进 `tools[]`（自造 transport 会静默过
validate_manifest 变潜伏炸弹）；`capabilities[]` 只承载发现/权限元数据控制面记录。

⚠️ scope 与落地工具对齐（hasn-node `crates/hasn-mcp/src/film.rs` `capability_scopes()` + `scopes.py`）：
读类（project.list/get、stage.artifact）`film:read`；写类（建项目/各阶段生成/sandbox/pipeline/
stage.intervene/continue）`film:write`（出厂 Ask，视频花钱）；上传类（artifact.upload）`film:export`。
零漂移守卫只比**管理类**（非 `:read`）键：{film:write, film:export}（见 test_local_tool_scope_alignment）。

⚠️ execution_mode：catalog 枚举（`cloud/embedded_desktop/local_tool`）取 **`local_tool`**
（本地工具驱动 + sidecar 本机执行 + 原生 webui UI，非 cloud 执行）。按需下载形态（设计 §3
`downloadable_local`）属 P2 分发底座，其新枚举/package/storage 字段待 P2 落地，本期先以
`local_tool` + `install_policy='manual'` 注册（同 designsystem 先例）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.app.hasn.service.workbench_app_registry import WorkbenchApp

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

_READ_SCOPE = 'film:read'
_WRITE_SCOPE = 'film:write'
_EXPORT_SCOPE = 'film:export'


def _read_cap(*, name: str, description: str, properties: dict, required: list[str], page_rank: int) -> dict:
    """读类能力（required_scopes=film:read；只读，出厂 Allow）——与 film.rs 读类工具对齐。"""
    short = name.split('.', 1)[-1]
    return {
        'capability_id': f'film.{name}.capability',
        'name': short,
        'description': description,
        'tool_id': f'film.{name}',
        'mcp_name': f'hasn.film.{name}',
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
            'tags': ['film', 'video'],
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
    """写/导出类能力（required_scopes=film:write 或 film:export；出厂 Ask——视频/上传花钱，owner 可覆盖）。"""
    return {
        'capability_id': f'film.{name}.capability',
        'name': title,
        'description': description,
        'tool_id': f'film.{name}',
        'mcp_name': f'hasn.film.{name}',
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
        # 出厂 Ask（视频/参考图/上传消耗配额或外发）；human_confirmation 仅 UI 提示，owner 三态可覆盖。
        'human_confirmation': {'required': True},
        'result_writeback': ['agent_message', 'audit'],
        'discovery': {
            'exposure': 'on_demand',
            'summary': description,
            'tags': ['film', 'video'],
            'schema_visibility': 'authorized_agents',
            'default_page_rank': page_rank,
        },
    }


_PROJECT_ID = {'project_id': {'type': 'string', 'minLength': 1, 'description': '视频项目 id'}}

FILM_AI_NATIVE_MANIFEST = {
    'app_id': 'film',
    'version': '1.0.0',
    'workspace_scope': ['personal'],
    'collaboration_mode': 'none',
    'execution_mode': 'local_tool',
    'transport_mode': 'local',
    'notifications': {
        'emit': {
            'categories': ['app'],
            'card_message': True,
            'display_name': '视频生成',
        }
    },
    'capabilities': [
        # —— 读类（film:read）——
        _read_cap(
            name='project.list',
            description='列出当前主人的视频项目。',
            properties={'limit': {'type': ['integer', 'null'], 'minimum': 1, 'maximum': 100}},
            required=[],
            page_rank=10,
        ),
        _read_cap(
            name='project.get',
            description='取一个视频项目的状态与各阶段进度。',
            properties=dict(_PROJECT_ID),
            required=['project_id'],
            page_rank=11,
        ),
        _read_cap(
            name='stage.artifact',
            description='取某阶段产物（停点时摊给主人确认：剧本概要/角色场景图/分镜/参考图/片段）。',
            properties={**_PROJECT_ID, 'stage': {'type': 'string', 'minLength': 1, 'description': '阶段名'}},
            required=['project_id', 'stage'],
            page_rank=12,
        ),
        # —— 写类：建项目 + 六阶段生成（film:write，出厂 Ask）——
        _write_cap(
            name='project.create',
            title='创建视频项目',
            description='以想法/风格/比例/分辨率/集数建新视频项目。',
            properties={
                'idea': {'type': 'string', 'minLength': 1, 'description': '视频创意/想法'},
                'style': {'type': ['string', 'null'], 'description': '风格'},
                'video_ratio': {'type': ['string', 'null'], 'description': '画面比例（如 16:9）'},
                'video_resolution': {'type': ['string', 'null'], 'description': '分辨率'},
                'episodes': {'type': ['integer', 'null'], 'minimum': 1, 'description': '集数'},
            },
            required=['idea'],
            page_rank=20,
        ),
        _write_cap(
            name='script.generate',
            title='生成剧本（阶段①）',
            description='为项目生成分集剧本。',
            properties=dict(_PROJECT_ID),
            required=['project_id'],
            page_rank=21,
        ),
        _write_cap(
            name='character.design',
            title='角色/场景设计（阶段②）',
            description='生成角色与场景设计图。',
            properties=dict(_PROJECT_ID),
            required=['project_id'],
            page_rank=22,
        ),
        _write_cap(
            name='storyboard.create',
            title='分镜设计（阶段③）',
            description='生成分镜列表（含每集时长）。',
            properties=dict(_PROJECT_ID),
            required=['project_id'],
            page_rank=23,
        ),
        _write_cap(
            name='reference.generate',
            title='参考图生成（阶段④）',
            description='为各分镜生成参考图（消耗图像配额）。',
            properties=dict(_PROJECT_ID),
            required=['project_id'],
            page_rank=24,
        ),
        _write_cap(
            name='clip.generate',
            title='视频片段生成（阶段⑤）',
            description='生成视频片段（消耗视频配额、耗时长——高成本动作）。',
            properties=dict(_PROJECT_ID),
            required=['project_id'],
            page_rank=25,
        ),
        _write_cap(
            name='post.compose',
            title='后期成片（阶段⑥）',
            description='拼接片段生成最终成片。',
            properties=dict(_PROJECT_ID),
            required=['project_id'],
            page_rank=26,
        ),
        # —— 写类：停点介入/推进（film:write）——
        _write_cap(
            name='stage.intervene',
            title='带修改意见介入当前阶段',
            description='主人确认要改时，带修改意见重做当前阶段。',
            properties={
                **_PROJECT_ID,
                'stage': {'type': 'string', 'minLength': 1},
                'feedback': {'type': 'string', 'minLength': 1, 'description': '主人的修改意见'},
            },
            required=['project_id', 'stage', 'feedback'],
            page_rank=27,
        ),
        _write_cap(
            name='stage.continue',
            title='推进到下一阶段',
            description='主人确认产物后，推进到下一阶段。',
            properties={**_PROJECT_ID, 'stage': {'type': 'string', 'minLength': 1}},
            required=['project_id', 'stage'],
            page_rank=28,
        ),
        # —— 写类：临时工作台 + 一次性短流程（film:write）——
        _write_cap(
            name='sandbox.t2i',
            title='文生图（临时工作台）',
            description='直接由提示词生成图片。',
            properties={'prompt': {'type': 'string', 'minLength': 1}},
            required=['prompt'],
            page_rank=30,
        ),
        _write_cap(
            name='sandbox.i2i',
            title='图生图/风格转换（临时工作台）',
            description='基于输入图与提示词做图生图/风格转换。',
            properties={
                'prompt': {'type': 'string', 'minLength': 1},
                'image': {'type': 'string', 'minLength': 1, 'description': '输入图（路径或引用）'},
            },
            required=['prompt', 'image'],
            page_rank=31,
        ),
        _write_cap(
            name='sandbox.short',
            title='短视频直出（临时工作台）',
            description='直接生成一段 ≤15s 短视频。',
            properties={'prompt': {'type': 'string', 'minLength': 1}},
            required=['prompt'],
            page_rank=32,
        ),
        _write_cap(
            name='pipeline.run',
            title='一次性短流程',
            description='跑文艺短视频/动作迁移/数字人口播一次性 Pipeline（后台执行）。',
            properties={
                'pipeline': {'type': 'string', 'minLength': 1, 'description': 'pipeline 类型'},
                'inputs': {'type': ['object', 'null'], 'description': 'pipeline 输入'},
            },
            required=['pipeline'],
            page_rank=33,
        ),
        # —— 导出类（film:export）——
        _write_cap(
            name='artifact.upload',
            title='上传产物到云端',
            description='把本地视频产物显式上传到云端私有桶以分享/跨设备查看（本地权威，显式才上云）。',
            properties={
                **_PROJECT_ID,
                'stage': {'type': 'string', 'minLength': 1},
                'item_id': {'type': 'string', 'minLength': 1, 'description': '产物条目 id'},
            },
            required=['project_id', 'stage', 'item_id'],
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


def build_film_workbench_app() -> WorkbenchApp:
    """film WorkbenchApp（local_tool / 内联路由 / 非自动挂载）。

    - ``install_policy='manual'``：本期（P4）只铸 scope + 注册 manifest，**不自动挂载**到工作台；
      用户可见的启动入口随 P8 webui + ``hasn_app_catalog`` 目录行落地（同 designsystem/creator 先例）。
      注册到 workbench_app_registry 是 ``validate_manifest`` 的硬前置（否则 workbench_app_not_found）。
    - ``collaboration_mode='none'`` / ``scope=('personal',)`` 必须与 manifest 对齐（validate 闸门）。
    - ``execution_mode='local_tool'`` / ``ui_kind=None``：原生 webui（非 embedded sidecar）。

    延迟导入 WorkbenchApp 避免循环依赖。
    """
    from backend.app.hasn.service.workbench_app_registry import WorkbenchApp

    return WorkbenchApp(
        id='film',
        name='视频生成',
        icon='brand-film',
        description='把一个创意做成完整视频——脚本→角色→分镜→参考图→片段→成片，分身逐阶段推进，每步你可确认。',
        scope=('personal',),
        collaboration_mode='none',
        entry_route='/workbench/apps/film',
        install_policy='manual',
        execution_mode='local_tool',
        ui_kind=None,
    )
