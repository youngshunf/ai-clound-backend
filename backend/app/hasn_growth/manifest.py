"""获客（hasn_growth，app_id=growth）AI-Native 内置 manifest + WorkbenchApp 声明。

设计事实源：
- docs/AI自动获客任务系统/07-获客营销全链路AI-Native应用设计.md §3（应用身份/catalog/scope）+ §6（工具面）
- docs/hasn-node设计文档/14-AI-Native应用平台/16-应用目录与商业化管理统一设计.md（catalog/manifest 四正交概念）
- docs/hasn-node设计文档/14-AI-Native应用平台/15-AI-Native应用命名空间与目录约定.md（ADR-15）

命名铁律：`app_id='growth'`（不带 hasn_ 前缀，对齐 community↔hasn_community 先例）；模块/schema 仍 `hasn_growth`。
manifest 落**应用根目录** `app/hasn_growth/manifest.py`（对齐 hasn_deck/hasn_knowledge/hasn_publish），
不放公共 `app/hasn/service`，也不放 `service/` 子目录。

形态（设计 §3.1/§3.2，对齐 community/knowledge 云端工具模型）：
- `execution_mode='cloud'`、`transport_mode='cloud'`——获客是**纯云端业务应用**（CRM/获客/触达/成交），
  **零本地文件/电脑操作**，故 `hasn.growth.*` 工具一律走**云端 MCP**：经 Agent MCP Key 命中
  `/api/v1/mcp/streamable` → `app_tool_loader` 把 capability 投影成 AppTool（`execution_location='cloud'`）→
  `ai_native_runtime_gateway.call_tool`（transport=`gateway_internal`）→ 进程内直调
  `app/hasn_growth/service/growth_tool_handlers.py` → growth service。
  **不经 hasn-node 本地 hasn-mcp 注册、不经 daemon Agent 工具代理**——那是 task/deck 等「需要操作
  用户电脑/读写本地文件」才有的本地模式；获客没有本地理由。daemon `domains/growth` 仅保留 owner WebUI
  操作面的薄代理（人用），不承载 Agent 工具数据面。
- `tools[]` 由 `capabilities` 派生（`_tool_from_cap`），每条 `transport='gateway_internal'` +
  `handler='growth.<name>'` 指向 handler 注册表键；`capabilities[]` 同时承载发现/权限元数据控制面记录。

工具调用授权（D-v3-1，出厂全 Allow）：manifest 只声明各工具 `risk_level` 与 required_scopes；
最终由统一授权三态（owner 对 agent 的 capability_modes）在工具网关**调用时**强制。所有写类工具
`human_confirmation.required=False`（出厂 Allow）。`outreach.send` 的「过主人审批」是**业务态**
（落 `pending_approval`，§8），不走 ask_gate；工具如实回报状态，零 fake。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.app.hasn.service.workbench_app_registry import WorkbenchApp

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

_SCOPE_READ = 'growth:read'
_SCOPE_MANAGE = 'growth:manage'
_SCOPE_OUTREACH = 'growth:outreach'
_SCOPE_COLLECT = 'growth:collect'


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
    """hasn.growth.* 能力声明（读/写类一律出厂 Allow 免确认，16-doc D-v3-1；owner 可设 ask/deny override）。

    ``name`` 是 capability/tool_id 用的扁平标识（点号转下划线）；``mcp_suffix`` 是 ``hasn.growth.<suffix>``。
    """
    return {
        'capability_id': f'hasn_growth.{name}.capability',
        'name': title,
        'description': description,
        'tool_id': f'hasn_growth.{name}',
        'mcp_name': f'hasn.growth.{mcp_suffix}',
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

    ``handler`` = ``growth.<flat_name>``（把 tool_id 的 ``hasn_growth.`` 前缀换成 ``growth.``），
    对应 `ai_native_runtime_gateway._internal_handlers()` 注册表键 → `growth_tool_handlers.handle_growth_<name>`。
    ``idempotent``：纯读类（仅需 growth:read）可安全重试；写类（collect/manage/outreach）非幂等不自动重放。
    """
    scopes = list(cap.get('required_scopes') or [])
    return {
        'tool_id': cap['tool_id'],
        'mcp_name': cap['mcp_name'],
        'transport': 'gateway_internal',
        'handler': str(cap['tool_id']).replace('hasn_growth.', 'growth.', 1),
        'required_scopes': scopes,
        'risk_level': cap['risk_level'],
        'idempotent': scopes == [_SCOPE_READ],
    }


