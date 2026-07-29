"""获客（hasn_growth，app_id=growth）AI-Native 内置 manifest + App 声明。

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

from typing import TYPE_CHECKING, Any

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

_SCOPE_READ = 'growth:read'
_SCOPE_MANAGE = 'growth:manage'
_SCOPE_OUTREACH = 'growth:outreach'
_SCOPE_COLLECT = 'growth:collect'

_RESOURCE_PARAM_TYPES = {
    'growth_project_id': 'growth_project',
    'customer_id': 'growth_customer',
    'opportunity_id': 'growth_opportunity',
}


def _resource_access_for_cap(cap: dict) -> list[dict]:
    """按工具入参生成 Growth 资源权限门声明。

    读工具需要 viewer，写工具需要 editor；线索工具的漏斗参数按线索池资源判权。
    可选资源参数显式声明 required=False，避免统一权限门把可选参数误判成必填。
    """
    properties = (cap.get('input_schema') or {}).get('properties') or {}
    required = set((cap.get('input_schema') or {}).get('required') or [])
    tool_id = str(cap.get('tool_id') or '')
    need = 'viewer' if cap.get('required_scopes') == [_SCOPE_READ] else 'editor'
    declarations: list[dict] = []

    for param, default_type in _RESOURCE_PARAM_TYPES.items():
        if param not in properties:
            continue
        resource_type = (
            'growth_leads' if param == 'growth_project_id' and tool_id.startswith('hasn_growth.lead_') else default_type
        )
        declaration: dict[str, Any] = {
            'param': param,
            'type': resource_type,
            'need': need,
        }
        if param not in required:
            declaration['required'] = False
        declarations.append(declaration)
    return declarations


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
    tool = {
        'tool_id': cap['tool_id'],
        'mcp_name': cap['mcp_name'],
        'transport': 'gateway_internal',
        'handler': str(cap['tool_id']).replace('hasn_growth.', 'growth.', 1),
        'required_scopes': scopes,
        'risk_level': cap['risk_level'],
        'idempotent': scopes == [_SCOPE_READ],
    }
    resource_access = _resource_access_for_cap(cap)
    if resource_access:
        tool['resource_access'] = resource_access
    return tool


# 获客 33 工具能力声明（云端 gateway_internal）。顺序即 tools[] 顺序；
# lead_request（2.1 请求线索·用户端默认入口）为第 1 条；其后 lookup/search/enrich_company
# （GROWTH-QCC-4 企业数据读穿中台）；customer_reassign（GE4）为末条。
_CAPABILITIES = [
    _cap(
        name='project_get',
        mcp_suffix='project.get',
        title='读取获客项目',
        description='按 Growth UUID 或平台项目 UUID 读取漏斗、生命周期与真实开通步骤。',
        scope=_SCOPE_READ,
        risk_level='low',
        properties={
            'growth_project_id': {
                'type': ['string', 'null'],
                'format': 'uuid',
                'description': 'Growth 云端权威 UUID；与 platform_project_id 二选一',
            },
            'platform_project_id': {
                'type': ['string', 'null'],
                'format': 'uuid',
                'description': '平台项目云端权威 UUID；与 growth_project_id 二选一',
            },
        },
        required=[],
        page_rank=10,
        tags=['growth', 'project', 'read'],
    ),
    _cap(
        name='project_create',
        mcp_suffix='project.create',
        title='启用获客项目',
        description=(
            '为主人名下平台项目幂等创建唯一获客漏斗并启动真实基础资源开通；返回已接受状态与每个步骤，不伪造同步完成。'
        ),
        scope=_SCOPE_MANAGE,
        risk_level='medium',
        properties={
            'platform_project_id': {
                'type': 'string',
                'format': 'uuid',
                'description': '平台项目云端权威 UUID',
            },
            'trace_id': {
                'type': 'string',
                'format': 'uuid',
                'description': '本次启用意图的稳定追踪 UUID；重试复用',
            },
            'idempotency_key': {
                'type': 'string',
                'minLength': 1,
                'maxLength': 200,
                'description': '稳定业务幂等键；重试复用',
            },
            'name': {
                'type': ['string', 'null'],
                'maxLength': 200,
                'description': '漏斗名称；空值沿用平台项目名',
            },
            'tagline': {
                'type': ['string', 'null'],
                'maxLength': 500,
                'description': '一句话说明',
            },
        },
        required=['platform_project_id', 'trace_id', 'idempotency_key'],
        page_rank=10,
        tags=['growth', 'project', 'create', 'provision'],
    ),
    _cap(
        name='project_update',
        mcp_suffix='project.update',
        title='更新获客项目',
        description='更新未归档获客漏斗的名称或一句话说明，并自动登记项目产物。',
        scope=_SCOPE_MANAGE,
        risk_level='medium',
        properties={
            'growth_project_id': {
                'type': 'string',
                'format': 'uuid',
                'description': 'Growth 云端权威 UUID',
            },
            'name': {
                'type': ['string', 'null'],
                'maxLength': 200,
                'description': '新名称',
            },
            'tagline': {
                'type': ['string', 'null'],
                'maxLength': 500,
                'description': '新说明；空字符串表示清空',
            },
        },
        required=['growth_project_id'],
        page_rank=7,
        tags=['growth', 'project', 'update'],
    ),
    _cap(
        name='project_update_profile',
        mcp_suffix='project.update_profile',
        title='提交产品与 ICP 画像建议',
        description=(
            '基于同项目 Knowledge 的真实文档版本提交待 Owner 确认的产品/ICP 画像建议；'
            '本工具不会直接改写当前已确认画像。'
        ),
        scope=_SCOPE_MANAGE,
        risk_level='medium',
        properties={
            'growth_project_id': {
                'type': 'string',
                'format': 'uuid',
                'description': 'Growth 云端权威 UUID',
            },
            'expected_version': {
                'type': 'integer',
                'minimum': 1,
                'description': '生成建议时读取的当前画像版本',
            },
            'product_profile': {
                'type': 'object',
                'description': '精简产品画像；证据只引用稳定 Knowledge 文档',
            },
            'icp_profile': {
                'type': 'object',
                'description': '精简 ICP 画像与排除条件',
            },
            'knowledge_document_ids': {
                'type': 'array',
                'items': {'type': 'integer', 'minimum': 1},
                'minItems': 1,
                'uniqueItems': True,
                'description': '同项目 Knowledge 中参与建议的云端文档 ID',
            },
            'trace_id': {
                'type': 'string',
                'format': 'uuid',
                'description': '本次建议意图的稳定追踪 UUID；重试复用',
            },
            'idempotency_key': {
                'type': 'string',
                'minLength': 1,
                'maxLength': 200,
                'description': '稳定业务幂等键；重试复用',
            },
        },
        required=[
            'growth_project_id',
            'expected_version',
            'product_profile',
            'icp_profile',
            'knowledge_document_ids',
            'trace_id',
            'idempotency_key',
        ],
        page_rank=7,
        tags=['growth', 'project', 'profile', 'suggestion'],
    ),
    _cap(
        name='project_pause',
        mcp_suffix='project.pause',
        title='暂停获客项目',
        description='暂停漏斗自动动作并保持已有资源可读；不会删除数据或隐式归档平台项目。',
        scope=_SCOPE_MANAGE,
        risk_level='medium',
        properties={
            'growth_project_id': {
                'type': 'string',
                'format': 'uuid',
                'description': 'Growth 云端权威 UUID',
            },
        },
        required=['growth_project_id'],
        page_rank=8,
        tags=['growth', 'project', 'pause'],
    ),
    _cap(
        name='lead_request',
        mcp_suffix='lead.request',
        title='请求线索',
        description=(
            '请求线索（用户端默认入口）：平台**先查公共池**命中即交付（零采集成本），'
            '缺口才后台**补爬**回流公共池补足。按行业/地区/关键词/城市检索，行业自动归一到标准类目。'
            'PII 恒脱敏，历史 growth:pii scope 不再授权 Agent 明文。'
        ),
        scope=_SCOPE_COLLECT,
        risk_level='medium',
        properties={
            'industry': {'type': ['string', 'null'], 'description': '行业（如「LED显示屏」，自动归一标准类目检索）'},
            'region': {'type': ['string', 'null'], 'description': '地区/省'},
            'city': {'type': ['string', 'null'], 'description': '城市'},
            'keyword': {'type': ['string', 'null'], 'description': '关键词（公司/产品等自由文本）'},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 500, 'default': 20, 'description': '请求线索条数 N'},
        },
        required=[],
        page_rank=9,
        tags=['growth', 'lead', 'request', 'collect'],
    ),
    _cap(
        name='lookup_company',
        mcp_suffix='lookup_company',
        title='查企业全画像',
        description=(
            '按企业名/统一社会信用代码取企业全画像（工商登记/法定代表人/行业/地址…）。'
            '平台**先查公共池**命中即返回（零成本秒回），未命中才经企查查取数并**自动结构化入池**；'
            '返回带 `lead_contact_id`，可直接用于 lead.qualify/建跟进。PII 恒脱敏。'
        ),
        scope=_SCOPE_COLLECT,
        risk_level='medium',
        properties={
            'query': {'type': 'string', 'minLength': 1, 'maxLength': 200, 'description': '企业名或统一社会信用代码'},
            'force_refresh': {'type': 'boolean', 'default': False, 'description': '强制重取（跳过池命中）'},
        },
        required=['query'],
        page_rank=6,
        tags=['growth', 'company', 'lookup', 'enterprise'],
    ),
    _cap(
        name='search_companies',
        mcp_suffix='search_companies',
        title='找企业（关键词/行业/地域）',
        description=(
            '按关键词/行业/地域批量找企业：先查公共池条件匹配，不足时经企查查补足并**自动结构化入池**；'
            '返回带 `lead_contact_id` 的企业列表，供 ICP 匹配/批量建线索。PII 默认脱敏。'
        ),
        scope=_SCOPE_COLLECT,
        risk_level='medium',
        properties={
            'query': {'type': ['string', 'null'], 'description': '关键词（公司/产品等自由文本）'},
            'industry': {'type': ['string', 'null'], 'description': '行业（自动归一标准类目检索）'},
            'region': {'type': ['string', 'null'], 'description': '地区/省'},
            'city': {'type': ['string', 'null'], 'description': '城市'},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20, 'default': 5, 'description': '返回企业数 N'},
        },
        required=[],
        page_rank=7,
        tags=['growth', 'company', 'search', 'enterprise'],
    ),
    _cap(
        name='enrich_company',
        mcp_suffix='enrich_company',
        title='深度富化企业（风险/知识产权/经营/高管/变更）',
        description=(
            '按维度深度富化已有线索：风险(risk)/知识产权(ipr)/经营(operation)/高管(executive)/变更历史(history)。'
            '维度缓存 TTL 内命中即返回（省成本），未命中才经企查查取数并**全量保真入库**。'
            '须先用 lookup/search 获取该线索（按 `lead_contact_id` 富化）。'
        ),
        scope=_SCOPE_COLLECT,
        risk_level='medium',
        properties={
            'lead_contact_id': {'type': 'integer', 'description': 'lookup/search 返回的线索 ID'},
            'dimensions': {
                'type': 'array',
                'items': {'type': 'string', 'enum': ['risk', 'ipr', 'operation', 'executive', 'history']},
                'minItems': 1,
                'description': '要富化的维度子集',
            },
            'tool': {'type': ['string', 'null'], 'description': '可选：显式指定 qcc 工具 canonical 名（覆盖默认解析）'},
            'force_refresh': {'type': 'boolean', 'default': False, 'description': '强制重取（跳过 TTL 缓存）'},
        },
        required=['lead_contact_id', 'dimensions'],
        page_rank=8,
        tags=['growth', 'company', 'enrich', 'enterprise'],
    ),
    _cap(
        name='collect_start',
        mcp_suffix='collect.start',
        title='发起线索采集（高级/补爬）',
        description=(
            '按关键词/URL 直接发起采集任务（包装 collection_job 创建，驱动既有采集引擎）。恒落主人私有池。'
            '**用户端默认走 lead.request 请求线索**；本工具为高级/管理员显式补爬入口。'
        ),
        scope=_SCOPE_COLLECT,
        risk_level='medium',
        properties={
            'keyword': {'type': 'string', 'minLength': 1, 'maxLength': 200, 'description': '采集关键词或 URL'},
            'source_types': {
                'type': 'array',
                'items': {'type': 'string'},
                'description': '来源类型（默认 [public_web]）',
            },
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
        name='lead_ingest',
        mcp_suffix='lead.ingest',
        title='批量写入项目线索',
        description=(
            '把真实来源或受控导入的线索按稳定 batch_id 写入指定获客项目；公共企业事实全局去重，'
            '私有联系人按当前主体加密隔离，并逐行返回确定性结果与错误。'
        ),
        scope=_SCOPE_COLLECT,
        risk_level='medium',
        properties={
            'growth_project_id': {
                'type': 'string',
                'format': 'uuid',
                'description': '获客项目云端权威 UUID',
            },
            'batch_id': {
                'type': 'string',
                'minLength': 1,
                'maxLength': 64,
                'pattern': '^[A-Za-z0-9][A-Za-z0-9._:-]*$',
                'description': '调用方生成并在重试时复用的稳定批次 ID',
            },
            'items': {
                'type': 'array',
                'minItems': 1,
                'maxItems': 100,
                'items': {
                    'type': 'object',
                    'properties': {
                        'client_ref': {'type': 'string', 'minLength': 1, 'maxLength': 64},
                        'lead_contact_id': {'type': ['integer', 'null'], 'minimum': 1},
                        'company_name': {'type': ['string', 'null'], 'maxLength': 255},
                        'website': {'type': ['string', 'null'], 'maxLength': 500},
                        'domain': {'type': ['string', 'null'], 'maxLength': 255},
                        'country': {'type': ['string', 'null'], 'maxLength': 8},
                        'region': {'type': ['string', 'null'], 'maxLength': 100},
                        'city': {'type': ['string', 'null'], 'maxLength': 100},
                        'industry': {'type': ['string', 'null'], 'maxLength': 100},
                        'source_kind': {'type': 'string', 'minLength': 1, 'maxLength': 32},
                        'source_tool': {'type': ['string', 'null'], 'maxLength': 64},
                        'source_ref': {'type': 'string', 'minLength': 1, 'maxLength': 255},
                        'source_meta': {'type': 'object'},
                        'match_score': {'type': ['number', 'null'], 'minimum': 0, 'maximum': 100},
                        'score_breakdown': {'type': 'object'},
                        'scoring_version': {'type': ['string', 'null'], 'maxLength': 64},
                        'evidence_fresh_at': {'type': ['string', 'null'], 'format': 'date-time'},
                        'private_contact': {
                            'type': ['object', 'null'],
                            'description': '仅当前主体有合法来源时填写；服务端加密隔离，绝不进入公共池',
                        },
                    },
                    'required': ['client_ref', 'source_kind', 'source_ref'],
                    'additionalProperties': False,
                },
            },
        },
        required=['growth_project_id', 'batch_id', 'items'],
        page_rank=12,
        tags=['growth', 'lead', 'ingest', 'batch'],
    ),
    _cap(
        name='lead_list',
        mcp_suffix='lead.list',
        title='分页读取项目线索',
        description=(
            '按项目分页读取线索关联行，返回状态、评分版本、来源、证据新鲜度及逐维解释；'
            '企业成员的可见范围由后端重新裁剪。'
        ),
        scope=_SCOPE_READ,
        risk_level='low',
        properties={
            'growth_project_id': {'type': 'string', 'format': 'uuid'},
            'page': {'type': 'integer', 'minimum': 1, 'default': 1},
            'size': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 20},
            'status': {
                'type': ['string', 'null'],
                'enum': ['new', 'qualified', 'dismissed', None],
            },
            'query': {'type': ['string', 'null'], 'maxLength': 200},
            'min_score': {'type': ['number', 'null'], 'minimum': 0, 'maximum': 100},
            'freshness': {
                'type': ['string', 'null'],
                'enum': ['fresh', 'stale', 'unknown', None],
            },
            'view': {'type': 'string', 'enum': ['team', 'mine'], 'default': 'team'},
            'assignee': {'type': ['string', 'null'], 'maxLength': 40},
        },
        required=['growth_project_id'],
        page_rank=12,
        tags=['growth', 'lead', 'list', 'read'],
    ),
    _cap(
        name='lead_search',
        mcp_suffix='lead.search',
        title='检索线索池',
        description='检索线索池（关键词过滤）。Agent PII 恒脱敏（§10.2）。',
        scope=_SCOPE_READ,
        risk_level='low',
        properties={
            'query': {'type': ['string', 'null'], 'description': '关键词（公司/行业/标签）'},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 20},
            'growth_project_id': {
                'type': ['string', 'null'],
                'format': 'uuid',
                'description': '获客项目云端 UUID；传入后新表优先读取',
            },
        },
        required=[],
        page_rank=12,
        tags=['growth', 'lead', 'search', 'read'],
    ),
    _cap(
        name='lead_get',
        mcp_suffix='lead.get',
        title='取线索详情',
        description='单线索详情（含多源证据；Agent PII 恒脱敏）。',
        scope=_SCOPE_READ,
        risk_level='low',
        properties={
            'lead_contact_id': {'type': 'integer'},
            'growth_project_id': {
                'type': ['string', 'null'],
                'format': 'uuid',
                'description': '获客项目云端 UUID；传入后新表优先读取',
            },
        },
        required=['lead_contact_id'],
        page_rank=13,
        tags=['growth', 'lead', 'get', 'read'],
    ),
    _cap(
        name='lead_qualify',
        mcp_suffix='lead.qualify',
        title='线索晋级为客户',
        description='按项目线索晋级客户，并在同一事务建立首个活动、接续任务和归因。重复调用返回同一客户。',
        scope=_SCOPE_MANAGE,
        risk_level='medium',
        properties={
            'growth_project_id': {'type': 'string', 'format': 'uuid'},
            'project_lead_id': {'type': 'integer', 'minimum': 1},
            'profile': {'type': ['object', 'null'], 'description': 'AI 画像（行业/规模/痛点/预算/角色）'},
            'intent_score': {'type': ['number', 'null'], 'description': '初始意向分'},
        },
        required=['growth_project_id', 'project_lead_id'],
        page_rank=14,
        tags=['growth', 'lead', 'qualify', 'manage'],
    ),
    _cap(
        name='lead_dismiss',
        mcp_suffix='lead.dismiss',
        title='忽略或恢复项目线索',
        description='按项目关联行忽略线索并记录原因，或恢复为待处理；已晋级线索不可回退。',
        scope=_SCOPE_MANAGE,
        risk_level='low',
        properties={
            'growth_project_id': {'type': 'string', 'format': 'uuid'},
            'project_lead_id': {'type': 'integer', 'minimum': 1},
            'action': {'type': 'string', 'enum': ['dismiss', 'restore']},
            'reason': {'type': ['string', 'null'], 'maxLength': 500},
        },
        required=['growth_project_id', 'project_lead_id', 'action'],
        page_rank=15,
        tags=['growth', 'lead', 'dismiss', 'restore', 'manage'],
    ),
    _cap(
        name='customer_list',
        mcp_suffix='customer.list',
        title='列客户',
        description='客户列表（含生命周期/意向分/跟进游标）。企业模式按视角裁剪（team=全部 / mine=自己负责）。',
        scope=_SCOPE_READ,
        risk_level='low',
        properties={
            'growth_project_id': {'type': 'string', 'format': 'uuid'},
            'lifecycle_status': {'type': ['string', 'null'], 'description': '按生命周期过滤'},
            'view': {
                'type': 'string',
                'enum': ['team', 'mine'],
                'default': 'team',
                'description': '企业视角：team 全部 / mine 仅自己负责',
            },
            'assignee': {'type': ['string', 'null'], 'description': '按负责人 hasn_id 过滤（企业经理用）'},
            'page': {'type': 'integer', 'minimum': 1, 'default': 1},
            'size': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 20},
        },
        required=['growth_project_id'],
        page_rank=16,
        tags=['growth', 'customer', 'list', 'read'],
    ),
    _cap(
        name='customer_get',
        mcp_suffix='customer.get',
        title='取客户详情',
        description='项目客户详情（含脱敏画像、时间线、接续任务、商机、触达和归因）。',
        scope=_SCOPE_READ,
        risk_level='low',
        properties={
            'growth_project_id': {'type': 'string', 'format': 'uuid'},
            'customer_id': {'type': 'integer'},
        },
        required=['growth_project_id', 'customer_id'],
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
        properties={
            'growth_project_id': {'type': 'string', 'format': 'uuid'},
            'customer_id': {'type': 'integer'},
        },
        required=['growth_project_id', 'customer_id'],
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
            'growth_project_id': {'type': 'string', 'format': 'uuid'},
            'customer_id': {'type': 'integer'},
            'profile': {'type': ['object', 'null']},
            'intent_score': {'type': ['number', 'null']},
            'tags': {'type': ['array', 'null'], 'items': {'type': 'string'}},
            'lifecycle_status': {'type': ['string', 'null']},
            'followup_task_id': {'type': ['string', 'null'], 'description': '绑定当前跟进任务（hasn_task task_uuid）'},
        },
        required=['growth_project_id', 'customer_id'],
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
            'growth_project_id': {'type': 'string', 'format': 'uuid'},
            'customer_id': {'type': 'integer'},
            'kind': {'type': 'string', 'description': '活动类型 note/call/meeting/...'},
            'content': {'type': 'string', 'minLength': 1},
            'opportunity_id': {'type': ['integer', 'null']},
        },
        required=['growth_project_id', 'customer_id', 'kind', 'content'],
        page_rank=20,
        tags=['growth', 'activity', 'log', 'manage'],
    ),
    _cap(
        name='customer_reassign',
        mcp_suffix='customer.reassign',
        title='分配/转移客户负责人',
        description=(
            '企业经理分配/转移客户负责人（GE4，仅企业经理主人的分身；'
            'assignee 为人或分身 hasn_id）。非经理由 service 拒。'
        ),
        scope=_SCOPE_MANAGE,
        risk_level='medium',
        properties={
            'growth_project_id': {'type': 'string', 'format': 'uuid'},
            'customer_id': {'type': 'integer'},
            'assignee': {
                'type': 'string',
                'minLength': 1,
                'maxLength': 64,
                'description': '新负责人 hasn_id（人或分身）',
            },
        },
        required=['growth_project_id', 'customer_id', 'assignee'],
        page_rank=21,
        tags=['growth', 'customer', 'assign', 'manage', 'enterprise'],
    ),
    _cap(
        name='outreach_draft',
        mcp_suffix='outreach.draft',
        title='起草项目触达',
        description='保存项目触达草稿，不审批、不排队、不发送；重试必须复用 idempotency_key。',
        scope=_SCOPE_OUTREACH,
        risk_level='medium',
        properties={
            'growth_project_id': {'type': 'string', 'format': 'uuid'},
            'customer_id': {'type': 'integer'},
            'channel': {'type': 'string', 'description': '渠道 manual_assist/wechat/email/hasn_dm/...'},
            'content': {'type': 'string', 'minLength': 1},
            'subject': {'type': ['string', 'null']},
            'intent_note': {'type': ['string', 'null']},
            'content_assets': {'type': ['object', 'null']},
            'opportunity_id': {'type': ['integer', 'null']},
            'idempotency_key': {'type': 'string', 'minLength': 1, 'maxLength': 200},
        },
        required=[
            'growth_project_id',
            'customer_id',
            'channel',
            'content',
            'idempotency_key',
        ],
        page_rank=22,
        tags=['growth', 'outreach', 'draft'],
    ),
    _cap(
        name='outreach_submit',
        mcp_suffix='outreach.submit',
        title='提交项目触达审批',
        description='按内容版本提交审批；首次触达强制待主人审批，返回正交审批态与投递态。',
        scope=_SCOPE_OUTREACH,
        risk_level='high',
        properties={
            'growth_project_id': {'type': 'string', 'format': 'uuid'},
            'message_id': {'type': 'integer'},
            'expected_content_version': {'type': 'integer', 'minimum': 1},
            'idempotency_key': {'type': 'string', 'minLength': 1, 'maxLength': 200},
        },
        required=[
            'growth_project_id',
            'message_id',
            'expected_content_version',
            'idempotency_key',
        ],
        page_rank=23,
        tags=['growth', 'outreach', 'submit', 'approval'],
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
            'growth_project_id': {'type': 'string', 'format': 'uuid'},
            'customer_id': {'type': 'integer'},
            'channel': {'type': 'string', 'description': '渠道 manual_assist/wechat/email/hasn_dm/...'},
            'content': {'type': 'string', 'minLength': 1},
            'subject': {'type': ['string', 'null']},
            'intent_note': {'type': ['string', 'null'], 'description': '给主人看：为什么现在发这条'},
            'content_assets': {'type': ['object', 'null'], 'description': '随附素材（图片/文件引用等）'},
            'opportunity_id': {'type': ['integer', 'null']},
            'idempotency_key': {'type': 'string', 'minLength': 1, 'maxLength': 200},
        },
        required=[
            'growth_project_id',
            'customer_id',
            'channel',
            'content',
            'idempotency_key',
        ],
        page_rank=24,
        tags=['growth', 'outreach', 'send', 'compatibility'],
    ),
    _cap(
        name='outreach_status',
        mcp_suffix='outreach.status',
        title='查触达状态',
        description='查某客户触达消息状态/回复（draft/pending_approval/sent/replied/...）。',
        scope=_SCOPE_READ,
        risk_level='low',
        properties={
            'growth_project_id': {'type': 'string', 'format': 'uuid'},
            'customer_id': {'type': 'integer'},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200, 'default': 50},
        },
        required=['growth_project_id', 'customer_id'],
        page_rank=25,
        tags=['growth', 'outreach', 'status', 'read'],
    ),
    _cap(
        name='opportunity_list',
        mcp_suffix='opportunity.list',
        title='读取项目商机列表',
        description='按项目、客户、阶段或开放状态读取脱敏商机列表。',
        scope=_SCOPE_READ,
        risk_level='low',
        properties={
            'growth_project_id': {'type': 'string', 'format': 'uuid'},
            'customer_id': {'type': ['integer', 'null']},
            'stage': {
                'type': ['string', 'null'],
                'enum': [
                    'contacted',
                    'replied',
                    'proposal',
                    'negotiation',
                    'closed_won',
                    'closed_lost',
                    None,
                ],
            },
            'open_only': {'type': 'boolean', 'default': False},
            'view': {'type': 'string', 'enum': ['team', 'mine'], 'default': 'team'},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200, 'default': 200},
        },
        required=['growth_project_id'],
        page_rank=24,
        tags=['growth', 'opportunity', 'list', 'read'],
    ),
    _cap(
        name='opportunity_get',
        mcp_suffix='opportunity.get',
        title='读取商机',
        description='按项目读取单个商机的当前版本和关闭事实。',
        scope=_SCOPE_READ,
        risk_level='low',
        properties={
            'growth_project_id': {'type': 'string', 'format': 'uuid'},
            'opportunity_id': {'type': 'integer'},
        },
        required=['growth_project_id', 'opportunity_id'],
        page_rank=24,
        tags=['growth', 'opportunity', 'get', 'read'],
    ),
    _cap(
        name='opportunity_create',
        mcp_suffix='opportunity.create',
        title='立商机',
        description='为客户立商机（金额/币种/阶段/赢率可选）。',
        scope=_SCOPE_MANAGE,
        risk_level='medium',
        properties={
            'growth_project_id': {'type': 'string', 'format': 'uuid'},
            'customer_id': {'type': 'integer'},
            'name': {'type': 'string', 'minLength': 1},
            'amount': {'type': ['number', 'null']},
            'currency': {'type': ['string', 'null'], 'pattern': '^[A-Z]{3}$'},
            'stage': {
                'type': ['string', 'null'],
                'enum': ['contacted', 'replied', 'proposal', 'negotiation', None],
                'description': '初始阶段（默认 contacted）',
            },
            'probability': {'type': ['number', 'null'], 'minimum': 0, 'maximum': 1, 'description': '赢率 0-1'},
            'idempotency_key': {'type': 'string', 'minLength': 1, 'maxLength': 150},
        },
        required=['growth_project_id', 'customer_id', 'name', 'idempotency_key'],
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
            'growth_project_id': {'type': 'string', 'format': 'uuid'},
            'opportunity_id': {'type': 'integer'},
            'stage': {
                'type': 'string',
                'enum': ['contacted', 'replied', 'proposal', 'negotiation'],
                'description': '目标阶段，仅限 contacted/replied/proposal/negotiation；成交/流失请用 deal.close',
            },
            'note': {'type': 'string', 'minLength': 1, 'maxLength': 500},
            'expected_version': {'type': 'integer', 'minimum': 1},
            'idempotency_key': {'type': 'string', 'minLength': 1, 'maxLength': 150},
        },
        required=[
            'growth_project_id',
            'opportunity_id',
            'stage',
            'note',
            'expected_version',
            'idempotency_key',
        ],
        page_rank=25,
        tags=['growth', 'opportunity', 'stage', 'manage'],
    ),
    _cap(
        name='deal_close',
        mcp_suffix='deal.close',
        title='成交/流失登记',
        description=(
            '成交/流失登记（won 需金额；附复盘 close_note / 败因 lost_reason）。'
            '商机进 closed_won/closed_lost，客户生命周期同步。'
        ),
        scope=_SCOPE_MANAGE,
        risk_level='high',
        properties={
            'growth_project_id': {'type': 'string', 'format': 'uuid'},
            'opportunity_id': {'type': 'integer'},
            'result': {'enum': ['won', 'lost'], 'description': '成交结果'},
            'amount': {'type': ['number', 'null'], 'exclusiveMinimum': 0, 'description': 'won 必填'},
            'currency': {
                'type': ['string', 'null'],
                'pattern': '^[A-Z]{3}$',
                'description': 'won 必填',
            },
            'close_note': {'type': ['string', 'null'], 'description': '复盘'},
            'lost_reason': {'type': ['string', 'null'], 'description': '败因（lost 时）'},
            'expected_version': {'type': 'integer', 'minimum': 1},
            'idempotency_key': {'type': 'string', 'minLength': 1, 'maxLength': 150},
        },
        required=[
            'growth_project_id',
            'opportunity_id',
            'result',
            'expected_version',
            'idempotency_key',
        ],
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
            'view': {
                'type': 'string',
                'enum': ['team', 'mine'],
                'default': 'team',
                'description': '企业视角：team 全部 / mine 仅自己负责',
            },
        },
        required=[],
        page_rank=27,
        tags=['growth', 'report', 'funnel', 'read'],
    ),
]


GROWTH_AI_NATIVE_MANIFEST: dict[str, Any] = {
    'app_id': 'growth',
    # 「可搜索域目录」：namespace 关键词 → 一句话（云端 tool.search 描述自动汇聚，agent 据此选关键词搜该域工具）。
    'domain_summary': {'growth': '获客（线索采集/触达/转化）'},
    'version': '1.1.0',
    'workspace_scope': ['personal', 'enterprise'],
    'collaboration_mode': 'workspace_shared',
    'project_aware': True,
    'project_required': True,
    'project_integration': 'project_required',
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
    # 获客是多资源应用，ref_type 决定工作会话产物应使用哪条稳定 URI。
    'resources': [
        {
            'resource_kind': 'growth.project',
            'ref_type': 'project',
            'uri_domain': 'growth/projects',
            'open': {
                'mode': 'internal_route',
                'route_template': '/apps/growth/projects/:id/overview',
            },
            'card': {'verb': '获客漏斗', 'action_label': '打开获客漏斗'},
            'artifact_kind': 'resource',
        },
        {
            'resource_kind': 'growth.leads',
            'ref_type': 'leads',
            'uri_domain': 'growth/leads',
            'open': {
                'mode': 'internal_route',
                'route_template': '/apps/growth/projects/:id/leads',
            },
            'card': {'verb': '获客线索池', 'action_label': '查看线索池'},
            'artifact_kind': 'resource',
        },
        {
            'resource_kind': 'growth.customer',
            'ref_type': 'customer',
            'uri_domain': 'growth/customers',
            'open': {'mode': 'internal_route', 'route_template': '/apps/growth/customers/:id'},
            'card': {'verb': '客户资料', 'action_label': '查看客户'},
            'artifact_kind': 'resource',
        },
        {
            'resource_kind': 'growth.opportunity',
            'ref_type': 'opportunity',
            'uri_domain': 'growth/opportunities',
            'open': {
                'mode': 'internal_route',
                'route_template': '/apps/growth/opportunities/:id',
            },
            'card': {'verb': '获客商机', 'action_label': '查看商机'},
            'artifact_kind': 'resource',
        },
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


def build_growth_app() -> App:
    """构造 growth 的 App（catalog seed 源 + 工作台入口）。

    UI 为 webui 原生路由（``ui_kind=None`` 内联导航至 ``/growth``，同 knowledge/community）。
    execution_mode=cloud（业务逻辑云端，工具走 gateway_internal）；install_policy=manual（获客非人人需要，
    default_mount=FALSE，用户在工作台主动挂载，设计 §3.2）。
    """
    from backend.app.hasn.service.app_catalog_registry import App

    return App(
        id='growth',
        name='获客',
        icon='brand-growth',
        description='让分身替你找客户、做跟进、促成交——每条线索、每一步推进都摆在你眼前。',
        scope=('personal', 'enterprise'),
        collaboration_mode='workspace_shared',
        entry_route='/apps/growth',
        install_policy='manual',
        execution_mode='cloud',
        project_aware=True,
        project_required=True,
    )
