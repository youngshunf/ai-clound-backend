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


# 创作运营工具能力声明（设计 §6.1，云端 gateway_internal）。顺序即 tools[] 顺序。
_CAPABILITIES = [
    # ---------------- 平台目录（S1，重构 §4）----------------
    _cap(
        name='platform_list',
        mcp_suffix='platform.list',
        title='列平台目录',
        description='列平台目录（选择制，含主页根 URL/主页模板/指标口径）：选平台、据平台+uid 拼主页链接、'
        '知道该平台粉丝/作品叫法时调。账号/竞品/项目的 platform 一律选自此目录。',
        scope=_SCOPE_READ,
        risk_level='low',
        properties={},
        required=[],
        page_rank=9,
        tags=['creator', 'platform', 'list', 'read'],
    ),
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
            'platform_project_id': {'type': ['string', 'null'], 'description': '按平台项目 UUID 过滤'},
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
            'platform_project_id': {
                'type': ['string', 'null'],
                'description': '可选：本人进行中的平台项目 UUID；项目会话中省略时自动继承',
            },
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
        description='给项目加一个平台账号（小红书/抖音/视频号等；可标主账号）。platform 必选自平台目录；'
        '有公开主页的平台（小红书/抖音/B站…）home_url 必填（供分身据此抓取粉丝/作品数据），'
        '公众号/视频号等无公开主页的平台豁免。',
        scope=_SCOPE_MANAGE,
        risk_level='low',
        properties={
            'project_id': {'type': 'integer'},
            'platform': {'type': 'string', 'minLength': 1, 'description': '平台标识（选自 platform.list）'},
            'fields': {
                'type': ['object', 'null'],
                'description': 'home_url（有公开主页的平台必填）/platform_uid/nickname/avatar_url/bio/is_primary/notes',
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
        description='列项目下的平台账号（含粉丝/获赞/作品数指标 + metrics_updated_at 数据新鲜度）。',
        scope=_SCOPE_READ,
        risk_level='low',
        properties={'project_id': {'type': 'integer'}},
        required=['project_id'],
        page_rank=17,
        tags=['creator', 'account', 'list', 'read'],
    ),
    _cap(
        name='account_update',
        mcp_suffix='account.update',
        title='更新平台账号',
        description='更新账号资料（昵称/uid/主页/简介/设主账号）或手填指标（知道就填；抓取走 account.update_metrics）。',
        scope=_SCOPE_MANAGE,
        risk_level='low',
        properties={
            'account_id': {'type': 'integer'},
            'fields': {
                'type': 'object',
                'description': 'nickname/platform_uid/avatar_url/home_url/bio/is_primary/notes + 手填指标 '
                'followers/following/total_likes/total_favorites/total_comments/total_posts',
            },
        },
        required=['account_id', 'fields'],
        page_rank=171,
        tags=['creator', 'account', 'update', 'manage'],
    ),
    _cap(
        name='account_update_metrics',
        mcp_suffix='account.update_metrics',
        title='回填账号指标',
        description='分身按 web-reach 抓取该账号公开主页后回填指标：粉丝/关注/获赞/收藏/评论/作品数（已知列落列，'
        '平台特有指标并入 metrics_json）。抓不到诚实报错、不编数（零 fake）。',
        scope=_SCOPE_MANAGE,
        risk_level='low',
        properties={
            'account_id': {'type': 'integer'},
            'metrics': {
                'type': 'object',
                'description': 'followers/following/total_likes/total_favorites/total_comments/total_posts + 平台特有键',
            },
        },
        required=['account_id', 'metrics'],
        page_rank=172,
        tags=['creator', 'account', 'metrics', 'manage'],
    ),
    _cap(
        name='account_works_upsert',
        mcp_suffix='account.works.upsert',
        title='回填账号作品',
        description='逐条 upsert 该账号的作品明细（标题/链接/封面/播放·赞·评·藏/发布时间）。归并键 external_id/url——'
        '同一作品重复抓取按此归并，与发布记录 published_url 对齐避免两套数字。封面走 hasn://asset 或平台原链接，禁 base64。',
        scope=_SCOPE_MANAGE,
        risk_level='low',
        properties={
            'account_id': {'type': 'integer'},
            'works': {
                'type': 'array',
                'items': {'type': 'object'},
                'description': '[{external_id?,url?,title,cover_uri?,published_at?,views,likes,comments,shares,favorites}]',
            },
        },
        required=['account_id', 'works'],
        page_rank=173,
        tags=['creator', 'account', 'works', 'manage'],
    ),
    _cap(
        name='account_works_list',
        mcp_suffix='account.works.list',
        title='列账号作品',
        description='列某账号的作品明细（按发布时间倒序），账号卡下钻作品用。',
        scope=_SCOPE_READ,
        risk_level='low',
        properties={
            'account_id': {'type': 'integer'},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 300, 'default': 100},
        },
        required=['account_id'],
        page_rank=174,
        tags=['creator', 'account', 'works', 'read'],
    ),
    _cap(
        name='competitor_log',
        mcp_suffix='competitor.log',
        title='记竞品调研',
        description='记一条竞品（工具层强制录真）：platform+url+name 必填；researched=true（分身调研完带真数据）时 '
        'follower_count+works_count 必填。兼容「先挂 URL 待分身调研」——researched=false 时指标待补。'
        '不允许空录一个名字（零 fake 的录真）。',
        scope=_SCOPE_MANAGE,
        risk_level='low',
        properties={
            'project_id': {'type': 'integer'},
            'name': {'type': 'string', 'minLength': 1},
            'fields': {
                'type': ['object', 'null'],
                'description': 'platform（必填）/url（必填）/researched/follower_count/works_count/avg_likes/'
                'content_style/strengths/notes/tags',
            },
        },
        required=['project_id', 'name'],
        page_rank=18,
        tags=['creator', 'competitor', 'log', 'manage'],
    ),
    _cap(
        name='competitor_update',
        mcp_suffix='competitor.update',
        title='回填竞品调研',
        description='分身按 web-reach 调研竞品后回填：粉丝数/作品数/风格/优势标签（带完整真数据）。刷新「上次调研 T」。',
        scope=_SCOPE_MANAGE,
        risk_level='low',
        properties={
            'competitor_id': {'type': 'integer'},
            'fields': {
                'type': 'object',
                'description': 'name/platform/url/follower_count/works_count/avg_likes/content_style/strengths/notes/tags',
            },
        },
        required=['competitor_id', 'fields'],
        page_rank=181,
        tags=['creator', 'competitor', 'update', 'manage'],
    ),
    _cap(
        name='competitor_works_upsert',
        mcp_suffix='competitor.works.upsert',
        title='回填竞品作品样本',
        description='逐条 upsert 竞品的作品样本（供差异化分析）。归并键 external_id/url；封面走 hasn://asset 或平台原链接，禁 base64。'
        '作品数随抓取结果自动刷新到 competitor.works_count。',
        scope=_SCOPE_MANAGE,
        risk_level='low',
        properties={
            'competitor_id': {'type': 'integer'},
            'works': {
                'type': 'array',
                'items': {'type': 'object'},
                'description': '[{external_id?,url?,title,cover_uri?,published_at?,views,likes,comments,shares,favorites}]',
            },
        },
        required=['competitor_id', 'works'],
        page_rank=182,
        tags=['creator', 'competitor', 'works', 'manage'],
    ),
    # ---------------- 素材库 / 草稿箱（S6，§6.7/§6.8）----------------
    _cap(
        name='media_add',
        mcp_suffix='media.add',
        title='登记素材',
        description='把配图/封面/视频/模板登记进素材库。二进制走 hasn://asset/ 引用（禁 base64 字节块，铁律）——'
        '字节由本地工具先上私有桶再登记；type ∈ image/video/audio/template。'
        'reel/film/studio 出的成片、imagelab 处理后的图，落桶后即可登记回素材库供复用。',
        scope=_SCOPE_MANAGE,
        risk_level='low',
        properties={
            'project_id': {'type': 'integer'},
            'type': {'type': 'string', 'description': 'image/video/audio/template'},
            'asset_uri': {'type': 'string', 'description': 'hasn://asset/{id} 私有桶引用（禁 base64）'},
            'fields': {
                'type': ['object', 'null'],
                'description': 'filename/file_size/width/height/duration/thumbnail_uri/tags/description',
            },
        },
        required=['project_id', 'type', 'asset_uri'],
        page_rank=183,
        tags=['creator', 'media', 'add', 'manage'],
    ),
    _cap(
        name='media_list',
        mcp_suffix='media.list',
        title='列素材',
        description='列项目素材库（可按 type 过滤 image/video/audio/template，按创建时间倒序）。'
        '找素材复用、配图选材时调。',
        scope=_SCOPE_READ,
        risk_level='low',
        properties={
            'project_id': {'type': 'integer'},
            'type': {'type': ['string', 'null'], 'description': 'image/video/audio/template（可选过滤）'},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 300, 'default': 100},
        },
        required=['project_id'],
        page_rank=184,
        tags=['creator', 'media', 'list', 'read'],
    ),
    _cap(
        name='media_update',
        mcp_suffix='media.update',
        title='改素材信息',
        description='改素材元信息（标签/描述/文件名/缩略图），整理素材库用。不改底层资产字节。',
        scope=_SCOPE_MANAGE,
        risk_level='low',
        properties={
            'media_id': {'type': 'integer'},
            'fields': {'type': 'object', 'description': 'filename/tags/description/thumbnail_uri'},
        },
        required=['media_id', 'fields'],
        page_rank=185,
        tags=['creator', 'media', 'update', 'manage'],
    ),
    _cap(
        name='media_delete',
        mcp_suffix='media.delete',
        title='删素材',
        description='从素材库删掉一条素材引用行（仅删库内引用，私有桶资产另行回收）。',
        scope=_SCOPE_MANAGE,
        risk_level='low',
        properties={'media_id': {'type': 'integer'}},
        required=['media_id'],
        page_rank=186,
        tags=['creator', 'media', 'delete', 'manage'],
    ),
    _cap(
        name='draft_create',
        mcp_suffix='draft.create',
        title='建草稿',
        description='建一条草稿（快速记灵感/半成品，不进正式内容流水线）。title 必填；content=正文；'
        'media=引用素材 asset 列表（hasn://asset/）；target_platforms=目标平台 key 列表。草稿养熟后经 draft.promote 转正。',
        scope=_SCOPE_MANAGE,
        risk_level='low',
        properties={
            'project_id': {'type': 'integer'},
            'title': {'type': 'string', 'minLength': 1},
            'fields': {
                'type': ['object', 'null'],
                'description': 'content/media[]（hasn://asset）/tags[]/target_platforms[]',
            },
        },
        required=['project_id', 'title'],
        page_rank=187,
        tags=['creator', 'draft', 'create', 'manage'],
    ),
    _cap(
        name='draft_update',
        mcp_suffix='draft.update',
        title='改草稿',
        description='改草稿（标题/正文/素材/标签/目标平台）。',
        scope=_SCOPE_MANAGE,
        risk_level='low',
        properties={
            'draft_id': {'type': 'integer'},
            'fields': {'type': 'object', 'description': 'title/content/media[]/tags[]/target_platforms[]'},
        },
        required=['draft_id', 'fields'],
        page_rank=188,
        tags=['creator', 'draft', 'update', 'manage'],
    ),
    _cap(
        name='draft_list',
        mcp_suffix='draft.list',
        title='列草稿',
        description='列项目草稿箱（按创建时间倒序）。',
        scope=_SCOPE_READ,
        risk_level='low',
        properties={
            'project_id': {'type': 'integer'},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 300, 'default': 100},
        },
        required=['project_id'],
        page_rank=189,
        tags=['creator', 'draft', 'list', 'read'],
    ),
    _cap(
        name='draft_delete',
        mcp_suffix='draft.delete',
        title='删草稿',
        description='删掉一条草稿。',
        scope=_SCOPE_MANAGE,
        risk_level='low',
        properties={'draft_id': {'type': 'integer'}},
        required=['draft_id'],
        page_rank=190,
        tags=['creator', 'draft', 'delete', 'manage'],
    ),
    _cap(
        name='draft_promote',
        mcp_suffix='draft.promote',
        title='草稿转正',
        description='把草稿转正为正式内容（draft → Content 进创作流水线），沿用草稿 title/target_platforms；'
        '转正后原草稿删除，避免草稿箱与内容流水线两处并存同一条。',
        scope=_SCOPE_MANAGE,
        risk_level='low',
        properties={'draft_id': {'type': 'integer'}},
        required=['draft_id'],
        page_rank=191,
        tags=['creator', 'draft', 'promote', 'manage'],
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
    _cap(
        name='topic_add',
        mcp_suffix='topic.add',
        title='加一条选题',
        description='往选题池单条加一个选题（§6.6「手动加选题」；人和分身共用）。分身提供 title，可选 reason/angle，service 落库；零 fake，不编造热度/潜力。',
        scope=_SCOPE_MANAGE,
        risk_level='low',
        properties={
            'project_id': {'type': 'integer'},
            'title': {'type': 'string', 'description': '选题标题'},
            'reason': {'type': ['string', 'null'], 'description': '选题理由（可选）'},
            'angle': {'type': ['string', 'null'], 'description': '创意角度（可选，存入 creative_angles）'},
        },
        required=['project_id', 'title'],
        page_rank=195,
        tags=['creator', 'topic', 'add', 'manage'],
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
        description='保存阶段产出（research/outline/draft/final_draft/cover/storyboard/voiceover/final_video）。同阶段再存 bump version 留迭代痕迹。视频轨成片用 stage=final_video + 本地引用 asset_refs 回流（成片本地优先不上云）。',
        scope=_SCOPE_MANAGE,
        risk_level='low',
        properties={
            'content_id': {'type': 'integer'},
            'stage': {
                'type': 'string',
                'minLength': 1,
                'description': '阶段标识（research/outline/first_draft/final_draft/cover/storyboard/voiceover/final_video；视频轨成片用 final_video）',
            },
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
    # 「可搜索域目录」：namespace 关键词 → 一句话（云端 tool.search 描述自动汇聚，agent 据此选关键词搜该域工具）。
    'domain_summary': {'creator': '创作运营（选题/创作/审核/发布/复盘）'},
    'version': '1.0.0',
    'workspace_scope': ['personal', 'enterprise'],
    'collaboration_mode': 'workspace_shared',
    'project_aware': True,
    'project_required': False,
    'project_integration': 'project_aware',
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
    # 资源描述符（doc31 §2，RC-P6）：创作项目（号即上下文）→ hasn://creator/projects/{server_id}，应用内详情路由打开。
    # 内容产物（contents）在项目上下文内打开（/projects/:id/contents/:cid，双 id 非单 id），不单列资源域。
    'resources': [
        {
            'resource_kind': 'creator.project',
            'uri_domain': 'creator/projects',  # → hasn://creator/projects/{server_id}（doc08 §3 登记 internal_route 域）
            'open': {'mode': 'internal_route', 'route_template': '/apps/creator/projects/:id'},
            'card': {'verb': '创作项目', 'action_label': '打开创作项目'},
            'artifact_kind': 'resource',
        }
    ],
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
        project_aware=True,
        project_required=False,
    )