# 获客 17 工具能力声明（云端 gateway_internal）。顺序即 tools[] 顺序；
# customer_reassign（GE4 企业经理分配负责人）为第 18 条。
_CAPABILITIES = [
    _cap(
        name='collect_start',
        mcp_suffix='collect.start',
        title='发起线索采集',
        description='按关键词/URL 发起采集任务（包装 collection_job 创建，驱动既有采集引擎）。恒落主人私有池。',
        scope=_SCOPE_COLLECT,
        risk_level='medium',
        properties={
            'keyword': {'type': 'string', 'minLength': 1, 'maxLength': 200, 'description': '采集关键词或 URL'},
            'source_types': {'type': 'array', 'items': {'type': 'string'}, 'description': '来源类型（默认 [public_web]）'},
            'max_pages': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 5},
            'max_results': {'type': 'integer', 'minimum': 1, 'maximum': 10000, 'default': 100},
            'request_config': {'type': 'object', 'description': '采集引擎扩展配置（可选）'},
        },
        required=['keyword'],
        page_rank=10,
        tags=['growth', 'collect', 'lead'],
    ),
    _cap(
        name='collect_status',
        mcp_suffix='collect.status',
        title='查采集状态',
        description='查采集任务状态与统计（进度/已收/去重/拒绝）。',
        scope=_SCOPE_READ,
        risk_level='low',
        properties={'job_id': {'type': 'integer', 'description': 'collect.start 返回的采集任务 ID'}},
        required=['job_id'],
        page_rank=11,
        tags=['growth', 'collect', 'read'],
    ),
    _cap(
        name='lead_search',
        mcp_suffix='lead.search',
        title='检索线索池',
        description='检索线索池（关键词过滤）。PII 默认脱敏（§10.2，需 growth:pii 才回明文）。',
        scope=_SCOPE_READ,
        risk_level='low',
        properties={
            'query': {'type': ['string', 'null'], 'description': '关键词（公司/行业/标签）'},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 20},
        },
        required=[],
        page_rank=12,
        tags=['growth', 'lead', 'search', 'read'],
    ),
    _cap(
        name='lead_get',
        mcp_suffix='lead.get',
        title='取线索详情',
        description='单线索详情（含多源证据；PII 默认脱敏，需 growth:pii 才回明文）。',
        scope=_SCOPE_READ,
        risk_level='low',
        properties={'lead_contact_id': {'type': 'integer'}},
        required=['lead_contact_id'],
        page_rank=13,
        tags=['growth', 'lead', 'get', 'read'],
    ),
    _cap(
        name='lead_qualify',
        mcp_suffix='lead.qualify',
        title='线索晋级为客户',
        description='线索晋级为客户（建 customer + 画像快照 + 回写 lead 状态）。企业模式落企业池并 assignee=主人。',
        scope=_SCOPE_MANAGE,
        risk_level='medium',
        properties={
            'lead_contact_id': {'type': 'integer'},
            'profile': {'type': ['object', 'null'], 'description': 'AI 画像（行业/规模/痛点/预算/角色）'},
            'intent_score': {'type': ['number', 'null'], 'description': '初始意向分'},
        },
        required=['lead_contact_id'],
        page_rank=14,
        tags=['growth', 'lead', 'qualify', 'manage'],
    ),
    _cap(
        name='lead_dismiss',
        mcp_suffix='lead.dismiss',
        title='标记线索不合格',
        description='标记线索不合格（写 reason，不再推荐）。',
        scope=_SCOPE_MANAGE,
        risk_level='low',
        properties={
            'lead_contact_id': {'type': 'integer'},
            'reason': {'type': ['string', 'null'], 'description': '不合格原因'},
        },
        required=['lead_contact_id'],
        page_rank=15,
        tags=['growth', 'lead', 'dismiss', 'manage'],
    ),
    _cap(
        name='customer_list',
        mcp_suffix='customer.list',
        title='列客户',
        description='客户列表（含生命周期/意向分/跟进游标）。企业模式按视角裁剪（team=全部 / mine=自己负责）。',
        scope=_SCOPE_READ,
        risk_level='low',
        properties={
            'lifecycle_status': {'type': ['string', 'null'], 'description': '按生命周期过滤'},
            'view': {'type': 'string', 'enum': ['team', 'mine'], 'default': 'team', 'description': '企业视角：team 全部 / mine 仅自己负责'},
            'assignee': {'type': ['string', 'null'], 'description': '按负责人 hasn_id 过滤（企业经理用）'},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 20},
        },
        required=[],
        page_rank=16,
        tags=['growth', 'customer', 'list', 'read'],
    ),
    _cap(
        name='customer_get',
        mcp_suffix='customer.get',
        title='取客户详情',
        description='客户详情（含画像、跟进游标、关联商机）。',
        scope=_SCOPE_READ,
        risk_level='low',
        properties={'customer_id': {'type': 'integer'}},
        required=['customer_id'],
        page_rank=17,
        tags=['growth', 'customer', 'get', 'read'],
    ),
    _cap(
        name='customer_timeline',
        mcp_suffix='customer.timeline',
        title='客户活动时间线',
        description='客户活动时间线（跟进决策的主要输入：触达/回复/阶段变更/备注）。',
        scope=_SCOPE_READ,
        risk_level='low',
        properties={'customer_id': {'type': 'integer'}},
        required=['customer_id'],
        page_rank=18,
        tags=['growth', 'customer', 'timeline', 'read'],
    ),
    _cap(
        name='customer_update_profile',
        mcp_suffix='customer.update_profile',
        title='更新客户画像',
        description='更新画像/意向分/标签/生命周期（止损标 silent 也走这）。',
        scope=_SCOPE_MANAGE,
        risk_level='low',
        properties={
            'customer_id': {'type': 'integer'},
            'profile': {'type': ['object', 'null']},
            'intent_score': {'type': ['number', 'null']},
            'tags': {'type': ['array', 'null'], 'items': {'type': 'string'}},
            'lifecycle_status': {'type': ['string', 'null']},
            'followup_task_id': {'type': ['string', 'null'], 'description': '绑定当前跟进任务（hasn_task task_uuid）'},
        },
        required=['customer_id'],
        page_rank=19,
        tags=['growth', 'customer', 'profile', 'manage'],
    ),
    _cap(
        name='activity_log',
        mcp_suffix='activity.log',
        title='记一条活动',
        description='记一条活动（备注/电话纪要/会议结论），进客户时间线。',
        scope=_SCOPE_MANAGE,
        risk_level='low',
        properties={
            'customer_id': {'type': 'integer'},
            'kind': {'type': 'string', 'description': '活动类型 note/call/meeting/...'},
            'content': {'type': 'string', 'minLength': 1},
            'opportunity_id': {'type': ['integer', 'null']},
        },
        required=['customer_id', 'kind', 'content'],
        page_rank=20,
        tags=['growth', 'activity', 'log', 'manage'],
    ),
    _cap(
        name='customer_reassign',
        mcp_suffix='customer.reassign',
        title='分配/转移客户负责人',
        description='企业经理分配/转移客户负责人（GE4，仅企业经理主人的分身；assignee 为人或分身 hasn_id）。非经理由 service 拒。',
        scope=_SCOPE_MANAGE,
        risk_level='medium',
        properties={
            'customer_id': {'type': 'integer'},
            'assignee': {'type': 'string', 'minLength': 1, 'maxLength': 64, 'description': '新负责人 hasn_id（人或分身）'},
        },
        required=['customer_id', 'assignee'],
        page_rank=21,
        tags=['growth', 'customer', 'assign', 'manage', 'enterprise'],
    ),
    _cap(
        name='outreach_send',
        mcp_suffix='outreach.send',
        title='请求发送触达',
        description=(
            '请求发送对外触达：服务端合规检查 → 落 pending_approval（白名单命中且非首触达 → approved）；'
            '返回真实状态，不假装已发出。退订命中即 blocked_optout（§8 状态机）。待审批自动提醒主人。'
        ),
        scope=_SCOPE_OUTREACH,
        risk_level='high',
        properties={
            'customer_id': {'type': 'integer'},
            'channel': {'type': 'string', 'description': '渠道 manual_assist/wechat/email/hasn_dm/...'},
            'content': {'type': 'string', 'minLength': 1},
            'subject': {'type': ['string', 'null']},
            'intent_note': {'type': ['string', 'null'], 'description': '给主人看：为什么现在发这条'},
            'content_assets': {'type': ['object', 'null'], 'description': '随附素材（图片/文件引用等）'},
            'opportunity_id': {'type': ['integer', 'null']},
        },
        required=['customer_id', 'channel', 'content'],
        page_rank=22,
        tags=['growth', 'outreach', 'send'],
    ),
    _cap(
        name='outreach_status',
        mcp_suffix='outreach.status',
        title='查触达状态',
        description='查某客户触达消息状态/回复（draft/pending_approval/sent/replied/...）。',
        scope=_SCOPE_READ,
        risk_level='low',
        properties={
            'customer_id': {'type': 'integer'},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200, 'default': 50},
        },
        required=['customer_id'],
        page_rank=23,
        tags=['growth', 'outreach', 'status', 'read'],
    ),
    _cap(
        name='opportunity_create',
        mcp_suffix='opportunity.create',
        title='立商机',
        description='为客户立商机（金额/币种/阶段/赢率可选）。',
        scope=_SCOPE_MANAGE,
        risk_level='medium',
        properties={
            'customer_id': {'type': 'integer'},
            'name': {'type': 'string', 'minLength': 1},
            'amount': {'type': ['number', 'null']},
            'currency': {'type': ['string', 'null']},
            'stage': {'type': ['string', 'null'], 'description': '初始阶段（默认 contacted）'},
            'probability': {'type': ['number', 'null'], 'minimum': 0, 'maximum': 1, 'description': '赢率 0-1'},
        },
        required=['customer_id', 'name'],
        page_rank=24,
        tags=['growth', 'opportunity', 'create', 'manage'],
    ),
    _cap(
        name='opportunity_update_stage',
        mcp_suffix='opportunity.update_stage',
        title='推进商机阶段',
        description='推进/回退商机阶段（写 stage_change activity）。',
        scope=_SCOPE_MANAGE,
        risk_level='medium',
        properties={
            'opportunity_id': {'type': 'integer'},
            'stage': {'type': 'string', 'description': 'contacted/replied/proposal/negotiation/closed_won/closed_lost'},
            'note': {'type': ['string', 'null']},
        },
        required=['opportunity_id', 'stage'],
        page_rank=25,
        tags=['growth', 'opportunity', 'stage', 'manage'],
    ),
    _cap(
        name='deal_close',
        mcp_suffix='deal.close',
        title='成交/流失登记',
        description='成交/流失登记（won 需金额；附复盘 close_note / 败因 lost_reason）。商机进 closed_won/closed_lost，客户生命周期同步。',
        scope=_SCOPE_MANAGE,
        risk_level='high',
        properties={
            'opportunity_id': {'type': 'integer'},
            'result': {'enum': ['won', 'lost'], 'description': '成交结果'},
            'amount': {'type': ['number', 'null'], 'description': 'won 必填'},
            'close_note': {'type': ['string', 'null'], 'description': '复盘'},
            'lost_reason': {'type': ['string', 'null'], 'description': '败因（lost 时）'},
        },
        required=['opportunity_id', 'result'],
        page_rank=26,
        tags=['growth', 'deal', 'close', 'manage'],
    ),
    _cap(
        name='report_funnel',
        mcp_suffix='report.funnel',
        title='漏斗统计',
        description='漏斗统计（线索/客户/商机/成交各层计数与转化率，简报任务的数据源）。企业模式按视角裁剪。',
        scope=_SCOPE_READ,
        risk_level='low',
        properties={
            'view': {'type': 'string', 'enum': ['team', 'mine'], 'default': 'team', 'description': '企业视角：team 全部 / mine 仅自己负责'},
        },
        required=[],
        page_rank=27,
        tags=['growth', 'report', 'funnel', 'read'],
    ),
]


