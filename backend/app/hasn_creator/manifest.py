"""创作运营（hasn_creator，app_id=creator）AI-Native 内置 manifest + App 声明。

设计事实源：
- docs/自媒体创作运营/00-自媒体创作运营全链路AI-Native应用设计.md §3（应用身份/形态）+ §6（工具面 17 行 21 工具）
- docs/自媒体创作运营/实施/91-施工与迁移方案.md M4（manifest + handlers + gateway 注册）
- docs/hasn-node设计文档/14-AI-Native应用平台/16-应用目录与商业化管理统一设计.md（catalog/manifest 四正交概念）
- docs/hasn-node设计文档/14-AI-Native应用平台/15-AI-Native应用命名空间与目录约定.md（ADR-15）

命名铁律：`app_id='creator'`（不带 hasn_ 前缀，对齐 community↔hasn_community / growth↔hasn_growth 先例）；
模块/schema 仍 `hasn_creator`。manifest 落**应用根目录** `app/hasn_creator/manifest.py`
（对齐 hasn_deck/hasn_knowledge/hasn_publish/hasn_growth），不放公共 `app/hasn/service`、不放 `service/` 子目录。

形态（设计 §6，对齐 community/knowledge/growth 云端工具模型）：
- `execution_mode='cloud'`、`transport_mode='cloud'`——创作运营是**纯云端业务应用**（定位/创作/审核/
  发布/复盘/进化），**零本地文件/电脑操作**，故 `hasn.creator.*` 工具一律走**云端 MCP**：经 Agent MCP
  Key 命中 `/api/v1/mcp/streamable` → `app_tool_loader` 把 capability 投影成 AppTool
  （`execution_location='cloud'`）→ `ai_native_runtime_gateway.call_tool`（transport=`gateway_internal`）
  → 进程内直调 `app/hasn_creator/service/creator_tool_handlers.py` → creator service。
  **不经 hasn-node 本地 hasn-mcp 注册、不经 daemon Agent 工具代理**——那是 task/deck 等「需要操作用户
  电脑/读写本地文件」才有的本地模式；创作运营没有本地理由。媒体生成（配图/封面）复用平台级
  `hasn.image.generate`、口播配音复用 `hasn.voice.synthesize`、素材落桶复用 `hasn.asset.upload`，
  不在本应用重造。daemon `domains/creator` 仅保留 owner WebUI 操作面的薄代理（人用），不承载 Agent 工具数据面。
- `tools[]` 由 `capabilities` 派生（`_tool_from_cap`），每条 `transport='gateway_internal'` +
  `handler='creator.<flat_name>'` 指向 handler 注册表键。

工具调用授权（D-v3-1，出厂全 Allow）：manifest 只声明各工具 `risk_level` 与 required_scopes；最终由统一
授权三态（owner 对 agent 的 capability_modes）在工具网关**调用时**强制。所有写类工具
`human_confirmation.required=False`（出厂 Allow）。`publish.submit` 的「过主人审核」是**业务态**
（落 `pending_review`，§3.3），不走 ask_gate；工具如实回报状态，零 fake。
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

_SCOPE_READ = 'creator:read'
_SCOPE_MANAGE = 'creator:manage'
_SCOPE_PUBLISH = 'creator:publish'


def _cap(
    *,
    name: str,
    mcp_suffix: str,
    title: str,
    description: str,
    scope: str | None,
    risk_level: str,
    properties: dict,
    required: list[str],
    page_rank: int,
    tags: list[str],
) -> dict:
    """hasn.creator.* 能力声明（读/写类一律出厂 Allow 免确认，16-doc D-v3-1；owner 可设 ask/deny override）。

    ``name`` 是 capability/tool_id 用的扁平标识（点号转下划线）；``mcp_suffix`` 是 ``hasn.creator.<suffix>``。
    """
    return {
        'capability_id': f'hasn_creator.{name}.capability',
        'name': title,
        'description': description,
        'tool_id': f'hasn_creator.{name}',
        'mcp_name': f'hasn.creator.{mcp_suffix}',
        'required_scopes': [scope] if scope else [],
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


def _tool_from_cap(cap: dict) -> dict:
    """从 capability 派生 `tools[]` 可执行声明（transport=gateway_internal，云端进程内直调 handler）。

    ``handler`` = ``creator.<flat_name>``（把 tool_id 的 ``hasn_creator.`` 前缀换成 ``creator.``），
    对应 `ai_native_runtime_gateway._internal_handlers()` 注册表键 → `creator_tool_handlers.handle_<flat_name>`。
    ``idempotent``：纯读类（仅需 creator:read）可安全重试；写/发布类非幂等不自动重放。
    """
    scopes = list(cap.get('required_scopes') or [])
    return {
        'tool_id': cap['tool_id'],
        'mcp_name': cap['mcp_name'],
        'transport': 'gateway_internal',
        'handler': str(cap['tool_id']).replace('hasn_creator.', 'creator.', 1),
        'required_scopes': scopes,
        'risk_level': cap['risk_level'],
        'idempotent': scopes == [_SCOPE_READ],
    }


# 创作运营 21 工具能力声明（设计 §6.1，云端 gateway_internal）。顺序即 tools[] 顺序。
_CAPABILITIES = [
    # ---------------- 项目 ----------------
    _cap(
        name='project_list',
        mcp_suffix='project.list',
        title='列项目',
        description='列创作项目（运营单元根，含负责人；企业模式按视角裁剪）。',
        scope=_SCOPE_READ,
        risk_level='low',
        properties={
            'status': {'type': ['string', 'null'], 'description': '按状态过滤 active/paused/archived'},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200, 'default': 50},
        },
        required=[],
        page_rank=10,
        tags=['creator', 'project', 'list', 'read'],
    ),
    _cap(
        name='project_get',
        mcp_suffix='project.get',
        title='取项目详情',
        description='项目详情（含 1:1 画像、多平台账号、内容计数、待审计数）。',
        scope=_SCOPE_READ,
        risk_level='low',
        properties={'project_id': {'type': 'integer'}},
        required=['project_id'],
        page_rank=11,
        tags=['creator', 'project', 'get', 'read'],
    ),
    _cap(
        name='project_create',
        mcp_suffix='project.create',
        title='建项目',
        description='建创作项目（运营单元根，同时建 1:1 空画像）。企业模式落企业池并 assignee=主人。',
        scope=_SCOPE_MANAGE,
        risk_level='low',
        properties={
            'name': {'type': 'string', 'minLength': 1, 'maxLength': 200},
            'description': {'type': ['string', 'null']},
            'primary_platform': {'type': ['string', 'null'], 'description': '主平台 xiaohongshu/douyin/...'},
            'pipeline_mode': {'type': ['string', 'null'], 'description': '流水线模式 semi-auto/manual/full-auto'},
            'playbook_id': {'type': ['integer', 'null']},
        },
        required=['name'],
        page_rank=12,
        tags=['creator', 'project', 'create', 'manage'],
    ),
    # ---------------- 画像（定位）----------------
    _cap(
        name='profile_get',
        mcp_suffix='profile.get',
        title='读画像',
        description='创作前必读：账号定位/内容支柱/支柱权重/调性/禁区。',
        scope=_SCOPE_READ,
        risk_level='low',
        properties={'project_id': {'type': 'integer'}},
        required=['project_id'],
        page_rank=13,
        tags=['creator', 'profile', 'get', 'read'],
    ),
    _cap(
        name='profile_analyze',
        mcp_suffix='profile.analyze',
        title='辅助定位',
        description='辅助定位：返回竞品 + 当前画像 + 草案骨架（真实数据），供分身据此提炼后调 profile.set（零 fake）。',
        scope=_SCOPE_MANAGE,
        risk_level='medium',
        properties={'project_id': {'type': 'integer'}},
        required=['project_id'],
        page_rank=14,
        tags=['creator', 'profile', 'analyze', 'manage'],
    ),
    _cap(
        name='profile_set',
        mcp_suffix='profile.set',
        title='设置画像',
        description='设置/更新账号画像（upsert，1:1）：赛道/人设/受众/调性/支柱/发布节奏/禁区。',
        scope=_SCOPE_MANAGE,
        risk_level='medium',
        properties={
            'project_id': {'type': 'integer'},
            'fields': {
                'type': 'object',
                'description': 'niche/sub_niche/persona/target_audience/tone/keywords/content_pillars/'
                'posting_frequency/best_posting_time/style_references/taboo_topics/bio',
            },
        },
        required=['project_id', 'fields'],
        page_rank=15,
        tags=['creator', 'profile', 'set', 'manage'],
    ),
    # ---------------- 账号 / 竞品 ----------------
    _cap(
        name='account_add',
        mcp_suffix='account.add',
        title='加平台账号',
        description='给项目加一个平台账号（小红书/抖音/视频号等；可标主账号）。',
        scope=_SCOPE_MANAGE,
        risk_level='low',
        properties={
            'project_id': {'type': 'integer'},
            'platform': {'type': 'string', 'minLength': 1, 'description': '平台标识'},
            'fields': {
                'type': ['object', 'null'],
                'description': 'platform_uid/nickname/home_url/bio/is_primary/notes',
            },
        },
        required=['project_id', 'platform'],
        page_rank=16,
        tags=['creator', 'account', 'add', 'manage'],
    ),
    _cap(
        name='account_list',
        mcp_suffix='account.list',
        title='列平台账号',
        description='列项目下的平台账号。',
        scope=_SCOPE_READ,
        risk_level='low',
        properties={'project_id': {'type': 'integer'}},
        required=['project_id'],
        page_rank=17,
        tags=['creator', 'account', 'list', 'read'],
    ),
    _cap(
        name='competitor_log',
        mcp_suffix='competitor.log',
        title='记竞品调研',
        description='记一条竞品调研结论（粉丝/互动/风格/优势/标签），供 profile.analyze 与选题参考。',
        scope=_SCOPE_MANAGE,
        risk_level='low',
        properties={
            'project_id': {'type': 'integer'},
            'name': {'type': 'string', 'minLength': 1},
            'fields': {
                'type': ['object', 'null'],
                'description': 'platform/url/follower_count/avg_likes/content_style/strengths/notes/tags',
            },
        },
        required=['project_id', 'name'],
        page_rank=18,
        tags=['creator', 'competitor', 'log', 'manage'],
    ),
    # ---------------- 选题 ----------------
    _cap(
        name='topic_suggest',
        mcp_suffix='topic.suggest',
        title='生成选题',
        description='把分身按画像+热点+竞品生成的选题写入选题池（分身提供 title/reason/angles，service 落库，零 fake）。',
        scope=_SCOPE_MANAGE,
        risk_level='low',
        properties={
            'project_id': {'type': 'integer'},
            'topics': {
                'type': 'array',
                'items': {'type': 'object'},
                'description': '[{title,reason,potential_score,heat_index,keywords[],creative_angles[]}]',
            },
            'batch_date': {'type': ['string', 'null'], 'description': '批次日期（可选）'},
        },
        required=['project_id', 'topics'],
        page_rank=19,
        tags=['creator', 'topic', 'suggest', 'manage'],
    ),
    # ---------------- 内容 / 阶段产出 ----------------
    _cap(
        name='content_create',
        mcp_suffix='content.create',
        title='建内容',
        description='建一条内容（归 project，记形态轨道 article/video/...；采纳选题则关联并置选题为已采纳）。',
        scope=_SCOPE_MANAGE,
        risk_level='low',
        properties={
            'project_id': {'type': 'integer'},
            'title': {'type': 'string', 'minLength': 1},
            'content_tracks': {'type': ['string', 'null'], 'description': '形态轨道（逗号分隔）article,video'},
            'target_platforms': {'type': ['array', 'null'], 'items': {'type': 'string'}},
            'topic_id': {'type': ['integer', 'null'], 'description': '采纳的选题 ID'},
            'viral_pattern_id': {'type': ['integer', 'null']},
            'playbook_id': {'type': ['integer', 'null']},
            'pipeline_mode': {'type': ['string', 'null']},
        },
        required=['project_id', 'title'],
        page_rank=20,
        tags=['creator', 'content', 'create', 'manage'],
    ),
    _cap(
        name='content_list',
        mcp_suffix='content.list',
        title='列内容',
        description='内容列表（可按 project/状态/审核态过滤）。',
        scope=_SCOPE_READ,
        risk_level='low',
        properties={
            'project_id': {'type': ['integer', 'null']},
            'status': {'type': ['string', 'null'], 'description': '内容状态过滤'},
            'review_status': {'type': ['string', 'null'], 'description': '审核态过滤'},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200, 'default': 50},
        },
        required=[],
        page_rank=21,
        tags=['creator', 'content', 'list', 'read'],
    ),
    _cap(
        name='content_get',
        mcp_suffix='content.get',
        title='取内容详情',
        description='内容详情（含阶段产出 stages、发布记录 publishes、审核意见）。',
        scope=_SCOPE_READ,
        risk_level='low',
        properties={'content_id': {'type': 'integer'}},
        required=['content_id'],
        page_rank=22,
        tags=['creator', 'content', 'get', 'read'],
    ),
    _cap(
        name='content_update',
        mcp_suffix='content.update',
        title='更新内容',
        description='更新内容状态（状态机推进）/标题/审核态/审核意见。非法状态迁移如实拒。',
        scope=_SCOPE_MANAGE,
        risk_level='low',
        properties={
            'content_id': {'type': 'integer'},
            'status': {
                'type': ['string', 'null'],
                'description': 'idea/researching/drafting/reviewing/ready/published/analyzing/completed/archived',
            },
            'title': {'type': ['string', 'null']},
            'review_status': {'type': ['string', 'null'], 'description': 'pending/approved/rejected'},
            'review_note': {'type': ['string', 'null']},
            'metadata': {'type': ['object', 'null'], 'description': '合并进 metadata_json'},
        },
        required=['content_id'],
        page_rank=23,
        tags=['creator', 'content', 'update', 'manage'],
    ),
    _cap(
        name='content_stage_save',
        mcp_suffix='content.stage.save',
        title='保存阶段产出',
        description='保存阶段产出（research/outline/draft/final_draft/cover/storyboard/voiceover）。同阶段再存 bump version 留迭代痕迹。',
        scope=_SCOPE_MANAGE,
        risk_level='low',
        properties={
            'content_id': {'type': 'integer'},
            'stage': {'type': 'string', 'minLength': 1, 'description': '阶段标识'},
            'content_text': {'type': ['string', 'null']},
            'asset_refs': {
                'type': ['array', 'null'],
                'items': {'type': 'object'},
                'description': (
                    '素材/成片引用，每项为云端引用 {kind:"cloud", asset_uri:"hasn://asset/..."} '
                    '或本地引用 {kind:"local", path, node_id, uploaded:false}（重资产成片本地优先不自动上云，doc19 §5.5）'
                ),
            },
            'source_type': {'type': ['string', 'null'], 'description': 'ai_generated/human_edited'},
        },
        required=['content_id', 'stage'],
        page_rank=24,
        tags=['creator', 'content', 'stage', 'manage'],
    ),
    # ---------------- 发布 ----------------
    _cap(
        name='publish_submit',
        mcp_suffix='publish.submit',
        title='请求发布',
        description=(
            '请求把内容发布到某平台账号：落 pending_review 等主人审核，绝不绕过审核（C3 铁律），'
            '返回真实状态，不假装已发出。method 默认 manual_assist（人工辅助发布为主）。'
        ),
        scope=_SCOPE_PUBLISH,
        risk_level='high',
        properties={
            'content_id': {'type': 'integer'},
            'account_id': {'type': 'integer'},
            'platform': {'type': ['string', 'null'], 'description': '不传则取账号平台'},
            'method': {'type': ['string', 'null'], 'description': 'manual_assist（默认）/api_auto'},
            'publish_note': {'type': ['string', 'null'], 'description': '给主人看：最佳时间/话题标签/置顶评论'},
        },
        required=['content_id', 'account_id'],
        page_rank=25,
        tags=['creator', 'publish', 'submit'],
    ),
    _cap(
        name='publish_list',
        mcp_suffix='publish.list',
        title='列发布记录',
        description='发布记录 + 数据指标（可按 project/content/状态过滤）。',
        scope=_SCOPE_READ,
        risk_level='low',
        properties={
            'project_id': {'type': ['integer', 'null']},
            'content_id': {'type': ['integer', 'null']},
            'status': {'type': ['string', 'null']},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 300, 'default': 100},
        },
        required=[],
        page_rank=26,
        tags=['creator', 'publish', 'list', 'read'],
    ),
    _cap(
        name='publish_update_metrics',
        mcp_suffix='publish.update_metrics',
        title='回填发布数据',
        description='回填发布数据指标（views/likes/comments/shares/favorites/new_followers + 扩展），已发布内容自动进数据跟踪态。',
        scope=_SCOPE_MANAGE,
        risk_level='low',
        properties={
            'publish_id': {'type': 'integer'},
            'metrics': {'type': 'object', 'description': '指标键值（views/likes/comments/...）'},
        },
        required=['publish_id', 'metrics'],
        page_rank=27,
        tags=['creator', 'publish', 'metrics', 'manage'],
    ),
    # ---------------- 进化 / 爆款 / 复盘 ----------------
    _cap(
        name='insight_log',
        mcp_suffix='insight.log',
        title='沉淀内容洞察',
        description=(
            '沉淀一条复盘洞察（支柱表现/钩子套路/发布时间/受众/教训）+ proposed_action。'
            '服务端据此回写 profile.pillar_weights / viral_pattern / playbook（进化闭环）。'
        ),
        scope=_SCOPE_MANAGE,
        risk_level='medium',
        properties={
            'project_id': {'type': 'integer'},
            'insight_type': {
                'type': 'string',
                'description': 'pillar_performance/hook_pattern/timing/audience/lesson',
            },
            'summary': {'type': 'string', 'minLength': 1},
            'period': {'type': ['string', 'null'], 'description': '复盘周期 2026-W24 / content:{id}'},
            'evidence_json': {'type': ['object', 'null'], 'description': '数据证据'},
            'proposed_action': {
                'type': ['object', 'null'],
                'description': '{pillar_weight_delta:{},new_viral_pattern:{},playbook_patch:{}}',
            },
            'confidence': {'type': ['number', 'null'], 'minimum': 0, 'maximum': 1},
        },
        required=['project_id', 'insight_type', 'summary'],
        page_rank=28,
        tags=['creator', 'insight', 'evolution', 'manage'],
    ),
    _cap(
        name='pattern_search',
        mcp_suffix='pattern.search',
        title='搜爆款模式库',
        description='搜爆款模式库（标题/钩子/结构模板），创作前选钩子/结构。含全局内置 + 自己的 + 本项目。',
        scope=_SCOPE_READ,
        risk_level='low',
        properties={
            'project_id': {'type': ['integer', 'null']},
            'pattern_type': {'type': ['string', 'null'], 'description': 'title/hook/structure/...'},
            'query': {'type': ['string', 'null']},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 30},
        },
        required=[],
        page_rank=29,
        tags=['creator', 'pattern', 'search', 'read'],
    ),
    _cap(
        name='report_overview',
        mcp_suffix='report.overview',
        title='数据总览',
        description='项目/全部数据总览：内容状态分布 + 发布数据汇总（复盘 + 简报数据源）。企业模式按视角裁剪。',
        scope=_SCOPE_READ,
        risk_level='low',
        properties={'project_id': {'type': ['integer', 'null']}},
        required=[],
        page_rank=30,
        tags=['creator', 'report', 'overview', 'read'],
    ),
]


CREATOR_AI_NATIVE_MANIFEST = {
    'app_id': 'creator',
    'version': '1.0.0',
    'workspace_scope': ['personal', 'enterprise'],
    'collaboration_mode': 'workspace_shared',
    'execution_mode': 'cloud',
    # 云端工具模型（对齐 community/knowledge/growth）：工具数据面经 gateway_internal 进程内直调云端 handler，
    # 不经本地 hasn-mcp / daemon Agent 代理（创作运营无本地文件/电脑操作的本地理由）。
    'transport_mode': 'cloud',
    # 通知：内容待审核 / 发布完成 / 关键洞察 → 主人提醒卡片（业务态非审批票据）。
    'notifications': {
        'emit': {
            'categories': ['app', 'reminder'],
            'card_message': True,
            'display_name': '创作运营',
        }
    },
    'capabilities': _CAPABILITIES,
    # tools[] 由 capabilities 派生：每条 gateway_internal + handler 指向云端 handler 注册表键。
    'tools': [_tool_from_cap(cap) for cap in _CAPABILITIES],
    'events': [],
    'reverse_invoke': {'supported': False},
    'ui_interfaces': [{'face': 'ui', 'transport': 'daemon_direct'}],
    'publisher': {'developer_id': 'huanxing-first-party', 'publisher_type': 'first_party', 'name': '唤星'},
    'endpoints': {'tool_endpoint': None, 'event_endpoint': None, 'component_origin': 'loopback'},
    'audit': {'fields': list(_AUDIT_FIELDS)},
}


def build_creator_app() -> App:
    """构造 creator 的 App（catalog seed 源 + 工作台入口）。

    UI 为 webui 原生路由（``ui_kind=None`` 内联导航至 ``/creator``，同 knowledge/community/growth）。
    execution_mode=cloud（业务逻辑云端，工具走 gateway_internal）；install_policy=manual（创作运营非人人需要，
    default_mount=FALSE，用户在工作台主动挂载，设计 §3.2）。
    """
    from backend.app.hasn.service.app_catalog_registry import App

    return App(
        id='creator',
        name='创作运营',
        icon='brand-creator',
        description='让分身替你做账号定位、选题创作、审核发布、数据复盘——内容运营一条龙，越做越有章法。',
        scope=('personal', 'enterprise'),
        collaboration_mode='workspace_shared',
        entry_route='/apps/creator',
        install_policy='manual',
        execution_mode='cloud',
    )