GROWTH_AI_NATIVE_MANIFEST = {
    'app_id': 'growth',
    'version': '1.0.0',
    'workspace_scope': ['personal', 'enterprise'],
    'collaboration_mode': 'workspace_shared',
    'execution_mode': 'cloud',
    # 云端工具模型（对齐 community/knowledge）：工具数据面经 gateway_internal 进程内直调云端 handler，
    # 不经本地 hasn-mcp / daemon Agent 代理（获客无本地文件/电脑操作的本地理由）。
    'transport_mode': 'cloud',
    # 通知：触达待审批 / 成交达成 → 主人提醒卡片（业务态非审批票据）。
    'notifications': {
        'emit': {
            'categories': ['app', 'reminder'],
            'card_message': True,
            'display_name': '获客',
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


def build_growth_workbench_app() -> WorkbenchApp:
    """构造 growth 的 WorkbenchApp（catalog seed 源 + 工作台入口）。

    UI 为 webui 原生路由（``ui_kind=None`` 内联导航至 ``/growth``，同 knowledge/community）。
    execution_mode=cloud（业务逻辑云端，工具走 gateway_internal）；install_policy=manual（获客非人人需要，
    default_mount=FALSE，用户在工作台主动挂载，设计 §3.2）。
    """
    from backend.app.hasn.service.workbench_app_registry import WorkbenchApp

    return WorkbenchApp(
        id='growth',
        name='获客',
        icon='target',
        description='让分身替你找线索、做跟进、促成交，每一步对你透明',
        scope=('personal', 'enterprise'),
        collaboration_mode='workspace_shared',
        entry_route='/growth',
        install_policy='manual',
        execution_mode='cloud',
    )
